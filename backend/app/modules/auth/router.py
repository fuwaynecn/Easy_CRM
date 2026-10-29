from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.errors import UnauthorizedError
from app.db.session import get_db
from app.modules.auth.schemas import (
    AccessTokenResponse,
    LoginRequest,
    TokenResponse,
)
from app.modules.auth.service import login, refresh_access
from app.modules.users.schemas import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_PATH = "/api/v1/auth"
REFRESH_MAX_AGE = 60 * 60 * 24 * settings.refresh_token_ttl_days


def _set_refresh_cookie(response: Response, token: str) -> None:
    """refresh token 只走 httpOnly cookie；access token 只走响应体。"""
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=REFRESH_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=REFRESH_PATH,
    )


@router.post("/login", response_model=TokenResponse)
def post_login(
    payload: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
):
    user, access, refresh, expires_in = login(
        db, payload.username, payload.password.get_secret_value()
    )
    _set_refresh_cookie(response, refresh)
    return TokenResponse(
        access_token=access, expires_in=expires_in, user=UserRead.model_validate(user)
    )


@router.post("/refresh", response_model=AccessTokenResponse)
def post_refresh(
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    refresh_token: Annotated[str | None, Cookie()] = None,
):
    if not refresh_token:
        raise UnauthorizedError("缺少刷新令牌")
    _, access, expires_in = refresh_access(db, refresh_token)
    _set_refresh_cookie(response, refresh_token)
    return AccessTokenResponse(access_token=access, expires_in=expires_in)


@router.post("/logout", status_code=204)
def post_logout(response: Response):
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_PATH,
        httponly=True,
        samesite="lax",
    )


@me_router.get("/me", response_model=UserRead)
def get_me(user: CurrentUser):
    return user
