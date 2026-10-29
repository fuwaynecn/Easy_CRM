"""客户数据作用域：可见性、可写性与"公海只读"硬约束。

夹具刻意造两个部门 + 五个用户 + 五条客户，其中华东部门有**两个**销售：只有一个
销售的部门里，"本部门"与"自己名下"恰好重合，把 sales 的作用域错写成"本部门 +
公海"也能全绿。戊公司（同部门同事 sales_a2 名下）就是那条区分点：

- 甲公司：销售A（华东）名下；
- 戊公司：销售A2（华东）名下——同部门同事的客户，销售A 不该看到；
- 乙公司：销售B（华南）名下；
- 丙公司：公海（owner_id 与 department_id 均为空）；
- 丁公司：公海但残留 department_id=华东（客户被回收后部门字段未清）。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.deps import CurrentUser
from app.core.errors import ForbiddenError, NotFoundError
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
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user


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
        username="sales_a",
        password="Passw0rd!",
        display_name="销售A",
        role=ROLE_SALES,
        department_id=east.id,
    )
    # 同部门的第二个销售：让"本部门"与"自己名下"不再重合（见模块 docstring）
    sa2 = create_user(
        db,
        username="sales_a2",
        password="Passw0rd!",
        display_name="销售A2",
        role=ROLE_SALES,
        department_id=east.id,
    )
    sb = create_user(
        db,
        username="sales_b",
        password="Passw0rd!",
        display_name="销售B",
        role=ROLE_SALES,
        department_id=west.id,
    )
    ca = Customer(name="甲公司", owner_id=sa.id, department_id=east.id)
    ce = Customer(name="戊公司", owner_id=sa2.id, department_id=east.id)
    cb = Customer(name="乙公司", owner_id=sb.id, department_id=west.id)
    cpool = Customer(name="丙公司", owner_id=None, department_id=None)
    cpool_e = Customer(name="丁公司", owner_id=None, department_id=east.id)
    db.add_all([ca, ce, cb, cpool, cpool_e])
    db.commit()
    for obj in (ca, ce, cb, cpool, cpool_e):
        db.refresh(obj)
    return {
        "admin": admin,
        "boss_e": boss_e,
        "sa": sa,
        "sa2": sa2,
        "sb": sb,
        "east": east,
        "west": west,
        "ca": ca,
        "ce": ce,
        "cb": cb,
        "cpool": cpool,
        "cpool_e": cpool_e,
    }


def _users(f):
    return (f["admin"], f["boss_e"], f["sa"], f["sa2"], f["sb"])


def _customers(f):
    return (f["ca"], f["ce"], f["cb"], f["cpool"], f["cpool_e"])


def _visible_names(db, user) -> set[str]:
    """按作用域条件取可见客户名。

    select 出 name 而不是整行：作用域是 WHERE 条件，与查询哪几列无关。
    """
    stmt = select(Customer.name).where(customer_scope(user))
    return set(db.execute(stmt).scalars().all())


def test_sales_sees_only_own_and_pool(db):
    f = _fixture(db)
    # 甲（自己名下）+ 两条公海。戊是同部门同事 sales_a2 的、乙属华南，都不该出现；
    # 夹具里真的存在"同部门其他销售的客户"，这条断言才能区分"按 owner"与"按部门"。
    assert _visible_names(db, f["sa"]) == {"甲公司", "丙公司", "丁公司"}
    assert _visible_names(db, f["sa2"]) == {"戊公司", "丙公司", "丁公司"}
    assert _visible_names(db, f["sb"]) == {"乙公司", "丙公司", "丁公司"}


def test_manager_sees_department_and_pool(db):
    f = _fixture(db)
    # 本部门（甲、戊、丁）+ 公海（丙）；乙公司属华南，东区主管看不到
    assert _visible_names(db, f["boss_e"]) == {"甲公司", "戊公司", "丙公司", "丁公司"}


def test_admin_sees_everything_including_pool(db):
    f = _fixture(db)
    assert _visible_names(db, f["admin"]) == {
        "甲公司",
        "戊公司",
        "乙公司",
        "丙公司",
        "丁公司",
    }


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


def test_missing_customer_cannot_be_written(db):
    """``db.get()`` 取不到记录时是 404，不是 AttributeError 撑出来的 500。"""
    f = _fixture(db)
    assert can_write_customer(None, f["admin"]) is False
    with pytest.raises(NotFoundError) as exc:
        assert_customer_writable(None, f["sa"])
    assert exc.value.status_code == 404


def test_soft_deleted_customer_is_absent_for_writes(db):
    """软删记录对外等同不存在：判可写等于允许更新把它复活。"""
    f = _fixture(db)
    f["ca"].deleted_at = datetime.now(UTC)
    db.commit()

    assert can_write_customer(f["ca"], f["admin"]) is False
    with pytest.raises(NotFoundError) as exc:
        assert_customer_writable(f["ca"], f["sa"])
    assert exc.value.status_code == 404


def test_manager_without_department_sees_pool_only(db):
    """无部门主管仍可能由历史数据/人工改库产生：只见公海，且全不可写。"""
    f = _fixture(db)
    boss_void = create_user(
        db,
        username="boss_void",
        password="Passw0rd!",
        display_name="无部门主管",
        role=ROLE_MANAGER,
        department_id=f["east"].id,
    )
    # 建号路径已禁止无部门主管，这里模拟库里残留的无部门状态。
    db.execute(
        text("UPDATE users SET department_id = NULL WHERE id = :user_id"),
        {"user_id": boss_void.id},
    )
    db.commit()
    db.refresh(boss_void)

    # 无部门主管若退化成"department_id IS NULL 即本部门"，会把这条无部门客户放进来
    orphan = Customer(name="己公司", owner_id=f["sa"].id, department_id=None)
    db.add(orphan)
    db.commit()
    assert _visible_names(db, boss_void) == {"丙公司", "丁公司"}
    writable_ids = (
        db.execute(select(Customer.id).where(customer_writable_scope(boss_void)))
        .scalars()
        .all()
    )
    assert writable_ids == []
    for customer in _customers(f):
        assert can_write_customer(customer, boss_void) is False


def test_unknown_role_fails_closed_to_pool(db):
    """角色被手工改成未知值（如 ``guest``）时只能看公海：fail-open 即全量泄露。"""
    f = _fixture(db)
    f["sa"].role = "guest"
    db.commit()

    # 甲公司在 sa 名下，若回退成"等价 sales"这里会看到它
    assert _visible_names(db, f["sa"]) == {"丙公司", "丁公司"}
    writable_ids = (
        db.execute(select(Customer.id).where(customer_writable_scope(f["sa"])))
        .scalars()
        .all()
    )
    assert writable_ids == []
    assert can_write_customer(f["ca"], f["sa"]) is False


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
