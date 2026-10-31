"""报表接口：全部只读，无写路径。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.db.session import get_db
from app.modules.reports.schemas import FunnelReport
from app.modules.reports.service import funnel

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/funnel", response_model=FunnelReport)
def get_funnel(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return funnel(db, user)
