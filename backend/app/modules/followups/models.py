"""待办（design §3.9）。

**不设负责人字段**：待办跟着客户走，客户转给谁，待办就归谁。两个真值源（客户负责人
+ 待办负责人）一定会打架，查询"我的今日待办"即 join customers 后按
`customers.owner_id = me` 过滤。

`done_at` 与 `status` 的一致性由 CHECK 兜底（`status='done'` 必须有 `done_at`）：
绕过 service 的写入（运维 SQL、将来的导入）也不能留下"已完成但没有完成时间"的行，
否则 P5 的跟进量/及时率报表会算不出周期。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.modules.activities.models import Activity  # noqa: F401
    from app.modules.contacts.models import Contact
    from app.modules.customers.models import Customer
    from app.modules.opportunities.models import Opportunity
    from app.modules.users.models import User

# design §3.9：pending / done / cancelled
FOLLOW_UP_STATUSES = ("pending", "done", "cancelled")

_STATUS_SQL = "(" + ", ".join(f"'{value}'" for value in FOLLOW_UP_STATUSES) + ")"


class FollowUp(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "follow_ups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=False
    )
    contact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("contacts.id"), nullable=True
    )
    opportunity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("opportunities.id"), nullable=True
    )
    # 含时分：首页「今日待办」按上海时区自然日切分，只到日期会丢掉"下午三点前回电"
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )

    customer: Mapped["Customer"] = relationship("Customer", lazy="raise")
    contact: Mapped["Contact | None"] = relationship("Contact", lazy="raise")
    opportunity: Mapped["Opportunity | None"] = relationship(
        "Opportunity", lazy="raise"
    )
    operator: Mapped["User"] = relationship(
        "User", foreign_keys=[created_by], lazy="raise"
    )

    __table_args__ = (
        Index("ix_follow_ups_status_due_at", "status", "due_at"),
        Index(
            "ix_follow_ups_customer_id_status_due_at",
            "customer_id",
            "status",
            "due_at",
        ),
        CheckConstraint(f"status IN {_STATUS_SQL}", name="status"),
        # done 必须有完成时间；未完成的行不允许有完成时间（复活后必须清干净）
        CheckConstraint(
            "(status = 'done') = (done_at IS NOT NULL)", name="done_at_consistency"
        ),
    )
