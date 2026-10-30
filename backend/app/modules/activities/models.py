"""跟进记录（design §3.8）。

`note` 类型承担「客户备注」职责，与其它跟进同表：备注本来就是某个时间点、某个人
写下的一条记录，合在一起才能进同一条时间线（spec §3.8 末段）。

`activity_at` 允许是过去（补录），因此**不**用 `server_default=now()`：默认值由
service 在缺省时填空，数据库只负责"必须有值"。索引按 spec 建复合索引；spec 写的是
`(customer_id, activity_at desc)`，这里建成普通升序复合索引——PostgreSQL 的 btree
可以反向扫描，`ORDER BY activity_at DESC` 走同一索引，建 `DESC` 索引只是把同一棵
树换个方式记录，没有收益。
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
    from app.modules.contacts.models import Contact
    from app.modules.customers.models import Customer
    from app.modules.opportunities.models import Opportunity
    from app.modules.users.models import User

# design §3.8：六种沟通方式。前端的下拉选项直接由这个元组渲染，改值等于契约变更。
ACTIVITY_TYPES = ("call", "visit", "wechat", "email", "note", "other")

_TYPE_SQL = "(" + ", ".join(f"'{value}'" for value in ACTIVITY_TYPES) + ")"


class Activity(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=False
    )
    # 联系人与机会都可空：电话可能是打给前台（没录联系人），也可能与具体机会无关
    contact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("contacts.id"), nullable=True
    )
    opportunity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("opportunities.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # NOT NULL：每条跟进都必须能回答"谁写的"（报表按 created_by 统计跟进量）
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )

    # 一律 lazy="raise"：读取入口统一 joinedload，漏预加载立刻报错而不是静默 N+1
    customer: Mapped["Customer"] = relationship("Customer", lazy="raise")
    contact: Mapped["Contact | None"] = relationship("Contact", lazy="raise")
    opportunity: Mapped["Opportunity | None"] = relationship(
        "Opportunity", lazy="raise"
    )
    operator: Mapped["User"] = relationship(
        "User", foreign_keys=[created_by], lazy="raise"
    )

    __table_args__ = (
        Index("ix_activities_customer_id_activity_at", "customer_id", "activity_at"),
        Index("ix_activities_created_by_activity_at", "created_by", "activity_at"),
        CheckConstraint(f"type IN {_TYPE_SQL}", name="type"),
    )
