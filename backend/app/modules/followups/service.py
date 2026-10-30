"""待办服务。

可见性完全继承客户（裁定 3）：读取先 ``get_customer``（403/404 分开），列表用
``customer_query(actor)`` 的子查询表达可见客户集合。
「我的待办」= 在可见集合再收窄到 ``customers.owner_id = actor.id``（spec §3.9），
**不是**按 ``follow_ups.created_by``——后者会让接手人看不到前任留下的待办。
"""

from datetime import datetime

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.db.scope import customer_query
from app.modules.customers.models import Customer
from app.modules.customers.service import get_customer
from app.modules.followups.models import FOLLOW_UP_STATUSES, FollowUp
from app.modules.users.models import User


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
