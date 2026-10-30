"""create activities

跟进记录（design §3.8）。三条结构保证写进迁移：
- `(customer_id, activity_at)` 复合索引（客户时间线按客户取倒序页）；
- `(created_by, activity_at)` 复合索引（P5 的跟进量报表按人按时间聚合）；
- `type` 六值 CHECK（绕过 service 的写入也会被挡住）。

`activity_at` 允许补录过去，因此没有 `server_default`：默认值由 service 填空，
数据库只保证"必须有值"。`deleted_at` 是 P3 裁定 5 的软删列（spec 表结构未列，
但删除接口需要它；软删与"改内容"是两件事，不能混）。

Revision ID: 3199fb8aa5f9
Revises: 7ec6fcb6c79f
Create Date: 2026-10-31 02:34:43.861333

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3199fb8aa5f9"
down_revision: str | Sequence[str] | None = "7ec6fcb6c79f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("contact_id", sa.BigInteger(), nullable=True),
        sa.Column("opportunity_id", sa.BigInteger(), nullable=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("activity_at", sa.DateTime(timezone=True), nullable=False),
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
            "type IN ('call', 'visit', 'wechat', 'email', 'note', 'other')",
            name=op.f("ck_activities_type"),
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"],
            name=op.f("fk_activities_contact_id_contacts"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name=op.f("fk_activities_created_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"],
            name=op.f("fk_activities_customer_id_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["opportunities.id"],
            name=op.f("fk_activities_opportunity_id_opportunities"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activities")),
    )
    op.create_index(
        "ix_activities_customer_id_activity_at",
        "activities",
        ["customer_id", "activity_at"],
        unique=False,
    )
    op.create_index(
        "ix_activities_created_by_activity_at",
        "activities",
        ["created_by", "activity_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_activities_created_by_activity_at", table_name="activities")
    op.drop_index("ix_activities_customer_id_activity_at", table_name="activities")
    op.drop_table("activities")
