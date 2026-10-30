"""跟进接口层：错误码、请求体白名单、线上形状、分页边界。

服务层规则已在 test_activities.py 钉过；这里只测**过一遍 HTTP 才成立**的东西：
403/404/400/422 各自带正确的 `code`、`activity_at` 的序列化形状、以及
`next_follow_up` 是否在同一事务里建出待办。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.modules.activities.models import Activity
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.followups.models import FollowUp
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


def test_activities_require_authentication(client, db):
    resp = client.get("/api/v1/activities")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_create_and_list_activity_through_http(client, db):
    f = _setup(db)
    headers = _auth(client, "sales_a")

    created = client.post(
        "/api/v1/activities",
        headers=headers,
        json={
            "customer_id": f["cust_a"].id,
            "type": "call",
            "content": "  电话确认交期  ",
            "activity_at": NOW.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["content"] == "电话确认交期"          # 服务端 strip
    assert body["type"] == "call"
    assert body["created_by_name"] == "销售A"
    # 线上时间形状：带偏移（实测是库会话时区的 `+08:00`，不是 `Z`）。
    # 断言"同一瞬时 + 带时区"而不是某个字面量：偏移由 PG 会话时区决定，
    # 前端必须按 ISO 解析，不能截字符串比较。
    parsed_at = datetime.fromisoformat(body["activity_at"])
    assert parsed_at.tzinfo is not None
    assert parsed_at == NOW

    listed = client.get("/api/v1/activities", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == body["id"]


def test_create_activity_with_next_follow_up_creates_todo(client, db):
    f = _setup(db)
    headers = _auth(client, "sales_a")

    created = client.post(
        "/api/v1/activities",
        headers=headers,
        json={
            "customer_id": f["cust_a"].id,
            "type": "visit",
            "content": "现场沟通",
            "activity_at": NOW.isoformat(),
            "next_follow_up": {
                "due_at": (NOW + timedelta(days=2)).isoformat(),
                "content": "寄样册",
            },
        },
    )

    assert created.status_code == 201, created.text
    rows = list(
        db.execute(
            select(FollowUp).where(FollowUp.customer_id == f["cust_a"].id)
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].content == "寄样册"
    assert rows[0].status == "pending"


def test_pool_customer_activity_is_403(client, db):
    """公海客户不可写跟进：HTTP 层 403，且**一行都没落库**。

    只断言 403 是不够的：路由若先建行再在序列化/提交阶段报错，状态码可能仍是 403，
    而库里已经多了一条跟进（下一次列表就能看到公海客户凭空多出一条记录）。
    """
    f = _setup(db)
    headers = _auth(client, "sales_a")

    resp = client.post(
        "/api/v1/activities",
        headers=headers,
        json={"customer_id": f["cust_pool"].id, "content": "打电话"},
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"
    # 同一会话里计数：不 expire 也能看到本事务的写入，所以这里刻意不 expire——
    # 要证明的正是"没有写入发生"，而不是"写入被别的会话挡住了"。
    assert db.execute(select(func.count()).select_from(Activity)).scalar_one() == 0


def test_request_body_whitelist_is_enforced(client, db):
    """`extra="forbid"`：想把 created_by / deleted_at 一起传进来必须 422。"""
    f = _setup(db)
    headers = _auth(client, "sales_a")

    resp = client.post(
        "/api/v1/activities",
        headers=headers,
        json={
            "customer_id": f["cust_a"].id,
            "content": "沟通",
            "created_by": 999,
        },
    )

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_patch_and_delete_activity(client, db):
    f = _setup(db)
    headers = _auth(client, "sales_a")
    created = client.post(
        "/api/v1/activities",
        headers=headers,
        json={"customer_id": f["cust_a"].id, "content": "原始内容"},
    ).json()

    patched = client.patch(
        f"/api/v1/activities/{created['id']}",
        headers=headers,
        json={"content": "更正后的内容", "type": "wechat"},
    )
    assert patched.status_code == 200
    assert patched.json()["content"] == "更正后的内容"
    assert patched.json()["type"] == "wechat"

    removed = client.delete(f"/api/v1/activities/{created['id']}", headers=headers)
    assert removed.status_code == 204
    assert removed.content == b""  # 204 不许带 body（FastAPI 里真会退化出 "null"）
    again = client.delete(f"/api/v1/activities/{created['id']}", headers=headers)
    assert again.status_code == 404
    assert again.json()["code"] == "not_found"


def test_patch_activity_blank_content_is_400(client, db):
    """HTTP 层的空内容：`"   "` 过得了 pydantic 的 min_length，由 service 清洗后判空。

    422 是"字段形状不对"，这里是"值不合法"——必须是 400 invalid_value（T4 遗留覆盖）。
    """
    f = _setup(db)
    headers = _auth(client, "sales_a")
    created = client.post(
        "/api/v1/activities",
        headers=headers,
        json={"customer_id": f["cust_a"].id, "content": "原本有内容"},
    ).json()

    resp = client.patch(
        f"/api/v1/activities/{created['id']}", headers=headers, json={"content": "   "}
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"


def test_create_activity_with_naive_activity_at_is_400(client, db):
    """无时区的 ISO 时间必须 400：数据库会按会话时区静默解释，错得无声。"""
    f = _setup(db)
    headers = _auth(client, "sales_a")

    resp = client.post(
        "/api/v1/activities",
        headers=headers,
        json={
            "customer_id": f["cust_a"].id,
            "content": "补录",
            "activity_at": "2026-10-31T09:00:00",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"


def test_cross_department_activity_is_403(client, db):
    f = _setup(db)
    created = client.post(
        "/api/v1/activities",
        headers=_auth(client, "sales_b"),
        json={"customer_id": f["cust_b"].id, "content": "别部门的"},
    ).json()

    resp = client.get(
        f"/api/v1/activities/{created['id']}", headers=_auth(client, "sales_a")
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_pagination_bounds_are_rejected(client, db):
    _setup(db)
    headers = _auth(client, "sales_a")

    assert client.get("/api/v1/activities?page=0", headers=headers).status_code == 422
    assert client.get("/api/v1/activities?page_size=201", headers=headers).status_code == 422
