"""baseline: departments / users / customers

首个迁移即基线，必须建出当时已存在的全部模型（departments、users、customers），
否则全新部署执行 ``alembic upgrade head`` 后会缺表。

Revision ID: c46f53fd6843
Revises:
Create Date: 2026-10-29 17:22:25.725521

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c46f53fd6843"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("name", name=op.f("uq_departments_name")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_users_department_id_departments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("short_name", sa.String(length=64), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("industry", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("scale", sa.String(length=32), nullable=True),
        sa.Column("level", sa.String(length=4), server_default="C", nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("procurement_type", sa.String(length=16), nullable=True),
        sa.Column(
            "budget_status",
            sa.String(length=16),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_customers_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_customers_department_id_departments"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_customers_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
    )
    # 索引一律显式命名：命名约定的 ix 模板只取 column_0_label，表达式索引会与
    # 普通列索引撞名。
    op.create_index(
        "ix_customers_owner_id", "customers", ["owner_id"], unique=False
    )
    op.create_index(
        "ix_customers_department_id", "customers", ["department_id"], unique=False
    )
    op.create_index(
        "ix_customers_last_activity_at", "customers", ["last_activity_at"], unique=False
    )
    op.create_index("ix_customers_status", "customers", ["status"], unique=False)
    op.create_index("ix_customers_name", "customers", ["name"], unique=False)


def downgrade() -> None:
    # 先删客户表（两个外键指向 users/departments），再删 users，最后 departments。
    # 索引随所属表一起删除，无需单独 drop_index。
    op.drop_table("customers")
    op.drop_table("users")
    op.drop_table("departments")
