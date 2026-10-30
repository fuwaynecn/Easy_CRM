"""跟进记录服务。

可见性完全继承客户（与 contacts 同口径）：读取先 ``get_customer``（403/404 分开），
列表用 ``customer_query(actor)`` 的子查询表达可见客户集合，本模块不自拼作用域。
写入侧的「写一条跟进顺带建待办」「完成待办顺带写跟进」的事务边界见 create_activity
与 followups.service.complete_follow_up。
"""

from datetime import date

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.core.timeutil import day_bounds
from app.db.scope import customer_query
from app.modules.activities.models import ACTIVITY_TYPES, Activity
from app.modules.customers.models import Customer
from app.modules.customers.service import get_customer
from app.modules.users.models import User


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


def _validate_type(value: str | None, label: str = "跟进方式") -> None:
    """枚举取值非法是 400（不是空列表）：空列表会把"参数拼错"显示成"没有数据"。"""
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
    """作用域内的跟进分页，按 `activity_at` 倒序（第二键 id 倒序，翻页稳定）。"""
    _validate_type(type)
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
