"""输单原因：只读列表。

只有 ``GET``：管理端的增删改属 P5 后台（P2 没有对应页面，接口没有消费者）。
登录即可读——输单对话框要拿这份字典，销售与主管都必须能读到。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.db.session import get_db
from app.modules.lost_reasons.schemas import LostReasonRead
from app.modules.lost_reasons.service import list_lost_reasons

router = APIRouter(prefix="/lost-reasons", tags=["lost-reasons"])


@router.get("", response_model=list[LostReasonRead])
def get_lost_reasons(
    db: Annotated[Session, Depends(get_db)],
    _user: CurrentUser,
):
    return list_lost_reasons(db)
