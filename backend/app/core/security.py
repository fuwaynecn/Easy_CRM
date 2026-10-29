"""口令哈希与 JWT 令牌。

access token 与 refresh token 用 payload 里的 ``type`` 区分；解码时校验类型，
refresh token 因此无法被当成 access token 使用。
"""

import logging
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import settings
from app.core.errors import UnauthorizedError

logger = logging.getLogger(__name__)

# pwdlib 的 recommended() 当前为 Argon2id（argon2-cffi 默认成本参数）
_password_hash = PasswordHash.recommended()

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def hash_password(raw: str) -> str:
    return _password_hash.hash(raw)


def verify_password(raw: str, hashed: str | None) -> bool:
    """校验口令；脏哈希（空串、None、非 argon2）一律返回 False。

    外部导入的数据可能带来无法解析的哈希，这里必须吞掉异常。异常分支只记录异常
    类型：日志里既不能有明文口令，也不能有口令哈希；同时保留类型名，线上才分得清
    "脏哈希"（InvalidHashError 之类）与 pwdlib 自身的真实故障。
    """
    try:
        return _password_hash.verify(raw, hashed or "")
    except Exception as exc:  # noqa: BLE001 — 脏哈希的异常类型不可枚举，必须全部吞掉
        logger.debug("口令校验失败: %s", type(exc).__name__)
        return False


# 固定假哈希：用户不存在时也跑一次等价耗时的校验，避免响应时间暴露账号是否存在。
# 这里的口令是常量占位，不对应任何账号。
DECOY_PASSWORD_HASH = hash_password("decoy-password-for-timing-only")


def _encode(user_id: int, token_type: str, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "type": token_type, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> tuple[str, int]:
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    return _encode(user_id, TOKEN_TYPE_ACCESS, ttl), int(ttl.total_seconds())


def create_refresh_token(user_id: int) -> str:
    ttl = timedelta(days=settings.refresh_token_ttl_days)
    return _encode(user_id, TOKEN_TYPE_REFRESH, ttl)


def decode_token(token: str, expected_type: str) -> int:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    if payload.get("type") != expected_type:
        raise UnauthorizedError("令牌类型不正确")
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("令牌内容不合法") from exc
