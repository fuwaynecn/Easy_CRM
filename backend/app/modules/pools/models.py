"""公海池相关表。

`pool_rules` 是单行配置表：id 固定 1，数据库 CHECK 兜底。规则必须只有一份真值，
否则首页、回收任务、领取配额会各自漂移。

`owner_change_logs` 只追加：客户 owner 每次变化的既成事实。审计与时间线都从这张
表读数，所以不允许 UPDATE/DELETE 语义（没有接口能改它）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class PoolRule(Base, TimestampMixin):
    __tablename__ = "pool_rules"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, default=1, server_default="1"
    )
    inactive_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    max_owned: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50, server_default="50"
    )
    auto_recycle_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    protect_with_open_deal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    protect_with_pending_followup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    recycle_notice_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
    )


OWNER_CHANGE_ACTIONS = ("claim", "recycle", "assign", "transfer", "release")


class OwnerChangeLog(Base):
    __tablename__ = "owner_change_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    from_owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    to_owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # spec §3.12 写 NOT NULL；P4 裁定 6：系统回收没有人类操作者，允许 NULL。
    # 时间线把 NULL 展示成“系统”，不伪造一个 admin id。
    operator_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    operator = relationship("User", foreign_keys=[operator_id], lazy="raise")
    from_owner = relationship("User", foreign_keys=[from_owner_id], lazy="raise")
    to_owner = relationship("User", foreign_keys=[to_owner_id], lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "action IN ('claim', 'recycle', 'assign', 'transfer', 'release')",
            name="action",
        ),
        Index("ix_owner_change_logs_customer_created", "customer_id", "created_at"),
    )
