"""create contacts

客户下的联系人（design §3.4）。除常规索引外还建了一条**部分唯一索引**：
``uq_contacts_customer_primary`` 保证同一客户下最多一条未删除的主联系人。
服务层"设新的先把旧的置 false"是第一道保证，这条索引是第二道——导入、运维脚本
或并发写入绕过服务层时，坏数据在数据库这一层进不来。条件里的
``deleted_at IS NULL`` 让软删的主联系人让出唯一位置。

Revision ID: cb97628e2378
Revises: c46f53fd6843
Create Date: 2026-10-29 18:55:08.675417

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cb97628e2378"
down_revision: str | Sequence[str] | None = "c46f53fd6843"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("wechat", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("decision_role", sa.String(length=32), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_contacts_customer_id_customers"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
    )
    op.create_index(
        "ix_contacts_customer_id", "contacts", ["customer_id"], unique=False
    )
    op.create_index("ix_contacts_phone", "contacts", ["phone"], unique=False)
    op.create_index(
        "uq_contacts_customer_primary",
        "contacts",
        ["customer_id"],
        unique=True,
        postgresql_where=sa.text("is_primary AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_contacts_customer_primary", table_name="contacts")
    op.drop_index("ix_contacts_phone", table_name="contacts")
    op.drop_index("ix_contacts_customer_id", table_name="contacts")
    op.drop_table("contacts")
