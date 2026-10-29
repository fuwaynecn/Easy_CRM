from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    # 口令一律用 SecretStr：repr 不出现明文；且不挂 Field 约束，否则超限时
    # 422 响应体的 detail[].input 会原样回显口令。长度校验在 service 层做。
    password: SecretStr
    display_name: str = Field(min_length=1, max_length=64)
    role: str
    department_id: int | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    department_id: int | None
    status: str
    created_at: datetime


class UserStatusUpdate(BaseModel):
    status: str


class PasswordReset(BaseModel):
    # 同 UserCreate.password：SecretStr + 无 Field 约束，长度校验在 service 层。
    new_password: SecretStr
