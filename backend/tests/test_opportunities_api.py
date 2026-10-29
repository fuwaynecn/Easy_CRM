"""机会接口层：越权、错误码、请求体白名单、线上形状、看板列与分页校验。

服务层的规则已在 test_opportunities.py 钉过，这里测的是**只有过一遍 HTTP 才成立**
的东西：

1. 越权与非法输入的网络语义：403 / 400 / 404 / 422 各自带上正确的 ``code``，
   不是被全局处理器改写成 404、也不是空列表；
2. 请求体白名单：`stage` / `owner_id` / `won_at` 出现在 PATCH 或 POST 体里是 422，
   而不是被静默丢弃；
3. 线上形状：金额是定点字符串（``"8000.00"``）、`win_rate` 是生效值而
   `win_rate_manual` 标明是否手改过、`customer_name` / `owner_name` 由服务端预加载；
4. `/opportunities/board` 没有被 `/{opportunity_id}` 抢走——FastAPI 按声明顺序
   匹配，写反了 "board" 会被当 int 解析成 422；看板固定六列且聚合口径正确；
5. 分页参数在参数层就被挡住（``page=0`` / ``page_size=201``），不能变成
   ``OFFSET -20`` 或超大 OFFSET 打到库里；
6. 公海客户建机会这条 admin-only 分支：管理员是本轮唯一能走到"客户没有负责人"
   那一步的角色（sales/manager 先拿到 403），因此它只能在 HTTP 层验证。
"""

from sqlalchemy import select

from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.lost_reasons.schemas import LostReasonCreate
from app.modules.lost_reasons.service import create_lost_reason
from app.modules.opportunities.models import Opportunity
from app.modules.opportunities.schemas import OpportunityCreate
from app.modules.opportunities.service import create_opportunity
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"


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
        "admin": admin,
        "boss": boss,
        "sa": sa,
        "sb": sb,
        "cust_a": create_customer(db, sa, CustomerCreate(name="甲制造有限公司")),
        "cust_b": create_customer(db, sb, CustomerCreate(name="乙制造有限公司")),
        # 管理员不指定负责人 → 公海客户（owner_id 为空），用于钉住"公海建不了机会"
        # 这条只有 admin 能走到的 400 分支
        "cust_pool": create_customer(db, admin, CustomerCreate(name="丙制造有限公司")),
    }


def _token(client, username: str, password: str = PASSWORD) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make(db, actor, customer, name="喷涂产线一期", **overrides):
    payload = {"customer_id": customer.id, "name": name, **overrides}
    return create_opportunity(db, actor, OpportunityCreate(**payload))


