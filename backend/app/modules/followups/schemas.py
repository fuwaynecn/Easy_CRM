"""待办的请求/响应模型。

`FollowUpUpdate` **没有 `customer_id` / `contact_id` / `opportunity_id`**：待办跟客户走，
搬客户等于换一条待办；关联对象创建后不可改（P3 裁定 11）。
`status` 在 PATCH 里只接受 `pending` / `cancelled`——完成必须走
`POST /follow_ups/{id}/done`（裁定 10），那条路径才会可选地写跟进并刷新
`customers.last_activity_at`。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.activities.schemas import ActivityRead

_DERIVED_FIELDS = ("customer_name", "created_by_name")


class FollowUpCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    contact_id: int | None = None
    opportunity_id: int | None = None
    due_at: datetime
    content: str = Field(min_length=1, max_length=2000)


class FollowUpUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_at: datetime | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    # 只接受 pending / cancelled（done 走 /done，见裁定 10）
    status: str | None = None


class FollowUpDone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 「顺便写条跟进记录」复选项（spec §4.5）
    create_activity: bool = False
    # 勾选时跟进方式；不勾选却显式传它会 400，不做静默忽略
    activity_type: str = "visit"


class FollowUpRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    # 首页与待办列表都要显示客户名，避免前端按 id 二次查名
    customer_name: str
    contact_id: int | None
    opportunity_id: int | None
    due_at: datetime
    content: str
    status: str
    done_at: datetime | None
    created_by: int
    created_by_name: str
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derive_joined_names(cls, data: Any) -> Any:
        if isinstance(data, (dict, cls)):
            return data
        customer = getattr(data, "customer", None)
        operator = getattr(data, "operator", None)
        values = {
            field: getattr(data, field)
            for field in cls.model_fields
            if field not in _DERIVED_FIELDS
        }
        values["customer_name"] = customer.name if customer is not None else ""
        values["created_by_name"] = (
            operator.display_name if operator is not None else ""
        )
        return values


class FollowUpDoneRead(BaseModel):
    """完成待办的返回体：待办本身 + 可选的随附跟进。"""

    follow_up: FollowUpRead
    activity: ActivityRead | None
