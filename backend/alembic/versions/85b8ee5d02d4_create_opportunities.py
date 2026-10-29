"""create opportunities

销售机会（design §3.5/§3.6）。阶段取值、金额非负、赢单率区间、终态必备字段都
落在 CHECK 约束上并且全部显式命名：机会表会被不止 service 一条写入路径碰到
（P5 的 Excel 导入、运维手工 SQL），应用层校验挡得住请求，挡不住裸 INSERT。
约束名进首个迁移即固化，后续想改必须再写一条迁移。

Revision ID: 85b8ee5d02d4
Revises: 203e726b09c8
Create Date: 2026-10-30 03:41:21.248657

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85b8ee5d02d4"
down_revision: str | Sequence[str] | None = "203e726b09c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "stage",
            sa.String(length=16),
            server_default="contact",
            nullable=False,
        ),
        sa.Column(
            "amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("expected_close_date", sa.Date(), nullable=True),
        sa.Column("competitor", sa.String(length=200), nullable=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("win_rate", sa.SmallInteger(), nullable=True),
        sa.Column("lost_reason_id", sa.BigInteger(), nullable=True),
        sa.Column("lost_note", sa.Text(), nullable=True),
        sa.Column("won_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint(
            "amount >= 0", name=op.f("ck_opportunities_amount_non_negative")
        ),
        sa.CheckConstraint(
            "stage IN ('contact', 'needs', 'proposal', 'negotiation', 'won', 'lost')",
            name=op.f("ck_opportunities_stage"),
        ),
        sa.CheckConstraint(
            "stage <> 'lost' OR lost_reason_id IS NOT NULL",
            name=op.f("ck_opportunities_lost_reason_required"),
        ),
        sa.CheckConstraint(
            "stage <> 'won' OR won_at IS NOT NULL",
            name=op.f("ck_opportunities_won_at_required"),
        ),
        sa.CheckConstraint(
            "win_rate IS NULL OR (win_rate >= 0 AND win_rate <= 100)",
            name=op.f("ck_opportunities_win_rate_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_opportunities_created_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_opportunities_customer_id_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["lost_reason_id"],
            ["lost_reasons.id"],
            name=op.f("fk_opportunities_lost_reason_id_lost_reasons"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_opportunities_owner_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunities")),
    )
    op.create_index(
        "ix_opportunities_customer_id", "opportunities", ["customer_id"], unique=False
    )
    op.create_index(
        "ix_opportunities_owner_id", "opportunities", ["owner_id"], unique=False
    )
    op.create_index("ix_opportunities_stage", "opportunities", ["stage"], unique=False)
    op.create_index(
        "ix_opportunities_expected_close_date",
        "opportunities",
        ["expected_close_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_opportunities_expected_close_date", table_name="opportunities")
    op.drop_index("ix_opportunities_stage", table_name="opportunities")
    op.drop_index("ix_opportunities_owner_id", table_name="opportunities")
    op.drop_index("ix_opportunities_customer_id", table_name="opportunities")
    op.drop_table("opportunities")
