"""客户模型。

字段与枚举取值以 design §3.3 为准：前端的下拉选项直接由这几个元组渲染，
改值等于前后端契约变更。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin

PROCUREMENT_TYPES = ("tender", "direct", "framework", "other")
BUDGET_STATUSES = ("approved", "pending", "none", "unknown")
CUSTOMER_LEVELS = ("A", "B", "C")
CUSTOMER_STATUSES = ("active", "lost", "inactive")


class Customer(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scale: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 三个枚举列的 server_default 是给 ORM 之外的写入兜底（Excel 导入、运维手工
    # 插入）：只有 Python 侧 default 时，裸 INSERT 会直接撞 NOT NULL。
    level: Mapped[str] = mapped_column(
        String(4), nullable=False, default="C", server_default="C"
    )
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    procurement_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    budget_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    # owner_id 为 NULL 即公海：领取/回收功能靠这条语义工作，不能改成 NOT NULL。
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("departments.id"), nullable=True
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )

    # 命名约定里的 ix 模板是 %(column_0_label)s，表达式索引会与普通列索引撞名，
    # 因此这里每个索引都显式命名。
    __table_args__ = (
        Index("ix_customers_owner_id", "owner_id"),
        Index("ix_customers_department_id", "department_id"),
        Index("ix_customers_last_activity_at", "last_activity_at"),
        Index("ix_customers_status", "status"),
        Index("ix_customers_name", "name"),
    )
