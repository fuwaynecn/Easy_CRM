"""跟进记录服务。

可见性完全继承客户（与 contacts 同口径）：读取先 ``get_customer``（403/404 分开），
列表用 ``customer_query(actor)`` 的子查询表达可见客户集合，本模块不自拼作用域。
写入侧的「写一条跟进顺带建待办」「完成待办顺带写跟进」的事务边界见 create_activity
与 followups.service.complete_follow_up。
"""

from datetime import UTC, date, datetime

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.core.timeutil import day_bounds, ensure_aware
from app.db.scope import assert_customer_writable, customer_query
from app.modules.activities.models import ACTIVITY_TYPES, Activity
from app.modules.activities.schemas import ActivityCreate, ActivityUpdate
from app.modules.customers.links import validate_child_links
from app.modules.customers.models import Customer
from app.modules.customers.service import get_customer, mark_customer_activity
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, User


def _visible_conditions(
    actor: User, *, customer_id: int | None = None
) -> list[ColumnElement[bool]]:
    """跟进可见性的唯一构造点：软删过滤 + 客户作用域（+ 可选的单客户过滤）。"""
    conditions: list[ColumnElement[bool]] = [
        Activity.deleted_at.is_(None),
        Activity.customer_id.in_(
            customer_query(actor).with_only_columns(Customer.id)
        ),
    ]
    if customer_id is not None:
        conditions.append(Activity.customer_id == customer_id)
    return conditions


def _with_relations(stmt: Select) -> Select:
    """列表与详情只预加载操作人：联系人/机会姓名只有时间线才需要。"""
    return stmt.options(joinedload(Activity.operator))


