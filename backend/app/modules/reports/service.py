"""报表查询。

全部函数都是只读的，不 commit、不写库。金额一律取 `stage_histories.amount_snapshot`；
只有历史脏数据没有 history 时才回落到机会现值。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.errors import InvalidValueError
from app.modules.activities.models import Activity
from app.modules.departments.models import Department
from app.modules.lost_reasons.models import LostReason
from app.modules.opportunities.models import OPPORTUNITY_STAGES, Opportunity
from app.modules.reports.common import (
    Period,
    activity_scope_conditions,
    day_bucket,
    fill_buckets,
    latest_stage_subquery,
    month_bucket,
    opportunity_scope_conditions,
    week_bucket,
)
from app.modules.reports.schemas import (
    ActivityBucketRead,
    ActivityOwnerRead,
    ActivityReport,
    CycleMonthRead,
    CycleOwnerRead,
    CycleReport,
    DepartmentRateRead,
    FunnelReport,
    FunnelStageRead,
    LostReasonRead,
    LostReasonReport,
    PerformanceMonthRead,
    PerformanceOwnerRead,
    PerformanceReport,
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


def lost_reasons(db: Session, actor: User, period: Period) -> LostReasonReport:
    """输单原因：期间内按原因分组的机会数与金额。

    输单时间没有独立列，取最后一条 `to_stage='lost'` 的 `changed_at`；
    金额取同一条历史的快照。
    """
    lost_hist = latest_stage_subquery(to_stage="lost")
    amount = func.coalesce(lost_hist.c.amount_snapshot, Opportunity.amount)
    rows = db.execute(
        select(
            LostReason.id,
            LostReason.name,
            func.count().label("count"),
            func.coalesce(func.sum(amount), 0).label("amount"),
        )
        .select_from(Opportunity)
        .join(
            lost_hist,
            and_(lost_hist.c.opportunity_id == Opportunity.id, lost_hist.c.rn == 1),
        )
        .join(LostReason, LostReason.id == Opportunity.lost_reason_id)
        .where(
            *opportunity_scope_conditions(actor),
            Opportunity.stage == "lost",
            lost_hist.c.changed_at >= period.start,
            lost_hist.c.changed_at < period.end,
        )
        .group_by(LostReason.id, LostReason.name)
        .order_by(func.count().desc(), func.sum(amount).desc(), LostReason.id.asc())
    ).all()
    items = [
        LostReasonRead(
            lost_reason_id=row.id,
            lost_reason_name=row.name,
            count=int(row.count),
            amount=Decimal(row.amount),
        )
        for row in rows
    ]
    return LostReasonReport(
        date_from=period.date_from,
        date_to=period.date_to,
        total_count=sum(item.count for item in items),
        total_amount=sum((item.amount for item in items), Decimal(0)),
        items=items,
    )


def _avg_days(value) -> float | None:
    return round(float(value), 1) if value is not None else None


def cycle(db: Session, actor: User, period: Period) -> CycleReport:
    """平均成交周期：当前赢单机会的 `won_at - created_at` 天数均值。

    期间按 `won_at` 归属；重开过的机会 `won_at` 被清空，不再计入。
    """
    days = func.extract(
        "epoch", Opportunity.won_at - Opportunity.created_at
    ) / 86400.0
    base = [
        *opportunity_scope_conditions(actor),
        Opportunity.stage == "won",
        Opportunity.won_at >= period.start,
        Opportunity.won_at < period.end,
    ]
    won_count, avg_days = db.execute(
        select(func.count(), func.avg(days)).where(*base)
    ).one()
    month = month_bucket(Opportunity.won_at)
    owner_rows = db.execute(
        select(User.id, User.display_name, func.count(), func.avg(days))
        .join(User, User.id == Opportunity.owner_id)
        .where(*base)
        .group_by(User.id, User.display_name)
        .order_by(User.display_name.asc(), User.id.asc())
    ).all()
    month_rows = db.execute(
        select(month.label("month"), func.count(), func.avg(days))
        .where(*base)
        .group_by(month)
        .order_by(month.asc())
    ).all()
    return CycleReport(
        date_from=period.date_from,
        date_to=period.date_to,
        won_count=int(won_count),
        avg_days=_avg_days(avg_days),
        by_owner=[
            CycleOwnerRead(
                owner_id=row.id,
                owner_name=row.display_name,
                won_count=int(row[2]),
                avg_days=_avg_days(row[3]),
            )
            for row in owner_rows
        ],
        by_month=[
            CycleMonthRead(
                month=row.month,
                won_count=int(row[1]),
                avg_days=_avg_days(row[2]),
            )
            for row in month_rows
        ],
    )


def performance(db: Session, actor: User, period: Period) -> PerformanceReport:
    """期间赢单金额：按机会负责人与月份分组。

    金额取最后一条 `to_stage='won'` 的 `amount_snapshot`；没有赢单历史的脏数据
    才回落到机会现值。
    """
    won_hist = latest_stage_subquery(to_stage="won")
    amount = func.coalesce(won_hist.c.amount_snapshot, Opportunity.amount)
    base = [
        *opportunity_scope_conditions(actor),
        Opportunity.stage == "won",
        Opportunity.won_at >= period.start,
        Opportunity.won_at < period.end,
    ]
    owner_rows = db.execute(
        select(
            User.id,
            User.display_name,
            func.count().label("won_count"),
            func.coalesce(func.sum(amount), 0).label("amount"),
        )
        .select_from(Opportunity)
        .join(User, User.id == Opportunity.owner_id)
        .outerjoin(
            won_hist,
            and_(won_hist.c.opportunity_id == Opportunity.id, won_hist.c.rn == 1),
        )
        .where(*base)
        .group_by(User.id, User.display_name)
        .order_by(User.display_name.asc(), User.id.asc())
    ).all()
    month = month_bucket(Opportunity.won_at)
    month_rows = db.execute(
        select(
            month.label("month"),
            func.count().label("won_count"),
            func.coalesce(func.sum(amount), 0).label("amount"),
        )
        .select_from(Opportunity)
        .outerjoin(
            won_hist,
            and_(won_hist.c.opportunity_id == Opportunity.id, won_hist.c.rn == 1),
        )
        .where(*base)
        .group_by(month)
        .order_by(month.asc())
    ).all()
    by_owner = [
        PerformanceOwnerRead(
            owner_id=row.id,
            owner_name=row.display_name,
            won_count=int(row.won_count),
            amount=Decimal(row.amount),
        )
        for row in owner_rows
    ]
    by_month = [
        PerformanceMonthRead(
            month=row.month,
            won_count=int(row.won_count),
            amount=Decimal(row.amount),
        )
        for row in month_rows
    ]
    return PerformanceReport(
        date_from=period.date_from,
        date_to=period.date_to,
        won_count=sum(row.won_count for row in by_owner),
        total_amount=sum((row.amount for row in by_owner), Decimal(0)),
        by_owner=by_owner,
        by_month=by_month,
    )


ACTIVITY_GRANULARITIES = ("day", "week")


def activities(
    db: Session,
    actor: User,
    period: Period,
    granularity: str,
) -> ActivityReport:
    """期间跟进量：按作者分组，按天/周聚合，并补齐空桶。"""
    if granularity not in ACTIVITY_GRANULARITIES:
        raise InvalidValueError("granularity 只能是 day 或 week")
    base = [
        *activity_scope_conditions(actor),
        Activity.activity_at >= period.start,
        Activity.activity_at < period.end,
    ]
    owner_rows = db.execute(
        select(User.id, User.display_name, func.count().label("count"))
        .select_from(Activity)
        .join(User, User.id == Activity.created_by)
        .where(*base)
        .group_by(User.id, User.display_name)
        .order_by(func.count().desc(), User.id.asc())
    ).all()
    bucket = day_bucket if granularity == "day" else week_bucket
    bucket = bucket(Activity.activity_at)
    bucket_rows = db.execute(
        select(bucket.label("bucket"), func.count().label("count"))
        .where(*base)
        .group_by(bucket)
        .order_by(bucket.asc())
    ).all()
    series = fill_buckets(
        {row.bucket: int(row.count) for row in bucket_rows}, period, granularity
    )
    return ActivityReport(
        date_from=period.date_from,
        date_to=period.date_to,
        granularity=granularity,
        total=sum(count for _, count in series),
        by_owner=[
            ActivityOwnerRead(
                user_id=row.id,
                user_name=row.display_name,
                count=int(row.count),
            )
            for row in owner_rows
        ],
        series=[
            ActivityBucketRead(bucket=key, count=count) for key, count in series
        ],
    )
