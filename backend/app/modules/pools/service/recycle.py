"""每日回收与部门一致性守护。

回收用**事务级 advisory lock**：多 worker 同时到点也只有一个执行；拿不到锁直接
返回 0，不阻塞、不报错。候选行用 `FOR UPDATE SKIP LOCKED`，正在被领取/分配的
客户会被跳过，不会和在线请求互相等锁。
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.modules.audit.service import write_audit_log
from app.modules.customers.models import Customer
from app.modules.pools.predicates import (
    open_deal_exists,
    pending_followup_exists,
)
from app.modules.pools.rules import PoolRules, load_pool_rules
from app.modules.pools.service.writes import record_owner_change
from app.modules.users.models import User

logger = logging.getLogger(__name__)

# 任意固定 key；只要全进程用同一个值即可。0x4543524D = "ECRM"
RECYCLE_LOCK_KEY = 0x4543524D


def recycle_customers(
    db: Session,
    *,
    now: datetime | None = None,
    rules: PoolRules | None = None,
) -> int:
    """把满足四条条件的客户回收进公海，返回实际回收数。"""
    current = now or datetime.now(UTC)
    effective = rules or load_pool_rules(db)
    if not effective.auto_recycle_enabled:
        return 0

    got_lock = db.execute(
        text("SELECT pg_try_advisory_xact_lock(:key)"),
        {"key": RECYCLE_LOCK_KEY},
    ).scalar()
    if not got_lock:
        # 另一个 worker 正在跑；本事务只读了规则，回滚即可
        db.rollback()
        return 0

    threshold = current - timedelta(days=effective.inactive_days)
    conditions = [
        Customer.deleted_at.is_(None),
        Customer.owner_id.is_not(None),
        Customer.status == "active",
        Customer.last_activity_at < threshold,
    ]
    if effective.protect_with_open_deal:
        conditions.append(~open_deal_exists())
    if effective.protect_with_pending_followup:
        conditions.append(~pending_followup_exists())

    customers = list(
        db.execute(
            select(Customer)
            .where(*conditions)
            .order_by(Customer.id.asc())
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for customer in customers:
        before = {
            "owner_id": customer.owner_id,
            "department_id": customer.department_id,
        }
        from_owner_id = customer.owner_id
        customer.owner_id = None
        customer.department_id = None
        # 裁定 2：不碰未关闭机会的 owner；下一次 claim/assign 会同步
        record_owner_change(
            db,
            customer_id=customer.id,
            action="recycle",
            from_owner_id=from_owner_id,
            to_owner_id=None,
            operator_id=None,
            reason=f"连续 {effective.inactive_days} 天无跟进，系统自动回收",
        )
        write_audit_log(
            db,
            entity="customers",
            entity_id=customer.id,
            action="transfer",
            before=before,
            after={"owner_id": None, "department_id": None},
            operator_id=None,
        )
    db.commit()
    return len(customers)


def sync_department_consistency(db: Session) -> int:
    """修两类冗余不一致：有 owner 但部门不等于 owner 部门；公海却有部门。"""
    owner_department = (
        select(User.department_id)
        .where(User.id == Customer.owner_id)
        .scalar_subquery()
    )
    owned = db.execute(
        update(Customer)
        .where(
            Customer.owner_id.is_not(None),
            Customer.deleted_at.is_(None),
            Customer.department_id.is_distinct_from(owner_department),
        )
        .values(department_id=owner_department)
        .execution_options(synchronize_session=False)
    )
    pool = db.execute(
        update(Customer)
        .where(
            Customer.owner_id.is_(None),
            Customer.department_id.is_not(None),
            Customer.deleted_at.is_(None),
        )
        .values(department_id=None)
        .execution_options(synchronize_session=False)
    )
    total = int(owned.rowcount or 0) + int(pool.rowcount or 0)
    if total:
        logger.warning("部门一致性修复：%d 条客户的 department_id 已校正", total)
    db.commit()
    return total
