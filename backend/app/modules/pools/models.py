"""公海池相关表。

`pool_rules` 是单行配置表：id 固定 1，数据库 CHECK 兜底。规则必须只有一份真值，
否则首页、回收任务、领取配额会各自漂移。
"""

from sqlalchemy import Boolean, CheckConstraint, Integer, SmallInteger, text
from sqlalchemy.orm import Mapped, mapped_column

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
