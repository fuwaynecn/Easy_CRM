"""create pool_rules

公海规则单行表（design §3.11）。迁移直接插入 id=1，保证新库一升级就有完整默认行；
应用侧 `load_pool_rules` 仍对“行被手工删掉”兜底返回默认值，GET 不写库。

Revision ID: b7f3c1a2d4e5
Revises: aca0bc139888
Create Date: 2026-10-31 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7f3c1a2d4e5"
down_revision: str | Sequence[str] | None = "aca0bc139888"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pool_rules",
        sa.Column("id", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("inactive_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("max_owned", sa.Integer(), server_default="50", nullable=False),
        sa.Column(
            "auto_recycle_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "protect_with_open_deal",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "protect_with_pending_followup",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "recycle_notice_days",
            sa.Integer(),
            server_default="3",
            nullable=False,
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
        sa.CheckConstraint("id = 1", name=op.f("ck_pool_rules_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pool_rules")),
    )
    op.execute("INSERT INTO pool_rules (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("pool_rules")
