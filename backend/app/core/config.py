from pydantic_settings import BaseSettings, SettingsConfigDict

# 开发占位密钥：仅用于本地与测试，长度 ≥48 字节以满足 PyJWT 的 HMAC 密钥长度建议。
# create_app() 启动时会检测该值并打中文告警，提示用 JWT_SECRET 环境变量覆盖。
DEV_JWT_SECRET_PLACEHOLDER = (
    "dev-only-change-me-0123456789abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/easycrm"
    jwt_secret: str = DEV_JWT_SECRET_PLACEHOLDER
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 120
    refresh_token_ttl_days: int = 7
    cors_origins: list[str] = ["http://localhost:5173"]
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    cookie_secure: bool = False


settings = Settings()
