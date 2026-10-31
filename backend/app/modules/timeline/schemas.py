"""时间线的线上契约。

`type` 会是 `activity` / `stage_change` / `owner_change`（P4 接入 `owner_change_logs`，
裁定 6）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TimelineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["activity", "stage_change", "owner_change"]
    id: int
    occurred_at: datetime

    # 操作人：账号停用后姓名仍在，配 operator_disabled 让前端标注"已停用"
    operator_id: int | None
    operator_name: str
    operator_disabled: bool

    # 活动字段（type == "activity" 时有值）
    activity_type: str | None
    content: str | None
    contact_id: int | None
    contact_name: str | None
    contact_deleted: bool

    # 机会与阶段字段（type == "stage_change" 时有值；活动也可能关联机会）
    opportunity_id: int | None
    opportunity_name: str | None
    opportunity_deleted: bool
    from_stage: str | None
    to_stage: str | None
    # 时点金额（裁定 4）：numeric(14,2) 在线上仍是定点字符串（pydantic 把 Decimal
    # 序列化成字符串，与金额列同一惯例，前端 formatAmount 直接可用）
    amount_snapshot: Decimal | None

    # P4：owner_change 条目的动作与前后负责人姓名（其它类型为 None）
    owner_change_action: str | None = None
    from_owner_name: str | None = None
    to_owner_name: str | None = None


class TimelinePageRead(BaseModel):
    items: list[TimelineItemRead]
    # 没有更多时为 null；客户端不得解析游标（裁定 14）
    next_cursor: str | None
