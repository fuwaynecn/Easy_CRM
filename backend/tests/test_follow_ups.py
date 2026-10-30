"""待办（follow_ups）的模型、作用域读取与「我的待办」口径。

spec §3.9 的关键结论：**待办不设负责人字段**，它跟着客户走。这里钉住的正是这条
——「我的待办」= join customers 后 `customers.owner_id = me`，而不是给待办加一列。
任何"顺手加个 owner_id"的改动都会让本文件里的 mine 用例失去意义。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.followups.models import FOLLOW_UP_STATUSES, FollowUp
from app.modules.followups.service import get_follow_up, list_follow_ups
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"
NOW = datetime(2026, 10, 31, 1, 0, tzinfo=UTC)


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
    sc = create_user(
        db, username="sales_c", password=PASSWORD, display_name="销售C",
        role=ROLE_SALES, department_id=east.id,
    )
    sb = create_user(
        db, username="sales_b", password=PASSWORD, display_name="销售B",
        role=ROLE_SALES, department_id=south.id,
    )
    return {
        "admin": admin, "boss": boss, "sa": sa, "sc": sc, "sb": sb,
        "cust_a": create_customer(db, sa, CustomerCreate(name="甲制造有限公司")),
        "cust_b": create_customer(db, sb, CustomerCreate(name="乙制造有限公司")),
    }


def _insert(db, actor, customer, *, content="下周回访", due_at=None, status="pending"):
    """读侧测试的最小造数（写接口在 Task 5；届时本 helper 换成 create_follow_up）。"""
    row = FollowUp(
        customer_id=customer.id,
        due_at=due_at or NOW,
        content=content,
        status=status,
        done_at=datetime.now(UTC) if status == "done" else None,
        created_by=actor.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_follow_up_table_is_registered_with_spec_columns():
    import app.db.models_registry  # noqa: F401
    from app.db.base import Base

    assert "follow_ups" in Base.metadata.tables
    columns = Base.metadata.tables["follow_ups"].columns
    assert columns["customer_id"].nullable is False
    assert columns["due_at"].nullable is False
    assert columns["content"].nullable is False
    assert columns["created_by"].nullable is False
    assert columns["done_at"].nullable is True
    # spec §3.9：待办没有负责人字段，这是设计结论，不是遗漏
    assert "owner_id" not in columns


def test_follow_up_statuses_match_spec():
    assert FOLLOW_UP_STATUSES == ("pending", "done", "cancelled")


def test_list_is_scoped_to_visible_customers(db):
    f = _setup(db)
    mine = _insert(db, f["sa"], f["cust_a"])
    _insert(db, f["sb"], f["cust_b"])

    items, total = list_follow_ups(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [mine.id]


def test_mine_filter_is_customer_owner_not_created_by(db):
    """「我的待办」按客户当前负责人判定，不是按创建者。

    主管给下属的客户建一条待办：在主管的「我的待办」里不该出现，在销售 A 的里该出现
    ——否则待办会跟着"谁写的"走，与 spec §3.9「客户转给谁，待办就归谁」相反。
    """
    f = _setup(db)
    by_boss = _insert(db, f["boss"], f["cust_a"], content="主管安排的待办")

    mine_sa, total_sa = list_follow_ups(db, f["sa"], mine=True)
    assert [item.id for item in mine_sa] == [by_boss.id]
    assert total_sa == 1

    mine_boss, total_boss = list_follow_ups(db, f["boss"], mine=True)
    assert mine_boss == []
    assert total_boss == 0

    # 不带 mine 时主管看得到本部门客户的待办（裁定 3：可见性与客户一致）
    all_boss, total_all = list_follow_ups(db, f["boss"])
    assert [item.id for item in all_boss] == [by_boss.id]
    assert total_all == 1


def test_list_filters_by_status_and_customer(db):
    f = _setup(db)
    pending = _insert(db, f["sa"], f["cust_a"], content="待办")
    _insert(db, f["sa"], f["cust_a"], content="已完成", status="done")
    other = _insert(db, f["sc"], f["cust_a"], content="同事的待办")

    pending_items, pending_total = list_follow_ups(db, f["boss"], status="pending")
    assert pending_total == 2
    assert {item.id for item in pending_items} == {pending.id, other.id}

    _by_customer, total = list_follow_ups(
        db, f["boss"], customer_id=f["cust_a"].id
    )
    assert total == 3


def test_list_rejects_unknown_status(db):
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        list_follow_ups(db, f["sa"], status="someday")


def test_list_excludes_soft_deleted_from_items_and_total(db):
    f = _setup(db)
    kept = _insert(db, f["sa"], f["cust_a"], content="保留")
    gone = _insert(db, f["sa"], f["cust_a"], content="删除")
    gone.deleted_at = datetime.now(UTC)
    db.commit()

    items, total = list_follow_ups(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [kept.id]


def test_list_orders_by_due_at_ascending(db):
    f = _setup(db)
    later = _insert(
        db, f["sa"], f["cust_a"], content="后天", due_at=NOW + timedelta(days=2)
    )
    sooner = _insert(db, f["sa"], f["cust_a"], content="今天", due_at=NOW)

    items, _ = list_follow_ups(db, f["sa"])

    assert [item.id for item in items] == [sooner.id, later.id]


def test_customer_name_is_part_of_the_read_shape(db):
    """首页与待办列表都要显示客户名，避免前端按 id 二次查名（N+1）。

    直接断言 wire 形状（若 read 不返回 customer_name，前端就得再发 N 个请求）。
    """
    from app.modules.followups.schemas import FollowUpRead

    f = _setup(db)
    row = _insert(db, f["sa"], f["cust_a"])
    loaded = get_follow_up(db, f["sa"], row.id)

    body = FollowUpRead.model_validate(loaded)

    assert body.customer_name == "甲制造有限公司"
    assert body.created_by_name == "销售A"


def test_get_other_departments_follow_up_is_403(db):
    f = _setup(db)
    foreign = _insert(db, f["sb"], f["cust_b"])
    with pytest.raises(ForbiddenError):
        get_follow_up(db, f["sa"], foreign.id)


def test_get_missing_follow_up_is_404(db):
    f = _setup(db)
    with pytest.raises(NotFoundError):
        get_follow_up(db, f["sa"], 999_999)
