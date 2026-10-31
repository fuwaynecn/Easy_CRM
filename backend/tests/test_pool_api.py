"""公海接口：列表、领取的 HTTP 契约与权限。"""

from datetime import UTC, datetime, timedelta

from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.pools.rules import update_pool_rules
from app.modules.pools.schemas import PoolRulesUpdate
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"


def _setup(db):
    east = create_department(db, "华东销售部")
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
    sales1 = create_user(
        db,
        username="sales1",
        password=PASSWORD,
        display_name="销售一",
        role=ROLE_SALES,
        department_id=east.id,
    )
    sales2 = create_user(
        db,
        username="sales2",
        password=PASSWORD,
        display_name="销售二",
        role=ROLE_SALES,
        department_id=east.id,
    )
    pool = create_customer(db, admin, CustomerCreate(name="苏州新能源电池科技有限公司"))
    owned = create_customer(
        db,
        admin,
        CustomerCreate(name="杭州智造装备有限公司", owner_id=sales1.id),
    )
    return {
        "admin": admin,
        "boss": boss,
        "sales1": sales1,
        "sales2": sales2,
        "east": east,
        "pool": pool,
        "owned": owned,
    }


def _auth(client, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_pool_list_returns_page_and_days_inactive(client, db):
    ctx = _setup(db)
    ctx["pool"].last_activity_at = datetime.now(UTC) - timedelta(days=5)
    db.commit()
    headers = _auth(client, "sales1")
    response = client.get("/api/v1/pool", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    item = body["items"][0]
    assert item["id"] == ctx["pool"].id
    assert item["name"] == "苏州新能源电池科技有限公司"
    assert "owner_id" not in item
    assert item["days_inactive"] == 5


def test_pool_list_never_null_is_null_days(client, db):
    _setup(db)
    headers = _auth(client, "sales1")
    body = client.get("/api/v1/pool", headers=headers).json()
    assert body["items"][0]["last_activity_at"] is None
    assert body["items"][0]["days_inactive"] is None


def test_pool_list_keyword_and_pagination(client, db):
    ctx = _setup(db)
    for index in range(3):
        create_customer(db, ctx["admin"], CustomerCreate(name=f"公海客户{index}"))
    headers = _auth(client, "sales1")
    body = client.get("/api/v1/pool?page=1&page_size=2", headers=headers).json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    body = client.get("/api/v1/pool?keyword=新能源", headers=headers).json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == ctx["pool"].id


def test_sales_claims_pool_customer(client, db):
    ctx = _setup(db)
    headers = _auth(client, "sales1")
    response = client.post(f"/api/v1/pool/{ctx['pool'].id}/claim", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["owner_id"] == ctx["sales1"].id
    assert body["department_id"] == ctx["east"].id
    assert body["owner_name"] == "销售一"
    # 领取后从公海列表消失
    assert client.get("/api/v1/pool", headers=headers).json()["total"] == 0


def test_claim_second_time_returns_409(client, db):
    ctx = _setup(db)
    first = _auth(client, "sales1")
    # 第二次用主管：sales2 看不到 sales1 名下的客户，会先 403；
    # 主管能看到部门内客户，才能验证“已领取”的 409。
    second = _auth(client, "boss")
    assert (
        client.post(f"/api/v1/pool/{ctx['pool'].id}/claim", headers=first).status_code
        == 200
    )
    response = client.post(f"/api/v1/pool/{ctx['pool'].id}/claim", headers=second)
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_admin_cannot_claim(client, db):
    ctx = _setup(db)
    headers = _auth(client, "admin")
    response = client.post(f"/api/v1/pool/{ctx['pool'].id}/claim", headers=headers)
    assert response.status_code == 403


def test_claim_quota_over_http_returns_409(client, db):
    ctx = _setup(db)
    # sales1 先领取一个，再把上限降到 1，再领第二个
    headers = _auth(client, "sales1")
    assert (
        client.post(f"/api/v1/pool/{ctx['pool'].id}/claim", headers=headers).status_code
        == 200
    )
    update_pool_rules(db, PoolRulesUpdate(max_owned=1))
    another = create_customer(db, ctx["admin"], CustomerCreate(name="第二个公海客户"))
    response = client.post(f"/api/v1/pool/{another.id}/claim", headers=headers)
    assert response.status_code == 409
    assert "上限" in response.json()["message"]


def test_claim_unknown_customer_returns_404(client, db):
    _setup(db)
    headers = _auth(client, "sales1")
    response = client.post("/api/v1/pool/999999/claim", headers=headers)
    assert response.status_code == 404


def test_pool_requires_login(client, db):
    _setup(db)
    assert client.get("/api/v1/pool").status_code == 401
