from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.modules.departments.models import Department

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_SALES = "sales"
ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES)

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
USER_STATUSES = (STATUS_ACTIVE, STATUS_DISABLED)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("departments.id"), nullable=True
    )
    # server_default 是给 ORM 之外的写入兜底（Excel 导入、运维手工插入）：
    # 只有 Python 侧 default 时，裸 INSERT 会直接撞 NOT NULL。
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_ACTIVE, server_default=STATUS_ACTIVE
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    department: Mapped["Department | None"] = relationship(lazy="joined")
