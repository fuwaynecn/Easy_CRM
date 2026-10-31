"""客户导入：模板、解析、校验、跳过、commit。

本文件只钉“会静默写坏客户数据”的东西：重名必须跳过而不是覆盖、负责人必须
可解析、manager 不能跨部门、commit 必须单事务。
"""

from io import BytesIO

from openpyxl import Workbook, load_workbook

from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.imports.service import (
    CUSTOMER_TEMPLATE_COLUMNS,
    build_customer_template,
)
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
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


def _xlsx(rows: list[list[str]], headers: list[str] | None = None) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "客户"
    sheet.append(headers or [name for name, _ in CUSTOMER_TEMPLATE_COLUMNS])
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _row(name: str, owner: str = "sales_a", **overrides: str) -> list[str]:
    values = [name, owner, "", "", "", "", "", "", "", "", "", "", "", "", ""]
    positions = {
        "简称": 2, "编码": 3, "行业": 4, "地区": 5, "地址": 6, "网站": 7,
        "规模": 8, "等级": 9, "来源": 10, "采购方式": 11, "预算状态": 12,
        "客户状态": 13, "备注": 14,
    }
    for label, value in overrides.items():
        values[positions[label]] = value
    return values


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
    boss2 = create_user(
        db, username="boss2", password=PASSWORD, display_name="南区主管",
        role=ROLE_MANAGER, department_id=south.id,
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
        "admin": admin, "boss": boss, "boss2": boss2, "sa": sa, "sb": sb,
    }


def _upload(client, token, content, *, kind="customers", filename="customers.xlsx"):
    return client.post(
        "/api/v1/imports",
        headers=_auth(token),
        data={"kind": kind},
        files={
            "file": (
                filename,
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def test_template_has_headers_example_and_guide():
    workbook = load_workbook(BytesIO(build_customer_template()), read_only=True)

    assert workbook.sheetnames == ["客户", "填写说明"]
    rows = list(workbook["客户"].iter_rows(values_only=True))
    assert rows[0] == tuple(name for name, _ in CUSTOMER_TEMPLATE_COLUMNS)
    assert rows[0][0] == "客户名称*"
    assert rows[0][1] == "负责人*"
    assert rows[1][0] == "杭州示例科技有限公司"
    assert rows[1][1] == "sales1"


def test_upload_valid_rows_creates_validated_job(client, db, tmp_path):
    _setup(db)
    content = _xlsx([
        # `_row` 只认模板里的中文列名；brief 这里写的是 `industry=`，会 KeyError
        _row("甲制造有限公司", 行业="制造业"),
        _row("乙贸易有限公司", owner="sales_b"),
    ])

    resp = _upload(client, _token(client, "admin"), content)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "validated"
    assert body["total_rows"] == 2
    assert body["success_rows"] == 0
    assert body["skipped_rows"] == 0
    assert body["error_rows"] == 0
    assert body["errors"] == []
    assert (tmp_path / "imports" / f"{body['id']}.xlsx").exists()


def test_upload_duplicate_name_is_skipped_not_error(client, db):
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲制造有限公司"))
    content = _xlsx([_row("甲制造有限公司")])

    resp = _upload(client, _token(client, "boss"), content)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "validated"
    assert body["skipped_rows"] == 1
    assert body["error_rows"] == 0
    assert body["errors"] == []


def test_upload_unknown_owner_is_row_error(client, db):
    _setup(db)
    content = _xlsx([_row("甲制造有限公司", owner="ghost")])

    resp = _upload(client, _token(client, "boss"), content)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_rows"] == 1
    assert body["errors"][0]["row"] == 2
    assert body["errors"][0]["column"] == "负责人*"
    assert "ghost" in body["errors"][0]["message"]


def test_upload_manager_cannot_use_other_department_owner(client, db):
    _setup(db)
    content = _xlsx([_row("甲制造有限公司", owner="sales_b")])

    resp = _upload(client, _token(client, "boss"), content)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert "本部门" in body["errors"][0]["message"]


def test_upload_rejects_sales_and_unknown_kind(client, db):
    _setup(db)
    content = _xlsx([_row("甲制造有限公司")])

    sales_resp = _upload(client, _token(client, "sales_a"), content)
    kind_resp = client.post(
        "/api/v1/imports",
        headers=_auth(_token(client, "boss")),
        data={"kind": "contacts"},
        files={
            "file": (
                "c.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert sales_resp.status_code == 403
    assert kind_resp.status_code == 400
    assert kind_resp.json()["code"] == "invalid_value"


def test_job_visibility_is_owner_scoped(client, db):
    _setup(db)
    content = _xlsx([_row("甲制造有限公司")])
    created = _upload(client, _token(client, "boss"), content).json()

    other = client.get(
        f"/api/v1/imports/{created['id']}", headers=_auth(_token(client, "boss2"))
    )
    admin = client.get(
        f"/api/v1/imports/{created['id']}", headers=_auth(_token(client, "admin"))
    )

    assert other.status_code == 404
    assert admin.status_code == 200


def test_template_endpoint_requires_manager_and_customers_kind(client, db):
    _setup(db)

    manager = client.get(
        "/api/v1/imports/customers/template", headers=_auth(_token(client, "boss"))
    )
    sales = client.get(
        "/api/v1/imports/customers/template",
        headers=_auth(_token(client, "sales_a")),
    )
    contacts = client.get(
        "/api/v1/imports/contacts/template", headers=_auth(_token(client, "boss"))
    )

    assert manager.status_code == 200
    assert manager.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert sales.status_code == 403
    assert contacts.status_code == 400
    assert contacts.json()["code"] == "invalid_value"
