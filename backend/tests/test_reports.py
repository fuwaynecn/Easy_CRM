"""报表口径。

本文件只钉“算错了会静默给出错误经营数字”的东西：
- 区间默认按上海时区本月，不是 UTC 本月；
- 漏斗金额读 `stage_histories.amount_snapshot`，不读机会现值；
- 报表作用域按机会负责人，不按客户可见性（公海机会不能在每个部门各算一次）。
"""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import update

from app.core.errors import InvalidValueError
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer, delete_customer
from app.modules.departments.service import create_department
from app.modules.opportunities.models import Opportunity, StageHistory
from app.modules.opportunities.schemas import (
    OpportunityCreate,
    OpportunityUpdate,
    StageChange,
)
from app.modules.opportunities.service import (
    change_stage,
    create_opportunity,
    delete_opportunity,
    update_opportunity,
)
from app.modules.reports.common import resolve_period
from app.modules.reports.service import funnel
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"
# 2026-10-31 15:00 UTC = 2026-10-31 23:00 Asia/Shanghai
NOW = datetime(2026, 10, 31, 15, 0, tzinfo=UTC)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _setup(db):
    east = create_department(db, "华东销售部")
    south = create_department(db, "华南销售部")
    admin = create_user(
        db, username="admin", password=PASSWORD, display_name="管理员",
        role=ROLE_ADMIN, department_id=None,
    )
    boss = create_user(
        db, username="boss", password=PASSWORD, display_name="东区主管",
        role=ROLE_MANAGER, department_id=east.id,
    )
    sa = create_user(
        db, username="sales_a", password=PASSWORD, display_name="销售A",
        role=ROLE_SALES, department_id=east.id,
    )
    sb = create_user(
        db, username="sales_b", password=PASSWORD, display_name="销售B",
        role=ROLE_SALES, department_id=south.id,
    )
    return {
        "admin": admin, "boss": boss, "sa": sa, "sb": sb,
        "cust_a": create_customer(db, sa, CustomerCreate(name="甲制造有限公司")),
        "cust_b": create_customer(db, sb, CustomerCreate(name="乙制造有限公司")),
    }


def _opp(db, actor, customer, *, name="一期", amount="1000.00", competitor=None):
    return create_opportunity(
        db,
        actor,
        OpportunityCreate(
            customer_id=customer.id,
            name=name,
            amount=Decimal(amount),
            competitor=competitor,
        ),
    )


def _win(db, actor, opp, *, at: datetime):
    change_stage(db, actor, opp.id, StageChange(stage="won"))
    db.execute(
        update(Opportunity).where(Opportunity.id == opp.id).values(won_at=at)
    )
    # 创建与赢单两条 history 一起回填：只回填赢单行会让创建行留在真实 now，
    # 夹具时序变成「先赢单、后创建」。Task 2 的 `_lose` 沿用同一做法。
    db.execute(
        update(StageHistory)
        .where(StageHistory.opportunity_id == opp.id)
        .values(changed_at=at)
    )
    db.commit()


def test_period_defaults_to_current_month_in_shanghai():
    period = resolve_period(None, None, now=NOW)

    assert period.date_from.isoformat() == "2026-10-01"
    assert period.date_to.isoformat() == "2026-10-31"
    assert period.start == datetime(2026, 10, 1, tzinfo=SHANGHAI)
    assert period.end == datetime(2026, 11, 1, tzinfo=SHANGHAI)


def test_period_rejects_single_bound():
    with pytest.raises(InvalidValueError, match="必须同时提供"):
        resolve_period(datetime(2026, 10, 1).date(), None)


def test_period_rejects_reversed_range():
    with pytest.raises(InvalidValueError, match="开始日期不能晚于结束日期"):
        resolve_period(
            datetime(2026, 10, 31).date(), datetime(2026, 10, 1).date()
        )


def test_funnel_uses_latest_snapshot_not_current_amount(db):
    f = _setup(db)
    opp = _opp(db, f["sa"], f["cust_a"], amount="1000.00")
    _win(db, f["sa"], opp, at=datetime(2026, 10, 15, tzinfo=UTC))

    # 赢单后改金额：不换阶段，不会产生新的 history
    update_opportunity(
        db, f["sa"], opp.id, OpportunityUpdate(amount=Decimal("9999.00"))
    )

    report = funnel(db, f["admin"])

    by_stage = {row.stage: row for row in report.stages}
    assert by_stage["won"].count == 1
    assert by_stage["won"].amount == Decimal("1000.00")
    assert by_stage["contact"].count == 0
    assert [row.stage for row in report.stages] == [
        "contact", "needs", "proposal", "negotiation", "won", "lost",
    ]


