from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.errors import AppError, UnauthorizedError, error_response
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


def _clear_refresh_cookie(response: Response) -> None:
    """清 cookie 的属性必须与写入时一致（path/httponly/samesite），否则浏览器匹配不到。"""
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_PATH,
        httponly=True,
        samesite="lax",
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
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> Response:
    """刷新 access token；失败时必须在实际返回的响应上清掉 refresh cookie。

    这里自己构造 JSONResponse 而不是 raise 交给全局处理器：FastAPI 抛出异常后会新建
    响应对象，挂在原响应上的 delete_cookie 会被丢弃（表现为"看起来清了其实没清"）。
    refresh JWT 的 exp 是登录时固定的，而 cookie 的 Max-Age 每次刷新都续期，不清掉
    就会让已失效的 token 对应的 cookie 在浏览器里再多活 7 天。
    """
    try:
        if not refresh_token:
            raise UnauthorizedError("缺少刷新令牌")
        _, access, expires_in = refresh_access(db, refresh_token)
    except AppError as exc:
        failed = error_response(exc)
        _clear_refresh_cookie(failed)
        return failed

    refreshed = JSONResponse(
        content=AccessTokenResponse(
            access_token=access, expires_in=expires_in
        ).model_dump()
    )
    _set_refresh_cookie(refreshed, refresh_token)
    return refreshed


@router.post("/logout", status_code=204)
def post_logout(response: Response):
    _clear_refresh_cookie(response)


@me_router.get("/me", response_model=UserRead)
def get_me(user: CurrentUser):
    return user
