"""create stage_histories

阶段变更流水（design §3.7）。这张表只追加、不修改，是漏斗与成交周期报表的唯一
数据来源，因此三条结构保证都显式写进迁移：`(opportunity_id, changed_at)` 复合索引
（报表按机会+时间取数）、`to_stage` 取值合法、`from_stage` 为空或取值合法（为空
只代表"创建"，不是脏数据）。约束显式命名，名字进首个迁移即固化。

Revision ID: 7ec6fcb6c79f
Revises: 85b8ee5d02d4
Create Date: 2026-10-30 03:52:21.914926

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7ec6fcb6c79f"
down_revision: str | Sequence[str] | None = "85b8ee5d02d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stage_histories",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("opportunity_id", sa.BigInteger(), nullable=False),
        sa.Column("from_stage", sa.String(length=16), nullable=True),
        sa.Column("to_stage", sa.String(length=16), nullable=False),
        sa.Column(
            "amount_snapshot", sa.Numeric(precision=14, scale=2), nullable=True
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
        sa.CheckConstraint(
            "from_stage IS NULL OR from_stage IN "
            "('contact', 'needs', 'proposal', 'negotiation', 'won', 'lost')",
            name=op.f("ck_stage_histories_from_stage"),
        ),
        sa.CheckConstraint(
            "to_stage IN "
            "('contact', 'needs', 'proposal', 'negotiation', 'won', 'lost')",
            name=op.f("ck_stage_histories_to_stage"),
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_stage_histories_opportunity_id_opportunities"),
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["users.id"],
            name=op.f("fk_stage_histories_operator_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stage_histories")),
    )
    op.create_index(
        "ix_stage_histories_opportunity_id_changed_at",
        "stage_histories",
        ["opportunity_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_stage_histories_opportunity_id_changed_at", table_name="stage_histories"
    )
    op.drop_table("stage_histories")
