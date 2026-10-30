"""待办服务。

可见性完全继承客户（裁定 3）：读取先 ``get_customer``（403/404 分开），列表用
``customer_query(actor)`` 的子查询表达可见客户集合。
「我的待办」= 在可见集合再收窄到 ``customers.owner_id = actor.id``（spec §3.9），
**不是**按 ``follow_ups.created_by``——后者会让接手人看不到前任留下的待办。
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.core.timeutil import ensure_aware
from app.db.scope import assert_customer_writable, customer_query
from app.modules.activities.models import Activity
from app.modules.activities.service import (
    create_activity_record,
    validate_activity_type,
)
from app.modules.customers.links import validate_child_links
from app.modules.customers.models import Customer
from app.modules.customers.service import get_customer
from app.modules.followups.models import FOLLOW_UP_STATUSES, FollowUp
from app.modules.followups.schemas import (
    FollowUpCreate,
    FollowUpDone,
    FollowUpUpdate,
)
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, User


def _clean_content(content: str | None) -> str:
    """strip 后不能为空：全空格能通过 schema 的 ``min_length=1``，却是一行看不见的空白。"""
    cleaned = (content or "").strip()
    if not cleaned:
        raise InvalidValueError("待办内容不能为空")
    return cleaned


def create_pending_follow_up_in_tx(
    db: Session,
    *,
    customer_id: int,
    contact_id: int | None,
    opportunity_id: int | None,
    due_at: datetime,
    content: str,
    created_by: int,
) -> FollowUp:
    """插一条 pending 待办，**不 commit**。

    两个调用方：``POST /follow_ups``（Task 5）与「写跟进时填了下次跟进」
    （activities.create_activity）。后者是跨模块调用，因此这里不校验权限、不 commit
    ——权限与事务由调用方负责，重复校验会让"谁能写待办"出现两个口径。
    """
    due_at = ensure_aware(due_at, "待办到期时间")
    follow_up = FollowUp(
        customer_id=customer_id,
        contact_id=contact_id,
        opportunity_id=opportunity_id,
        due_at=due_at,
        content=_clean_content(content),
        status="pending",
        created_by=created_by,
    )
    db.add(follow_up)
    db.flush()  # 拿到自增 id，调用方要在同一响应里返回它
    return follow_up


def _visible_conditions(
    actor: User, *, customer_id: int | None = None, mine: bool = False
) -> list[ColumnElement[bool]]:
    """待办可见性的唯一构造点：软删过滤 + 客户作用域（+ 单客户 / 只看自己）。"""
    visible_customers = customer_query(actor).with_only_columns(Customer.id)
    if mine:
        visible_customers = visible_customers.where(Customer.owner_id == actor.id)
    conditions: list[ColumnElement[bool]] = [
        FollowUp.deleted_at.is_(None),
        FollowUp.customer_id.in_(visible_customers),
    ]
    if customer_id is not None:
        conditions.append(FollowUp.customer_id == customer_id)
    return conditions


def _with_relations(stmt: Select) -> Select:
    """客户名与创建人姓名随列表返回，两条 LEFT JOIN，不退化成逐条回查。"""
    return stmt.options(
        joinedload(FollowUp.customer), joinedload(FollowUp.operator)
    )


def validate_status(value: str | None) -> None:
    """枚举取值非法是 400；空串按"未筛选"处理（前端清空下拉时会带 ``?status=``）。"""
    if value and value not in FOLLOW_UP_STATUSES:
        raise InvalidValueError(f"未知待办状态：{value}")


def get_follow_up(db: Session, actor: User, follow_up_id: int) -> FollowUp:
    """按作用域取单条待办：不存在 404，存在但看不到 403。"""
    follow_up = db.execute(
        _with_relations(select(FollowUp)).where(
            *_visible_conditions(actor), FollowUp.id == follow_up_id
        )
    ).scalar_one_or_none()
    if follow_up is not None:
        return follow_up
    exists = db.execute(
        select(FollowUp.id).where(
            FollowUp.id == follow_up_id, FollowUp.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise NotFoundError("待办不存在")
    raise ForbiddenError("无权查看该待办")


def list_follow_ups(
    db: Session,
    actor: User,
    *,
    customer_id: int | None = None,
    status: str | None = None,
    mine: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[FollowUp], int]:
    """作用域内的待办分页，按 `due_at` 升序（第二键 id，翻页稳定）。"""
    validate_status(status)
    if customer_id is not None:
        get_customer(db, actor, customer_id)

    stmt = select(FollowUp).where(
        *_visible_conditions(actor, customer_id=customer_id, mine=mine)
    )
    if status:
        stmt = stmt.where(FollowUp.status == status)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    stmt = _with_relations(stmt).order_by(FollowUp.due_at.asc(), FollowUp.id.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return list(db.execute(stmt).scalars().all()), int(total)


@dataclass(frozen=True)
class Completion:
    """完成待办的结果：待办自身 + 可选的随附跟进。"""

    follow_up: FollowUp
    activity: Activity | None


def _load_full(db: Session, follow_up_id: int) -> FollowUp:
    """写完回读：`customer` / `operator` 都是 lazy="raise"。"""
    return db.execute(
        _with_relations(select(FollowUp))
        .where(FollowUp.id == follow_up_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _assert_can_edit_follow_up(
    follow_up: FollowUp, actor: User, customer: Customer
) -> None:
    """裁定 5 + 9：本人（创建者或客户当前负责人）与管理员可改。

    「创建者」也放行是刻意的：主管替下属客户建的待办，建完就该能改期；但主管
    **不能**改别人建的待办——那种情况只有客户负责人和管理员能改（见裁定 9）。
    """
    if (
        actor.role == ROLE_ADMIN
        or follow_up.created_by == actor.id
        or customer.owner_id == actor.id
    ):
        return
    raise ForbiddenError("只能修改自己或自己客户名下的待办")


def _assert_can_delete_follow_up(
    follow_up: FollowUp, actor: User, customer: Customer
) -> None:
    """裁定 5：本人可删；主管可删本部门；管理员全部。"""
    if (
        actor.role == ROLE_MANAGER
        and actor.department_id is not None
        and customer.department_id == actor.department_id
    ):
        return
    _assert_can_edit_follow_up(follow_up, actor, customer)


def create_follow_up(
    db: Session, actor: User, payload: FollowUpCreate
) -> FollowUp:
    """建待办：可见客户且可写（公海只读）才允许；建待办不推进 ``last_activity_at``。"""
    # 先校验时间再碰库：naive 会被 session 时区静默解释，None 会撞 NOT NULL 变 500
    due_at = ensure_aware(payload.due_at, "待办到期时间")
    if due_at is None:
        raise InvalidValueError("待办到期时间不能为空")

    customer = get_customer(db, actor, payload.customer_id)
    assert_customer_writable(customer, actor)
    validate_child_links(
        db,
        customer_id=customer.id,
        contact_id=payload.contact_id,
        opportunity_id=payload.opportunity_id,
    )
    follow_up = create_pending_follow_up_in_tx(
        db,
        customer_id=customer.id,
        contact_id=payload.contact_id,
        opportunity_id=payload.opportunity_id,
        due_at=due_at,
        content=payload.content,
        created_by=actor.id,
    )
    db.commit()
    return _load_full(db, follow_up.id)


def update_follow_up(
    db: Session, actor: User, follow_up_id: int, payload: FollowUpUpdate
) -> FollowUp:
    """编辑待办：改内容/到期时间，或在 pending ↔ cancelled 之间切换。

    所有校验都在 ``setattr`` 之前完成，不会出现"改了一半再抛"的半写状态：
    - ``done`` 是终态：任何 PATCH 都 400（完成/取消/改期都必须先想清楚）；
    - ``cancelled`` 只冻结内容与时间，``status → pending`` 仍可重开；
    - ``status="done"`` 永远走不通（完成只能走 ``complete_follow_up``，裁定 10）。
    """
    follow_up = get_follow_up(db, actor, follow_up_id)
    customer = get_customer(db, actor, follow_up.customer_id)
    assert_customer_writable(customer, actor)
    _assert_can_edit_follow_up(follow_up, actor, customer)

    # 终态无条件冻结：不能只在"这次 PATCH 带了 status"时才挡——否则改 content /
    # due_at 会绕过终态，等于完成后的待办还能被悄悄改写。
    if follow_up.status == "done":
        raise InvalidValueError("已完成的待办不能再修改")

    changes = payload.model_dump(exclude_unset=True)
    if follow_up.status == "cancelled" and ({"content", "due_at"} & changes.keys()):
        raise InvalidValueError("已取消的待办不能修改内容或时间，请先恢复为待办")
    if "status" in changes:
        status = changes["status"]
        if status is None:
            raise InvalidValueError("待办状态不能为空")
        if status == "done":
            raise InvalidValueError("标记完成请使用 POST /follow_ups/{id}/done")
        if status not in ("pending", "cancelled"):
            raise InvalidValueError(f"未知待办状态：{status}")
    if "due_at" in changes and changes["due_at"] is None:
        raise InvalidValueError("到期时间不能为空")
    if "due_at" in changes:
        changes["due_at"] = ensure_aware(changes["due_at"], "待办到期时间")
    if "content" in changes:
        changes["content"] = _clean_content(changes["content"])
    for field, value in changes.items():
        setattr(follow_up, field, value)
    db.commit()
    return _load_full(db, follow_up.id)


def complete_follow_up(
    db: Session, actor: User, follow_up_id: int, payload: FollowUpDone
) -> Completion:
    """完成待办；可选在同一事务里写一条跟进并刷新 last_activity_at（spec §4.5）。

    三件事一起成/一起不成：待办置 done、可选的跟进记录、``last_activity_at``。
    校验全部在写入之前：已完成的不能再完成、已取消的不能完成、不勾选却指定跟进
    方式一律 400。

    权限比"能写客户"更紧一档：完成会改变待办归属状态的最终结论，按裁定 5 只有
    创建者、客户当前负责人与管理员能做；主管对下属的待办**只能删**（明明写不了
    内容却能替人"完成"，等于绕过编辑权限）。
    """
    follow_up = get_follow_up(db, actor, follow_up_id)
    customer = get_customer(db, actor, follow_up.customer_id)
    assert_customer_writable(customer, actor)
    _assert_can_edit_follow_up(follow_up, actor, customer)

    if follow_up.status == "done":
        raise InvalidValueError("该待办已完成")
    if follow_up.status == "cancelled":
        raise InvalidValueError("该待办已取消，不能标记完成")
    if "activity_type" in payload.model_fields_set and not payload.create_activity:
        raise InvalidValueError("未勾选写跟进时不需要指定跟进方式")
    if payload.create_activity:
        validate_activity_type(payload.activity_type)

    now = datetime.now(UTC)
    follow_up.status = "done"
    follow_up.done_at = now

    activity: Activity | None = None
    if payload.create_activity:
        activity = create_activity_record(
            db,
            customer_id=follow_up.customer_id,
            contact_id=follow_up.contact_id,
            opportunity_id=follow_up.opportunity_id,
            type=payload.activity_type,
            # 纵深防御：API 写不进空白内容，但历史数据/运维改库可以；复制前再清洗一次
            content=_clean_content(follow_up.content),
            activity_at=now,
            operator_id=actor.id,
        )

    db.commit()
    return Completion(
        follow_up=_load_full(db, follow_up.id),
        activity=(
            db.execute(
                select(Activity).options(joinedload(Activity.operator)).where(
                    Activity.id == activity.id
                ).execution_options(populate_existing=True)
            ).scalar_one()
            if activity is not None
            else None
        ),
    )


def delete_follow_up(db: Session, actor: User, follow_up_id: int) -> None:
    """软删：误建才用它；「不做了」用 status='cancelled'（留痕），两者语义不同。"""
    follow_up = get_follow_up(db, actor, follow_up_id)
    customer = get_customer(db, actor, follow_up.customer_id)
    assert_customer_writable(customer, actor)
    _assert_can_delete_follow_up(follow_up, actor, customer)
    follow_up.deleted_at = datetime.now(UTC)
    db.commit()
