"""机会服务层：读取、写入、阶段变更。

作用域规则不在这里另写一份——机会完全继承客户。因此本文件第一条测试就是
「销售A 看不到销售B 客户下的机会」，并且刻意在同一个部门里放两个销售：
只有一个销售时"本部门"与"自己名下"恰好重合，把规则写错也能全绿。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.opportunities.models import Opportunity
from app.modules.opportunities.service import (
    board,
    get_opportunity,
    list_opportunities,
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
    """直接插一条机会行（**不走 service**）。

    本任务只测读取，创建语义（负责人继承、写第一条 history）属 Task 5；这里先用
    ORM 造出"库里已经有机会"的状态。Task 5 会把这个 helper 换成走
    ``create_opportunity``，那时它才同时验证创建规则。
    """
    opportunity = Opportunity(
        customer_id=customer.id,
        name=name,
        owner_id=customer.owner_id,
        **overrides,
    )
    db.add(opportunity)
    db.commit()
    return opportunity


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
