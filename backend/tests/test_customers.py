"""客户服务与接口。

分两层：service 层锁业务规则（软查重、归属与冗余字段、last_activity_at），
接口层用真实 HTTP 锁权限——越权 403、不存在 404、重名 409 三者不能混用。

作用域测试刻意造同部门两个销售 + 另一个部门：同部门同事的客户看不到，说明
作用域按 owner 而非按部门；主管看得到本部门、看不到别的部门，说明按部门。
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
)
from app.modules.contacts.schemas import ContactCreate
from app.modules.contacts.service import create_contact, delete_contact
from app.modules.customers.models import Customer
from app.modules.customers.schemas import (
    CUSTOMER_CREATE_FIELDS,
    CUSTOMER_UPDATE_FIELDS,
    CustomerCreate,
    CustomerUpdate,
)
from app.modules.customers.service import (
    create_customer,
    delete_customer,
    find_similar_customers,
    get_customer,
    list_customers,
    normalize_name,
    update_customer,
)
from app.modules.departments.service import create_department
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user, update_user

PASSWORD = "Passw0rd!"


def _setup(db):
    """华东两人（主管 + 销售A + 销售C）、华南一人（销售B）、管理员一个。"""
    east = create_department(db, "华东销售部")
    west = create_department(db, "华南销售部")
    admin = create_user(
        db,
        username="admin",
        password=PASSWORD,
        display_name="管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )
    boss = create_user(
        db,
        username="boss",
        password=PASSWORD,
        display_name="东区主管",
        role=ROLE_MANAGER,
        department_id=east.id,
    )
    sa = create_user(
        db,
        username="sales_a",
        password=PASSWORD,
        display_name="销售A",
        role=ROLE_SALES,
        department_id=east.id,
    )
    sc = create_user(
        db,
        username="sales_c",
        password=PASSWORD,
        display_name="销售C",
        role=ROLE_SALES,
        department_id=east.id,
    )
    sb = create_user(
        db,
        username="sales_b",
        password=PASSWORD,
        display_name="销售B",
        role=ROLE_SALES,
        department_id=west.id,
    )
    return {
        "admin": admin,
        "boss": boss,
        "sa": sa,
        "sb": sb,
        "sc": sc,
        "east": east,
        "west": west,
    }


def _orphan_customers(db):
    """孤儿客户：有负责人但没有部门。

    这类客户不在公海、也不在任何部门作用域里，owner 自己都看不到；创建路径
    一旦放行就会留下这种"谁都看不见"的行，因此测试直接查库断言它们为零。
    """
    return (
        db.execute(
            select(Customer).where(
                Customer.owner_id.is_not(None), Customer.department_id.is_(None)
            )
        )
        .scalars()
        .all()
    )


# --- 名称归一化 ---------------------------------------------------------


def test_normalize_name_strips_noise():
    assert normalize_name("  浙江 甲（制造）有限公司 ") == "浙江甲制造有限公司"
    assert normalize_name("ABC Co.,Ltd.") == "abccoltd"


def test_normalize_name_normalizes_character_width():
    """Excel 导出里全角字母数字极常见，宽度不同的同一个名字必须归一。"""
    assert normalize_name("杭州ＸＸ智能装备有限公司") == "杭州xx智能装备有限公司"
    assert normalize_name("　浙江 甲（制造）有限公司　") == "浙江甲制造有限公司"
    assert normalize_name("ＡＢＣ 有限公司") == normalize_name("ABC 有限公司")


# --- 创建：归属与冗余字段 -----------------------------------------------


def test_create_customer_sets_owner_and_department(db):
    f = _setup(db)

    customer = create_customer(db, f["sa"], CustomerCreate(name="甲制造有限公司"))

    assert customer.owner_id == f["sa"].id
    assert customer.department_id == f["east"].id
    assert customer.created_by == f["sa"].id


def test_create_does_not_touch_last_activity_at(db):
    """spec §3.3 的三条规则里，创建与改字段都不是 last_activity_at 的同步点。"""
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲制造有限公司"))
    assert customer.last_activity_at is None

    # 手工填一个历史时间，再走一次普通编辑：编辑不能把它往前推、也不能清掉
    historical = datetime(2020, 1, 1, tzinfo=UTC)
    customer.last_activity_at = historical
    db.commit()

    update_customer(
        db, f["sa"], customer.id, CustomerUpdate(level="A", status="lost")
    )

    db.refresh(customer)
    assert customer.last_activity_at == historical


def test_admin_can_assign_owner_on_create(db):
    f = _setup(db)

    customer = create_customer(
        db, f["admin"], CustomerCreate(name="甲公司", owner_id=f["sb"].id)
    )

    assert customer.owner_id == f["sb"].id
    # department_id 是冗余字段，必须跟着负责人走（同步点之一：创建）
    assert customer.department_id == f["west"].id


def test_admin_create_without_owner_lands_in_public_pool(db):
    f = _setup(db)

    customer = create_customer(db, f["admin"], CustomerCreate(name="甲公司"))

    assert customer.owner_id is None
    assert customer.department_id is None


def test_manager_can_assign_to_own_department_member(db):
    f = _setup(db)

    customer = create_customer(
        db, f["boss"], CustomerCreate(name="甲公司", owner_id=f["sa"].id)
    )

    assert customer.owner_id == f["sa"].id
    assert customer.department_id == f["east"].id


def test_manager_cannot_assign_outside_department(db):
    f = _setup(db)

    with pytest.raises(ForbiddenError) as exc:
        create_customer(
            db, f["boss"], CustomerCreate(name="甲公司", owner_id=f["sb"].id)
        )

    assert exc.value.status_code == 403


def test_sales_cannot_create_for_someone_else(db):
    """销售自建归自己；把客户建到同事名下就是越权，不是静默改写成自己。"""
    f = _setup(db)

    with pytest.raises(ForbiddenError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="甲公司", owner_id=f["sc"].id))

    assert exc.value.status_code == 403
    db.expire_all()
    assert db.execute(select(Customer)).scalars().all() == []


def test_unknown_role_cannot_create_customer(db):
    """未知角色不能退化成"等价 sales"：否则能建客户，建完按作用域又看不见。"""
    f = _setup(db)
    db.execute(
        text("UPDATE users SET role = 'guest' WHERE id = :user_id"),
        {"user_id": f["sa"].id},
    )
    db.commit()
    db.refresh(f["sa"])

    with pytest.raises(ForbiddenError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    assert exc.value.status_code == 403
    assert exc.value.message == "账号角色异常，请联系管理员"
    db.expire_all()
    assert db.execute(select(Customer)).scalars().all() == []


def test_manager_without_department_cannot_create_customer(db):
    """无部门主管建客户会造出"owner 有值、department 为空"的孤儿行。

    这类客户既不在公海、也不在任何部门作用域里，连 owner 自己都看不到；
    ``None != None`` 放行是根因，必须 400 拒绝而不是静默落库。
    """
    f = _setup(db)
    void = create_user(
        db,
        username="boss_void",
        password=PASSWORD,
        display_name="无部门主管",
        role=ROLE_MANAGER,
        department_id=f["east"].id,
    )
    # 建号路径已经禁止无部门主管；这里模拟历史数据/人工改库留下的无部门状态，
    # 客户创建路径仍必须自己兜住。
    db.execute(
        text("UPDATE users SET department_id = NULL WHERE id = :user_id"),
        {"user_id": void.id},
    )
    db.commit()
    db.refresh(void)

    with pytest.raises(InvalidValueError) as exc:
        create_customer(db, void, CustomerCreate(name="甲公司"))

    assert exc.value.status_code == 400
    assert exc.value.message == "负责人未分配部门，无法归属客户"
    db.expire_all()
    assert db.execute(select(Customer)).scalars().all() == []
    assert _orphan_customers(db) == []


def test_admin_cannot_assign_owner_without_department(db):
    """同一个洞的另一条入口：管理员把客户指定给无部门账号（如另一个 admin）。"""
    f = _setup(db)
    void = create_user(
        db,
        username="it_support",
        password=PASSWORD,
        display_name="IT 支持",
        role=ROLE_ADMIN,
        department_id=None,
    )

    with pytest.raises(InvalidValueError) as exc:
        create_customer(
            db, f["admin"], CustomerCreate(name="甲公司", owner_id=void.id)
        )

    assert exc.value.status_code == 400
    assert exc.value.message == "负责人未分配部门，无法归属客户"
    db.expire_all()
    assert _orphan_customers(db) == []


def test_create_rejects_unknown_owner(db):
    f = _setup(db)

    with pytest.raises(NotFoundError):
        create_customer(
            db, f["admin"], CustomerCreate(name="甲公司", owner_id=999_999)
        )


def test_create_rejects_disabled_owner(db):
    f = _setup(db)
    update_user(db, f["sa"].id, {"status": "disabled"})

    with pytest.raises(InvalidValueError) as exc:
        create_customer(
            db, f["admin"], CustomerCreate(name="甲公司", owner_id=f["sa"].id)
        )

    assert exc.value.status_code == 400


# --- 创建：重名软查重（spec §4.4） --------------------------------------


def test_create_customer_rejects_similar_name(db):
    f = _setup(db)
    existing = create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))

    with pytest.raises(ConflictError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="浙江 甲制造 有限公司"))

    assert exc.value.status_code == 409
    assert exc.value.detail["similar"][0] == {
        "id": existing.id,
        "name": "浙江甲制造有限公司",
    }


def test_create_customer_rejects_full_width_duplicate(db):
    """同一家公司的全角/半角两种写法必须命中同一条重名 409，不能当成两家客户。"""
    f = _setup(db)
    existing = create_customer(
        db, f["sa"], CustomerCreate(name="杭州ＸＸ智能装备有限公司")
    )

    with pytest.raises(ConflictError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="杭州XX智能装备有限公司"))

    assert exc.value.status_code == 409
    assert [item["id"] for item in exc.value.detail["similar"]] == [existing.id]


def test_duplicate_check_only_sees_actors_scope(db):
    """查重只在调用者可见的客户里做：看不见的名字不能被 409 回吐。

    早期实现取 ``customers`` 全表比对，把 ``detail.similar`` 变成了枚举全公司客户名
    的旁路：华南销售能看到华东客户的名字，而直接 ``GET`` 那条客户是 403。
    """
    f = _setup(db)
    hidden = create_customer(db, f["sb"], CustomerCreate(name="浙江甲制造有限公司"))
    # 销售A 的作用域里确实没有这条
    items, total = list_customers(db, f["sa"])
    assert total == 0 and items == []

    created = create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))

    assert created.id != hidden.id
    visible = find_similar_customers(db, f["sa"], "浙江甲制造有限公司")
    assert [c.id for c in visible] == [created.id]


def test_duplicate_check_includes_public_pool(db):
    """公海客户对所有角色可见，同名创建仍然要提示重复。"""
    f = _setup(db)
    pool = create_customer(db, f["admin"], CustomerCreate(name="浙江甲制造有限公司"))

    with pytest.raises(ConflictError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))

    assert [item["id"] for item in exc.value.detail["similar"]] == [pool.id]


def test_duplicate_check_caps_similar_at_five(db):
    f = _setup(db)
    for i in range(7):
        create_customer(
            db, f["sa"], CustomerCreate(name=f"浙江甲制造有限公司{i}", force=True)
        )

    with pytest.raises(ConflictError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))

    assert len(exc.value.detail["similar"]) == 5


def test_duplicate_check_ignores_soft_deleted(db):
    f = _setup(db)
    gone = create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))
    delete_customer(db, f["admin"], gone.id)

    recreated = create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))

    assert recreated.id != gone.id


def test_create_customer_with_force_skips_duplicate_check(db):
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))

    dup = create_customer(
        db, f["sa"], CustomerCreate(name="浙江甲制造有限公司", force=True)
    )

    assert dup.id is not None


def test_force_only_applies_to_the_current_request(db):
    """``force`` 是单次请求的开关，不是客户名上的持久豁免。

    第一次带 force 建成功后，紧接着同名再建（不带 force）必须仍然 409；
    否则重名软查重会被一次 force 永久绕过。
    """
    f = _setup(db)
    create_customer(
        db, f["sa"], CustomerCreate(name="浙江甲制造有限公司", force=True)
    )

    with pytest.raises(ConflictError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))

    assert exc.value.status_code == 409


def test_create_strips_name_and_rejects_blank(db):
    f = _setup(db)

    customer = create_customer(db, f["sa"], CustomerCreate(name="  甲 公司  "))
    assert customer.name == "甲 公司"

    with pytest.raises(InvalidValueError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="   "))
    assert exc.value.status_code == 400


# --- 枚举校验 -----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"level": "Z"},
        {"procurement_type": "bad"},
        {"budget_status": "bad"},
    ],
    ids=["level", "procurement", "budget"],
)
def test_create_customer_validates_enums(db, payload):
    """取值非法是 400：409 留给"与现有数据冲突"（重名、已被领取）。"""
    f = _setup(db)

    with pytest.raises(InvalidValueError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="甲公司", **payload))

    assert exc.value.status_code == 400
    db.expire_all()
    assert db.execute(select(Customer)).scalars().all() == []


@pytest.mark.parametrize(
    "field", ["department_id", "last_activity_at", "created_by", "deleted_at"]
)
def test_schemas_reject_internal_fields(field):
    """内部字段不能从请求体写进来：冗余字段与审计字段由后端同步。"""
    with pytest.raises(ValidationError):
        CustomerUpdate(**{field: 1})
    with pytest.raises(ValidationError):
        CustomerCreate(name="甲公司", **{field: 1})


def test_create_whitelist_allows_owner_id():
    """创建可传 owner_id：管理员/主管建客户时直接指定负责人（由 service 校验范围）。"""
    assert "owner_id" in CUSTOMER_CREATE_FIELDS
    assert CustomerCreate(name="甲公司", owner_id=7).owner_id == 7


def test_update_whitelist_forbids_owner_id():
    """更新不可传 owner_id：转移客户要写 owner_change_logs、走专门流程（§4.3）。"""
    assert "owner_id" not in CUSTOMER_UPDATE_FIELDS
    with pytest.raises(ValidationError):
        CustomerUpdate(owner_id=7)

def test_list_customers_keyword_matches_contact_name_and_phone(db):
    """spec §5:396：keyword 也要命中联系人姓名与电话（客户列表是唯一的搜索入口）。"""
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    create_contact(
        db, f["sa"], customer.id, ContactCreate(name="王小明", phone="13800138000")
    )
    create_customer(db, f["sa"], CustomerCreate(name="乙公司", force=True))

    by_name, total_name = list_customers(db, f["sa"], keyword="王小明")
    assert total_name == 1 and [c.id for c in by_name] == [customer.id]

    by_phone, total_phone = list_customers(db, f["sa"], keyword="13800138000")
    assert total_phone == 1 and [c.id for c in by_phone] == [customer.id]

    by_fragment, total_fragment = list_customers(db, f["sa"], keyword="1380013")
    assert total_fragment == 1 and [c.id for c in by_fragment] == [customer.id]


def test_list_customers_keyword_ignores_soft_deleted_contacts(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    gone = create_contact(db, f["sa"], customer.id, ContactCreate(name="王小明"))
    delete_contact(db, f["sa"], gone.id)

    items, total = list_customers(db, f["sa"], keyword="王小明")

    assert total == 0 and items == []


def test_list_customers_keyword_contact_matches_do_not_duplicate_rows(db):
    """EXISTS 而不是 join：同一客户两条联系人都命中时只能出现一行，total 也不能翻倍。"""
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    create_contact(db, f["sa"], customer.id, ContactCreate(name="王小明"))
    create_contact(db, f["sa"], customer.id, ContactCreate(name="王小华"))
    other = create_customer(db, f["sb"], CustomerCreate(name="乙公司"))
    create_contact(db, f["sb"], other.id, ContactCreate(name="王小明"))

    items, total = list_customers(db, f["sa"], keyword="王")

    assert total == 1
    assert [c.id for c in items] == [customer.id]


# --- 列表：最后跟进时间区间（spec §5:396） ------------------------------


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _customer_followed_up(db, actor, name: str, moment: datetime):
    customer = create_customer(db, actor, CustomerCreate(name=name))
    customer.last_activity_at = moment
    db.commit()
    return customer


def test_list_customers_date_from_is_inclusive(db):
    f = _setup(db)
    target = _customer_followed_up(
        db, f["sa"], "甲公司", datetime(2026, 3, 10, 0, 0, tzinfo=SHANGHAI)
    )
    _customer_followed_up(
        db, f["sa"], "乙公司", datetime(2026, 3, 9, 23, 59, tzinfo=SHANGHAI)
    )

    items, total = list_customers(db, f["sa"], date_from=date(2026, 3, 10))

    assert total == 1 and [c.id for c in items] == [target.id]


def test_list_customers_date_to_includes_the_whole_day(db):
    """右端是半开区间：``date_to`` 当天下午的记录必须还在（闭区间会漏掉）。"""
    f = _setup(db)
    afternoon = _customer_followed_up(
        db, f["sa"], "甲公司", datetime(2026, 3, 10, 15, 30, tzinfo=SHANGHAI)
    )
    next_day = _customer_followed_up(
        db, f["sa"], "乙公司", datetime(2026, 3, 11, 0, 0, tzinfo=SHANGHAI)
    )

    items, total = list_customers(db, f["sa"], date_to=date(2026, 3, 10))

    assert total == 1 and [c.id for c in items] == [afternoon.id]
    assert next_day.id not in {c.id for c in items}


def test_list_customers_date_range_combined(db):
    f = _setup(db)
    inside = _customer_followed_up(
        db, f["sa"], "甲公司", datetime(2026, 3, 10, 15, 30, tzinfo=SHANGHAI)
    )
    _customer_followed_up(
        db, f["sa"], "乙公司", datetime(2026, 3, 12, 0, 0, tzinfo=SHANGHAI)
    )

    items, total = list_customers(
        db, f["sa"], date_from=date(2026, 3, 10), date_to=date(2026, 3, 10)
    )

    assert total == 1 and [c.id for c in items] == [inside.id]


def test_list_customers_rejects_reversed_date_range(db):
    f = _setup(db)

    with pytest.raises(InvalidValueError) as exc:
        list_customers(
            db, f["sa"], date_from=date(2026, 3, 11), date_to=date(2026, 3, 10)
        )

    assert exc.value.status_code == 400


def test_list_customers_date_filter_excludes_never_followed_up(db):
    """``last_activity_at`` 为 NULL 的客户在用了任一端日期筛选时都不出现。"""
    f = _setup(db)
    followed = _customer_followed_up(
        db, f["sa"], "甲公司", datetime(2026, 3, 10, 15, 30, tzinfo=SHANGHAI)
    )
    never = create_customer(db, f["sa"], CustomerCreate(name="乙公司", force=True))
    assert never.last_activity_at is None

    only_from, total_from = list_customers(
        db, f["sa"], date_from=date(2000, 1, 1)
    )
    assert total_from == 1 and [c.id for c in only_from] == [followed.id]

    only_to, total_to = list_customers(db, f["sa"], date_to=date(2100, 1, 1))
    assert total_to == 1 and [c.id for c in only_to] == [followed.id]

    _all_items, total_without_dates = list_customers(db, f["sa"])
    assert total_without_dates == 2


# --- 列表：作用域、筛选、排序、分页 -------------------------------------


def test_list_customers_respects_scope_and_total(db):
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    create_customer(db, f["sc"], CustomerCreate(name="丙公司"))
    create_customer(db, f["sb"], CustomerCreate(name="乙公司"))

    names_a, total_a = list_customers(db, f["sa"])
    names_boss, total_boss = list_customers(db, f["boss"])
    names_admin, total_admin = list_customers(db, f["admin"])

    # 同部门同事的客户也看不到：作用域按 owner 而不是按部门
    assert [c.name for c in names_a] == ["甲公司"]
    assert total_a == 1
    assert {c.name for c in names_boss} == {"甲公司", "丙公司"}
    assert total_boss == 2
    assert {c.name for c in names_admin} == {"甲公司", "乙公司", "丙公司"}
    assert total_admin == 3


def test_list_customers_filters_by_keyword_and_level(db):
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="杭州机器人厂", level="A"))
    create_customer(db, f["sa"], CustomerCreate(name="宁波喷涂厂", level="B", force=True))

    items, total = list_customers(db, f["sa"], keyword="机器人")
    assert total == 1 and items[0].name == "杭州机器人厂"

    items2, total2 = list_customers(db, f["sa"], level="B")
    assert total2 == 1 and items2[0].name == "宁波喷涂厂"

    _items3, total3 = list_customers(db, f["sa"], level="C")
    assert total3 == 0


def test_list_customers_keyword_wildcards_are_literal(db):
    """关键字里的 % 与 _ 只能按字面量匹配，不能退化成"匹配全部"。"""
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    create_customer(db, f["sa"], CustomerCreate(name="100%控股公司", force=True))

    items, total = list_customers(db, f["sa"], keyword="%")
    assert total == 1
    assert [c.name for c in items] == ["100%控股公司"]

    _items, total_underscore = list_customers(db, f["sa"], keyword="_")
    assert total_underscore == 0


def test_list_customers_filters_by_owner_and_status(db):
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    create_customer(db, f["sc"], CustomerCreate(name="丙公司"))
    lost = create_customer(
        db, f["sa"], CustomerCreate(name="乙公司", force=True)
    )
    update_customer(db, f["sa"], lost.id, CustomerUpdate(status="lost"))

    _items, total_owner = list_customers(db, f["boss"], owner_id=f["sc"].id)
    assert total_owner == 1

    _items2, total_lost = list_customers(db, f["sa"], status="lost")
    assert total_lost == 1


def test_list_customers_paginates(db):
    f = _setup(db)
    for i in range(5):
        create_customer(db, f["sa"], CustomerCreate(name=f"客户{i}", force=True))

    page1, total = list_customers(db, f["sa"], page=1, page_size=2)
    page3, _ = list_customers(db, f["sa"], page=3, page_size=2)

    assert total == 5
    assert len(page1) == 2 and len(page3) == 1


def test_list_customers_sorts_newest_first_and_rejects_unknown_field(db):
    f = _setup(db)
    first = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    second = create_customer(db, f["sa"], CustomerCreate(name="乙公司", force=True))
    third = create_customer(db, f["sa"], CustomerCreate(name="丙公司", force=True))

    items, _total = list_customers(db, f["sa"])

    assert [c.id for c in items] == [third.id, second.id, first.id]

    with pytest.raises(InvalidValueError) as exc:
        list_customers(db, f["sa"], sort="deleted_at")
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "sort",
    ["name;DROP TABLE customers", "name,id", "-name,unknown"],
    ids=["semicolon", "comma", "multi-field"],
)
def test_list_customers_rejects_sort_injection_forms(db, sort):
    """sort 只接受白名单里的单字段（可带 ``-`` 前缀）；其余形态 400 且不动表。"""
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    with pytest.raises(InvalidValueError) as exc:
        list_customers(db, f["sa"], sort=sort)

    assert exc.value.status_code == 400
    assert db.execute(text("SELECT count(*) FROM customers")).scalar_one() == 1


def test_list_customers_excludes_soft_deleted(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    delete_customer(db, f["admin"], customer.id)

    items, total = list_customers(db, f["admin"])

    assert total == 0 and items == []


def test_list_customers_eager_loads_owner_without_n_plus_one(db):
    """取 20 条客户只允许两条 SELECT：总数 + 分页（负责人随分页一条 join 取回）。

    用独立 Session 查询，避开身份映射里已有的 Customer/User 实例：否则即使服务层
    删掉预加载，测试也会因为对象已在内存里而变绿。访问 ``owner`` 也放在计数区间内：
    预加载被删掉时，要么直接报错（``lazy="raise"``），要么多出 20 条 SELECT。
    """
    f = _setup(db)
    for i in range(20):
        create_customer(db, f["sa"], CustomerCreate(name=f"客户{i}", force=True))

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        with Session(bind=engine) as fresh:
            items, total = list_customers(fresh, f["sa"])
            assert total == 20
            assert {item.owner.display_name for item in items} == {"销售A"}
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(statements) <= 2, "\n".join(statements)


# --- 详情：越权 403、不存在 404 -----------------------------------------


def test_get_customer_of_other_sales_raises_forbidden(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    with pytest.raises(ForbiddenError) as exc:
        get_customer(db, f["sc"], customer.id)

    assert exc.value.status_code == 403


def test_manager_cannot_read_other_department_customer(db):
    f = _setup(db)
    customer = create_customer(db, f["sb"], CustomerCreate(name="乙公司"))

    with pytest.raises(ForbiddenError) as exc:
        get_customer(db, f["boss"], customer.id)

    assert exc.value.status_code == 403


def test_get_pool_customer_is_readable(db):
    f = _setup(db)
    pool = create_customer(db, f["admin"], CustomerCreate(name="公海公司"))

    assert get_customer(db, f["sa"], pool.id).id == pool.id
    assert get_customer(db, f["boss"], pool.id).id == pool.id


def test_get_missing_customer_raises_not_found(db):
    f = _setup(db)

    with pytest.raises(NotFoundError) as exc:
        get_customer(db, f["sa"], 9999)

    assert exc.value.status_code == 404


def test_deleted_customer_detail_is_not_found(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    delete_customer(db, f["admin"], customer.id)

    with pytest.raises(NotFoundError):
        get_customer(db, f["admin"], customer.id)


# --- 修改 ---------------------------------------------------------------


def test_update_customer_requires_write_permission(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    updated = update_customer(db, f["sa"], customer.id, CustomerUpdate(level="A"))
    assert updated.level == "A"

    with pytest.raises(ForbiddenError):
        update_customer(db, f["sc"], customer.id, CustomerUpdate(level="B"))


def test_pool_customer_cannot_be_updated_before_claiming(db):
    f = _setup(db)
    pool = create_customer(db, f["admin"], CustomerCreate(name="公海公司"))

    with pytest.raises(ForbiddenError) as exc:
        update_customer(db, f["sa"], pool.id, CustomerUpdate(level="A"))

    assert exc.value.status_code == 403


def test_update_customer_validates_enums(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    with pytest.raises(InvalidValueError):
        update_customer(db, f["sa"], customer.id, CustomerUpdate(status="bad"))

    db.refresh(customer)
    assert customer.status == "active"


def test_update_does_not_trigger_duplicate_check(db):
    """spec §4.4 的软查重只发生在创建：改名撞名不弹 409，否则改名就改不动了。"""
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    other = create_customer(db, f["sa"], CustomerCreate(name="乙公司", force=True))

    renamed = update_customer(db, f["sa"], other.id, CustomerUpdate(name="甲公司"))

    assert renamed.name == "甲公司"


def test_update_never_moves_owner_or_department(db):
    """owner 的变更只走领取/分配/转移，PATCH 不许顺手改归属。"""
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    owner_id = customer.owner_id
    department_id = customer.department_id

    update_customer(db, f["sa"], customer.id, CustomerUpdate(level="A"))

    db.refresh(customer)
    assert customer.owner_id == owner_id
    assert customer.department_id == department_id


def test_update_rejects_blank_name(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    with pytest.raises(InvalidValueError):
        update_customer(db, f["sa"], customer.id, CustomerUpdate(name="   "))

    with pytest.raises(InvalidValueError):
        update_customer(db, f["sa"], customer.id, CustomerUpdate(level=None))


# --- 删除 ---------------------------------------------------------------


def test_delete_is_soft_and_sales_cannot_delete(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    with pytest.raises(ForbiddenError):
        delete_customer(db, f["sa"], customer.id)

    delete_customer(db, f["boss"], customer.id)

    db.refresh(customer)
    assert customer.deleted_at is not None
    items, total = list_customers(db, f["boss"])
    assert total == 0 and items == []


def test_manager_cannot_delete_pool_customer(db):
    """公海客户连主管也不能删：可读不等于可写。"""
    f = _setup(db)
    pool = create_customer(db, f["admin"], CustomerCreate(name="公海公司"))

    with pytest.raises(ForbiddenError) as exc:
        delete_customer(db, f["boss"], pool.id)

    assert exc.value.status_code == 403
    db.refresh(pool)
    assert pool.deleted_at is None


def test_sales_from_other_department_cannot_delete_customer(db):
    """跨部门销售删除 403：删除仅限主管及以上，先撞角色守卫。"""
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    with pytest.raises(ForbiddenError) as exc:
        delete_customer(db, f["sb"], customer.id)

    assert exc.value.status_code == 403
    db.refresh(customer)
    assert customer.deleted_at is None


def test_manager_can_delete_customer_in_own_department(db):
    """本部门（自己管辖范围内）的客户可删。"""
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    delete_customer(db, f["boss"], customer.id)

    db.expire_all()
    assert db.get(Customer, customer.id).deleted_at is not None


def test_delete_missing_customer_returns_not_found(db):
    f = _setup(db)

    with pytest.raises(NotFoundError):
        delete_customer(db, f["admin"], 9999)


# --- 接口层：真实 HTTP 下的越权与错误码 ---------------------------------


def _token(client, username: str, password: str = PASSWORD) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_api_requires_authentication(client, db):
    resp = client.get("/api/v1/customers")

    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_api_list_scoped_to_own_and_pool(client, db):
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    create_customer(db, f["sc"], CustomerCreate(name="丙公司"))
    create_customer(db, f["sb"], CustomerCreate(name="乙公司"))
    pool = create_customer(db, f["admin"], CustomerCreate(name="公海公司"))

    body = client.get(
        "/api/v1/customers", headers=_auth(_token(client, "sales_a"))
    ).json()

    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"甲公司", "公海公司"}
    assert pool.id in {item["id"] for item in body["items"]}


def test_api_list_and_detail_expose_owner_name(client, db):
    """列表与详情都返回负责人的 display_name；公海客户（没有负责人）是 null。"""
    f = _setup(db)
    mine = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    pool = create_customer(db, f["admin"], CustomerCreate(name="公海公司"))
    headers = _auth(_token(client, "sales_a"))

    body = client.get("/api/v1/customers", headers=headers).json()
    names = {item["id"]: item["owner_name"] for item in body["items"]}
    assert names == {mine.id: "销售A", pool.id: None}

    detail = client.get(f"/api/v1/customers/{mine.id}", headers=headers).json()
    assert detail["owner_name"] == "销售A"
    pool_detail = client.get(f"/api/v1/customers/{pool.id}", headers=headers).json()
    assert pool_detail["owner_name"] is None

    # 主管看下属名下的客户也拿得到姓名，不是"只有自己"的特例
    boss_detail = client.get(
        f"/api/v1/customers/{mine.id}", headers=_auth(_token(client, "boss"))
    ).json()
    assert boss_detail["owner_name"] == "销售A"


def test_api_create_and_patch_return_owner_name(client, db):
    """写接口同样带 owner_name：单条路径的回读也必须预加载负责人。"""
    _setup(db)
    headers = _auth(_token(client, "sales_a"))

    created = client.post("/api/v1/customers", headers=headers, json={"name": "甲公司"})
    assert created.status_code == 201, created.text
    assert created.json()["owner_name"] == "销售A"

    patched = client.patch(
        f"/api/v1/customers/{created.json()['id']}",
        headers=headers,
        json={"level": "A"},
    )
    assert patched.status_code == 200
    assert patched.json()["owner_name"] == "销售A"


def test_api_detail_of_other_sales_customer_returns_403(client, db):
    """越权必须是 403：404 会让前端以为记录被删了，空对象会让它以为没数据。"""
    f = _setup(db)
    target = create_customer(db, f["sc"], CustomerCreate(name="丙公司"))

    resp = client.get(
        f"/api/v1/customers/{target.id}", headers=_auth(_token(client, "sales_a"))
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "forbidden"
    assert body["message"]


def test_api_pool_customer_readable_but_patch_forbidden(client, db):
    f = _setup(db)
    pool = create_customer(db, f["admin"], CustomerCreate(name="公海公司"))
    headers = _auth(_token(client, "sales_a"))

    detail = client.get(f"/api/v1/customers/{pool.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "公海公司"

    patched = client.patch(
        f"/api/v1/customers/{pool.id}", headers=headers, json={"level": "A"}
    )
    assert patched.status_code == 403
    assert patched.json()["code"] == "forbidden"

    db.expire_all()
    assert db.get(Customer, pool.id).level == "C"


def test_api_manager_reads_department_but_not_other_department(client, db):
    f = _setup(db)
    east_customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    west_customer = create_customer(db, f["sb"], CustomerCreate(name="乙公司"))
    headers = _auth(_token(client, "boss"))

    assert (
        client.get(f"/api/v1/customers/{east_customer.id}", headers=headers).status_code
        == 200
    )
    other = client.get(f"/api/v1/customers/{west_customer.id}", headers=headers)
    assert other.status_code == 403

    listed = client.get("/api/v1/customers", headers=headers).json()
    assert {item["name"] for item in listed["items"]} == {"甲公司"}


def test_api_missing_customer_returns_404(client, db):
    _setup(db)

    resp = client.get("/api/v1/customers/999999", headers=_auth(_token(client, "admin")))

    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    assert resp.json()["message"] == "客户不存在"


def test_api_create_conflict_then_force(client, db):
    _setup(db)
    headers = _auth(_token(client, "sales_a"))
    first = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江甲制造有限公司"}
    )
    assert first.status_code == 201, first.text

    duplicate = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江 甲制造 有限公司"}
    )
    assert duplicate.status_code == 409
    body = duplicate.json()
    assert body["code"] == "conflict"
    # 前端按 detail.similar 渲染"可能重复"列表，每项必须有 id 与 name
    assert body["detail"]["similar"] == [
        {"id": first.json()["id"], "name": "浙江甲制造有限公司"}
    ]

    forced = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "浙江甲制造有限公司", "force": True},
    )
    assert forced.status_code == 201, forced.text


def test_api_duplicate_check_still_finds_own_customer(client, db):
    """可见范围内（自己名下，同一个部门）的同名客户仍然 409，查重照常生效。"""
    _setup(db)
    headers = _auth(_token(client, "sales_a"))
    first = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江甲制造有限公司"}
    )
    assert first.status_code == 201, first.text

    dup = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江甲制造有限公司"}
    )

    assert dup.status_code == 409
    assert dup.json()["detail"]["similar"] == [
        {"id": first.json()["id"], "name": "浙江甲制造有限公司"}
    ]


def test_api_duplicate_check_still_finds_public_pool(client, db):
    """公海客户对所有人可见，同名创建仍然 409。"""
    f = _setup(db)
    pool = create_customer(db, f["admin"], CustomerCreate(name="浙江甲制造有限公司"))

    resp = client.post(
        "/api/v1/customers",
        headers=_auth(_token(client, "sales_a")),
        json={"name": "浙江甲制造有限公司"},
    )

    assert resp.status_code == 409, resp.text
    assert [item["id"] for item in resp.json()["detail"]["similar"]] == [pool.id]


def test_api_duplicate_check_includes_manager_department_scope(client, db):
    """主管可见本部门全部客户，因此下属的同名客户必须仍被查出来。

    §2.2 只给 ``sales`` 自己名下（+ 公海）的可见范围，同部门同事的客户对销售仍是
    403；"同部门可见"成立的是 ``manager``。这条盯住部门可见范围内的查重不能因为
    收紧作用域而一起失效。
    """
    f = _setup(db)
    colleague = create_customer(db, f["sc"], CustomerCreate(name="浙江甲制造有限公司"))

    resp = client.post(
        "/api/v1/customers",
        headers=_auth(_token(client, "boss")),
        json={"name": "浙江甲制造有限公司"},
    )

    assert resp.status_code == 409, resp.text
    assert [item["id"] for item in resp.json()["detail"]["similar"]] == [colleague.id]


def test_api_duplicate_check_cross_department_creates_and_does_not_leak(client, db):
    """跨部门同名：201，响应里不能出现别部门客户（查重越权泄漏的回归点）。

    旧实现在全表查重，这条会 409 并把华南客户的名字回给华东销售——而那条客户
    他直接 GET 是 403。收紧到可见范围后跨部门重名不再提示，这是有意的取舍。
    """
    f = _setup(db)
    hidden = create_customer(db, f["sb"], CustomerCreate(name="浙江甲制造有限公司"))

    resp = client.post(
        "/api/v1/customers",
        headers=_auth(_token(client, "sales_a")),
        json={"name": "浙江甲制造有限公司"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] != hidden.id
    # 201 体里没有任何 similar/detail 结构，也不含别部门的客户 id
    assert "similar" not in resp.text
    assert "detail" not in body


def test_api_duplicate_check_does_not_leak_fuzzy_match_name(client, db):
    """互相包含的模糊命中同样不能泄漏：别部门的"浙江甲制造有限公司"不能出现在响应里。"""
    f = _setup(db)
    create_customer(db, f["sb"], CustomerCreate(name="浙江甲制造有限公司"))

    resp = client.post(
        "/api/v1/customers",
        headers=_auth(_token(client, "sales_a")),
        json={"name": "浙江甲制造"},
    )

    assert resp.status_code == 201, resp.text
    assert "浙江甲制造有限公司" not in resp.text


def test_api_duplicate_similar_ids_are_all_readable_by_caller(client, db):
    """``detail.similar`` 里每条 id，调用者都必须 GET 到 200。

    这条是防未来回归的关键：只要查重再次套错作用域，similar 里就会出现调用者
    读不到的客户，循环里的 GET 立刻从 200 变成 403。
    """
    f = _setup(db)
    own = create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))
    pool = create_customer(
        db,
        f["admin"],
        CustomerCreate(name="浙江甲制造有限公司（宁波）", force=True),
    )
    hidden = create_customer(
        db, f["sb"], CustomerCreate(name="浙江甲制造有限公司嘉兴", force=True)
    )
    headers = _auth(_token(client, "sales_a"))

    resp = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江甲制造有限公司"}
    )

    assert resp.status_code == 409, resp.text
    similar = resp.json()["detail"]["similar"]
    assert {item["id"] for item in similar} == {own.id, pool.id}
    assert hidden.id not in {item["id"] for item in similar}
    for item in similar:
        detail = client.get(f"/api/v1/customers/{item['id']}", headers=headers)
        assert detail.status_code == 200, (item, detail.text)


def test_api_create_sets_owner_and_hides_internal_fields(client, db):
    f = _setup(db)

    resp = client.post(
        "/api/v1/customers",
        headers=_auth(_token(client, "sales_a")),
        json={"name": "  甲公司  "},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "甲公司"
    assert body["owner_id"] == f["sa"].id
    assert body["department_id"] == f["east"].id
    assert body["last_activity_at"] is None
    assert "created_by" not in body
    assert "deleted_at" not in body
    assert resp.text.count("password") == 0


def test_api_create_rejects_internal_fields_with_422(client, db):
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))

    resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "甲公司", "department_id": f["west"].id},
    )

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_api_create_invalid_enum_returns_400(client, db):
    _setup(db)

    resp = client.post(
        "/api/v1/customers",
        headers=_auth(_token(client, "admin")),
        json={"name": "甲公司", "level": "Z"},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"


def test_api_patch_updates_fields_without_touching_activity(client, db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    headers = _auth(_token(client, "sales_a"))

    resp = client.patch(
        f"/api/v1/customers/{customer.id}",
        headers=headers,
        json={"level": "A", "status": "lost", "remark": "初次接触"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["level"] == "A"
    assert body["status"] == "lost"
    assert body["remark"] == "初次接触"
    assert body["last_activity_at"] is None
    assert body["owner_id"] == f["sa"].id


def test_api_patch_to_existing_name_does_not_conflict(client, db):
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    other = create_customer(db, f["sa"], CustomerCreate(name="乙公司", force=True))

    resp = client.patch(
        f"/api/v1/customers/{other.id}",
        headers=_auth(_token(client, "sales_a")),
        json={"name": "甲公司"},
    )

    assert resp.status_code == 200
    assert resp.json()["name"] == "甲公司"


def test_api_patch_rejects_owner_field_with_422(client, db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    resp = client.patch(
        f"/api/v1/customers/{customer.id}",
        headers=_auth(_token(client, "sales_a")),
        json={"owner_id": f["sc"].id},
    )

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"
    db.expire_all()
    assert db.get(Customer, customer.id).owner_id == f["sa"].id


def test_api_delete_is_manager_only_and_soft(client, db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))

    denied = client.delete(
        f"/api/v1/customers/{customer.id}", headers=_auth(_token(client, "sales_a"))
    )
    assert denied.status_code == 403

    deleted = client.delete(
        f"/api/v1/customers/{customer.id}", headers=_auth(_token(client, "boss"))
    )
    assert deleted.status_code == 204
    assert deleted.text == ""

    detail = client.get(
        f"/api/v1/customers/{customer.id}", headers=_auth(_token(client, "admin"))
    )
    assert detail.status_code == 404

    listed = client.get(
        "/api/v1/customers", headers=_auth(_token(client, "admin"))
    ).json()
    assert customer.id not in {item["id"] for item in listed["items"]}


def test_api_list_pagination_and_keyword(client, db):
    f = _setup(db)
    for name in ("甲公司", "乙公司", "100%控股公司"):
        create_customer(db, f["sa"], CustomerCreate(name=name, force=True))
    headers = _auth(_token(client, "sales_a"))

    page2 = client.get(
        "/api/v1/customers", headers=headers, params={"page": 2, "page_size": 2}
    ).json()
    assert page2["page"] == 2
    assert page2["page_size"] == 2
    assert page2["total"] == 3
    assert len(page2["items"]) == 1

    percent = client.get(
        "/api/v1/customers", headers=headers, params={"keyword": "%"}
    ).json()
    assert percent["total"] == 1
    assert [item["name"] for item in percent["items"]] == ["100%控股公司"]


def test_api_list_filters_by_activity_date_range(client, db):
    """日期区间参数在 HTTP 上可用：当天下午的记录不能漏，反向区间是 400。"""
    f = _setup(db)
    inside = _customer_followed_up(
        db, f["sa"], "甲公司", datetime(2026, 3, 10, 15, 30, tzinfo=SHANGHAI)
    )
    headers = _auth(_token(client, "sales_a"))

    body = client.get(
        "/api/v1/customers",
        headers=headers,
        params={"date_from": "2026-03-10", "date_to": "2026-03-10"},
    ).json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [inside.id]

    reversed_range = client.get(
        "/api/v1/customers",
        headers=headers,
        params={"date_from": "2026-03-11", "date_to": "2026-03-10"},
    )
    assert reversed_range.status_code == 400
    assert reversed_range.json()["code"] == "invalid_value"


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("level", "Z"),
        ("status", "gone"),
        ("procurement_type", "bid"),
        ("budget_status", "maybe"),
    ],
    ids=["level", "status", "procurement", "budget"],
)
def test_api_list_rejects_invalid_filter_enum(client, db, param, value):
    """筛选枚举非法是 400：空列表会把"参数拼错"伪装成"没有数据"。"""
    _setup(db)

    resp = client.get(
        "/api/v1/customers",
        headers=_auth(_token(client, "admin")),
        params={param: value},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"