def test_funnel_excludes_deleted_customer_and_opportunity(db):
    f = _setup(db)
    opp = _opp(db, f["sa"], f["cust_a"])
    _opp(db, f["sa"], f["cust_a"], name="二期")
    delete_opportunity(db, f["boss"], opp.id)
    delete_customer(db, f["admin"], f["cust_a"].id)

    report = funnel(db, f["admin"])

    assert sum(row.count for row in report.stages) == 0


def test_funnel_scope_by_owner_department(db):
    f = _setup(db)
    _opp(db, f["sa"], f["cust_a"])
    _opp(db, f["sb"], f["cust_b"])

    east = funnel(db, f["boss"])
    south = funnel(db, f["sb"])
    admin = funnel(db, f["admin"])

    assert sum(row.count for row in east.stages) == 1
    assert sum(row.count for row in south.stages) == 1
    assert sum(row.count for row in admin.stages) == 2


from app.modules.lost_reasons.schemas import LostReasonCreate
from app.modules.lost_reasons.service import create_lost_reason
from app.modules.reports.service import win_rate


def _lose(db, actor, opp, *, at: datetime, reason_id: int):
    change_stage(
        db, actor, opp.id, StageChange(stage="lost", lost_reason_id=reason_id)
    )
    # 与 _win 同理：创建 history 也要回填，避免 fixture 变成“先输单、后创建”
    db.execute(
        update(StageHistory)
        .where(StageHistory.opportunity_id == opp.id)
        .values(changed_at=at)
    )
    db.commit()


def test_win_rate_uses_terminal_arrival_in_period(db):
    f = _setup(db)
    period = resolve_period(
        datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date(), now=NOW
    )
    reason = create_lost_reason(db, LostReasonCreate(name="价格高"))
    won_in = _opp(db, f["sa"], f["cust_a"], name="期内赢")
    lost_in = _opp(db, f["sa"], f["cust_a"], name="期内输")
    won_out = _opp(db, f["sa"], f["cust_a"], name="期外赢")
    # 输单机会创建在区间外：如果实现把“输单时间”错写成 created_at，这条会漏掉
    db.execute(
        update(Opportunity)
        .where(Opportunity.id == lost_in.id)
        .values(created_at=datetime(2026, 9, 1, tzinfo=UTC))
    )
    db.commit()

    _win(db, f["sa"], won_in, at=datetime(2026, 10, 15, tzinfo=UTC))
    _lose(
        db, f["sa"], lost_in,
        at=datetime(2026, 10, 20, tzinfo=UTC), reason_id=reason.id,
    )
    _win(db, f["sa"], won_out, at=datetime(2026, 9, 30, tzinfo=UTC))

    report = win_rate(db, f["admin"], period)

    assert (report.won, report.lost, report.total) == (1, 1, 2)
    assert report.rate == 50.0


def test_win_rate_excludes_reopened_opportunity(db):
    f = _setup(db)
    period = resolve_period(
        datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date(), now=NOW
    )
    opp = _opp(db, f["sa"], f["cust_a"])
    _win(db, f["sa"], opp, at=datetime(2026, 10, 15, tzinfo=UTC))

    # 重开会清空 won_at（P3 裁定），因此不再算作期内赢单
    change_stage(db, f["boss"], opp.id, StageChange(stage="needs", note="客户反悔"))

    report = win_rate(db, f["admin"], period)

    assert report.won == 0
    assert report.total == 0
    assert report.rate is None


