"""联系人模型。

字段以 design §3.4 为准。除索引外本表还有一条**数据库级**约束：同一客户下
最多一条未删除的主联系人（``uq_contacts_customer_primary``，部分唯一索引）。
服务层负责"设新的先把旧的置 false"，数据库负责"无论如何都进不来第二条"——
导入脚本、运维手插、并发写入绕过服务层时，坏数据在数据库这一层就被挡住。
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin

# design §3.4 的决策角色：决策人 / 技术负责人 / 采购 / 使用者 / 其他
DECISION_ROLES = ("decision_maker", "tech", "purchase", "user", "other")


class Contact(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # phone / wechat 不设唯一约束也不做格式校验：design §3.4 明确允许同一客户下
    # 手机号重复（一人多岗、前台总机），格式硬校验会误伤座机分机与微信同号。
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    wechat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    decision_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 命名约定里的 ix 模板是 %(column_0_label)s，表达式/部分索引会与普通列索引
    # 撞名，因此这里每个索引都显式命名。
    __table_args__ = (
        Index("ix_contacts_customer_id", "customer_id"),
        Index("ix_contacts_phone", "phone"),
        # 软删的主联系人必须让出位置：否则删了旧主联系人后再设新的会撞约束。
        Index(
            "uq_contacts_customer_primary",
            "customer_id",
            unique=True,
            postgresql_where=text("is_primary AND deleted_at IS NULL"),
        ),
    )
