"""审计日志写入。

不 commit：审计必须和业务行在同一事务里，否则会出现“负责人已变、审计没写”的
中间态。调用方（claim/assign/release/recycle）负责提交。
"""

from sqlalchemy.orm import Session

from app.core.errors import InvalidValueError
from app.modules.audit.models import AUDIT_ACTIONS, AuditLog


def write_audit_log(
    db: Session,
    *,
    entity: str,
    entity_id: int,
    action: str,
    before: dict | None,
    after: dict | None,
    operator_id: int | None,
) -> AuditLog:
    if action not in AUDIT_ACTIONS:
        raise InvalidValueError(f"未知审计动作：{action}")
    log = AuditLog(
        entity=entity,
        entity_id=entity_id,
        action=action,
        # 写入边界拷贝：调用方复用同一个 dict 并在 flush 前改写，不能篡改历史快照。
        # 浅拷贝足够——快照的契约是标量字段的扁平 dict。
        before_json=dict(before) if before is not None else None,
        after_json=dict(after) if after is not None else None,
        operator_id=operator_id,
    )
    db.add(log)
    return log
