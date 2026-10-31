"""首页接口：三块一次返回。

spec §5 的端点清单没有首页数据接口（§6.1 只定了页面），这是 P3 唯一新增的端点
（裁定 7）：三块各有聚合与豁免条件，拆成三次列表请求会让首屏多 3 个 RTT 且口径
分散在三个前端文件里。
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.db.session import get_db
from app.modules.followups.schemas import FollowUpRead
from app.modules.home.schemas import (
    FollowUpBlock,
    HomeRead,
    PoolNoticeBlock,
    PoolNoticeItem,
    PoolNoticeRule,
)
from app.modules.home.service import days_since_local, summary
from app.modules.pools.rules import load_pool_rules

router = APIRouter(tags=["home"])


@router.get("/home", response_model=HomeRead)
def get_home(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    now = datetime.now(UTC)
    rules = load_pool_rules(db)
    result = summary(db, user, now=now, rules=rules)
    return HomeRead(
        today=FollowUpBlock(
            total=result.today_total,
            items=[FollowUpRead.model_validate(item) for item in result.today],
        ),
        overdue=FollowUpBlock(
            total=result.overdue_total,
            items=[FollowUpRead.model_validate(item) for item in result.overdue],
        ),
        pool_notice=PoolNoticeBlock(
            total=result.pool_notice_total,
            items=[
                PoolNoticeItem(
                    id=customer.id,
                    name=customer.name,
                    owner_id=customer.owner_id,
                    owner_name=(
                        customer.owner.display_name if customer.owner else None
                    ),
                    last_activity_at=customer.last_activity_at,
                    days_inactive=days_since_local(
                        customer.last_activity_at, now=now
                    ),
                )
                for customer in result.pool_notice
            ],
            rule=PoolNoticeRule(
                inactive_days=rules.inactive_days,
                recycle_notice_days=rules.recycle_notice_days,
                threshold_days=rules.notice_threshold_days,
            ),
        ),
    )
