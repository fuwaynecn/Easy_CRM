"""create follow_ups

待办（design §3.9）。两条结构保证写进迁移：
- `(status, due_at)` 与 `(customer_id, status, due_at)` 复合索引（首页今日/逾期、
  单客户待办列表各走一条，避免全表扫）；
- `status` 三值 CHECK + `(status = 'done') = (done_at IS NOT NULL)` 一致性 CHECK
  （绕过 service 的写入也不能留下"已完成但没完成时间"的行）。

**没有 owner_id 列**：待办跟客户走（spec §3.9 的核心结论），加负责人等于造第二个真值源。

Revision ID: aca0bc139888
Revises: 3199fb8aa5f9
Create Date: 2026-10-31 03:05:26.629107

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "aca0bc139888"
down_revision: str | Sequence[str] | None = "3199fb8aa5f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "follow_ups",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("contact_id", sa.BigInteger(), nullable=True),
        sa.Column("opportunity_id", sa.BigInteger(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "(status = 'done') = (done_at IS NOT NULL)",
            name=op.f("ck_follow_ups_done_at_consistency"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'cancelled')",
            name=op.f("ck_follow_ups_status"),
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"],
            name=op.f("fk_follow_ups_contact_id_contacts"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name=op.f("fk_follow_ups_created_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"],
            name=op.f("fk_follow_ups_customer_id_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["opportunities.id"],
            name=op.f("fk_follow_ups_opportunity_id_opportunities"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_follow_ups")),
    )
    op.create_index(
        "ix_follow_ups_status_due_at",
        "follow_ups",
        ["status", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_follow_ups_customer_id_status_due_at",
        "follow_ups",
        ["customer_id", "status", "due_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_follow_ups_customer_id_status_due_at", table_name="follow_ups"
    )
    op.drop_index("ix_follow_ups_status_due_at", table_name="follow_ups")
    op.drop_table("follow_ups")
