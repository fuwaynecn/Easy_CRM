"""客户导入：模板、解析、校验、跳过、commit。

本文件只钉“会静默写坏客户数据”的东西：重名必须跳过而不是覆盖、负责人必须
可解析、manager 不能跨部门、commit 必须单事务。
"""

from io import BytesIO

from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select

from app.modules.audit.models import AuditLog
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.imports.models import ImportJob
from app.modules.imports.service import (
    CUSTOMER_TEMPLATE_COLUMNS,
    ParsedCustomerRow,
    build_customer_template,
    commit_import_job,
    validate_customer_rows,
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
    # 落盘的是上传原文，不是重新序列化的结果（Task 11 commit 要重新解析这份文件）
    assert (tmp_path / "imports" / f"{body['id']}.xlsx").read_bytes() == content
    # 上传阶段只校验，不写客户
    assert db.scalar(select(func.count()).select_from(Customer)) == 0


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


def test_validate_existing_duplicate_stays_out_of_valid(db):
    """重名必须只进 skipped：一旦同时进 valid，commit 就会覆盖已有客户。"""
    f = _setup(db)
    create_customer(db, f["sa"], CustomerCreate(name="甲制造有限公司"))

    result = validate_customer_rows(
        db,
        f["admin"],
        [
            # 尾随空格是有意的：命中说明重名判定走了 normalize_name
            ParsedCustomerRow(
                row=2,
                name="甲制造有限公司 ",
                owner_username="sales_a",
                fields={},
            )
        ],
    )

    assert result.valid == []
    assert len(result.skipped) == 1
    assert result.errors == []


def test_validate_in_file_duplicate_keeps_one_valid(db):
    """文件内同名：第一行有效，第二行跳过，且不算错误。"""
    f = _setup(db)

    result = validate_customer_rows(
        db,
        f["admin"],
        [
            ParsedCustomerRow(
                row=2, name="甲制造有限公司", owner_username="sales_a", fields={}
            ),
            ParsedCustomerRow(
                row=3, name="甲制造有限公司 ", owner_username="sales_a", fields={}
            ),
        ],
    )

    assert len(result.valid) == 1
    assert len(result.skipped) == 1
    assert result.errors == []


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


def test_commit_creates_customers_and_single_audit(client, db):
    f = _setup(db)
    content = _xlsx([
        # `_row` 只认模板里的中文列名；brief 这里写的是 `industry=`/`level=`，会 KeyError
        _row("甲制造有限公司", 行业="制造业", 等级="A"),
        _row("乙贸易有限公司", owner="sales_b"),
    ])
    created = _upload(client, _token(client, "admin"), content).json()

    resp = client.post(
        f"/api/v1/imports/{created['id']}/commit",
        headers=_auth(_token(client, "admin")),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["success_rows"] == 2
    assert body["error_rows"] == 0
    assert body["errors"] == []
    customers = db.execute(
        select(Customer).where(Customer.name.in_(["甲制造有限公司", "乙贸易有限公司"]))
    ).scalars().all()
    assert len(customers) == 2
    assert {customer.owner_id for customer in customers} == {f["sa"].id, f["sb"].id}
    logs = db.execute(
        select(AuditLog).where(AuditLog.entity == "imports", AuditLog.action == "import")
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].entity_id == created["id"]


def test_commit_skips_duplicate_created_after_upload(client, db):
    f = _setup(db)
    content = _xlsx([_row("甲制造有限公司")])
    created = _upload(client, _token(client, "boss"), content).json()
    create_customer(db, f["sa"], CustomerCreate(name="甲制造有限公司"))

    commit_import_job(db, f["boss"], created["id"])

    job = db.get(ImportJob, created["id"])
    assert job.status == "done"
    assert job.success_rows == 0
    assert job.skipped_rows == 1
    assert db.execute(
        select(func.count()).select_from(Customer).where(Customer.name == "甲制造有限公司")
    ).scalar_one() == 1


def test_commit_revalidates_owner_and_writes_nothing(client, db):
    f = _setup(db)
    content = _xlsx([_row("甲制造有限公司")])
    created = _upload(client, _token(client, "boss"), content).json()
    # 上传后负责人被停用：commit 必须重新校验并整体放弃
    from sqlalchemy import update

    from app.modules.users.models import User
    db.execute(update(User).where(User.id == f["sa"].id).values(status="disabled"))
    db.commit()

    resp = client.post(
        f"/api/v1/imports/{created['id']}/commit",
        headers=_auth(_token(client, "boss")),
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "invalid_value"
    assert db.execute(
        select(func.count()).select_from(Customer).where(Customer.name == "甲制造有限公司")
    ).scalar_one() == 0


def test_commit_rejects_failed_or_done_job(client, db):
    f = _setup(db)
    bad = _upload(
        client, _token(client, "boss"), _xlsx([_row("甲制造有限公司", owner="ghost")])
    ).json()
    good = _upload(
        client, _token(client, "boss"), _xlsx([_row("乙贸易有限公司")])
    ).json()
    commit_import_job(db, f["boss"], good["id"])

    failed_resp = client.post(
        f"/api/v1/imports/{bad['id']}/commit", headers=_auth(_token(client, "boss"))
    )
    again_resp = client.post(
        f"/api/v1/imports/{good['id']}/commit", headers=_auth(_token(client, "boss"))
    )

    assert failed_resp.status_code == 400
    assert again_resp.status_code == 400


def test_commit_in_file_duplicate_writes_one(client, db):
    """同一文件内重名：只写一条客户，另一条进 skipped。"""
    _setup(db)
    content = _xlsx([_row("甲制造有限公司"), _row("甲制造有限公司 ")])
    uploaded = _upload(client, _token(client, "admin"), content)
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["skipped_rows"] == 1

    resp = client.post(
        f"/api/v1/imports/{body['id']}/commit",
        headers=_auth(_token(client, "admin")),
    )

    assert resp.status_code == 200, resp.text
    committed = resp.json()
    assert committed["success_rows"] == 1
    assert committed["skipped_rows"] == 1
    assert db.execute(
        select(func.count())
        .select_from(Customer)
        .where(Customer.name == "甲制造有限公司")
    ).scalar_one() == 1
    assert db.execute(select(Customer.name)).scalars().all() == ["甲制造有限公司"]
