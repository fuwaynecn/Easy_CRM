from pydantic import BaseModel, Field, SecretStr

from app.modules.users.schemas import UserRead


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # 刻意不给 password 加任何 Field 校验：pydantic 校验失败时会把原始输入写进
    # detail[].input（SecretStr 也一样，value_error 的 input 仍是明文），等于把口令
    # 回显给客户端。长度交给 authenticate 处理即可。
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
