"""报表查询。

全部函数都是只读的，不 commit、不写库。金额一律取 `stage_histories.amount_snapshot`；
只有历史脏数据没有 history 时才回落到机会现值。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.modules.opportunities.models import OPPORTUNITY_STAGES, Opportunity
from app.modules.reports.common import (
    latest_stage_subquery,
    opportunity_scope_conditions,
)
from app.modules.reports.schemas import FunnelReport, FunnelStageRead
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
