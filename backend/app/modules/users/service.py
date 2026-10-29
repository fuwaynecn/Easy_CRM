from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    LockedError,
    NotFoundError,
    UnauthorizedError,
    WeakPasswordError,
)
from app.core.security import DECOY_PASSWORD_HASH, hash_password, verify_password
from app.modules.departments.service import get_department
from app.modules.users.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_SALES,
    ROLES,
    STATUS_ACTIVE,
    USER_STATUSES,
    User,
)

__all__ = [
    "ROLES",
    "ROLE_ADMIN",
    "ROLE_MANAGER",
    "ROLE_SALES",
    "STATUS_ACTIVE",
    "USER_STATUSES",
    "authenticate",
    "create_user",
    "get_user_by_username",
    "list_users",
    "reset_password",
    "set_user_status",
    "update_user",
]

# 用户不存在与口令错误必须给出完全相同的对外文案，避免账号存在性探测
INVALID_CREDENTIALS_MESSAGE = "用户名或密码错误"

MIN_PASSWORD_LENGTH = 8
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 64

# update_user 认的字段。路由用 model_dump(exclude_unset=True) 只传提交过的键。
UPDATABLE_FIELDS = ("display_name", "role", "department_id", "status")


def _validate_password(password: str) -> None:
    """口令最短长度在 service 层校验。

    不放在 schema 的 Field 约束上：字段一旦挂约束，Pydantic 会把原始输入
    回显到 422 响应体的 detail[].input，等于把明文口令写进响应。
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"密码长度不能少于 {MIN_PASSWORD_LENGTH} 位")


def _validate_role(role: str) -> None:
    """角色必须在 ROLES 内：取值非法是 400，不是与现有数据冲突的 409。"""
    if role not in ROLES:
        raise InvalidValueError(f"未知角色：{role}")


def _validate_status(status: str) -> None:
    if status not in USER_STATUSES:
        raise InvalidValueError(f"未知状态：{status}")


def _ensure_department_exists(db: Session, department_id: int | None) -> None:
    """外键之前先查一次：坏 id 直接撞外键只会得到 500，调用方拿不到可用提示。"""
    if department_id is not None:
        _ = get_department(db, department_id)


def _now() -> datetime:
    return datetime.now(UTC)


def get_user_by_username(db: Session, username: str) -> User | None:
    """按登录名查用户，两端统一 strip。

    建号时 create_user 会 strip，若这里不 strip，就会出现"带空格建出来的账号
    用同一个带空格的用户名登不进去"。authenticate 也走这里，三处口径一致。
    """
    return db.execute(
        select(User).where(User.username == username.strip())
    ).scalar_one_or_none()


def list_users(
    db: Session,
    *,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[User], int]:
    """返回当前页用户与满足条件的总数（分页体需要 total，不能只看本页）。"""
    stmt = select(User)
    if keyword and keyword.strip():
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.display_name.ilike(like)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(User.id).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


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
    # schema 的长度约束跑在 strip 之前："  a  " 长度 5 能过 min_length=3，
    # strip 后却只剩 1 个字符。非空与最终长度必须在 strip 之后再校验一次。
    if not cleaned_username:
        raise InvalidValueError("登录名不能为空")
    if not MIN_USERNAME_LENGTH <= len(cleaned_username) <= MAX_USERNAME_LENGTH:
        raise InvalidValueError(
            f"登录名长度必须在 {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} 位之间"
        )
    cleaned_display_name = display_name.strip()
    if not cleaned_display_name:
        raise InvalidValueError("显示名不能为空")
    _validate_password(password)
    _validate_role(role)
    _ensure_department_exists(db, department_id)
    if get_user_by_username(db, cleaned_username) is not None:
        raise ConflictError("登录名已存在")
    user = User(
        username=cleaned_username,
        password_hash=hash_password(password),
        display_name=cleaned_display_name,
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
    if user.status != STATUS_ACTIVE:
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
    _validate_status(status)
    user.status = status
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user_id: int, changes: dict[str, Any]) -> User:
    """按"实际提交的字段"更新用户。

    用 dict 而不是一串可选关键字，是因为必须区分"没提交"与"显式置空"：
    department_id=null 是合法的清空动作，而 display_name / role / status 传 null
    一律视为未提交（这几列在库里是 NOT NULL）。
    """
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("用户不存在")

    unknown = set(changes) - set(UPDATABLE_FIELDS)
    if unknown:
        raise InvalidValueError(f"不可更新的字段：{'、'.join(sorted(unknown))}")

    display_name = changes.get("display_name")
    if display_name is not None:
        cleaned_display_name = display_name.strip()
        if not cleaned_display_name:
            raise InvalidValueError("显示名不能为空")
        user.display_name = cleaned_display_name

    role = changes.get("role")
    if role is not None:
        _validate_role(role)
        user.role = role

    if "department_id" in changes:
        _ensure_department_exists(db, changes["department_id"])
        user.department_id = changes["department_id"]

    status = changes.get("status")
    if status is not None:
        _validate_status(status)
        user.status = status

    db.commit()
    db.refresh(user)
    return user


def reset_password(db: Session, user_id: int, new_password: str) -> User:
    """管理员重置口令。

    口径（评审裁定）：只清 ``failed_login_count`` 与 ``locked_until``，**不解除
    disabled**。停用是独立的管理动作，重置口令顺带把账号恢复成 active 等于意外
    提权；需要恢复时必须再调一次 update_user(status="active")。
    """
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    _validate_password(new_password)
    user.password_hash = hash_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    db.refresh(user)
    return user
