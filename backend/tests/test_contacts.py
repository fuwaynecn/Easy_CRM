"""联系人服务与接口。

权限**完全继承客户**，本模块不定义自己的可见性规则：读取先过
``get_customer``（作用域内才可见，越权 403、不存在 404），写入先过
``assert_customer_writable``（公海只读）。两套规则一旦各写一份必然漂移，
漂移的结果就是权限漏洞，所以这里只做转调，测试也只盯"是否原样转调"。

另有三条容易写错、必须由测试钉住的规则：

- 主联系人在同一客户下**至多一条、可以为 0**：服务层切换旧的置 false，数据库再用
  部分唯一索引兜一层（服务层漏了就直接撞约束，不会静默出现两条主联系人）；
- 改联系人**不更新**客户的 ``last_activity_at``（design §3.3 只有三条同步点，
  联系人不在其中，最容易顺手写成"有交互就刷新"）；
- 删除是软删除，列表与写入都按 ``deleted_at`` 过滤，重复删除是 404 而不是幂等的 200。
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
)
from app.modules.contacts import service as contacts_service
from app.modules.contacts.models import Contact
from app.modules.contacts.schemas import ContactCreate, ContactUpdate
from app.modules.contacts.service import (
    create_contact,
    delete_contact,
    list_contacts,
    update_contact,
)
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.users.service import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_SALES,
    create_user,
)

PASSWORD = "Passw0rd!"


def _setup(db):
    """华东两人（主管 + 销售A + 同部门销售C）、华南一人（销售B）、管理员一个。

    刻意造同部门同事：联系人的作用域必须跟客户一样按 owner/部门判定，
    同部门同事的客户不能因为"同部门"就被放行。
    """
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
        "cust_a": create_customer(db, sa, CustomerCreate(name="甲制造有限公司")),
        "cust_b": create_customer(db, sb, CustomerCreate(name="乙制造有限公司")),
        "pool": create_customer(db, admin, CustomerCreate(name="公海公司")),
    }


def _primaries(db, customer_id: int) -> list[Contact]:
    return list(
        db.execute(
            select(Contact).where(
                Contact.customer_id == customer_id,
                Contact.is_primary.is_(True),
                Contact.deleted_at.is_(None),
            )
        )
        .scalars()
        .all()
    )


# --- 创建与列表 ---------------------------------------------------------


def test_create_and_list_contacts(db):
    f = _setup(db)

    contact = create_contact(
        db,
        f["sa"],
        f["cust_a"].id,
        ContactCreate(name="王工", title="技术负责人", phone="13800000000"),
    )

    assert contact.id is not None
    assert contact.customer_id == f["cust_a"].id
    assert contact.title == "技术负责人"
    contacts = list_contacts(db, f["sa"], f["cust_a"].id)
    assert [c.name for c in contacts] == ["王工"]


def test_create_strips_name_and_rejects_blank(db):
    f = _setup(db)

    created = create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="  王工  "))
    assert created.name == "王工"

    with pytest.raises(InvalidValueError) as exc:
        create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="   "))
    assert exc.value.status_code == 400


def test_list_orders_primary_first(db):
    """详情页把主联系人排在第一位，顺序由后端定，前端不再排一遍。"""
    f = _setup(db)
    create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="王工"))
    primary = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="李工", is_primary=True)
    )
    create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="张工"))

    contacts = list_contacts(db, f["sa"], f["cust_a"].id)

    assert contacts[0].id == primary.id
    assert [c.name for c in contacts] == ["李工", "王工", "张工"]


# --- 模型与数据库约束 ---------------------------------------------------


def test_contact_indexes_exist(db):
    """三条索引必须真实存在：迁移/建表漏建时这里直接红。"""
    names = {index["name"] for index in inspect(db.get_bind()).get_indexes("contacts")}

    assert {
        "ix_contacts_customer_id",
        "ix_contacts_phone",
        "uq_contacts_customer_primary",
    } <= names


def test_primary_unique_index_predicate_covers_soft_delete(db):
    """``compare_metadata`` 不比对部分索引的谓词，只能直接读 pg_indexes。

    谓词少了 ``deleted_at IS NULL`` 时，软删的主联系人会永久占住唯一位置：
    删掉旧主联系人后再设新的会莫名 409。
    """
    indexdef = db.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() "
            "AND tablename = 'contacts' "
            "AND indexname = 'uq_contacts_customer_primary'"
        )
    ).scalar_one()

    assert "deleted_at IS NULL" in indexdef


def test_is_primary_has_server_default(db):
    """裸 INSERT 不给 is_primary：数据库默认值必须兜住，不能撞 NOT NULL。"""
    f = _setup(db)
    db.execute(
        text(
            "INSERT INTO contacts (customer_id, name) "
            "VALUES (:customer_id, '裸插联系人')"
        ),
        {"customer_id": f["cust_a"].id},
    )
    db.commit()

    is_primary = db.execute(
        text("SELECT is_primary FROM contacts WHERE name = '裸插联系人'")
    ).scalar_one()
    assert is_primary is False


# --- 主联系人唯一性：服务层 + 数据库 -------------------------------------


def test_only_one_primary_contact_per_customer(db):
    f = _setup(db)

    first = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", is_primary=True)
    )
    second = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="李工", is_primary=True)
    )

    contacts = list_contacts(db, f["sa"], f["cust_a"].id)
    primaries = [c for c in contacts if c.is_primary]
    assert len(primaries) == 1
    assert primaries[0].name == "李工"

    db.expire_all()
    assert db.get(Contact, first.id).is_primary is False
    assert [c.id for c in _primaries(db, f["cust_a"].id)] == [second.id]


def test_setting_primary_via_update_clears_previous(db):
    """设 B 为主联系人后，A 的 is_primary 变 false，且库里恰好一条 primary。"""
    f = _setup(db)
    a = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", is_primary=True)
    )
    b = create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="李工"))

    update_contact(db, f["sa"], b.id, ContactUpdate(is_primary=True))

    db.expire_all()
    assert db.get(Contact, a.id).is_primary is False
    assert db.get(Contact, b.id).is_primary is True
    assert [c.id for c in _primaries(db, f["cust_a"].id)] == [b.id]


def test_primary_is_scoped_per_customer(db):
    """唯一性按客户判定：两个客户各有一条主联系人互不影响。"""
    f = _setup(db)

    a = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", is_primary=True)
    )
    b = create_contact(
        db, f["sb"], f["cust_b"].id, ContactCreate(name="李工", is_primary=True)
    )

    db.expire_all()
    assert db.get(Contact, a.id).is_primary is True
    assert db.get(Contact, b.id).is_primary is True


def test_database_rejects_two_primary_contacts(db):
    """绕过服务层直插两条 primary 必须撞数据库约束。

    服务层负责"切换旧的"，数据库负责"绝不出现两条"：服务层将来漏掉某条写入
    路径（导入、运维脚本、并发），约束还在，坏数据就进不来。
    """
    f = _setup(db)
    db.add_all(
        [
            Contact(customer_id=f["cust_a"].id, name="王工", is_primary=True),
            Contact(customer_id=f["cust_a"].id, name="李工", is_primary=True),
        ]
    )

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_concurrent_primary_create_returns_409(db, monkeypatch):
    """并发下"先清旧的"失效时，后提交者撞部分唯一索引必须 409，不能 500。"""
    f = _setup(db)
    create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", is_primary=True)
    )
    # 模拟另一事务在我们清理之后、提交之前插入的主联系人。
    monkeypatch.setattr(contacts_service, "_clear_other_primaries", lambda *a, **k: None)

    with pytest.raises(ConflictError) as exc:
        create_contact(
            db, f["sa"], f["cust_a"].id, ContactCreate(name="李工", is_primary=True)
        )

    assert exc.value.status_code == 409
    assert exc.value.message == "该客户已有主联系人，请刷新后重试"
    db.expire_all()
    assert len(_primaries(db, f["cust_a"].id)) == 1


def test_concurrent_primary_update_returns_409(db, monkeypatch):
    f = _setup(db)
    first = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", is_primary=True)
    )
    second = create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="李工"))
    monkeypatch.setattr(contacts_service, "_clear_other_primaries", lambda *a, **k: None)

    with pytest.raises(ConflictError) as exc:
        update_contact(db, f["sa"], second.id, ContactUpdate(is_primary=True))

    assert exc.value.status_code == 409
    db.expire_all()
    assert db.get(Contact, first.id).is_primary is True
    assert db.get(Contact, second.id).is_primary is False


@pytest.mark.parametrize("operation", ["create", "update"])
def test_non_primary_integrity_error_is_not_mapped_to_409(db, operation):
    """错误映射按约束名细分：别的唯一约束原样抛（500），不能伪装成 409 主联系人冲突。

    临时加一条唯一索引制造真实的 psycopg IntegrityError——异常文本里带的是
    ``uq_contacts_wechat_tmp``，与主联系人的约束名无关；若服务层还按"所有
    IntegrityError"映射，这里会拿到 ConflictError 而不是 IntegrityError。
    """
    f = _setup(db)
    db.execute(text("CREATE UNIQUE INDEX uq_contacts_wechat_tmp ON contacts (wechat)"))
    db.commit()
    create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", wechat="wx-1")
    )
    second = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="李工", wechat="wx-2")
    )

    with pytest.raises(IntegrityError) as exc:
        if operation == "create":
            create_contact(
                db, f["sa"], f["cust_a"].id, ContactCreate(name="张工", wechat="wx-1")
            )
        else:
            update_contact(db, f["sa"], second.id, ContactUpdate(wechat="wx-1"))

    assert "uq_contacts_wechat_tmp" in str(exc.value)


def test_soft_deleted_primary_frees_the_slot(db):
    """主联系人删掉后可以再设一个：约束条件带 deleted_at IS NULL。"""
    f = _setup(db)
    old = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", is_primary=True)
    )
    delete_contact(db, f["sa"], old.id)

    new = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="李工", is_primary=True)
    )

    db.expire_all()
    assert db.get(Contact, new.id).is_primary is True


def test_primary_can_be_cleared(db):
    """允许一个客户暂时没有主联系人：新建联系人默认就不是主联系人。"""
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", is_primary=True)
    )

    update_contact(db, f["sa"], contact.id, ContactUpdate(is_primary=False))

    db.expire_all()
    assert db.get(Contact, contact.id).is_primary is False
    assert _primaries(db, f["cust_a"].id) == []


# --- 权限：完全继承客户 -------------------------------------------------


def test_contact_of_foreign_customer_is_forbidden(db):
    f = _setup(db)

    with pytest.raises(ForbiddenError) as exc:
        create_contact(db, f["sb"], f["cust_a"].id, ContactCreate(name="王工"))

    assert exc.value.status_code == 403
    db.expire_all()
    assert list_contacts(db, f["sa"], f["cust_a"].id) == []


def test_same_department_colleague_cannot_touch_contact(db):
    """同部门不等于有权限：销售的作用域按 owner，不按部门。"""
    f = _setup(db)
    create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="王工"))

    with pytest.raises(ForbiddenError):
        list_contacts(db, f["sc"], f["cust_a"].id)


def test_manager_reads_and_writes_own_department_only(db):
    f = _setup(db)

    created = create_contact(
        db, f["boss"], f["cust_a"].id, ContactCreate(name="王工")
    )
    assert created.id is not None
    assert [c.name for c in list_contacts(db, f["boss"], f["cust_a"].id)] == ["王工"]

    with pytest.raises(ForbiddenError):
        list_contacts(db, f["boss"], f["cust_b"].id)
    with pytest.raises(ForbiddenError):
        create_contact(db, f["boss"], f["cust_b"].id, ContactCreate(name="李工"))


def test_pool_customer_contacts_are_read_only(db):
    """公海客户（owner_id IS NULL）的联系人：能读，任何写操作 403。"""
    f = _setup(db)
    contact = create_contact(
        db, f["admin"], f["pool"].id, ContactCreate(name="王工")
    )

    assert [c.name for c in list_contacts(db, f["sa"], f["pool"].id)] == ["王工"]
    assert [c.name for c in list_contacts(db, f["boss"], f["pool"].id)] == ["王工"]

    with pytest.raises(ForbiddenError) as exc:
        create_contact(db, f["sa"], f["pool"].id, ContactCreate(name="李工"))
    assert exc.value.status_code == 403
    with pytest.raises(ForbiddenError) as exc:
        update_contact(db, f["sa"], contact.id, ContactUpdate(phone="13900000000"))
    assert exc.value.status_code == 403
    with pytest.raises(ForbiddenError) as exc:
        delete_contact(db, f["sa"], contact.id)
    assert exc.value.status_code == 403


def test_update_contact_requires_scope(db):
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工")
    )

    updated = update_contact(db, f["sa"], contact.id, ContactUpdate(phone="13900000000"))
    assert updated.phone == "13900000000"

    with pytest.raises(ForbiddenError):
        update_contact(db, f["sb"], contact.id, ContactUpdate(phone="1"))


def test_update_without_is_primary_keeps_the_value(db):
    """PATCH 不传 is_primary 时既不 400 也不改原值。

    ``_validate_required`` 依赖 ``exclude_unset`` 区分"没提交"与"显式 null"；
    改成 ``model_dump()`` 会把未提交的 is_primary 当成 None，每次 PATCH 都 400。
    """
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", is_primary=True)
    )

    updated = update_contact(db, f["sa"], contact.id, ContactUpdate(title="技术负责人"))

    assert updated.title == "技术负责人"
    db.expire_all()
    assert db.get(Contact, contact.id).is_primary is True


def test_delete_contact_requires_scope(db):
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工")
    )

    with pytest.raises(ForbiddenError):
        delete_contact(db, f["sb"], contact.id)

    db.expire_all()
    assert db.get(Contact, contact.id).deleted_at is None


# --- last_activity_at 不受联系人影响（design §3.3） ----------------------


def test_contact_changes_do_not_touch_customer_last_activity(db):
    """§3.3 的同步点只有三条：写 activity、领取、转移。改联系人不在其中。

    顺手写成"有交互就刷新"会让客户永远进不了公海回收，所以这里连创建、编辑、
    删除三类联系人写操作一起钉住。
    """
    f = _setup(db)
    customer = f["cust_a"]
    historical = datetime(2020, 1, 1, tzinfo=UTC)
    customer.last_activity_at = historical
    db.commit()

    created = create_contact(
        db, f["sa"], customer.id, ContactCreate(name="王工", is_primary=True)
    )
    update_contact(
        db, f["sa"], created.id, ContactUpdate(title="技术负责人", is_primary=False)
    )
    delete_contact(db, f["sa"], created.id)

    db.expire_all()
    assert db.get(Customer, customer.id).last_activity_at == historical


# --- 软删除 -------------------------------------------------------------


def test_delete_contact_is_soft(db):
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工")
    )

    delete_contact(db, f["sa"], contact.id)

    assert list_contacts(db, f["sa"], f["cust_a"].id) == []
    db.expire_all()
    row = db.get(Contact, contact.id)
    assert row is not None
    assert row.deleted_at is not None


def test_delete_twice_raises_not_found(db):
    """重复删除是 404：软删后再删等于操作一条不存在的记录，不能返回 200 假装成功。"""
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工")
    )
    delete_contact(db, f["sa"], contact.id)

    with pytest.raises(NotFoundError) as exc:
        delete_contact(db, f["sa"], contact.id)
    assert exc.value.status_code == 404


def test_deleted_contact_cannot_be_updated(db):
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工")
    )
    delete_contact(db, f["sa"], contact.id)

    with pytest.raises(NotFoundError):
        update_contact(db, f["sa"], contact.id, ContactUpdate(phone="13900000000"))


def test_update_missing_contact_raises(db):
    f = _setup(db)

    with pytest.raises(NotFoundError):
        update_contact(db, f["sa"], 9999, ContactUpdate(phone="1"))


def test_contacts_of_missing_customer_raise_not_found(db):
    f = _setup(db)

    with pytest.raises(NotFoundError):
        list_contacts(db, f["sa"], 9999)


def test_contacts_of_deleted_customer_are_not_found(db):
    """客户软删后其联系人一并不可见：父记录没了，子记录不该还能读写。"""
    f = _setup(db)
    create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="王工"))
    f["cust_a"].deleted_at = datetime.now(UTC)
    db.commit()

    with pytest.raises(NotFoundError):
        list_contacts(db, f["sa"], f["cust_a"].id)


# --- 字段校验 -----------------------------------------------------------


def test_invalid_decision_role_is_client_error(db):
    """取值非法是 400（同客户模块的枚举口径），不是 409——409 留给数据冲突。"""
    f = _setup(db)

    with pytest.raises(InvalidValueError) as exc:
        create_contact(
            db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", decision_role="boss")
        )
    assert exc.value.status_code == 400

    contact = create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="李工"))
    with pytest.raises(InvalidValueError):
        update_contact(db, f["sa"], contact.id, ContactUpdate(decision_role="boss"))


def test_decision_roles_accept_documented_values(db):
    f = _setup(db)

    for role in ("decision_maker", "tech", "purchase", "user", "other"):
        created = create_contact(
            db, f["sa"], f["cust_a"].id, ContactCreate(name=role, decision_role=role)
        )
        assert created.decision_role == role


def test_update_rejects_null_on_not_null_column(db):
    """显式传 null 给 ``is_primary`` 是 400，不是撞 NOT NULL 的 500。

    ``ContactUpdate`` 里每个字段都是可空的（PATCH 语义），但 ``is_primary`` 在库里
    是 NOT NULL：``{"is_primary": null}`` 会让 setattr 把 None 写进列里，直接撞
    数据库约束。客户模块用 ``_REQUIRED_FIELDS`` 处理同一类问题，这里保持同口径。
    """
    f = _setup(db)
    contact = create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="王工"))

    with pytest.raises(InvalidValueError) as exc:
        update_contact(db, f["sa"], contact.id, ContactUpdate(is_primary=None))

    assert exc.value.status_code == 400
    db.expire_all()
    assert db.get(Contact, contact.id).is_primary is False


def test_email_must_be_valid_when_provided(db):
    """email 填了必须是合法地址；不填则允许为空（可空的联系邮箱很常见）。"""
    f = _setup(db)

    created = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", email="wang@example.com")
    )
    assert created.email == "wang@example.com"

    with pytest.raises(ValidationError):
        ContactCreate(name="王工", email="not-an-email")


def test_phone_and_wechat_are_not_format_validated(db):
    """国内号码格式太杂（座机分机、微信同号、带区号），硬校验只会误伤真实数据。"""
    f = _setup(db)

    created = create_contact(
        db,
        f["sa"],
        f["cust_a"].id,
        ContactCreate(name="王工", phone="0571-8888 6666 转 801", wechat="wang_gong-01"),
    )

    assert created.phone == "0571-8888 6666 转 801"
    assert created.wechat == "wang_gong-01"


def test_schemas_reject_unknown_fields():
    """联系人不能通过请求体改归属：customer_id / deleted_at 一律 422。"""
    with pytest.raises(ValidationError):
        ContactCreate(name="王工", customer_id=1)
    with pytest.raises(ValidationError):
        ContactUpdate(customer_id=2)
    with pytest.raises(ValidationError):
        ContactUpdate(deleted_at=None)


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
    f = _setup(db)

    resp = client.get(f"/api/v1/customers/{f['cust_a'].id}/contacts")

    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_api_create_list_patch_delete(client, db):
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))

    created = client.post(
        f"/api/v1/customers/{f['cust_a'].id}/contacts",
        headers=headers,
        json={"name": "  王工  ", "title": "技术负责人", "is_primary": True},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "王工"
    assert body["customer_id"] == f["cust_a"].id
    assert body["is_primary"] is True
    contact_id = body["id"]

    listed = client.get(
        f"/api/v1/customers/{f['cust_a'].id}/contacts", headers=headers
    )
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["王工"]

    patched = client.patch(
        f"/api/v1/contacts/{contact_id}", headers=headers, json={"phone": "13900000000"}
    )
    assert patched.status_code == 200
    assert patched.json()["phone"] == "13900000000"

    deleted = client.delete(f"/api/v1/contacts/{contact_id}", headers=headers)
    assert deleted.status_code == 204
    assert deleted.text == ""

    assert (
        client.get(
            f"/api/v1/customers/{f['cust_a'].id}/contacts", headers=headers
        ).json()
        == []
    )


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_api_nested_contact_write_routes_are_removed(client, db, method):
    """写操作只保留 ``/contacts/{id}``：重复的嵌套实现已删除，不得再被调用到。"""
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))
    contact = create_contact(db, f["sa"], f["cust_a"].id, ContactCreate(name="王工"))
    kwargs = {"json": {"title": "采购"}} if method == "patch" else {}

    resp = getattr(client, method)(
        f"/api/v1/customers/{f['cust_a'].id}/contacts/{contact.id}",
        headers=headers,
        **kwargs,
    )

    assert resp.status_code == 404
    db.expire_all()
    row = db.get(Contact, contact.id)
    assert row.title is None
    assert row.deleted_at is None


def test_api_foreign_contact_access_is_403(client, db):
    """销售A 对销售B 的客户：列表、新增、修改、删除一律 403（不是 404、不是空列表）。"""
    f = _setup(db)
    contact = create_contact(db, f["sb"], f["cust_b"].id, ContactCreate(name="李工"))
    headers = _auth(_token(client, "sales_a"))

    listed = client.get(f"/api/v1/customers/{f['cust_b'].id}/contacts", headers=headers)
    assert listed.status_code == 403
    assert listed.json()["code"] == "forbidden"

    created = client.post(
        f"/api/v1/customers/{f['cust_b'].id}/contacts",
        headers=headers,
        json={"name": "王工"},
    )
    assert created.status_code == 403

    patched = client.patch(
        f"/api/v1/contacts/{contact.id}", headers=headers, json={"phone": "139"}
    )
    assert patched.status_code == 403

    deleted = client.delete(f"/api/v1/contacts/{contact.id}", headers=headers)
    assert deleted.status_code == 403

    db.expire_all()
    row = db.get(Contact, contact.id)
    assert row.phone is None and row.deleted_at is None


def test_api_manager_scope_on_contacts(client, db):
    f = _setup(db)
    east_contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工")
    )
    west_contact = create_contact(
        db, f["sb"], f["cust_b"].id, ContactCreate(name="李工")
    )
    headers = _auth(_token(client, "boss"))

    assert (
        client.get(
            f"/api/v1/customers/{f['cust_a'].id}/contacts", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/customers/{f['cust_a'].id}/contacts",
            headers=headers,
            json={"name": "张工"},
        ).status_code
        == 201
    )
    assert (
        client.patch(
            f"/api/v1/contacts/{east_contact.id}", headers=headers, json={"title": "采购"}
        ).status_code
        == 200
    )

    assert (
        client.get(
            f"/api/v1/customers/{f['cust_b'].id}/contacts", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/contacts/{west_contact.id}", headers=headers, json={"phone": "139"}
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/v1/contacts/{west_contact.id}", headers=headers).status_code
        == 403
    )


def test_api_pool_contacts_readable_writes_forbidden(client, db):
    f = _setup(db)
    contact = create_contact(
        db, f["admin"], f["pool"].id, ContactCreate(name="王工")
    )
    headers = _auth(_token(client, "sales_a"))

    listed = client.get(f"/api/v1/customers/{f['pool'].id}/contacts", headers=headers)
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["王工"]

    assert (
        client.post(
            f"/api/v1/customers/{f['pool'].id}/contacts",
            headers=headers,
            json={"name": "李工"},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/contacts/{contact.id}", headers=headers, json={"phone": "139"}
        ).status_code
        == 403
    )
    assert client.delete(f"/api/v1/contacts/{contact.id}", headers=headers).status_code == 403


def test_api_delete_twice_is_404(client, db):
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工")
    )
    headers = _auth(_token(client, "sales_a"))

    assert client.delete(f"/api/v1/contacts/{contact.id}", headers=headers).status_code == 204

    again = client.delete(f"/api/v1/contacts/{contact.id}", headers=headers)
    assert again.status_code == 404
    assert again.json()["code"] == "not_found"


def test_api_missing_customer_and_contact_return_404(client, db):
    _setup(db)
    headers = _auth(_token(client, "admin"))

    missing_customer = client.get("/api/v1/customers/999999/contacts", headers=headers)
    assert missing_customer.status_code == 404
    assert missing_customer.json()["code"] == "not_found"

    missing_contact = client.patch(
        "/api/v1/contacts/999999", headers=headers, json={"phone": "139"}
    )
    assert missing_contact.status_code == 404
    assert missing_contact.json()["code"] == "not_found"


def test_api_invalid_email_and_decision_role(client, db):
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))
    path = f"/api/v1/customers/{f['cust_a'].id}/contacts"

    bad_email = client.post(
        path, headers=headers, json={"name": "王工", "email": "not-an-email"}
    )
    assert bad_email.status_code == 422
    assert bad_email.json()["code"] == "validation_error"

    bad_role = client.post(
        path, headers=headers, json={"name": "王工", "decision_role": "boss"}
    )
    assert bad_role.status_code == 400
    assert bad_role.json()["code"] == "invalid_value"


def test_api_primary_switch_through_http(client, db):
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))
    path = f"/api/v1/customers/{f['cust_a'].id}/contacts"

    first = client.post(path, headers=headers, json={"name": "王工", "is_primary": True})
    second = client.post(path, headers=headers, json={"name": "李工"})
    assert first.status_code == 201 and second.status_code == 201

    patched = client.patch(
        f"/api/v1/contacts/{second.json()['id']}", headers=headers, json={"is_primary": True}
    )
    assert patched.status_code == 200

    db.expire_all()
    assert db.get(Contact, first.json()["id"]).is_primary is False
    assert [c.id for c in _primaries(db, f["cust_a"].id)] == [second.json()["id"]]


def test_api_body_does_not_leak_internals(client, db):
    f = _setup(db)
    headers = _auth(_token(client, "sales_a"))

    resp = client.post(
        f"/api/v1/customers/{f['cust_a'].id}/contacts",
        headers=headers,
        json={"name": "王工", "email": "wang@example.com"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert "deleted_at" not in body
    assert body["email"] == "wang@example.com"
