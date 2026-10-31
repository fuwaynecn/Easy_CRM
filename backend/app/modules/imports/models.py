"""Excel 导入任务（design §3.14）。

`errors_json` 存 `[{row, column, message}]`；`skipped_rows` 是 P5 的增量列：
spec 要求「已存在则跳过并在结果中记为已存在，跳过」，但没有列能放这个计数，
塞进 `error_rows` 会把「跳过」误报成「失败」。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

IMPORT_KINDS = ("customers", "contacts", "opportunities")
IMPORT_STATUSES = ("validating", "validated", "importing", "done", "failed")

_KIND_SQL = "(" + ", ".join(f"'{kind}'" for kind in IMPORT_KINDS) + ")"
_STATUS_SQL = "(" + ", ".join(f"'{status}'" for status in IMPORT_STATUSES) + ")"


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="customers", server_default="customers"
    )
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="validating", server_default="validating"
    )
    total_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    success_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    skipped_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    errors_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"kind IN {_KIND_SQL}", name="kind"),
        CheckConstraint(f"status IN {_STATUS_SQL}", name="status"),
        Index("ix_import_jobs_created_by_created_at", "created_by", "created_at"),
        Index("ix_import_jobs_status", "status"),
    )
