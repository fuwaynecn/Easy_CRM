from datetime import UTC, datetime, timedelta
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    LockedError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.security import DECOY_PASSWORD_HASH, hash_password, verify_password
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES, ROLES, User

__all__ = [
    "ROLES",
    "ROLE_ADMIN",
    "ROLE_MANAGER",
    "ROLE_SALES",
    "authenticate",
    "create_user",
    "get_user_by_username",
    "list_users",
    "reset_password",
    "set_user_status",
]

# 用户不存在与口令错误必须给出完全相同的对外文案，避免账号存在性探测
INVALID_CREDENTIALS_MESSAGE = "用户名或密码错误"


def _now() -> datetime:
    return datetime.now(UTC)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.id)).scalars().all())


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str,
    role: str,
    department_id: int | None,
) -> User:
    cleaned_username = username.strip()
    if role not in ROLES:
        raise ConflictError(f"未知角色：{role}")
    if get_user_by_username(db, cleaned_username) is not None:
        raise ConflictError("登录名已存在")
    user = User(
        username=cleaned_username,
        password_hash=hash_password(password),
        display_name=display_name.strip(),
        role=role,
        department_id=department_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    """校验登录凭据。

    对外口径（评审裁定）：
    - 用户不存在 / 口令错误：401 + 同一文案，且耗时对齐，防账号存在性探测；
    - 账号停用：403，明确提示（内部系统里让用户知道该找管理员更有价值）；
    - 账号锁定：423 + 剩余等待时间。
    """
    user = get_user_by_username(db, username)
    if user is None:
        # 不存在也跑一次哈希校验，让两条失败路径的响应时间没有明显差异
        verify_password(password, DECOY_PASSWORD_HASH)
        raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE)
    if user.status != "active":
        raise ForbiddenError("账号已停用，请联系管理员")

    now = _now()
    if user.locked_until is not None and user.locked_until > now:
        remaining = max(1, ceil((user.locked_until - now).total_seconds() / 60))
        raise LockedError(f"账号已锁定，请在 {remaining} 分钟后重试")

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.failed_login_count = 0
        db.commit()
        raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    db.refresh(user)
    return user


def set_user_status(db: Session, user_id: int, status: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    if status not in ("active", "disabled"):
        raise ConflictError("未知状态")
    user.status = status
    db.commit()
    db.refresh(user)
    return user


def reset_password(db: Session, user_id: int, new_password: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    user.password_hash = hash_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    db.refresh(user)
    return user
