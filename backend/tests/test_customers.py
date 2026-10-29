"""客户服务与接口。

分两层：service 层锁业务规则（软查重、归属与冗余字段、last_activity_at），
接口层用真实 HTTP 锁权限——越权 403、不存在 404、重名 409 三者不能混用。

作用域测试刻意造同部门两个销售 + 另一个部门：同部门同事的客户看不到，说明
作用域按 owner 而非按部门；主管看得到本部门、看不到别的部门，说明按部门。
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
)
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate, CustomerUpdate
from app.modules.customers.service import (
    create_customer,
    delete_customer,
    get_customer,
    list_customers,
    normalize_name,
    update_customer,
)
from app.modules.departments.service import create_department
from app.modules.users.service import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_SALES,
    create_user,
    set_user_status,
)

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


# --- 名称归一化 ---------------------------------------------------------


def test_normalize_name_strips_noise():
    assert normalize_name("  浙江 甲（制造）有限公司 ") == "浙江甲制造有限公司"
    assert normalize_name("ABC Co.,Ltd.") == "abccoltd"


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


def test_create_rejects_unknown_owner(db):
    f = _setup(db)

    with pytest.raises(NotFoundError):
        create_customer(
            db, f["admin"], CustomerCreate(name="甲公司", owner_id=999_999)
        )


def test_create_rejects_disabled_owner(db):
    f = _setup(db)
    set_user_status(db, f["sa"].id, "disabled")

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


def test_duplicate_check_is_cross_scope(db):
    """查重必须对全库生效：别的部门建过的同名客户我看不到，但创建时也要判重。"""
    f = _setup(db)
    hidden = create_customer(db, f["sb"], CustomerCreate(name="浙江甲制造有限公司"))
    # 销售A 的作用域里确实没有这条
    items, total = list_customers(db, f["sa"])
    assert total == 0 and items == []

    with pytest.raises(ConflictError) as exc:
        create_customer(db, f["sa"], CustomerCreate(name="浙江甲制造有限公司"))

    assert [item["id"] for item in exc.value.detail["similar"]] == [hidden.id]


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
    "field", ["owner_id", "department_id", "last_activity_at", "created_by", "deleted_at"]
)
def test_schemas_reject_internal_fields(field):
    """内部字段不能从请求体写进来：owner 变更走领取/分配，冗余字段由后端同步。"""
    with pytest.raises(ValidationError):
        CustomerUpdate(**{field: 1})
    if field != "owner_id":
        with pytest.raises(ValidationError):
            CustomerCreate(name="甲公司", **{field: 1})

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


def test_list_customers_excludes_soft_deleted(db):
    f = _setup(db)
    customer = create_customer(db, f["sa"], CustomerCreate(name="甲公司"))
    delete_customer(db, f["admin"], customer.id)

    items, total = list_customers(db, f["admin"])

    assert total == 0 and items == []


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
