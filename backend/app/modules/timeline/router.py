"""客户时间线接口。

路径挂在 `/customers/{id}/timeline`（spec §5），但实现放在独立模块：客户服务
import activities/followups 会形成循环（activities.service 已经 import
customers.service），timeline 只向下依赖，不制造反向边。

错误码口径（全项目一致，拍定于 T6 收尾）：**参数与请求体校验 = 422**（`limit`
越界、body 白名单由 FastAPI/pydantic 在参数层挡住），**业务规则 = 400**（非法
游标、service 层收到的非法 limit），**权限 = 403**，**不存在 = 404**。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.db.session import get_db
from app.modules.timeline.schemas import TimelineItemRead, TimelinePageRead
from app.modules.timeline.service import get_timeline

router = APIRouter(prefix="/customers", tags=["timeline"])


@router.get("/{customer_id}/timeline", response_model=TimelinePageRead)
def get_customer_timeline(
    customer_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    entries, next_cursor = get_timeline(
        db, user, customer_id, cursor=cursor, limit=limit
    )
    return TimelinePageRead(
        items=[TimelineItemRead.model_validate(entry) for entry in entries],
        next_cursor=next_cursor,
    )
