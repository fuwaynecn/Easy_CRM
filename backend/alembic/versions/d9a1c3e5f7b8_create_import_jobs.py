"""create import_jobs

design §3.14。`skipped_rows` 是 P5 增量列：spec 要求把「已存在，跳过」记进结果，
但没有列能放这个计数。

Revision ID: d9a1c3e5f7b8
Revises: c8e4d2b3f5a6
Create Date: 2026-10-31 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d9a1c3e5f7b8"
down_revision: str | Sequence[str] | None = "c8e4d2b3f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind", sa.String(length=16), server_default="customers", nullable=False
        ),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="validating", nullable=False
        ),
        sa.Column("total_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("success_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('customers', 'contacts', 'opportunities')",
            name=op.f("ck_import_jobs_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('validating', 'validated', 'importing', 'done', 'failed')",
            name=op.f("ck_import_jobs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_import_jobs_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
    )
    op.create_index(
        "ix_import_jobs_created_by_created_at",
        "import_jobs",
        ["created_by", "created_at"],
        unique=False,
    )
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_import_jobs_status", table_name="import_jobs")
    op.drop_index("ix_import_jobs_created_by_created_at", table_name="import_jobs")
    op.drop_table("import_jobs")