def test_api_requires_authentication(client, db):
    resp = client.get("/api/v1/opportunities")

    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_api_full_lifecycle_through_http(client, db):
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))

    created = client.post(
        "/api/v1/opportunities",
        headers=headers,
        json={
            "customer_id": f["cust_a"].id,
            "name": "  喷涂产线一期  ",
            "amount": "5000.00",
            "expected_close_date": "2026-12-15",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "喷涂产线一期"
    assert body["amount"] == "5000.00"        # numeric → 定点字符串
    assert body["stage"] == "contact"
    assert body["win_rate"] == 10             # 阶段默认值
    assert body["win_rate_manual"] is False
    assert body["customer_name"] == "甲制造有限公司"
    assert body["owner_name"] == "销售A"      # 继承客户负责人
    opportunity_id = body["id"]

    listed = client.get("/api/v1/opportunities", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == opportunity_id

    detail = client.get(f"/api/v1/opportunities/{opportunity_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["expected_close_date"] == "2026-12-15"

    patched = client.patch(
        f"/api/v1/opportunities/{opportunity_id}",
        headers=headers,
        json={"amount": "8000.00", "win_rate": 85},
    )
    assert patched.status_code == 200
    assert patched.json()["amount"] == "8000.00"
    assert patched.json()["win_rate"] == 85
    assert patched.json()["win_rate_manual"] is True

    moved = client.post(
        f"/api/v1/opportunities/{opportunity_id}/stage",
        headers=headers,
        json={"stage": "proposal"},
    )
    assert moved.status_code == 200
    assert moved.json()["stage"] == "proposal"
    assert moved.json()["win_rate"] == 85     # 手改值不因改阶段被覆盖

    won = client.post(
        f"/api/v1/opportunities/{opportunity_id}/stage",
        headers=headers,
        json={"stage": "won"},
    )
    assert won.status_code == 200
    assert won.json()["won_at"] is not None

    board = client.get("/api/v1/opportunities/board", headers=headers)
    assert board.status_code == 200
    columns = {column["stage"]: column for column in board.json()["columns"]}
    assert len(columns) == 6
    assert columns["won"]["total"] == 1
    assert columns["won"]["amount_total"] == "8000.00"
    assert columns["contact"]["total"] == 0


def test_api_body_whitelist_rejects_stage_and_owner(client, db):
    """改阶段只能走 /stage、负责人只能继承客户：这两个字段出现在请求体里必须 422。"""
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))
    opportunity = _make(db, f["sa"], f["cust_a"])

    patch_stage = client.patch(
        f"/api/v1/opportunities/{opportunity.id}",
        headers=headers,
        json={"stage": "won"},
    )
    assert patch_stage.status_code == 422
    assert patch_stage.json()["code"] == "validation_error"

    patch_owner = client.patch(
        f"/api/v1/opportunities/{opportunity.id}",
        headers=headers,
        json={"owner_id": f["sa"].id},
    )
    assert patch_owner.status_code == 422
    assert patch_owner.json()["code"] == "validation_error"

    patch_terminal = client.patch(
        f"/api/v1/opportunities/{opportunity.id}",
        headers=headers,
        json={"won_at": "2026-10-30T00:00:00Z"},
    )
    assert patch_terminal.status_code == 422
    assert patch_terminal.json()["code"] == "validation_error"

    create_with_owner = client.post(
        "/api/v1/opportunities",
        headers=headers,
        json={"customer_id": f["cust_a"].id, "name": "另一单", "owner_id": f["sa"].id},
    )
    assert create_with_owner.status_code == 422
    assert create_with_owner.json()["code"] == "validation_error"

    db.expire_all()
    assert db.get(Opportunity, opportunity.id).stage == "contact"
    assert db.get(Opportunity, opportunity.id).won_at is None


def test_api_foreign_opportunity_is_403(client, db):
    f = _setup(db)
    foreign = _make(db, f["sb"], f["cust_b"], name="乙公司机会")
    headers = _auth(_token(client, "sales_a"))

    detail = client.get(f"/api/v1/opportunities/{foreign.id}", headers=headers)
    assert detail.status_code == 403
    assert detail.json()["code"] == "forbidden"

    patched = client.patch(
        f"/api/v1/opportunities/{foreign.id}", headers=headers, json={"name": "改别人的"}
    )
    assert patched.status_code == 403
    assert patched.json()["code"] == "forbidden"

    moved = client.post(
        f"/api/v1/opportunities/{foreign.id}/stage",
        headers=headers,
        json={"stage": "needs"},
    )
    assert moved.status_code == 403
    assert moved.json()["code"] == "forbidden"

    deleted = client.delete(f"/api/v1/opportunities/{foreign.id}", headers=headers)
    assert deleted.status_code == 403
    assert deleted.json()["code"] == "forbidden"

    filtered = client.get(
        f"/api/v1/opportunities?customer_id={f['cust_b'].id}", headers=headers
    )
    assert filtered.status_code == 403
    assert filtered.json()["code"] == "forbidden"

    board = client.get(
        f"/api/v1/opportunities/board?customer_id={f['cust_b'].id}", headers=headers
    )
    assert board.status_code == 403
    assert board.json()["code"] == "forbidden"


def test_api_admin_cannot_create_opportunity_on_public_pool_customer(client, db):
    """公海客户没有负责人，而机会 ``owner_id`` 是 NOT NULL：管理员也一样是 400。

    这条分支只有管理员走得到——sales/manager 对公海客户先被
    ``assert_customer_writable`` 挡成 403，服务层测试已覆盖；HTTP 层此前为零覆盖。
    """
    f = _setup(db)
    admin_headers = _auth(_token(client, "admin"))

    resp = client.post(
        "/api/v1/opportunities",
        headers=admin_headers,
        json={"customer_id": f["cust_pool"].id, "name": "公海建单"},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"
    assert "负责人" in resp.json()["message"]
    # 400 不能是"先写后报错"：库里一条机会都不该留下
    assert db.execute(select(Opportunity.id)).all() == []


def test_api_delete_needs_manager(client, db):
    f = _setup(db)
    opportunity = _make(db, f["sa"], f["cust_a"])
    sales_headers = _auth(_token(client, "sales_a"))

    forbidden = client.delete(
        f"/api/v1/opportunities/{opportunity.id}", headers=sales_headers
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"

    boss_headers = _auth(_token(client, "boss"))
    deleted = client.delete(
        f"/api/v1/opportunities/{opportunity.id}", headers=boss_headers
    )
    assert deleted.status_code == 204
    assert deleted.text == ""

    again = client.delete(
        f"/api/v1/opportunities/{opportunity.id}", headers=boss_headers
    )
    assert again.status_code == 404
    assert again.json()["code"] == "not_found"


def test_api_invalid_values_and_missing_records(client, db):
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))
    reason = create_lost_reason(db, LostReasonCreate(name="价格过高"))
    opportunity = _make(db, f["sa"], f["cust_a"])

    terminal_create = client.post(
        "/api/v1/opportunities",
        headers=headers,
        json={"customer_id": f["cust_a"].id, "name": "建即赢", "stage": "won"},
    )
    assert terminal_create.status_code == 400
    assert terminal_create.json()["code"] == "invalid_value"

    unknown_stage = client.post(
        f"/api/v1/opportunities/{opportunity.id}/stage",
        headers=headers,
        json={"stage": "prospecting"},
    )
    assert unknown_stage.status_code == 400
    assert unknown_stage.json()["code"] == "invalid_value"

    same_stage = client.post(
        f"/api/v1/opportunities/{opportunity.id}/stage",
        headers=headers,
        json={"stage": "contact"},
    )
    assert same_stage.status_code == 400
    assert same_stage.json()["code"] == "invalid_value"

    lost_without_reason = client.post(
        f"/api/v1/opportunities/{opportunity.id}/stage",
        headers=headers,
        json={"stage": "lost"},
    )
    assert lost_without_reason.status_code == 400
    assert lost_without_reason.json()["code"] == "invalid_value"

    lost_bad_reason = client.post(
        f"/api/v1/opportunities/{opportunity.id}/stage",
        headers=headers,
        json={"stage": "lost", "lost_reason_id": 999},
    )
    assert lost_bad_reason.status_code == 404
    assert lost_bad_reason.json()["code"] == "not_found"

    bad_filter = client.get("/api/v1/opportunities?stage=prospecting", headers=headers)
    assert bad_filter.status_code == 400
    assert bad_filter.json()["code"] == "invalid_value"

    bad_sort = client.get("/api/v1/opportunities?sort=customer_id", headers=headers)
    assert bad_sort.status_code == 400
    assert bad_sort.json()["code"] == "invalid_value"

    missing = client.get("/api/v1/opportunities/999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    # 反向验证：合法输单能走通（上面那批 400/404 不是"怎么都不行"）
    lost = client.post(
        f"/api/v1/opportunities/{opportunity.id}/stage",
        headers=headers,
        json={"stage": "lost", "lost_reason_id": reason.id, "lost_note": "预算取消"},
    )
    assert lost.status_code == 200
    assert lost.json()["lost_reason_name"] == "价格过高"
    assert lost.json()["amount"] == "0.00"


def test_api_page_params_are_validated(client, db):
    """分页上/下界必须在参数层变 422。

    ``page=0`` 会算成 ``OFFSET -20``（PG 直接报错 → 500）；``page_size=201`` 说明
    有人绕过了 ``PageParams`` 的上界，一次就能把整张表拉进内存。
    """
    _setup(db)
    headers = _auth(_token(client, "sales_a"))

    for query in ("page=0", "page_size=201"):
        resp = client.get(f"/api/v1/opportunities?{query}", headers=headers)
        assert resp.status_code == 422, query
        assert resp.json()["code"] == "validation_error"
