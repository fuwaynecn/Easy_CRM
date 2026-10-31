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
