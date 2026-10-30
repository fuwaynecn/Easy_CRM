"""跟进记录的请求/响应模型。

`ActivityCreate.next_follow_up` 是 spec §4.5 的「下次跟进」区块：填了就在**同一个
事务**里建一条 pending 待办。做成嵌套对象而不是"前端再发一个请求"，是因为两个
请求之间失败会留下"有跟进、没待办"的静默缺口。

`ActivityUpdate` 的白名单只有 `type / content / activity_at`：关联对象（客户/联系人/
机会）创建后不可改（P3 裁定 11），改错就删了重写。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 派生字段：不是 ORM 列，由 model_validator 从关系里补出来
_DERIVED_FIELDS = ("created_by_name",)


class NextFollowUpIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_at: datetime
    content: str = Field(min_length=1, max_length=2000)


class ActivityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    contact_id: int | None = None
    opportunity_id: int | None = None
    type: str = "call"
    content: str = Field(min_length=1)
    # None = 现在；允许补录过去（spec §3.8）
    activity_at: datetime | None = None
    next_follow_up: NextFollowUpIn | None = None


class ActivityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    content: str | None = Field(default=None, min_length=1)
    activity_at: datetime | None = None


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    contact_id: int | None
    opportunity_id: int | None
    type: str
    content: str
    activity_at: datetime
    created_by: int
    # 操作人姓名：时间线与列表都要显示"谁写的"，不额外开放 /users 名录
    created_by_name: str
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derive_operator_name(cls, data: Any) -> Any:
        if isinstance(data, (dict, cls)):
            return data
        operator = getattr(data, "operator", None)
        values = {
            field: getattr(data, field)
            for field in cls.model_fields
            if field not in _DERIVED_FIELDS
        }
        values["created_by_name"] = operator.display_name if operator is not None else ""
        return values
