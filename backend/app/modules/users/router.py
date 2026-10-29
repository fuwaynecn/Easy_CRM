"""用户管理接口：全部仅管理员可访问。

口令字段一律用 `.get_secret_value()` 解包：schema 里是 SecretStr，直接把
payload.password 传下去会把掩码串 "**********" 当成口令哈希入库——不报任何错，
只表现为"新建的账号全都登不进去"。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import AdminUser
from app.core.errors import ForbiddenError
from app.core.pagination import Page, PageParams
from app.db.session import get_db
from app.modules.users.models import ROLE_ADMIN, STATUS_DISABLED, User
from app.modules.users.schemas import PasswordReset, UserCreate, UserRead, UserUpdate
from app.modules.users.service import (
    create_user,
    list_users,
    reset_password,
    update_user,
)

router = APIRouter(prefix="/users", tags=["users"])


def _guard_self_lockout(actor: User, user_id: int, changes: dict[str, Any]) -> None:
    """拦住管理员把自己锁死的两条路：停用自己、把自己的 admin 角色降级。

    系统里只要有一个 admin 这么干过，就再也没人能进后台管理用户了。
    放在路由层是因为它取决于"当前操作者是谁"，属于 HTTP 上下文的判断，
    不是用户数据本身的约束。
    """
    if user_id != actor.id:
        return
    if changes.get("status") == STATUS_DISABLED:
        raise ForbiddenError("不能停用自己的账号")
    role = changes.get("role")
    if role is not None and role != ROLE_ADMIN:
        raise ForbiddenError("不能降级自己的管理员角色")


@router.get("", response_model=Page[UserRead])
def get_users(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends()],
    _admin: AdminUser,
    keyword: str | None = None,
):
    items, total = list_users(
        db, keyword=keyword, page=page.page, page_size=page.page_size
    )
    return Page[UserRead](
        items=[UserRead.model_validate(user) for user in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("", response_model=UserRead, status_code=201)
def post_user(
    payload: UserCreate,
    db: Annotated[Session, Depends(get_db)],
    _admin: AdminUser,
):
    return create_user(
        db,
        username=payload.username,
        password=payload.password.get_secret_value(),
        display_name=payload.display_name,
        role=payload.role,
        department_id=payload.department_id,
    )


@router.patch("/{user_id}", response_model=UserRead)
def patch_user(
    user_id: int,
    payload: UserUpdate,
    db: Annotated[Session, Depends(get_db)],
    actor: AdminUser,
):
    changes = payload.model_dump(exclude_unset=True)
    _guard_self_lockout(actor, user_id, changes)
    return update_user(db, user_id, changes)


@router.post("/{user_id}/reset-password", status_code=204)
def post_reset_password(
    user_id: int,
    payload: PasswordReset,
    db: Annotated[Session, Depends(get_db)],
    _admin: AdminUser,
):
    reset_password(db, user_id, payload.new_password.get_secret_value())
    return Response(status_code=204)
