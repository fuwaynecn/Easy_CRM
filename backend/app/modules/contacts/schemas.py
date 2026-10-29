"""联系人的请求/响应模型。

``extra="forbid"``：``customer_id``、``deleted_at`` 这类字段一旦能从前端写进来，
联系人就能被"搬家"到别人的客户名下（等于绕过客户作用域），所以必须 422 而不是
被静默丢弃。字段长度与 design §3.4 的列定义一一对应。

``email`` 用 ``EmailStr``（依赖 email-validator）：填了就必须合法；``phone`` 与
``wechat`` 不做任何格式限制——国内号码格式太杂（座机分机、微信同号、带区号），
硬校验只会误伤真实数据。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = Field(default=None, max_length=128)
    is_primary: bool = False
    decision_role: str | None = Field(default=None, max_length=32)
    remark: str | None = None


class ContactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = Field(default=None, max_length=128)
    is_primary: bool | None = None
    decision_role: str | None = Field(default=None, max_length=32)
    remark: str | None = None


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    name: str
    title: str | None
    phone: str | None
    wechat: str | None
    email: str | None
    is_primary: bool
    decision_role: str | None
    remark: str | None
    created_at: datetime
