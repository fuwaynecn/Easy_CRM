"""报表 HTTP 层：只测过一遍 HTTP 才成立的东西（401、形状、参数错误）。"""

from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.opportunities.schemas import OpportunityCreate
from app.modules.opportunities.service import create_opportunity
from app.modules.users.models import ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"


def _token(client, username: str) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _setup(db):
    east = create_department(db, "华东销售部")
    sa = create_user(
        db, username="sales_a", password=PASSWORD, display_name="销售A",
        role=ROLE_SALES, department_id=east.id,
    )
    customer = create_customer(db, sa, CustomerCreate(name="甲制造有限公司"))
    create_opportunity(
        db, sa, OpportunityCreate(customer_id=customer.id, name="一期", amount="1000.00")
    )
    return sa


def test_reports_require_authentication(client, db):
    resp = client.get("/api/v1/reports/funnel")

    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_funnel_shape_returns_decimal_strings(client, db):
    _setup(db)
    headers = _auth(_token(client, "sales_a"))

    resp = client.get("/api/v1/reports/funnel", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "as_of" in body
    assert [row["stage"] for row in body["stages"]] == [
        "contact", "needs", "proposal", "negotiation", "won", "lost",
    ]
    assert body["stages"][0]["count"] == 1
    assert body["stages"][0]["amount"] == "1000.00"
