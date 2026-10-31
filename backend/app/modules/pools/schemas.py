"""公海池的 HTTP 契约。

规则更新全部可选：PATCH 的语义是“只改给的字段”，其余保持当前值。跨字段约束
（`recycle_notice_days < inactive_days`）必须拿当前值合并后再判，所以放在
`rules.update_pool_rules`，不放在 Pydantic 的 model_validator 里。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PoolRuleRead(BaseModel):
    inactive_days: int
    max_owned: int
    auto_recycle_enabled: bool
    protect_with_open_deal: bool
    protect_with_pending_followup: bool
    recycle_notice_days: int


class PoolRulesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inactive_days: int | None = Field(default=None, ge=1, le=3650)
    max_owned: int | None = Field(default=None, ge=1, le=10000)
    auto_recycle_enabled: bool | None = None
    protect_with_open_deal: bool | None = None
    protect_with_pending_followup: bool | None = None
    recycle_notice_days: int | None = Field(default=None, ge=0, le=3650)


class PoolCustomerRead(BaseModel):
    id: int
    name: str
    short_name: str | None
    industry: str | None
    level: str
    region: str | None
    status: str
    last_activity_at: datetime | None
    # 从未跟进是 None，不是 0：0 会被前端显示成“今天刚跟过”
    days_inactive: int | None
    created_at: datetime


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: int
    reason: str | None = Field(default=None, max_length=200)


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)


class AssignableUserRead(BaseModel):
    id: int
    display_name: str
    department_id: int
    department_name: str
    owned_count: int
    max_owned: int
    quota_remaining: int
