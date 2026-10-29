"""客户数据作用域：可见性、可写性与"公海只读"硬约束。

夹具刻意造两个部门 + 四个用户 + 三条不同归属的客户，单条数据无法区分
"看不到" 与 "看得到但不该看"：

- 甲公司：销售A（华东）名下；
- 乙公司：销售B（华南）名下；
- 丙公司：公海（owner_id 与 department_id 均为空）；
- 丁公司：公海但残留 department_id=华东（客户被回收后部门字段未清）。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.deps import CurrentUser
from app.core.errors import ForbiddenError
from app.core.security import create_access_token
from app.db.scope import (
    assert_customer_writable,
    can_write_customer,
    customer_query,
    customer_scope,
    customer_writable_scope,
)
from app.db.session import get_db
from app.main import create_app
from app.modules.customers.models import Customer
from app.modules.departments.service import create_department
from app.modules.users.service import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES, create_user


def _fixture(db):
    east = create_department(db, "华东销售部")
    west = create_department(db, "华南销售部")
    admin = create_user(
        db,
        username="admin",
        password="Passw0rd!",
        display_name="管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )
    boss_e = create_user(
        db,
        username="boss_e",
        password="Passw0rd!",
        display_name="东区主管",
        role=ROLE_MANAGER,
        department_id=east.id,
    )
    sa = create_user(
        db,
        username="sa",
        password="Passw0rd!",
        display_name="销售A",
        role=ROLE_SALES,
        department_id=east.id,
    )
    sb = create_user(
        db,
        username="sb",
        password="Passw0rd!",
        display_name="销售B",
        role=ROLE_SALES,
        department_id=west.id,
    )
    ca = Customer(name="甲公司", owner_id=sa.id, department_id=east.id)
    cb = Customer(name="乙公司", owner_id=sb.id, department_id=west.id)
    cpool = Customer(name="丙公司", owner_id=None, department_id=None)
    cpool_e = Customer(name="丁公司", owner_id=None, department_id=east.id)
    db.add_all([ca, cb, cpool, cpool_e])
    db.commit()
    for obj in (ca, cb, cpool, cpool_e):
        db.refresh(obj)
    return {
        "admin": admin,
        "boss_e": boss_e,
        "sa": sa,
        "sb": sb,
        "east": east,
        "west": west,
        "ca": ca,
        "cb": cb,
        "cpool": cpool,
        "cpool_e": cpool_e,
    }


def _users(f):
    return (f["admin"], f["boss_e"], f["sa"], f["sb"])


def _customers(f):
    return (f["ca"], f["cb"], f["cpool"], f["cpool_e"])


def _visible_names(db, user) -> set[str]:
    """按作用域条件取可见客户名。

    select 出 name 而不是整行：作用域是 WHERE 条件，与查询哪几列无关。
    """
    stmt = select(Customer.name).where(customer_scope(user))
    return set(db.execute(stmt).scalars().all())


def test_sales_sees_only_own_and_pool(db):
    f = _fixture(db)
    # 甲（自己名下）+ 两条公海；看不到同部门其他销售的、也看不到别部门的
    assert _visible_names(db, f["sa"]) == {"甲公司", "丙公司", "丁公司"}
    assert _visible_names(db, f["sb"]) == {"乙公司", "丙公司", "丁公司"}


def test_manager_sees_department_and_pool(db):
    f = _fixture(db)
    # 本部门（甲、丁）+ 公海（丙）；乙公司属华南，东区主管看不到
    assert _visible_names(db, f["boss_e"]) == {"甲公司", "丙公司", "丁公司"}
    assert "乙公司" not in _visible_names(db, f["boss_e"])


def test_admin_sees_everything_including_pool(db):
    f = _fixture(db)
    assert _visible_names(db, f["admin"]) == {"甲公司", "乙公司", "丙公司", "丁公司"}


def test_customer_query_is_the_scoped_entry(db):
    """单一入口：customer_query 与作用域条件给出的可见集合必须逐一相等。"""
    f = _fixture(db)
    for user in _users(f):
        rows = db.execute(customer_query(user)).scalars().all()
        assert {c.name for c in rows} == _visible_names(db, user)


def test_customer_query_excludes_soft_deleted(db):
    f = _fixture(db)
    assert "甲公司" in _visible_names(db, f["sa"])
    f["ca"].deleted_at = datetime.now(UTC)
    db.commit()
    names = {c.name for c in db.execute(customer_query(f["sa"])).scalars().all()}
    assert "甲公司" not in names


def test_sales_cannot_write_others_customer(db):
    f = _fixture(db)
    assert can_write_customer(f["ca"], f["sa"]) is True
    assert can_write_customer(f["ca"], f["sb"]) is False
    with pytest.raises(ForbiddenError) as exc:
        assert_customer_writable(f["ca"], f["sb"])
    assert exc.value.status_code == 403


def test_manager_can_write_department_customer_but_not_others(db):
    f = _fixture(db)
    assert can_write_customer(f["ca"], f["boss_e"]) is True
    assert can_write_customer(f["cb"], f["boss_e"]) is False
    with pytest.raises(ForbiddenError) as exc:
        assert_customer_writable(f["cb"], f["boss_e"])
    assert exc.value.status_code == 403


def test_admin_can_write_any_customer(db):
    f = _fixture(db)
    for customer in _customers(f):
        assert can_write_customer(customer, f["admin"]) is True


def test_pool_customer_is_readable_but_not_writable_for_sales(db):
    f = _fixture(db)
    # 读得到：公海是销售可浏览的共享池
    assert "丙公司" in _visible_names(db, f["sa"])
    # 写不了：领取之前不许改
    assert can_write_customer(f["cpool"], f["sa"]) is False
    with pytest.raises(ForbiddenError) as exc:
        assert_customer_writable(f["cpool"], f["sa"])
    assert exc.value.status_code == 403


def test_pool_customer_with_leftover_department_is_read_only_for_manager(db):
    """公海判定只看 owner_id：残留 department_id 不能让它变成可写。"""
    f = _fixture(db)
    assert f["cpool_e"].department_id == f["east"].id
    assert can_write_customer(f["cpool_e"], f["boss_e"]) is False
    with pytest.raises(ForbiddenError):
        assert_customer_writable(f["cpool_e"], f["boss_e"])


def test_writable_scope_matches_can_write_customer(db):
    """SQL 侧可写条件与对象侧判定必须给出同一个集合，防止两套规则漂移。"""
    f = _fixture(db)
    for user in _users(f):
        sql_ids = set(
            db.execute(
                select(Customer.id).where(customer_writable_scope(user))
            )
            .scalars()
            .all()
        )
        obj_ids = {
            c.id for c in _customers(f) if can_write_customer(c, user)
        }
        assert sql_ids == obj_ids


def test_pool_customer_update_returns_403(db):
    """越权是 403，不是 404、也不是空结果：前端必须能区分"没权限"与"没数据"。"""
    f = _fixture(db)
    app = create_app()

    def _override_get_db():
        return db

    @app.patch("/__test/customers/{customer_id}")
    def _update_customer(customer_id: int, actor: CurrentUser) -> dict[str, str]:
        customer = db.get(Customer, customer_id)
        assert_customer_writable(customer, actor)
        return {"status": "updated"}

    app.dependency_overrides[get_db] = _override_get_db
    token, _ = create_access_token(f["sa"].id)
    with TestClient(app) as client:
        response = client.patch(
            f"/__test/customers/{f['cpool'].id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
