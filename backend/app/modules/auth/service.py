from sqlalchemy.orm import Session

from app.core.errors import UnauthorizedError
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.modules.users.models import User
from app.modules.users.service import authenticate


def login(db: Session, username: str, password: str) -> tuple[User, str, str, int]:
    """返回 (用户, access_token, refresh_token, expires_in)。"""
    user = authenticate(db, username, password)
    access, expires_in = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    return user, access, refresh, expires_in


def refresh_access(db: Session, refresh_token: str) -> tuple[User, str, int]:
    user_id = decode_token(refresh_token, TOKEN_TYPE_REFRESH)
    user = db.get(User, user_id)
    if user is None or user.status != "active":
        raise UnauthorizedError("账号不可用")
    access, expires_in = create_access_token(user.id)
    return user, access, expires_in
