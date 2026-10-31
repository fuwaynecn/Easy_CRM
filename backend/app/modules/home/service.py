"""首页三块的查询。

今日/逾期默认是「我的」范围，P4 起 admin 是「全公司」：
- 今日待办 / 已逾期：可见客户再收窄到 `customers.owner_id = me`（裁定 3），admin 例外；
- 即将进公海：可见客户里处于预警区间的（主管能看到本部门，销售能看到自己的）。

预警**不落表**（spec §4.2）：直接查询是幂等的，落表就要维护"客户被跟进后预警消失"
的状态同步，必然产生脏数据。

「今天/逾期」一律走 `today_bounds`（上海自然日）：自己拼
`now.replace(hour=0, ...)` 等于按 UTC 划天，凌晨 0–8 点的待办会整批错位一天。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

# 明确 re-export：home/router.py 与既有测试仍从 home.service 取 days_since_local，
# 所以保留模块级导入（真身已移到 app/core/timeutil.py，P4 公海列表也复用它）。
from app.core.timeutil import (
    days_since_local,  # noqa: F401
    today_bounds,
)
from app.db.scope import customer_query
from app.modules.customers.models import Customer
from app.modules.followups.models import FollowUp
from app.modules.pools.predicates import (
    open_deal_exists,
    pending_followup_exists,
)
from app.modules.pools.rules import PoolRules, load_pool_rules
from app.modules.users.models import ROLE_ADMIN, User

# 每块最多返回多少条：`total` 是全量计数（页面显示真实条数），items 只给前 N 条
HOME_BLOCK_LIMIT = 50


@dataclass(frozen=True)
class HomeSummary:
    today: list[FollowUp]
    today_total: int
    overdue: list[FollowUp]
    overdue_total: int
    pool_notice: list[Customer]
    pool_notice_total: int


def _home_customers(actor: User):
    """首页待办的客户范围：admin 全公司；manager/sales 只看自己名下。

    - admin 的首页是“全公司待办”视角（P4 裁定 12），但公海提醒仍是 owner 非空；
    - manager 不看本部门全部，只看自己名下：待办跟客户负责人走（spec §3.9）。
    """
    stmt = customer_query(actor).with_only_columns(Customer.id)
    if actor.role != ROLE_ADMIN:
        stmt = stmt.where(Customer.owner_id == actor.id)
    return stmt


def _follow_up_block(
    db: Session, actor: User, *, start: datetime, end: datetime, overdue: bool
) -> tuple[list[FollowUp], int]:
    stmt = select(FollowUp).where(
        FollowUp.deleted_at.is_(None),
        FollowUp.status == "pending",
        FollowUp.customer_id.in_(_home_customers(actor)),
    )
    if overdue:
        stmt = stmt.where(FollowUp.due_at < start)
    else:
        stmt = stmt.where(FollowUp.due_at >= start, FollowUp.due_at < end)

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    items = list(
        db.execute(
            stmt.options(
                joinedload(FollowUp.customer), joinedload(FollowUp.operator)
            )
            .order_by(FollowUp.due_at.asc(), FollowUp.id.asc())
            .limit(HOME_BLOCK_LIMIT)
        )
        .scalars()
        .all()
    )
    return items, int(total)


def _pool_notice(
    db: Session, actor: User, *, now: datetime, rules: PoolRules
) -> tuple[list[Customer], int]:
    threshold = now - timedelta(days=rules.notice_threshold_days)
    # 判定与展示是两把尺子，别互相反推：
    # - **判定**按瞬时阈值 `last_activity_at < now - threshold×24h`（spec §4.2 原文的
    #   `now() - (...)`，是时间戳比较）；
    # - **展示**的 `days_inactive` 按上海自然日（`days_since_local`），前端只负责显示。
    # 因此边界上会出现"显示 N 天但还没进预警"（差几分钟到 N×24 小时）。P4 做回收
    # 任务时也必须用瞬时阈值，不能拿展示天数反推。
    #
    # 两条豁免（spec §4.1/§4.2）：有未关闭机会、有 pending 待办。判定复用
    # `pools.predicates`，保证首页提示与实际回收永远是同一份条件。
    conditions = [
        Customer.owner_id.is_not(None),
        Customer.status == "active",
        Customer.last_activity_at < threshold,
    ]
    if rules.protect_with_open_deal:
        conditions.append(~open_deal_exists())
    if rules.protect_with_pending_followup:
        conditions.append(~pending_followup_exists())

    stmt = customer_query(actor).where(*conditions)

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    items = list(
        db.execute(
            stmt.options(joinedload(Customer.owner))
            .order_by(Customer.last_activity_at.asc(), Customer.id.asc())
            .limit(HOME_BLOCK_LIMIT)
        )
        .scalars()
        .all()
    )
    return items, int(total)


def summary(
    db: Session,
    actor: User,
    *,
    now: datetime | None = None,
    rules: PoolRules | None = None,
) -> HomeSummary:
    """一次算三块。`now` 只给测试注入，生产走 `datetime.now(UTC)`。

    `rules` 由调用方传入（路由已读库）；不传时现读一次，避免测试与其它调用方
    被迫先构造规则。
    """
    current = now or datetime.now(UTC)
    effective = rules or load_pool_rules(db)
    start, end = today_bounds(current)

    today_items, today_total = _follow_up_block(
        db, actor, start=start, end=end, overdue=False
    )
    overdue_items, overdue_total = _follow_up_block(
        db, actor, start=start, end=end, overdue=True
    )
    pool_items, pool_total = _pool_notice(
        db, actor, now=current, rules=effective
    )

    return HomeSummary(
        today=today_items,
        today_total=today_total,
        overdue=overdue_items,
        overdue_total=overdue_total,
        pool_notice=pool_items,
        pool_notice_total=pool_total,
    )
