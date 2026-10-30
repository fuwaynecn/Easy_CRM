"""跟进记录（activities）的模型、作用域读取与写入规则。

可见性完全继承客户：这里测的是「客户可见 ⇒ 跟进可见」这条继承关系在每个入口
（列表 / 单条 / 不存在 / 已删）上是否成立，而不是再造一套作用域。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.modules.activities.models import ACTIVITY_TYPES, Activity
from app.modules.activities.service import get_activity, list_activities
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
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
        "cust_pool": create_customer(db, admin, CustomerCreate(name="丙制造有限公司")),
    }


def test_activity_table_is_registered_in_metadata():
    """新表必须在 models_registry 里注册，否则测试夹具建表缺表、迁移比对也看不见。"""
    import app.db.models_registry  # noqa: F401
    from app.db.base import Base

    assert "activities" in Base.metadata.tables
    columns = Base.metadata.tables["activities"].columns
    assert columns["customer_id"].nullable is False
    assert columns["content"].nullable is False
    assert columns["activity_at"].nullable is False
    assert columns["created_by"].nullable is False
    assert columns["contact_id"].nullable is True
    assert columns["opportunity_id"].nullable is True


def _insert(db, actor, customer, *, content="电话沟通", type="call", activity_at=None):
    """读侧测试的最小造数：直接插表，不依赖尚不存在的写接口（写接口在 Task 4）。

    这是"设置既有列"的同类做法，不是伪造业务动作——Task 4 会把本 helper 全体替换成
    ``create_activity`` 调用，届时删掉它。
    """
    row = Activity(
        customer_id=customer.id,
        type=type,
        content=content,
        activity_at=activity_at or NOW,
        created_by=actor.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_activity_type_tuple_matches_spec():
    assert ACTIVITY_TYPES == ("call", "visit", "wechat", "email", "note", "other")


def test_list_is_scoped_to_visible_customers(db):
    """销售只看到自己客户的跟进；同部门同事与别部门都看不到。"""
    f = _setup(db)
    mine = _insert(db, f["sa"], f["cust_a"])
    _insert(db, f["sb"], f["cust_b"])

    items, total = list_activities(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [mine.id]


def test_manager_sees_department_activities_but_not_other_department(db):
    f = _setup(db)
    east = _insert(db, f["sa"], f["cust_a"])
    _insert(db, f["sb"], f["cust_b"])

    items, total = list_activities(db, f["boss"])

    assert total == 1
    assert [item.id for item in items] == [east.id]


def test_list_excludes_soft_deleted_rows_from_items_and_total(db):
    f = _setup(db)
    kept = _insert(db, f["sa"], f["cust_a"], content="保留")
    gone = _insert(db, f["sa"], f["cust_a"], content="删除")
    gone.deleted_at = datetime.now(UTC)
    db.commit()

    items, total = list_activities(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [kept.id]


def test_list_filters_by_customer_type_and_created_by(db):
    f = _setup(db)
    call = _insert(db, f["sa"], f["cust_a"], content="电话", type="call")
    _insert(db, f["sa"], f["cust_a"], content="拜访", type="visit")
    other = _insert(db, f["sc"], f["cust_a"], content="同事的拜访", type="visit")

    # 只看 total：本用例关心的是「按客户筛选确实命中 3 条」而不是返回内容
    _by_customer, total = list_activities(
        db, f["boss"], customer_id=f["cust_a"].id
    )
    assert total == 3

    by_type, total = list_activities(db, f["boss"], type="call")
    assert total == 1
    assert [item.id for item in by_type] == [call.id]

    by_author, total = list_activities(db, f["boss"], created_by=f["sc"].id)
    assert total == 1
    assert [item.id for item in by_author] == [other.id]


def test_list_rejects_unknown_type(db):
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        list_activities(db, f["sa"], type="telepathy")


def test_list_on_invisible_customer_is_403(db):
    f = _setup(db)
    with pytest.raises(ForbiddenError):
        list_activities(db, f["sa"], customer_id=f["cust_b"].id)


def test_get_missing_activity_is_404(db):
    f = _setup(db)
    with pytest.raises(NotFoundError):
        get_activity(db, f["sa"], 999_999)


def test_get_other_departments_activity_is_403(db):
    f = _setup(db)
    foreign = _insert(db, f["sb"], f["cust_b"])
    with pytest.raises(ForbiddenError):
        get_activity(db, f["sa"], foreign.id)


def test_list_orders_by_activity_at_descending(db):
    f = _setup(db)
    older = _insert(
        db, f["sa"], f["cust_a"], content="早", activity_at=NOW - timedelta(days=2)
    )
    newer = _insert(db, f["sa"], f["cust_a"], content="晚", activity_at=NOW)

    items, _ = list_activities(db, f["sa"])

    assert [item.id for item in items] == [newer.id, older.id]
