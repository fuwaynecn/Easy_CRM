"""create lost_reasons

输单原因字典（design §3.10）。`name` 的唯一约束显式命名：字典重名是数据问题，
数据库必须挡住（服务层查重只是第一道），而匿名约束在 PostgreSQL 上不可预期地
改名会让后续排障与删除迁移都难写。

Revision ID: 203e726b09c8
Revises: cb97628e2378
Create Date: 2026-10-30 03:25:31.371852

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "203e726b09c8"
down_revision: str | Sequence[str] | None = "cb97628e2378"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lost_reasons",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lost_reasons")),
        sa.UniqueConstraint("name", name=op.f("uq_lost_reasons_name")),
    )


def downgrade() -> None:
    op.drop_table("lost_reasons")
