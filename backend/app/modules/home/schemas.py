"""首页三块的线上契约。

`pool_notice.rule` 把阈值随响应一起返回：前端据此渲染“N 天无跟进、提前 M 天预警”，
不在前端复制一份常量。P4 起这些值来自 `pool_rules`，管理员可在「公海规则」里修改。
"""

from datetime import datetime

from pydantic import BaseModel

from app.modules.followups.schemas import FollowUpRead


class FollowUpBlock(BaseModel):
    total: int
    items: list[FollowUpRead]


class PoolNoticeItem(BaseModel):
    id: int
    name: str
    owner_id: int
    owner_name: str | None
    last_activity_at: datetime
    # 已多少天没活动（按 Asia/Shanghai 自然日），前端直接显示，不自己算
    days_inactive: int


class PoolNoticeRule(BaseModel):
    inactive_days: int
    recycle_notice_days: int
    threshold_days: int


class PoolNoticeBlock(BaseModel):
    total: int
    items: list[PoolNoticeItem]
    rule: PoolNoticeRule


class HomeRead(BaseModel):
    today: FollowUpBlock
    overdue: FollowUpBlock
    pool_notice: PoolNoticeBlock
