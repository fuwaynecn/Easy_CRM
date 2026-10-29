"""机会服务层：读取、写入、阶段变更。

作用域规则不在这里另写一份——机会完全继承客户。因此本文件第一条测试就是
「销售A 看不到销售B 客户下的机会」，并且刻意在同一个部门里放两个销售：
只有一个销售时"本部门"与"自己名下"恰好重合，把规则写错也能全绿。

``Customer`` 与 ``StageChange`` 本任务还用不到，那是 Task 6（阶段变更）的测试要用的
名字；这里一次性按最终形态摆好 import 区，不为一个名字再来改一趟。它们带
``noqa: F401`` 是 ruff 对未使用 import 的硬性要求。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.modules.customers.models import Customer  # noqa: F401
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.lost_reasons.schemas import LostReasonCreate
from app.modules.lost_reasons.service import create_lost_reason
from app.modules.opportunities.models import Opportunity, StageHistory
from app.modules.opportunities.schemas import (
    OpportunityCreate,
    OpportunityUpdate,
    StageChange,  # noqa: F401
)
from app.modules.opportunities.service import (
    board,
    create_opportunity,
    delete_opportunity,
    get_opportunity,
    list_opportunities,
    sync_opportunity_owner,
    update_opportunity,
)
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"


def _setup(db):
    """华东：主管 boss、销售 sa、同部门销售 sc；华南：销售 sb；外加管理员与公海客户。"""
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
    sc = create_user(
        db, username="sales_c", password=PASSWORD, display_name="销售C",
        role=ROLE_SALES, department_id=east.id,
    )
    sb = create_user(
        db, username="sales_b", password=PASSWORD, display_name="销售B",
        role=ROLE_SALES, department_id=south.id,
    )
    return {
        "admin": admin,
        "boss": boss,
        "sa": sa,
        "sc": sc,
        "sb": sb,
        "east": east,
        "south": south,
        "cust_a": create_customer(db, sa, CustomerCreate(name="甲制造有限公司")),
        "cust_c": create_customer(db, sc, CustomerCreate(name="丙制造有限公司")),
        "cust_b": create_customer(db, sb, CustomerCreate(name="乙制造有限公司")),
        "pool": create_customer(db, admin, CustomerCreate(name="公海公司")),
    }


def _make(db, actor, customer, name="喷涂产线一期", **overrides) -> Opportunity:
    """经 create_opportunity 造机会：连同"负责人继承 + 首条 history"一起被测。"""
    payload = {"customer_id": customer.id, "name": name, **overrides}
    return create_opportunity(db, actor, OpportunityCreate(**payload))


# --- 读取：作用域 --------------------------------------------------------


def test_sales_sees_only_own_customers_opportunities(db):
    f = _setup(db)
    mine = _make(db, f["sa"], f["cust_a"])
    colleagues = _make(db, f["sc"], f["cust_c"], name="丙公司机会")
    foreign = _make(db, f["sb"], f["cust_b"], name="乙公司机会")

    items, total = list_opportunities(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [mine.id]
    assert colleagues.id not in {item.id for item in items}
    assert foreign.id not in {item.id for item in items}


def test_manager_sees_department_scope_and_pool_opportunities(db):
    f = _setup(db)
    mine = _make(db, f["sa"], f["cust_a"])
    colleague = _make(db, f["sc"], f["cust_c"], name="丙公司机会")
    foreign = _make(db, f["sb"], f["cust_b"], name="乙公司机会")

    items, _ = list_opportunities(db, f["boss"])

    ids = {item.id for item in items}
    assert {mine.id, colleague.id} <= ids
    assert foreign.id not in ids


def test_admin_sees_all_opportunities(db):
    f = _setup(db)
    mine = _make(db, f["sa"], f["cust_a"])
    foreign = _make(db, f["sb"], f["cust_b"], name="乙公司机会")

    items, total = list_opportunities(db, f["admin"])

    assert total == 2
    assert {item.id for item in items} == {mine.id, foreign.id}


def test_get_opportunity_foreign_is_403_and_missing_is_404(db):
    f = _setup(db)
    foreign = _make(db, f["sb"], f["cust_b"], name="乙公司机会")

    with pytest.raises(ForbiddenError):
        get_opportunity(db, f["sa"], foreign.id)

    with pytest.raises(NotFoundError):
        get_opportunity(db, f["sa"], 999999)


def test_deleted_opportunity_is_invisible(db):
    f = _setup(db)
    opportunity = _make(db, f["sa"], f["cust_a"])
    opportunity.deleted_at = datetime.now(UTC)
    db.commit()

    with pytest.raises(NotFoundError):
        get_opportunity(db, f["sa"], opportunity.id)


# --- 读取：筛选、排序、分页 ----------------------------------------------


def test_keyword_matches_name_competitor_and_customer_name(db):
    f = _setup(db)
    by_name = _make(db, f["sa"], f["cust_a"], name="机器人焊接线")
    by_customer = _make(db, f["sa"], f["cust_a"], name="涂装线改造")
    by_competitor = _make(db, f["sa"], f["cust_a"], name="整体方案", competitor="XX 自动化")

    assert [o.id for o in list_opportunities(db, f["sa"], keyword="焊接")[0]] == [by_name.id]
    # 这里断言"命中集合"而不是顺序：默认排序是 ``-created_at``（新建的排前面），
    # 把插入顺序写死会让本测试在 Task 5 把 `_make` 换成 `create_opportunity`
    # （同一事务内批量插入 → created_at 相同）后莫名其妙地变红。
    assert {o.id for o in list_opportunities(db, f["sa"], keyword="甲制造")[0]} == {
        by_name.id,
        by_customer.id,
        by_competitor.id,
    }
    assert [o.id for o in list_opportunities(db, f["sa"], keyword="XX 自动化")[0]] == [
        by_competitor.id
    ]


def test_keyword_wildcards_are_literal(db):
    f = _setup(db)
    _make(db, f["sa"], f["cust_a"], name="100% 验收")
    _make(db, f["sa"], f["cust_a"], name="普通机会")

    items, _ = list_opportunities(db, f["sa"], keyword="%")

    assert [o.name for o in items] == ["100% 验收"]


def test_stage_filter_and_invalid_value(db):
    f = _setup(db)
    _make(db, f["sa"], f["cust_a"], name="早期")
    proposal = _make(db, f["sa"], f["cust_a"], name="报价中", stage="proposal")

    items, _ = list_opportunities(db, f["sa"], stage="proposal")
    assert [o.id for o in items] == [proposal.id]

    with pytest.raises(InvalidValueError):
        list_opportunities(db, f["sa"], stage="prospecting")


def test_customer_filter_foreign_customer_is_403(db):
    f = _setup(db)
    _make(db, f["sb"], f["cust_b"], name="乙公司机会")

    with pytest.raises(ForbiddenError):
        list_opportunities(db, f["sa"], customer_id=f["cust_b"].id)


def test_date_filter_uses_expected_close_date(db):
    f = _setup(db)
    early = _make(
        db, f["sa"], f["cust_a"], name="早", expected_close_date=date(2026, 11, 1)
    )
    late = _make(
        db, f["sa"], f["cust_a"], name="晚", expected_close_date=date(2026, 12, 31)
    )
    none = _make(db, f["sa"], f["cust_a"], name="未定")

    items, _ = list_opportunities(
        db, f["sa"], date_from=date(2026, 12, 1), date_to=date(2026, 12, 31)
    )
    assert [o.id for o in items] == [late.id]

    # 没填预计成交日的机会不属于任何"成交日期区间"
    assert none.id not in {o.id for o in items}

    with pytest.raises(InvalidValueError):
        list_opportunities(
            db, f["sa"], date_from=date(2026, 12, 31), date_to=date(2026, 11, 1)
        )
    assert early.id != late.id


def test_sort_whitelist_and_pagination(db):
    f = _setup(db)
    first = _make(db, f["sa"], f["cust_a"], name="一", amount=Decimal(100))
    second = _make(db, f["sa"], f["cust_a"], name="二", amount=Decimal(300))
    third = _make(db, f["sa"], f["cust_a"], name="三", amount=Decimal(200))

    items, total = list_opportunities(db, f["sa"], sort="-amount", page=1, page_size=2)
    assert total == 3
    assert [o.id for o in items] == [second.id, third.id]

    items, _ = list_opportunities(db, f["sa"], sort="-amount", page=2, page_size=2)
    assert [o.id for o in items] == [first.id]

    with pytest.raises(InvalidValueError):
        list_opportunities(db, f["sa"], sort="customer_id")


# --- 看板 ----------------------------------------------------------------


def test_board_returns_all_six_columns_with_totals(db):
    f = _setup(db)
    _make(db, f["sa"], f["cust_a"], name="接触中", amount=Decimal(100))
    _make(db, f["sa"], f["cust_a"], name="报价中", stage="proposal", amount=Decimal(900))

    columns = board(db, f["sa"])

    assert [column.stage for column in columns] == [
        "contact",
        "needs",
        "proposal",
        "negotiation",
        "won",
        "lost",
    ]
    by_stage = {column.stage: column for column in columns}
    assert by_stage["contact"].total == 1
    assert by_stage["contact"].amount_total == Decimal("100.00")
    assert [item.name for item in by_stage["proposal"].items] == ["报价中"]
    assert by_stage["needs"].total == 0
    assert by_stage["needs"].items == []
    assert by_stage["needs"].amount_total == Decimal(0)


def test_board_orders_by_expected_close_date_nulls_last(db):
    f = _setup(db)
    no_date = _make(db, f["sa"], f["cust_a"], name="未定")
    late = _make(db, f["sa"], f["cust_a"], name="晚", expected_close_date=date(2026, 12, 1))
    early = _make(db, f["sa"], f["cust_a"], name="早", expected_close_date=date(2026, 11, 1))

    column = {c.stage: c for c in board(db, f["sa"])}["contact"]

    assert [item.id for item in column.items] == [early.id, late.id, no_date.id]


def test_board_is_scoped_like_the_list(db):
    f = _setup(db)
    _make(db, f["sb"], f["cust_b"], name="乙公司机会")

    columns = board(db, f["sa"])

    assert sum(column.total for column in columns) == 0


# --- 写入：创建 ----------------------------------------------------------


def _history_rows(db, opportunity_id: int):
    return list(
        db.execute(
            select(StageHistory)
            .where(StageHistory.opportunity_id == opportunity_id)
            .order_by(StageHistory.id)
        )
        .scalars()
        .all()
    )


def test_create_inherits_owner_from_customer_and_writes_history(db):
    f = _setup(db)

    opportunity = _make(db, f["boss"], f["cust_a"], amount=Decimal(5000))

    assert opportunity.owner_id == f["sa"].id            # 客户负责人是 sales_a
    assert opportunity.stage == "contact"
    rows = _history_rows(db, opportunity.id)
    assert len(rows) == 1
    assert rows[0].from_stage is None
    assert rows[0].to_stage == "contact"
    assert str(rows[0].amount_snapshot) == "5000.00"
    assert rows[0].operator_id == f["boss"].id


def test_create_on_pool_customer_is_invalid(db):
    """公海客户没有负责人，而机会 owner_id 是 NOT NULL（§3.5 负责人必须与客户一致）。"""
    f = _setup(db)

    with pytest.raises(InvalidValueError) as excinfo:
        _make(db, f["admin"], f["pool"])

    assert excinfo.value.status_code == 400


def test_create_rejects_terminal_stage(db):
    f = _setup(db)

    for stage in ("won", "lost"):
        with pytest.raises(InvalidValueError):
            _make(db, f["sa"], f["cust_a"], stage=stage)


def test_create_rejects_unknown_stage_and_blank_name(db):
    f = _setup(db)

    with pytest.raises(InvalidValueError):
        _make(db, f["sa"], f["cust_a"], stage="prospecting")

    with pytest.raises(InvalidValueError):
        _make(db, f["sa"], f["cust_a"], name="   ")


def test_create_on_foreign_customer_is_403(db):
    f = _setup(db)

    with pytest.raises(ForbiddenError):
        _make(db, f["sa"], f["cust_b"])


def test_create_keeps_manual_win_rate(db):
    f = _setup(db)

    opportunity = _make(db, f["sa"], f["cust_a"], win_rate=85)

    assert opportunity.win_rate == 85


def test_create_normalizes_empty_competitor(db):
    f = _setup(db)

    opportunity = _make(db, f["sa"], f["cust_a"], competitor="   ")

    assert opportunity.competitor is None


# --- 写入：编辑 ----------------------------------------------------------


def test_update_changes_only_submitted_fields(db):
    f = _setup(db)
    opportunity = _make(db, f["sa"], f["cust_a"], amount=Decimal(100))

    updated = update_opportunity(
        db,
        f["sa"],
        opportunity.id,
        OpportunityUpdate(name="  改名后的机会  ", amount=Decimal(900)),
    )

    assert updated.name == "改名后的机会"
    assert str(updated.amount) == "900.00"
    assert updated.stage == "contact"          # 阶段只能走 /stage
    assert updated.won_at is None


def test_update_can_clear_win_rate_back_to_auto(db):
    f = _setup(db)
    opportunity = _make(db, f["sa"], f["cust_a"], win_rate=85)

    updated = update_opportunity(
        db, f["sa"], opportunity.id, OpportunityUpdate(win_rate=None)
    )

    assert updated.win_rate is None            # NULL = 跟随阶段


def test_update_rejects_null_required_fields(db):
    f = _setup(db)
    opportunity = _make(db, f["sa"], f["cust_a"])

    with pytest.raises(InvalidValueError):
        update_opportunity(db, f["sa"], opportunity.id, OpportunityUpdate(name=None))

    with pytest.raises(InvalidValueError):
        update_opportunity(db, f["sa"], opportunity.id, OpportunityUpdate(amount=None))


def test_update_foreign_opportunity_is_403(db):
    f = _setup(db)
    opportunity = _make(db, f["sb"], f["cust_b"], name="乙公司机会")

    with pytest.raises(ForbiddenError):
        update_opportunity(
            db, f["sa"], opportunity.id, OpportunityUpdate(name="改别人的")
        )


# --- 写入：删除 ----------------------------------------------------------


def test_sales_cannot_delete_opportunity(db):
    f = _setup(db)
    opportunity = _make(db, f["sa"], f["cust_a"])

    with pytest.raises(ForbiddenError):
        delete_opportunity(db, f["sa"], opportunity.id)


def test_manager_soft_deletes_and_second_delete_is_404(db):
    f = _setup(db)
    opportunity = _make(db, f["sa"], f["cust_a"])

    delete_opportunity(db, f["boss"], opportunity.id)

    db.expire_all()
    row = db.get(Opportunity, opportunity.id)
    assert row.deleted_at is not None
    assert _history_rows(db, opportunity.id) != []      # 历史不跟着删

    with pytest.raises(NotFoundError):
        delete_opportunity(db, f["boss"], opportunity.id)


# --- 负责人同步 seam（P4 的领取/分配/转移必须调用它） ---------------------


def test_sync_owner_moves_only_open_opportunities(db):
    f = _setup(db)
    open_opp = _make(db, f["sa"], f["cust_a"], name="在谈")
    won = _make(db, f["sa"], f["cust_a"], name="已赢")
    lost = _make(db, f["sa"], f["cust_a"], name="已输")
    # 终态只能由阶段变更产生（Task 6 才实现），这里直接改 ORM 造夹具状态：
    won.stage = "won"
    won.won_at = datetime.now(UTC)
    reason = create_lost_reason(db, LostReasonCreate(name="价格过高"))
    lost.stage = "lost"
    lost.lost_reason_id = reason.id
    db.commit()

    affected = sync_opportunity_owner(db, customer_id=f["cust_a"].id, owner_id=f["sc"].id)
    db.commit()

    db.expire_all()
    assert affected == 1
    assert db.get(Opportunity, open_opp.id).owner_id == f["sc"].id
    # 已赢/已输保留原负责人：三年前的成单不能算到新人头上（§3.5）
    assert db.get(Opportunity, won.id).owner_id == f["sa"].id
    assert db.get(Opportunity, lost.id).owner_id == f["sa"].id


def test_sync_owner_is_noop_when_already_synced(db):
    f = _setup(db)
    _make(db, f["sa"], f["cust_a"])

    assert sync_opportunity_owner(
        db, customer_id=f["cust_a"].id, owner_id=f["sa"].id
    ) == 0


def test_sync_owner_skips_deleted_opportunities(db):
    f = _setup(db)
    opportunity = _make(db, f["sa"], f["cust_a"])
    opportunity.deleted_at = datetime.now(UTC)
    db.commit()

    assert sync_opportunity_owner(
        db, customer_id=f["cust_a"].id, owner_id=f["sc"].id
    ) == 0
