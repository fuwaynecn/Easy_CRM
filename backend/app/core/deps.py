from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.session import get_db


def get_current_user(db: Annotated[Session, Depends(get_db)]) -> object:
    """Task 3 占位：认证尚未接入，一律视为未登录；Task 5 替换为真实实现。"""
    raise UnauthorizedError("未登录")


CurrentUser = Annotated[object, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> object:
    if getattr(user, "role", None) != "admin":
        raise ForbiddenError("需要管理员权限")
    return user
