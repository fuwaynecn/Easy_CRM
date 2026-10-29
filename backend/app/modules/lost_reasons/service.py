"""输单原因字典服务。

P2 只有三个入口：列表（选择器数据源）、创建（种子脚本用）、按 id 取（阶段变更时
校验 `lost_reason_id` 真实存在）。管理端的增删改属 P5 后台，此处不做。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, InvalidValueError, NotFoundError
from app.modules.lost_reasons.models import LostReason
from app.modules.lost_reasons.schemas import LostReasonCreate


def _clean_name(name: str | None) -> str:
    """strip 后不能为空。

    schema 的 ``min_length=1`` 拦不住 ``"   "``；空名字在输单原因下拉框里就是一行
    看不见的空白，选项点了也不知道代表了什么。
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidValueError("输单原因不能为空")
    return cleaned


def _ensure_name_free(db: Session, name: str) -> None:
    """重名是 409（与现有数据冲突），不是静默覆盖。数据库唯一索引是第二道防线。"""
    exists = db.execute(
        select(LostReason.id).where(LostReason.name == name)
    ).first()
    if exists is not None:
        raise ConflictError("输单原因已存在")


def list_lost_reasons(
    db: Session, *, include_inactive: bool = False
) -> list[LostReason]:
    """按 ``sort_order`` 升序返回；默认只给启用中的原因（下拉框直接用这个顺序）。"""
    stmt = select(LostReason)
    if not include_inactive:
        stmt = stmt.where(LostReason.active.is_(True))
    return list(
        db.execute(stmt.order_by(LostReason.sort_order, LostReason.id))
        .scalars()
        .all()
    )


def get_lost_reason(db: Session, reason_id: int) -> LostReason:
    """按 id 取。不存在抛 404——阶段变更里一个不存在的 `lost_reason_id` 是参数错，
    不是"外键冲突"，不能拖到 commit 时才变成 500。"""
    reason = db.get(LostReason, reason_id)
    if reason is None:
        raise NotFoundError("输单原因不存在")
    return reason


def create_lost_reason(db: Session, payload: LostReasonCreate) -> LostReason:
    name = _clean_name(payload.name)
    _ensure_name_free(db, name)
    reason = LostReason(
        name=name, sort_order=payload.sort_order, active=payload.active
    )
    db.add(reason)
    db.commit()
    db.refresh(reason)
    return reason
