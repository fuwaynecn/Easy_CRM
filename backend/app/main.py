import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import DEV_JWT_SECRET_PLACEHOLDER, settings
from app.core.errors import AppError, error_response
from app.modules.activities.router import router as activities_router
from app.modules.auth.router import me_router
from app.modules.auth.router import router as auth_router
from app.modules.contacts.router import router as contacts_router
from app.modules.customers.router import router as customers_router
from app.modules.departments.router import router as departments_router
from app.modules.followups.router import router as follow_ups_router
from app.modules.home.router import router as home_router
from app.modules.lost_reasons.router import router as lost_reasons_router
from app.modules.opportunities.router import router as opportunities_router
from app.modules.pools.router import router as pools_router
from app.modules.timeline.router import router as timeline_router
from app.modules.users.router import router as users_router
from app.scheduler import shutdown_scheduler, start_scheduler

logger = logging.getLogger(__name__)

# 开发/测试环境不需要 COOKIE_SECURE；其余环境一律要求开启。
NON_PRODUCTION_ENVIRONMENTS = frozenset({"dev", "development", "local", "test", "testing"})

# 已打印过的启动告警键。create_app() 在测试里会被反复调用（每个 client 夹具一次），
# 不加这个标记同一句告警会重复几十遍，把真正的问题淹掉。
_warned_startup_issues: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key in _warned_startup_issues:
        return
    _warned_startup_issues.add(key)
    logger.warning(message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.scheduler_enabled:
        start_scheduler()
    try:
        yield
    finally:
        if settings.scheduler_enabled:
            shutdown_scheduler()


def create_app() -> FastAPI:
    if settings.jwt_secret == DEV_JWT_SECRET_PLACEHOLDER:
        _warn_once(
            "jwt_secret",
            "当前 JWT 密钥仍是开发占位值，请通过环境变量 JWT_SECRET 配置真实密钥",
        )
    if (
        not settings.cookie_secure
        and settings.environment.strip().lower() not in NON_PRODUCTION_ENVIRONMENTS
    ):
        _warn_once(
            "cookie_secure",
            f"当前环境为 {settings.environment}，但 COOKIE_SECURE 未开启："
            "生产环境必须设置 COOKIE_SECURE=true，否则 refresh cookie 会以明文 HTTP 传输",
        )

    app = FastAPI(title="Easy CRM API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if exc.status_code == 404:
            code, message = "not_found", "资源不存在"
        elif exc.status_code == 405:
            code, message = "method_not_allowed", "请求方法不被允许"
        else:
            code = f"http_{exc.status_code}"
            message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code,
                "message": message,
                "detail": jsonable_encoder(exc.detail),
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 一律删掉 input：pydantic 会把校验失败的原始输入回显到 detail[].input，
        # 口令字段（哪怕声明成 SecretStr）也会以明文出现。password 只是当前已知的
        # 一种泄漏，整类删掉比逐个字段打补丁更稳；loc/msg/type/ctx 足够前端定位。
        details = [
            {key: value for key, value in error.items() if key != "input"}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "请求参数校验失败",
                "detail": jsonable_encoder(details),
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(departments_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(me_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(customers_router, prefix="/api/v1")
    app.include_router(timeline_router, prefix="/api/v1")
    app.include_router(activities_router, prefix="/api/v1")
    app.include_router(follow_ups_router, prefix="/api/v1")
    app.include_router(contacts_router, prefix="/api/v1")
    app.include_router(lost_reasons_router, prefix="/api/v1")
    app.include_router(opportunities_router, prefix="/api/v1")
    app.include_router(home_router, prefix="/api/v1")
    app.include_router(pools_router, prefix="/api/v1")

    return app


app = create_app()
