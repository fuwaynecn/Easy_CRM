from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/easycrm"
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 120
    refresh_token_ttl_days: int = 7
    cors_origins: list[str] = ["http://localhost:5173"]
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    cookie_secure: bool = False


settings = Settings()
