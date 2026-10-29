"""口令哈希。JWT 相关能力在 Task 5 追加到本模块。"""

from pwdlib import PasswordHash

# pwdlib 的 recommended() 当前为 Argon2id（argon2-cffi 默认成本参数）
_password_hash = PasswordHash.recommended()


def hash_password(raw: str) -> str:
    return _password_hash.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _password_hash.verify(raw, hashed)