def get_activity(db: Session, actor: User, activity_id: int) -> Activity:
    """按作用域取单条跟进：不存在 404，存在但看不到 403。"""
    activity = db.execute(
        _with_relations(select(Activity)).where(
            *_visible_conditions(actor), Activity.id == activity_id
        )
    ).scalar_one_or_none()
    if activity is not None:
        return activity
    exists = db.execute(
        select(Activity.id).where(
            Activity.id == activity_id, Activity.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise NotFoundError("跟进记录不存在")
    raise ForbiddenError("无权查看该跟进记录")


def validate_activity_type(value: str | None, label: str = "跟进方式") -> None:
    """枚举取值非法是 400（不是空列表）：空列表会把"参数拼错"显示成"没有数据"。

    公开函数：待办完成顺带写跟进时也要复用同一个枚举（不能各写一份）。
    """
    if value is not None and value not in ACTIVITY_TYPES:
        raise InvalidValueError(f"未知{label}：{value}")


def list_activities(
    db: Session,
    actor: User,
    *,
    customer_id: int | None = None,
    type: str | None = None,
    created_by: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Activity], int]:
    """作用域内的跟进分页，按 `activity_at` 倒序（第二键 id 倒序，翻页稳定）。

    筛选参数的空串按"不筛选"处理（前端清空下拉会带 `?type=`），与
    `followups.list_follow_ups` 的 `status` 保持同一语义。**写入路径仍然严格**：
    Task 4 的 create/update 传 `type=""` 必须 400（`validate_activity_type` 不加 `if`），
    否则空串会以非法枚举落库、只剩数据库 CHECK 兜底。
    """
    # 只有非空取值才校验：空串 = 不筛选，非法值（如 "telepathy"）仍是 400
    if type:
        validate_activity_type(type)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidValueError("开始日期不能晚于结束日期")
    if customer_id is not None:
        # 筛一个看不见的客户应当 403，而不是返回空列表（同 P2 机会列表）
        get_customer(db, actor, customer_id)

    stmt = select(Activity).where(
        *_visible_conditions(actor, customer_id=customer_id)
    )
    if type:
        stmt = stmt.where(Activity.type == type)
    if created_by is not None:
        stmt = stmt.where(Activity.created_by == created_by)
    if date_from is not None:
        stmt = stmt.where(Activity.activity_at >= day_bounds(date_from)[0])
    if date_to is not None:
        stmt = stmt.where(Activity.activity_at < day_bounds(date_to)[1])

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    stmt = _with_relations(stmt).order_by(
        Activity.activity_at.desc(), Activity.id.desc()
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return list(db.execute(stmt).scalars().all()), int(total)


def _clean_content(content: str | None) -> str:
    cleaned = (content or "").strip()
    if not cleaned:
        raise InvalidValueError("跟进内容不能为空")
    return cleaned


def _load_operator(db: Session, activity_id: int) -> Activity:
    """写完回读：`operator` 是 lazy="raise"，不回读就无法序列化成 ActivityRead。"""
    return db.execute(
        _with_relations(select(Activity))
        .where(Activity.id == activity_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def create_activity_record(
    db: Session,
    *,
    customer_id: int,
    contact_id: int | None,
    opportunity_id: int | None,
    type: str,
    content: str,
    activity_at: datetime,
    operator_id: int,
) -> Activity:
    """插入一条跟进并推进 `last_activity_at`，**不 commit**。

    这是「写一条跟进」的唯一原语：`create_activity` 与 `complete_follow_up`
    （完成待办顺带写跟进）都走这里，避免"其中一条路径忘了刷新 last_activity_at"。
    不做权限校验——调用方已经校验过客户可写性。
    """
    activity = Activity(
        customer_id=customer_id,
        contact_id=contact_id,
        opportunity_id=opportunity_id,
        type=type,
        content=content,
        activity_at=activity_at,
        created_by=operator_id,
    )
    db.add(activity)
    db.flush()
    mark_customer_activity(
        db, customer_id=customer_id, occurred_at=activity_at
    )
    return activity


def _assert_can_edit_activity(activity: Activity, actor: User) -> None:
    """裁定 5：跟进是"某人说过的原话"，只有本人（与管理员）能改。"""
    if actor.role == ROLE_ADMIN or activity.created_by == actor.id:
        return
    raise ForbiddenError("只能修改自己写的跟进记录")


def _assert_can_delete_activity(
    activity: Activity, actor: User, customer: Customer
) -> None:
    """裁定 5：本人可删；主管可删本部门；管理员全部。"""
    if actor.role == ROLE_ADMIN or activity.created_by == actor.id:
        return
    if (
        actor.role == ROLE_MANAGER
        and actor.department_id is not None
        and customer.department_id == actor.department_id
    ):
        return
    raise ForbiddenError("无权删除该跟进记录")


def create_activity(
    db: Session, actor: User, payload: ActivityCreate
) -> Activity:
    """写一条跟进；填了「下次跟进」则在同一个事务里建待办（spec §4.5）。

    公海客户对 sales/manager 是 403（`assert_customer_writable`），admin 可写
    （与 P1 的客户口径一致）。
    """
    validate_activity_type(payload.type)
    content = _clean_content(payload.content)
    customer = get_customer(db, actor, payload.customer_id)
    assert_customer_writable(customer, actor)
    validate_child_links(
        db,
        customer_id=customer.id,
        contact_id=payload.contact_id,
        opportunity_id=payload.opportunity_id,
    )

    activity = create_activity_record(
        db,
        customer_id=customer.id,
        contact_id=payload.contact_id,
        opportunity_id=payload.opportunity_id,
        type=payload.type,
        content=content,
        activity_at=ensure_aware(payload.activity_at, "跟进时间")
        or datetime.now(UTC),
        operator_id=actor.id,
    )

    if payload.next_follow_up is not None:
        # 函数内导入：followups.service 在模块顶层 import 本模块的
        # create_activity_record（完成待办顺带写跟进），顶层互相 import 会成环。
        from app.modules.followups.service import create_pending_follow_up_in_tx

        create_pending_follow_up_in_tx(
            db,
            customer_id=customer.id,
            contact_id=payload.contact_id,
            opportunity_id=payload.opportunity_id,
            due_at=payload.next_follow_up.due_at,
            content=payload.next_follow_up.content,
            created_by=actor.id,
        )

    db.commit()
    return _load_operator(db, activity.id)


def update_activity(
    db: Session, actor: User, activity_id: int, payload: ActivityUpdate
) -> Activity:
    """编辑跟进：只能改类型/内容/时间；**不碰 last_activity_at**（裁定 8）。"""
    activity = get_activity(db, actor, activity_id)
    customer = get_customer(db, actor, activity.customer_id)
    assert_customer_writable(customer, actor)
    _assert_can_edit_activity(activity, actor)

    changes = payload.model_dump(exclude_unset=True)
    if "type" in changes:
        if changes["type"] is None:
            raise InvalidValueError("跟进方式不能为空")
        validate_activity_type(changes["type"])
    if "content" in changes:
        changes["content"] = _clean_content(changes["content"])
    if "activity_at" in changes and changes["activity_at"] is None:
        raise InvalidValueError("跟进时间不能为空")
    if "activity_at" in changes:
        changes["activity_at"] = ensure_aware(
            changes["activity_at"], "跟进时间"
        )
    for field, value in changes.items():
        setattr(activity, field, value)
    db.commit()
    return _load_operator(db, activity.id)


def delete_activity(db: Session, actor: User, activity_id: int) -> None:
    """软删：已删的再删一次是 404（不是幂等 200）。"""
    activity = get_activity(db, actor, activity_id)
    customer = get_customer(db, actor, activity.customer_id)
    assert_customer_writable(customer, actor)
    _assert_can_delete_activity(activity, actor, customer)
    activity.deleted_at = datetime.now(UTC)
    db.commit()
