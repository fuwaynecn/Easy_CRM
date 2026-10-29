"""跨角色的 P1 端到端冒烟（HTTP 级）。

一个测试文件覆盖 P1 最容易坏的路径，全部走真实 HTTP 与真实数据库，不打桩：

1. 销售建客户 → 加两个联系人 → 改第二个 → 设为主联系人 → 原主的标记被清掉；
2. 销售读同部门同事的客户是 403（不是空列表、不是 404）；
3. 公海客户：销售能读、PATCH 403，且库里的值一个都没变；
4. 主管看得到本部门全部 + 公海，看不到别部门客户详情（403），能改本部门客户；
5. 管理员能读任何人名下的客户，``owner_name`` 是真实姓名而不是「用户 #N」；
6. 重名先 409（``detail.similar`` 为 ``[{id, name}]``）→ ``force=true`` 成功 →
   紧接着再建同名仍 409（force 只对当次生效）。

与 brief 样例的差异（样例写于早期，按现状改写）：联系人改/删走扁平
``PATCH|DELETE /api/v1/contacts/{id}``（嵌套写路由已删除）；``create_user`` 要求
manager/sales 必须带 ``department_id``；登录名至少 3 个字符；``CustomerRead`` 多
返回 ``owner_name``（公海客户为 null）。

夹具刻意给华东部门放**两个**销售：只有一个销售时"本部门"与"自己名下"恰好重合，
把 sales 的作用域错写成"本部门 + 公海"也能全绿。
"""

import pytest
from sqlalchemy import select

from app.modules.contacts.models import Contact
from app.modules.customers.models import Customer
from app.modules.departments.service import create_department
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"


