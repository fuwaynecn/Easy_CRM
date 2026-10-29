from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class UserCreate(BaseModel):
    # 未知字段一律 422：默认的 extra="ignore" 会让 password_hash 之类的字段
    # 静默消失并返回 201，调用方以为写进去了。
    model_config = ConfigDict(extra="forbid")

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


class UserUpdate(BaseModel):
    """部分更新：只处理实际提交的字段。

    department_id 显式传 null 表示"清空部门"，因此不能只看值是否为 None；
    路由用 model_dump(exclude_unset=True) 区分"没提交"与"提交了 null"。
    """

    # 未知字段一律 422（而非静默丢弃后返回 200）：否则 service 层的
    # UPDATABLE_FIELDS 白名单守卫在 HTTP 路径上永远不可达。
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    role: str | None = None
    department_id: int | None = None
    status: str | None = None


class PasswordReset(BaseModel):
    # 同 UserCreate.password：SecretStr + 无 Field 约束，长度校验在 service 层。
    new_password: SecretStr
