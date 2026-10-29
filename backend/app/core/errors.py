from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, detail: Any = None) -> None:
        self.message = message
        self.detail = detail
        super().__init__(message)


def error_response(exc: AppError) -> JSONResponse:
    """把 AppError 统一转成响应对象。

    全局异常处理器与 /auth/refresh 都走这里，保证两处错误体不会漂移；后者必须自己
    构造响应对象（不能 raise），才能在同一个对象上清 refresh cookie。
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "detail": jsonable_encoder(exc.detail),
        },
    )


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class WeakPasswordError(AppError):
    """口令不满足最低强度：400，供创建用户与重置口令共用。"""

    status_code = 400
    code = "weak_password"


class InvalidValueError(AppError):
    """取值不在允许集合内（角色、账号状态等）：400。

    与 409 区分：409 表示与现有数据冲突（重名、已存在），取值非法并不是冲突。
    """

    status_code = 400
    code = "invalid_value"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class LockedError(AppError):
    """账号锁定：423 Locked，调用方据此提示剩余等待时间。"""

    status_code = 423
    code = "locked"
