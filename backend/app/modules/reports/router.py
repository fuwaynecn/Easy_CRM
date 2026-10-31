"""报表接口：全部只读，无写路径。"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.db.session import get_db
from app.modules.reports.common import resolve_period
from app.modules.reports.schemas import FunnelReport, WinRateReport
from app.modules.reports.service import funnel, win_rate

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/funnel", response_model=FunnelReport)
def get_funnel(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return funnel(db, user)


@router.get("/win-rate", response_model=WinRateReport)
def get_win_rate(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    date_from: date | None = None,
    date_to: date | None = None,
):
    return win_rate(db, user, resolve_period(date_from, date_to))
