"""待办接口层：CRUD、`/done` 的返回形状、请求体白名单与错误码。

另含一条「400 之后不留脏数据」的用例：T5 的完成冻结/取消冻结都是先校验后抛，
HTTP 层必须证明失败请求既没落库、也没把连接弄坏。
"""

from datetime import UTC, datetime, timedelta

from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.users.models import ROLE_ADMIN, ROLE_SALES
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
    sa = create_user(
        db, username="sales_a", password=PASSWORD, display_name="销售A",
        role=ROLE_SALES, department_id=east.id,
    )
    sb = create_user(
        db, username="sales_b", password=PASSWORD, display_name="销售B",
        role=ROLE_SALES, department_id=south.id,
    )
    return {
        "admin": admin, "sa": sa, "sb": sb,
        "cust_a": create_customer(db, sa, CustomerCreate(name="甲制造有限公司")),
        "cust_b": create_customer(db, sb, CustomerCreate(name="乙制造有限公司")),
        "cust_pool": create_customer(db, admin, CustomerCreate(name="丙制造有限公司")),
    }


def _auth(client, username):
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_follow_ups_require_authentication(client, db):
    resp = client.get("/api/v1/follow_ups")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_create_list_patch_cancel(client, db):
    f = _setup(db)
    headers = _auth(client, "sales_a")

    created = client.post(
        "/api/v1/follow_ups",
        headers=headers,
        json={
            "customer_id": f["cust_a"].id,
            "due_at": (NOW + timedelta(days=1)).isoformat(),
            "content": "  寄样册  ",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["content"] == "寄样册"
    assert body["status"] == "pending"
    assert body["customer_name"] == "甲制造有限公司"
    assert body["created_by_name"] == "销售A"

    listed = client.get("/api/v1/follow_ups?status=pending", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    cancelled = client.patch(
        f"/api/v1/follow_ups/{body['id']}", headers=headers, json={"status": "cancelled"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_done_returns_follow_up_and_activity(client, db):
    f = _setup(db)
    headers = _auth(client, "sales_a")
    created = client.post(
        "/api/v1/follow_ups",
        headers=headers,
        json={
            "customer_id": f["cust_a"].id,
            "due_at": NOW.isoformat(),
            "content": "把样册送到现场",
        },
    ).json()

    done = client.post(
        f"/api/v1/follow_ups/{created['id']}/done",
        headers=headers,
        json={"create_activity": True, "activity_type": "visit"},
    )

    assert done.status_code == 200, done.text
    body = done.json()
    assert body["follow_up"]["status"] == "done"
    assert body["follow_up"]["done_at"] is not None
    assert body["activity"]["content"] == "把样册送到现场"
    assert body["activity"]["type"] == "visit"

    again = client.post(
        f"/api/v1/follow_ups/{created['id']}/done", headers=headers, json={}
    )
    assert again.status_code == 400
    assert again.json()["code"] == "invalid_value"


def test_done_without_activity_returns_null_activity(client, db):
    f = _setup(db)
    headers = _auth(client, "sales_a")
    created = client.post(
        "/api/v1/follow_ups",
        headers=headers,
        json={"customer_id": f["cust_a"].id, "due_at": NOW.isoformat(), "content": "回访"},
    ).json()

    done = client.post(
        f"/api/v1/follow_ups/{created['id']}/done", headers=headers, json={}
    )

    assert done.status_code == 200
    assert done.json()["activity"] is None


def test_follow_up_request_body_whitelist(client, db):
    f = _setup(db)
    headers = _auth(client, "sales_a")

    resp = client.post(
        "/api/v1/follow_ups",
        headers=headers,
        json={
            "customer_id": f["cust_a"].id,
            "due_at": NOW.isoformat(),
            "content": "回访",
            "owner_id": 1,   # spec §3.9：待办没有负责人字段，传了就是 422
        },
    )

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_follow_up_of_invisible_customer_is_403(client, db):
    f = _setup(db)
    resp = client.post(
        "/api/v1/follow_ups",
        headers=_auth(client, "sales_a"),
        json={"customer_id": f["cust_b"].id, "due_at": NOW.isoformat(), "content": "回访"},
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_delete_follow_up_then_404(client, db):
    f = _setup(db)
    headers = _auth(client, "sales_a")
    created = client.post(
        "/api/v1/follow_ups",
        headers=headers,
        json={"customer_id": f["cust_a"].id, "due_at": NOW.isoformat(), "content": "回访"},
    ).json()

    removed = client.delete(f"/api/v1/follow_ups/{created['id']}", headers=headers)
    assert removed.status_code == 204

    resp = client.get(f"/api/v1/follow_ups/{created['id']}", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_400_does_not_leak_dirty_state(client, db):
    """失败请求既不能落库，也不能让后续请求读到"改了一半"的行。

    `PATCH status=done` 与 `cancel 后改 content` 都是 service 先校验再抛 400；
    路由层没有 try/except，靠 `get_db` 在异常路径 rollback + close 兜底。
    """
    f = _setup(db)
    headers = _auth(client, "sales_a")
    created = client.post(
        "/api/v1/follow_ups",
        headers=headers,
        json={"customer_id": f["cust_a"].id, "due_at": NOW.isoformat(), "content": "原始"},
    ).json()

    # 400 一号：完成必须走 /done
    bad = client.patch(
        f"/api/v1/follow_ups/{created['id']}", headers=headers, json={"status": "done"}
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_value"

    # 400 二号：取消后不能再改内容
    assert client.patch(
        f"/api/v1/follow_ups/{created['id']}",
        headers=headers,
        json={"status": "cancelled"},
    ).status_code == 200
    frozen = client.patch(
        f"/api/v1/follow_ups/{created['id']}", headers=headers, json={"content": "偷改"}
    )
    assert frozen.status_code == 400
    assert frozen.json()["code"] == "invalid_value"

    # 后续请求照常可用，且两次 400 都没留下痕迹
    after = client.get(f"/api/v1/follow_ups/{created['id']}", headers=headers)
    assert after.status_code == 200
    assert after.json()["status"] == "cancelled"
    assert after.json()["content"] == "原始"

    # 同一连接上再写一次成功请求，证明事务没卡在 aborted 状态
    fresh = client.post(
        "/api/v1/follow_ups",
        headers=headers,
        json={"customer_id": f["cust_a"].id, "due_at": NOW.isoformat(), "content": "第二条"},
    )
    assert fresh.status_code == 201, fresh.text