def test_win_rate_groups_by_current_owner_department(db):
    f = _setup(db)
    period = resolve_period(
        datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date(), now=NOW
    )
    reason = create_lost_reason(db, LostReasonCreate(name="预算取消"))
    _win(db, f["sa"], _opp(db, f["sa"], f["cust_a"], name="华东赢"),
         at=datetime(2026, 10, 15, tzinfo=UTC))
    _lose(db, f["sb"], _opp(db, f["sb"], f["cust_b"], name="华南输"),
          at=datetime(2026, 10, 16, tzinfo=UTC), reason_id=reason.id)

    admin = win_rate(db, f["admin"], period)
    east = win_rate(db, f["boss"], period)
    south = win_rate(db, f["sb"], period)

    assert admin.total == 2
    assert {row.department_name for row in admin.by_department} == {
        "华东销售部", "华南销售部",
    }
    assert (east.won, east.lost, east.rate) == (1, 0, 100.0)
    assert (south.won, south.lost, south.rate) == (0, 1, 0.0)


from app.modules.reports.service import cycle


def _set_created_at(db, opp, moment: datetime):
    db.execute(
        update(Opportunity).where(Opportunity.id == opp.id).values(created_at=moment)
    )
    db.commit()


def test_cycle_uses_won_at_minus_created_at(db):
    f = _setup(db)
    period = resolve_period(
        datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date(), now=NOW
    )
    opp = _opp(db, f["sa"], f["cust_a"])
    _set_created_at(db, opp, datetime(2026, 10, 5, tzinfo=UTC))
    _win(db, f["sa"], opp, at=datetime(2026, 10, 15, tzinfo=UTC))

    report = cycle(db, f["admin"], period)

    assert report.won_count == 1
    assert report.avg_days == 10.0
    assert report.by_owner[0].owner_name == "销售A"
    assert report.by_month[0].month == "2026-10"


def test_cycle_month_bucket_uses_shanghai_timezone(db):
    f = _setup(db)
    period = resolve_period(
        datetime(2026, 10, 1).date(), datetime(2026, 11, 30).date(), now=NOW
    )
    # 2026-10-31 15:30 UTC = 2026-10-31 23:30 上海 → 10 月
    oct_opp = _opp(db, f["sa"], f["cust_a"], name="十月")
    _set_created_at(db, oct_opp, datetime(2026, 10, 20, tzinfo=UTC))
    _win(db, f["sa"], oct_opp, at=datetime(2026, 10, 31, 15, 30, tzinfo=UTC))
    # 2026-10-31 16:30 UTC = 2026-11-01 00:30 上海 → 11 月
    nov_opp = _opp(db, f["sa"], f["cust_a"], name="十一月")
    _set_created_at(db, nov_opp, datetime(2026, 10, 25, tzinfo=UTC))
    _win(db, f["sa"], nov_opp, at=datetime(2026, 10, 31, 16, 30, tzinfo=UTC))

    report = cycle(db, f["admin"], period)

    assert [row.month for row in report.by_month] == ["2026-10", "2026-11"]


from app.modules.reports.service import performance


def test_performance_uses_won_snapshot_amount(db):
    f = _setup(db)
    period = resolve_period(
        datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date(), now=NOW
    )
    opp = _opp(db, f["sa"], f["cust_a"], amount="1000.00")
    _win(db, f["sa"], opp, at=datetime(2026, 10, 15, tzinfo=UTC))
    # 赢单后改金额：报表仍按赢单时点的快照
    update_opportunity(
        db, f["sa"], opp.id, OpportunityUpdate(amount=Decimal("9999.00"))
    )

    report = performance(db, f["admin"], period)

    assert report.won_count == 1
    assert report.total_amount == Decimal("1000.00")
    assert report.by_owner[0].amount == Decimal("1000.00")
    assert report.by_month[0].month == "2026-10"


def test_performance_groups_by_owner_and_month(db):
    f = _setup(db)
    period = resolve_period(
        datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date(), now=NOW
    )
    _win(db, f["sa"], _opp(db, f["sa"], f["cust_a"], name="A1", amount="100.00"),
         at=datetime(2026, 10, 10, tzinfo=UTC))
    _win(db, f["sb"], _opp(db, f["sb"], f["cust_b"], name="B1", amount="200.00"),
         at=datetime(2026, 10, 11, tzinfo=UTC))

    admin = performance(db, f["admin"], period)
    east = performance(db, f["boss"], period)

    assert admin.total_amount == Decimal("300.00")
    assert {row.owner_name: row.amount for row in admin.by_owner} == {
        "销售A": Decimal("100.00"),
        "销售B": Decimal("200.00"),
    }
    assert east.total_amount == Decimal("100.00")
