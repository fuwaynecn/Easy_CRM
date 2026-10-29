from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.session import get_db
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLES, User


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedError("未登录")
    user_id = decode_token(token.strip(), "access")
    user = db.get(User, user_id)
    if user is None or user.status != "active":
        raise UnauthorizedError("账号不可用")
    # 角色只该来自 ROLES。数据库被手工改成 "guest" 之类时，作用域层虽然 fail-closed，
    # 但放它过鉴权会把"账号异常"伪装成"没有数据/没有权限"，排查时看不出根因。
    if user.role not in ROLES:
        raise ForbiddenError("账号角色异常，请联系管理员")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != ROLE_ADMIN:
        raise ForbiddenError("需要管理员权限")
    return user


def require_manager_or_admin(user: CurrentUser) -> User:
    if user.role not in (ROLE_ADMIN, ROLE_MANAGER):
        raise ForbiddenError("需要主管及以上权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
ManagerOrAdminUser = Annotated[User, Depends(require_manager_or_admin)]
