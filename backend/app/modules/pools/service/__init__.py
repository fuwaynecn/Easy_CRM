"""公海池服务层。"""

from sqlalchemy.orm import Session

from app.core.errors import InvalidValueError
from app.modules.pools.models import (
    OWNER_CHANGE_ACTIONS,
    OwnerChangeLog,
    PoolRule,
)
from app.modules.pools.rules import PoolRules, load_pool_rules, update_pool_rules

__all__ = [
    "OWNER_CHANGE_ACTIONS",
    "OwnerChangeLog",
    "PoolRule",
    "PoolRules",
    "load_pool_rules",
    "record_owner_change",
    "update_pool_rules",
]


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
