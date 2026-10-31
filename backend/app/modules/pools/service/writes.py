"""负责人变更的唯一写入口。

P4 之后 customers.owner_id / department_id 只能从这里改：
- claim：领取，重置 last_activity_at；
- assign：分配，不重置 last_activity_at；
- release：释放，不重置 last_activity_at，也不清机会 owner（裁定 2）。
每次变更同事务写 owner_change_logs + audit_logs。
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
)
from app.modules.audit.service import write_audit_log
from app.modules.customers.models import Customer
from app.modules.customers.service import get_customer
from app.modules.opportunities.service import sync_opportunity_owner
from app.modules.pools.models import OWNER_CHANGE_ACTIONS, OwnerChangeLog
from app.modules.pools.rules import load_pool_rules
from app.modules.users.models import ROLE_MANAGER, ROLE_SALES, User

MAX_REASON_LENGTH = 200


def record_owner_change(
    db: Session,
    *,
    customer_id: int,
    action: str,
    from_owner_id: int | None,
    to_owner_id: int | None,
    operator_id: int | None,
    reason: str | None = None,
) -> OwnerChangeLog:
    """写一条负责人变更日志，不 commit。"""
    if action not in OWNER_CHANGE_ACTIONS:
        raise InvalidValueError(f"未知负责人变更动作：{action}")
    cleaned = (reason or "").strip() or None
    if cleaned is not None and len(cleaned) > MAX_REASON_LENGTH:
        raise InvalidValueError(f"原因不能超过 {MAX_REASON_LENGTH} 个字符")
    log = OwnerChangeLog(
        customer_id=customer_id,
        action=action,
        from_owner_id=from_owner_id,
        to_owner_id=to_owner_id,
        operator_id=operator_id,
        reason=cleaned,
    )
    db.add(log)
    return log


def _lock_customer(db: Session, customer_id: int) -> Customer | None:
    """行锁 + populate_existing：拿到的是锁住之后的最新值，不是 identity map 的旧值。"""
    return db.execute(
        select(Customer)
        .where(Customer.id == customer_id, Customer.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _owned_count(db: Session, user_id: int) -> int:
    """配额口径：名下所有未删除客户（含 lost/inactive，裁定 7）。"""
    return int(
        db.execute(
            select(func.count())
            .select_from(Customer)
            .where(Customer.owner_id == user_id, Customer.deleted_at.is_(None))
        ).scalar_one()
    )


def _clean_reason(reason: str | None, *, required: bool) -> str | None:
    cleaned = (reason or "").strip()
    if not cleaned:
        if required:
            raise InvalidValueError("释放原因不能为空")
        return None
    if len(cleaned) > MAX_REASON_LENGTH:
        raise InvalidValueError(f"原因不能超过 {MAX_REASON_LENGTH} 个字符")
    return cleaned


def _audit_snapshot(customer: Customer) -> dict[str, int | None]:
    return {
        "owner_id": customer.owner_id,
        "department_id": customer.department_id,
    }


def claim_customer(
    db: Session,
    actor: User,
    customer_id: int,
    *,
    now: datetime | None = None,
) -> Customer:
    """领取公海客户。

    顺序是刻意的：先 ``get_customer`` 过可见性（别的部门的已归属客户返回 403，
    不泄露“已被领取”），再锁行复查 owner。这样并发领取只有一个赢家，另一个 409。
    """
    if actor.role not in (ROLE_SALES, ROLE_MANAGER):
        raise ForbiddenError("仅销售或主管可领取客户")
    if actor.department_id is None:
        raise InvalidValueError("账号未分配部门，无法领取客户")

    get_customer(db, actor, customer_id)
    locked = _lock_customer(db, customer_id)
    if locked is None:
        raise NotFoundError("客户不存在")
    if locked.owner_id is not None:
        raise ConflictError("该客户已被他人领取")

    # 锁目标用户：同一销售并发领取两个客户时，配额判定必须串行
    db.execute(
        # 只取 id：User.department 是 joined eager load，选整行会生成
        # LEFT OUTER JOIN，PG 报 "FOR UPDATE cannot be applied to the
        # nullable side of an outer join"。
        select(User.id).where(User.id == actor.id).with_for_update()
    ).scalar_one()
    rules = load_pool_rules(db)
    if _owned_count(db, actor.id) >= rules.max_owned:
        raise ConflictError(
            f"持有客户数已达上限（{rules.max_owned}），请先释放客户"
        )

    before = _audit_snapshot(locked)
    current = now or datetime.now(UTC)
    locked.owner_id = actor.id
    locked.department_id = actor.department_id
    locked.last_activity_at = current
    sync_opportunity_owner(db, customer_id=locked.id, owner_id=actor.id)
    record_owner_change(
        db,
        customer_id=locked.id,
        action="claim",
        from_owner_id=None,
        to_owner_id=actor.id,
        operator_id=actor.id,
    )
    write_audit_log(
        db,
        entity="customers",
        entity_id=locked.id,
        action="transfer",
        before=before,
        after=_audit_snapshot(locked),
        operator_id=actor.id,
    )
    db.commit()
    return get_customer(db, actor, customer_id)
