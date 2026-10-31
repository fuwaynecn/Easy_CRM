"""报表公共口径：区间、作用域、最新阶段快照、时间桶。

报表不读 `opportunities.amount` 现值：P3 已裁定报表沿用 `stage_histories.amount_snapshot`
（`docs/superpowers/plans/2026-10-31-easy-crm-p3.md` 第 89 行）。只改金额、不换阶段不会
产生 history，所以报表金额会滞后于机会现值——这是刻意的稳定口径，不是 bug。

作用域**不套** `customer_query`：公海客户对所有主管可见，按客户作用域统计会让同一条
公海机会在每个部门各算一次。这里按机会负责人当前部门归属（P5 裁定 7/8）。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import ColumnElement, func, select

from app.core.errors import InvalidValueError
from app.core.timeutil import SHANGHAI, day_start
from app.modules.activities.models import Activity
from app.modules.customers.models import Customer
from app.modules.opportunities.models import Opportunity, StageHistory
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, User


@dataclass(frozen=True)
class Period:
    date_from: date
    date_to: date

    @property
    def start(self) -> datetime:
        """区间起点：上海时区 `date_from` 00:00（带偏移）。"""
        return day_start(self.date_from)

    @property
    def end(self) -> datetime:
        """区间终点：上海时区 `date_to` 次日 00:00，半开区间。"""
        return day_start(self.date_to + timedelta(days=1))


def resolve_period(
    date_from: date | None,
    date_to: date | None,
    *,
    now: datetime | None = None,
) -> Period:
    """解析报表区间；都不传时默认「上海时区本月 1 日到今天」。"""
    if (date_from is None) != (date_to is None):
        raise InvalidValueError("开始日期与结束日期必须同时提供")
    if date_from is None:
        today = (now or datetime.now(UTC)).astimezone(SHANGHAI).date()
        return Period(date_from=today.replace(day=1), date_to=today)
    if date_from > date_to:
        raise InvalidValueError("开始日期不能晚于结束日期")
    return Period(date_from=date_from, date_to=date_to)


def opportunity_scope_conditions(actor: User) -> list[ColumnElement[bool]]:
    """机会报表的作用域：admin 全部、manager 本部门、sales 本人。

    部门取负责人**当前** `users.department_id`；客户已软删的机会不计入。
    未知角色按本人处理（fail-closed）。
    """
    conditions: list[ColumnElement[bool]] = [
        Opportunity.deleted_at.is_(None),
        Opportunity.customer_id.in_(
            select(Customer.id).where(Customer.deleted_at.is_(None))
        ),
    ]
    if actor.role == ROLE_ADMIN:
        return conditions
    if actor.role == ROLE_MANAGER and actor.department_id is not None:
        conditions.append(
            Opportunity.owner_id.in_(
                select(User.id).where(User.department_id == actor.department_id)
            )
        )
    else:
        conditions.append(Opportunity.owner_id == actor.id)
    return conditions


def activity_scope_conditions(actor: User) -> list[ColumnElement[bool]]:
    """跟进量报表的作用域：按跟进作者当前部门，不按客户归属。

    跟进记录一旦写下，作者就是作者；客户后来转给别人，历史跟进量仍算在原作者部门。
    """
    conditions: list[ColumnElement[bool]] = [Activity.deleted_at.is_(None)]
    if actor.role == ROLE_ADMIN:
        return conditions
    if actor.role == ROLE_MANAGER and actor.department_id is not None:
        conditions.append(
            Activity.created_by.in_(
                select(User.id).where(User.department_id == actor.department_id)
            )
        )
    else:
        conditions.append(Activity.created_by == actor.id)
    return conditions


def latest_stage_subquery(*, to_stage: str | None = None):
    """每个机会最后一条阶段历史（可按目标阶段过滤）。

    过滤必须放在窗口函数**之前**：`to_stage="won"` 要取「最后一条赢单历史」，
    而不是「最后一条历史恰好是赢单」——后者在重开后会把赢单快照丢掉。

    「最后一条」按插入顺序（`id`）取：`stage_histories` 只追加、不修改，
    `changed_at` 是 `server_default=now()` 落库的写入时间，生产上只有 `change_stage`
    插入、没有任何回填 `changed_at` 的写入方，所以写入顺序就是业务顺序。
    `changed_at` 只用来做**区间归属**（这条历史算在哪个报表区间），**绝不能**用来挑
    最新一行：它一旦被改成过去时点（测试夹具、将来的数据迁移），按它排序就会让真正
    最新的阶段被更早的流水盖掉。

    P5 全任务口径（Tasks 2–7 同此）：`changed_at` 只作范围过滤条件，不参与排序。
    """
    stmt = select(
        StageHistory.opportunity_id,
        StageHistory.to_stage,
        StageHistory.amount_snapshot,
        StageHistory.changed_at,
        func.row_number()
        .over(
            partition_by=StageHistory.opportunity_id,
            order_by=StageHistory.id.desc(),
        )
        .label("rn"),
    )
    if to_stage is not None:
        stmt = stmt.where(StageHistory.to_stage == to_stage)
    return stmt.subquery()


def month_bucket(column):
    """按上海时区取 `YYYY-MM`；不要用 UTC 月份，月初/月末会错位。"""
    return func.to_char(func.timezone(SHANGHAI.key, column), "YYYY-MM")


def day_bucket(column):
    return func.to_char(func.timezone(SHANGHAI.key, column), "YYYY-MM-DD")


def week_bucket(column):
    """ISO 周桶，返回该周周一的 `YYYY-MM-DD`。"""
    return func.to_char(
        func.date_trunc("week", func.timezone(SHANGHAI.key, column)),
        "YYYY-MM-DD",
    )


def fill_buckets(
    buckets: dict[str, int], period: Period, granularity: str
) -> list[tuple[str, int]]:
    """把 SQL 只返回非空桶的结果补齐成连续序列，前端不用自己补 0。"""
    if granularity == "day":
        cursor = period.date_from
        step = timedelta(days=1)
    else:
        cursor = period.date_from - timedelta(days=period.date_from.weekday())
        step = timedelta(days=7)
    result: list[tuple[str, int]] = []
    while cursor <= period.date_to:
        key = cursor.isoformat()
        result.append((key, buckets.get(key, 0)))
        cursor += step
    return result