def _setup(db) -> dict[str, object]:
    """两个部门、一个管理员、两个主管、三个销售。"""
    east = create_department(db, "华东销售部")
    west = create_department(db, "华南销售部")
    admin = create_user(
        db,
        username="admin",
        password=PASSWORD,
        display_name="系统管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )
    boss = create_user(
        db,
        username="boss",
        password=PASSWORD,
        display_name="李建国",
        role=ROLE_MANAGER,
        department_id=east.id,
    )
    sales1 = create_user(
        db,
        username="sales1",
        password=PASSWORD,
        display_name="张明",
        role=ROLE_SALES,
        department_id=east.id,
    )
    sales2 = create_user(
        db,
        username="sales2",
        password=PASSWORD,
        display_name="王丽",
        role=ROLE_SALES,
        department_id=east.id,
    )
    boss_south = create_user(
        db,
        username="boss_south",
        password=PASSWORD,
        display_name="陈晓峰",
        role=ROLE_MANAGER,
        department_id=west.id,
    )
    sales3 = create_user(
        db,
        username="sales3",
        password=PASSWORD,
        display_name="赵强",
        role=ROLE_SALES,
        department_id=west.id,
    )
    return {
        "east": east,
        "west": west,
        "admin": admin,
        "boss": boss,
        "sales1": sales1,
        "sales2": sales2,
        "boss_south": boss_south,
        "sales3": sales3,
    }


def _auth(client, username: str) -> dict[str, str]:
    """登录拿 access token；access token 只走请求头，refresh cookie 不参与断言。"""
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_customer(client, headers, name: str, **extra) -> dict:
    resp = client.post(
        "/api/v1/customers", headers=headers, json={"name": name, **extra}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _names(body: dict) -> set[str]:
    return {item["name"] for item in body["items"]}


def test_sales_creates_customer_and_primary_contact_moves(client, db):
    """销售建客户 → 加联系人 → 改联系人 → 设主联系人 → 原主的标记被清掉。"""
    _setup(db)
    headers = _auth(client, "sales1")
    customer = _create_customer(
        client,
        headers,
        "杭州智造装备有限公司",
        level="A",
        budget_status="approved",
    )
    customer_id = customer["id"]
    assert customer["owner_name"] == "张明"
    assert customer["owner_id"] is not None

    first = client.post(
        f"/api/v1/customers/{customer_id}/contacts",
        headers=headers,
        json={"name": "王工", "phone": "13800000001", "is_primary": True},
    )
    assert first.status_code == 201, first.text
    assert first.json()["is_primary"] is True

    second = client.post(
        f"/api/v1/customers/{customer_id}/contacts",
        headers=headers,
        json={"name": "刘总", "title": "总经理", "decision_role": "decision_maker"},
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]

    # 修改 + 设为主联系人：扁平路径，联系人 id 全局唯一，不需要父客户 id。
    # 嵌套写路由 /customers/{cid}/contacts/{id} 已删除，用它只会拿到 404。
    patched = client.patch(
        f"/api/v1/contacts/{second_id}",
        headers=headers,
        json={"name": "刘建国", "phone": "13800000002", "is_primary": True},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "刘建国"
    assert patched.json()["is_primary"] is True

    listed = client.get(f"/api/v1/customers/{customer_id}/contacts", headers=headers)
    assert listed.status_code == 200
    primaries = {item["name"]: item["is_primary"] for item in listed.json()}
    assert primaries == {"刘建国": True, "王工": False}

    # 库里也不能残留两条 primary：接口返回对、库里错是最难发现的一种坏
    db.expire_all()
    rows = (
        db.execute(select(Contact).where(Contact.customer_id == customer_id))
        .scalars()
        .all()
    )
    assert [row.name for row in rows if row.is_primary] == ["刘建国"]


def test_sales_cannot_read_same_department_colleague_customer(client, db):
    """同部门同事的客户是 403，不是空列表、不是 404。"""
    _setup(db)
    colleague = _auth(client, "sales2")
    customer = _create_customer(client, colleague, "宁波海天涂装有限公司")

    headers = _auth(client, "sales1")
    detail = client.get(f"/api/v1/customers/{customer['id']}", headers=headers)
    assert detail.status_code == 403, detail.text
    assert detail.json()["code"] == "forbidden"
    assert detail.json()["message"] == "无权查看该客户"

    listed = client.get("/api/v1/customers", headers=headers)
    assert listed.status_code == 200
    assert _names(listed.json()) == set()
    assert listed.json()["total"] == 0

    # 联系人读取继承客户作用域：绕道 /contacts 也拿不到数据
    contacts = client.get(
        f"/api/v1/customers/{customer['id']}/contacts", headers=headers
    )
    assert contacts.status_code == 403


def test_sales_patch_pool_customer_forbidden_and_data_unchanged(client, db):
    """公海只读：销售能读、PATCH 403，且库里的值一个都没变。"""
    _setup(db)
    pool = _create_customer(
        client,
        _auth(client, "admin"),
        "苏州新能源电池科技有限公司",
        level="B",
        budget_status="unknown",
        remark="官网留资，尚未分配负责人",
    )
    assert pool["owner_id"] is None
    assert pool["owner_name"] is None

    headers = _auth(client, "sales1")
    readable = client.get(f"/api/v1/customers/{pool['id']}", headers=headers)
    assert readable.status_code == 200
    assert readable.json()["name"] == "苏州新能源电池科技有限公司"

    denied = client.patch(
        f"/api/v1/customers/{pool['id']}",
        headers=headers,
        json={"level": "A", "remark": "被改写"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "forbidden"
    assert "公海" in denied.json()["message"]

    # 403 之后库里必须原封不动：拒绝写入却已落库是最坏的失败方式
    db.expire_all()
    row = db.get(Customer, pool["id"])
    assert row.level == "B"
    assert row.remark == "官网留资，尚未分配负责人"
    assert row.owner_id is None


def test_manager_sees_department_only_and_can_edit_it(client, db):
    """主管看到本部门全部 + 公海；别部门客户详情 403；本部门客户可改。"""
    f = _setup(db)
    east_a = _create_customer(client, _auth(client, "sales1"), "杭州智造装备有限公司")
    east_b = _create_customer(client, _auth(client, "sales2"), "宁波海天涂装有限公司")
    west = _create_customer(
        client, _auth(client, "sales3"), "广州鼎盛汽车零部件有限公司"
    )
    pool = _create_customer(
        client, _auth(client, "admin"), "苏州新能源电池科技有限公司"
    )

    boss = _auth(client, "boss")
    listed = client.get("/api/v1/customers", headers=boss)
    assert listed.status_code == 200
    body = listed.json()
    assert _names(body) == {
        "杭州智造装备有限公司",
        "宁波海天涂装有限公司",
        "苏州新能源电池科技有限公司",
    }
    assert west["name"] not in _names(body)
    assert body["total"] == 3

    other = client.get(f"/api/v1/customers/{west['id']}", headers=boss)
    assert other.status_code == 403
    assert other.json()["code"] == "forbidden"

    # 主管可写本部门全部（含下属名下），写响应里的 owner_name 是下属姓名
    patched = client.patch(
        f"/api/v1/customers/{east_a['id']}", headers=boss, json={"level": "B"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["level"] == "B"
    assert patched.json()["owner_name"] == "张明"

    # 公海对主管同样只读
    denied = client.patch(
        f"/api/v1/customers/{pool['id']}", headers=boss, json={"level": "A"}
    )
    assert denied.status_code == 403
    assert east_b["owner_id"] == f["sales2"].id


def test_admin_reads_any_customer_with_real_owner_name(client, db):
    """管理员能读任何人名下的客户，且 owner_name 是真实姓名而不是「用户 #N」。"""
    _setup(db)
    _create_customer(client, _auth(client, "sales3"), "广州鼎盛汽车零部件有限公司")
    _create_customer(client, _auth(client, "sales1"), "杭州智造装备有限公司")
    _create_customer(client, _auth(client, "admin"), "苏州新能源电池科技有限公司")

    admin = _auth(client, "admin")
    listed = client.get("/api/v1/customers", headers=admin)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 3
    owners = {item["name"]: item["owner_name"] for item in body["items"]}
    assert owners == {
        "广州鼎盛汽车零部件有限公司": "赵强",
        "杭州智造装备有限公司": "张明",
        "苏州新能源电池科技有限公司": None,
    }

    # 详情页同样带姓名：前端 ownerLabel 退化到「用户 #id」说明这里丢了字段
    west_id = next(
        item["id"] for item in body["items"] if item["name"] == "广州鼎盛汽车零部件有限公司"
    )
    detail = client.get(f"/api/v1/customers/{west_id}", headers=admin)
    assert detail.status_code == 200, detail.text
    assert detail.json()["owner_name"] == "赵强"
    assert "用户 #" not in detail.text
    assert "用户名" not in detail.text


def test_duplicate_name_conflict_then_force_is_one_shot(client, db):
    """重名 409（detail.similar 是 [{id, name}]）→ force 成功 → 再建仍 409。"""
    _setup(db)
    headers = _auth(client, "sales1")
    first = _create_customer(client, headers, "浙江甲制造有限公司")

    # 归一化后同名（多一个空格）也判重：前端按 detail.similar 渲染候选列表
    duplicate = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江 甲制造 有限公司"}
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "conflict"
    assert duplicate.json()["detail"]["similar"] == [
        {"id": first["id"], "name": "浙江甲制造有限公司"}
    ]

    forced = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "浙江甲制造有限公司", "force": True},
    )
    assert forced.status_code == 201, forced.text
    forced_id = forced.json()["id"]
    assert forced_id != first["id"]

    # force 只对当次生效：紧接着的同名请求仍要经过查重，两条都出现在候选里
    again = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江甲制造有限公司"}
    )
    assert again.status_code == 409, again.text
    assert {item["id"] for item in again.json()["detail"]["similar"]} == {
        first["id"],
        forced_id,
    }


@pytest.mark.parametrize("username", ["admin", "boss", "sales1", "sales2", "sales3"])
def test_every_seed_style_account_can_login(client, db, username):
    """所有角色的账号都能登录：夹具的部门必修课没有把某类账号挤掉。"""
    _setup(db)
    assert _auth(client, username)
