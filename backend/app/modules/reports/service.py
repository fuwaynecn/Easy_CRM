"""报表查询。

全部函数都是只读的，不 commit、不写库。金额一律取 `stage_histories.amount_snapshot`；
只有历史脏数据没有 history 时才回落到机会现值。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.modules.departments.models import Department
from app.modules.opportunities.models import OPPORTUNITY_STAGES, Opportunity
from app.modules.reports.common import (
    Period,
    latest_stage_subquery,
    opportunity_scope_conditions,
)
from app.modules.reports.schemas import (
    DepartmentRateRead,
    FunnelReport,
    FunnelStageRead,
    WinRateReport,
)
from app.modules.users.models import User


def funnel(
    db: Session, actor: User, *, now: datetime | None = None
) -> FunnelReport:
    """漏斗：当前各阶段的机会数与金额合计（存量快照，不是流量转化）。

    阶段直接读 `Opportunity.stage`：它是 `change_stage` 维护的当前阶段真值，
    比「最后一条 history 的 `to_stage`」少一层推导，也不受历史写入顺序/回填影响。
    history 只贡献金额快照（`amount_snapshot`），因为金额现值会被后续修改覆盖。
    """
    latest = latest_stage_subquery()
    stage = Opportunity.stage
    amount = func.coalesce(latest.c.amount_snapshot, Opportunity.amount)
    rows = db.execute(
        select(
            stage.label("stage"),
            func.count().label("count"),
            func.coalesce(func.sum(amount), 0).label("amount"),
        )
        .select_from(Opportunity)
        .outerjoin(
            latest,
            and_(latest.c.opportunity_id == Opportunity.id, latest.c.rn == 1),
        )
        .where(*opportunity_scope_conditions(actor))
        .group_by(stage)
    ).all()
    stats = {
        row.stage: (int(row.count), Decimal(row.amount)) for row in rows
    }
    return FunnelReport(
        as_of=now or datetime.now(UTC),
        stages=[
            FunnelStageRead(
                stage=name,
                count=stats.get(name, (0, Decimal(0)))[0],
                amount=stats.get(name, (0, Decimal(0)))[1],
            )
            for name in OPPORTUNITY_STAGES
        ],
    )


def _rate(won: int, lost: int) -> float | None:
    total = won + lost
    return round(won / total * 100, 1) if total else None


def win_rate(db: Session, actor: User, period: Period) -> WinRateReport:
    """期间赢单率：won / (won + lost)，按机会数，不按金额。

    赢单按 `won_at` 归属；输单没有 `lost_at` 列，按最后一条 `to_stage='lost'`
    的 `changed_at` 归属。重开过的机会当前不是终态，自然不计入。
    """
    latest = latest_stage_subquery()
    won = func.count().filter(
        Opportunity.stage == "won",
        Opportunity.won_at >= period.start,
        Opportunity.won_at < period.end,
    ).label("won")
    lost = func.count().filter(
        Opportunity.stage == "lost",
        latest.c.to_stage == "lost",
        latest.c.changed_at >= period.start,
        latest.c.changed_at < period.end,
    ).label("lost")
    rows = db.execute(
        select(User.department_id, Department.name, won, lost)
        .select_from(Opportunity)
        .join(User, User.id == Opportunity.owner_id)
        .outerjoin(Department, Department.id == User.department_id)
        .outerjoin(
            latest,
            and_(latest.c.opportunity_id == Opportunity.id, latest.c.rn == 1),
        )
        .where(
            *opportunity_scope_conditions(actor),
            Opportunity.stage.in_(("won", "lost")),
        )
        .group_by(User.department_id, Department.name)
    ).all()
    by_department = [
        DepartmentRateRead(
            department_id=row.department_id,
            department_name=row.name or "未分配",
            won=int(row.won),
            lost=int(row.lost),
            total=int(row.won) + int(row.lost),
            rate=_rate(int(row.won), int(row.lost)),
        )
        for row in rows
        if int(row.won) + int(row.lost) > 0
    ]
    by_department.sort(key=lambda row: (row.department_id is None, row.department_name))
    total_won = sum(row.won for row in by_department)
    total_lost = sum(row.lost for row in by_department)
    return WinRateReport(
        date_from=period.date_from,
        date_to=period.date_to,
        won=total_won,
        lost=total_lost,
        total=total_won + total_lost,
        rate=_rate(total_won, total_lost),
        by_department=by_department,
    )
