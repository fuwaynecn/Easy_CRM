from pydantic import BaseModel, Field, SecretStr

from app.modules.users.schemas import UserRead


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # 刻意不给 password 加任何 Field 约束：pydantic 校验失败时会把原始输入写进
    # detail[].input（SecretStr 也一样，value_error 的 input 仍是明文），等于把口令
    # 回显给客户端。长度校验在 service 层（users.service._validate_password），且只
    # 用于创建/重置口令；登录路径本身不校验长度，超长口令按普通错口令处理。
    # 统一校验错误处理器另外会删掉所有 detail[].input 作为兜底。
    password: SecretStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
