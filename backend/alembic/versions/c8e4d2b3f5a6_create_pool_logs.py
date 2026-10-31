"""create owner_change_logs and audit_logs

design §3.12/§3.13。两处刻意选择：
- `owner_change_logs.operator_id` 可空：系统回收没有人类操作者（P4 裁定 6）；
- `audit_logs.before_json/after_json` 用 JSONB：快照要能按字段查询，text 存 JSON
  会让后续审计页只能全表取回再解析。

Revision ID: c8e4d2b3f5a6
Revises: b7f3c1a2d4e5
Create Date: 2026-10-31 14:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c8e4d2b3f5a6"
down_revision: str | Sequence[str] | None = "b7f3c1a2d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owner_change_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("from_owner_id", sa.BigInteger(), nullable=True),
        sa.Column("to_owner_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('claim', 'recycle', 'assign', 'transfer', 'release')",
            name=op.f("ck_owner_change_logs_action"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_owner_change_logs_customer_id_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["from_owner_id"],
            ["users.id"],
            name=op.f("fk_owner_change_logs_from_owner_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["users.id"],
            name=op.f("fk_owner_change_logs_operator_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["to_owner_id"],
            ["users.id"],
            name=op.f("fk_owner_change_logs_to_owner_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_owner_change_logs")),
    )
    op.create_index(
        "ix_owner_change_logs_customer_created",
        "owner_change_logs",
        ["customer_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("entity", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('create', 'delete', 'permission_change', 'transfer', 'import')",
            name=op.f("ck_audit_logs_action"),
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_operator_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_entity_entity_id",
        "audit_logs",
        ["entity", "entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_created_at",
        "audit_logs",
        [sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_entity_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(
        "ix_owner_change_logs_customer_created",
        table_name="owner_change_logs",
    )
    op.drop_table("owner_change_logs")
