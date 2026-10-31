"""公海规则配置。

三条硬约束：
1. 没有行时读默认值，GET 不能顺手写库（只读接口有副作用是运维事故）；
2. 只有 id=1 一行，数据库 CHECK 兜底；
3. `recycle_notice_days < inactive_days`，否则“提前 N 天预警”会晚于回收日。
"""

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.core.errors import InvalidValueError
from app.modules.departments.service import create_department
from app.modules.pools.models import PoolRule
from app.modules.pools.rules import (
    DEFAULT_INACTIVE_DAYS,
    DEFAULT_MAX_OWNED,
    DEFAULT_RECYCLE_NOTICE_DAYS,
    load_pool_rules,
    update_pool_rules,
)
from app.modules.pools.schemas import PoolRulesUpdate
from app.modules.users.models import ROLE_ADMIN, ROLE_SALES
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
    sales = create_user(
        db,
        username="sales1",
        password=PASSWORD,
        display_name="销售一",
        role=ROLE_SALES,
        department_id=east.id,
    )
    return {"admin": admin, "sales": sales, "east": east}


def _auth(client, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_load_defaults_when_row_missing(db):
    assert db.get(PoolRule, 1) is None
    rules = load_pool_rules(db)
    assert rules.inactive_days == DEFAULT_INACTIVE_DAYS == 30
    assert rules.max_owned == DEFAULT_MAX_OWNED == 50
    assert rules.recycle_notice_days == DEFAULT_RECYCLE_NOTICE_DAYS == 3
    assert rules.auto_recycle_enabled is True
    assert rules.protect_with_open_deal is True
    assert rules.protect_with_pending_followup is True
    assert rules.notice_threshold_days == 27
    # GET 不写库：默认值不是靠一次隐式 INSERT 变出来的
    assert db.get(PoolRule, 1) is None


def test_update_creates_single_row_and_keeps_unspecified_defaults(db):
    rules = update_pool_rules(db, PoolRulesUpdate(inactive_days=10))
    assert rules.inactive_days == 10
    assert rules.max_owned == 50
    assert rules.recycle_notice_days == 3
    assert rules.notice_threshold_days == 7
    row = db.get(PoolRule, 1)
    assert row is not None
    assert row.inactive_days == 10
    assert row.max_owned == 50


def test_update_merges_existing_row(db):
    update_pool_rules(db, PoolRulesUpdate(max_owned=2))
    rules = update_pool_rules(db, PoolRulesUpdate(inactive_days=12))
    assert rules.max_owned == 2
    assert rules.inactive_days == 12


def test_update_retries_once_when_row_created_concurrently(db):
    """两个并发 PATCH 都读到空表时，后提交者必须重试一次而不是 500。

    真实双线程并发依赖调度顺序，容易假红；这里用 ``before_flush`` 钩子在“本会话
    已决定 INSERT、尚未真正写入”的窗口，让另一个会话抢先插入 id=1 并提交，精确
    制造这个竞态。断言：不抛异常，且按库里当前值重新合并——并发写入者没被本
    PATCH 触及的字段不能被默认值覆盖。
    """
    from app.db.session import SessionLocal

    injected = []

    def _let_other_session_win(_session, _flush_context, _instances):
        if injected:
            return
        injected.append(True)
        with SessionLocal() as other:
            other.add(PoolRule(id=1, inactive_days=25))
            other.commit()

    event.listen(db, "before_flush", _let_other_session_win)
    try:
        rules = update_pool_rules(db, PoolRulesUpdate(max_owned=8))
    finally:
        event.remove(db, "before_flush", _let_other_session_win)

    assert len(injected) == 1  # 只重试一次，没有重试循环
    assert rules.max_owned == 8
    assert rules.inactive_days == 25
    row = db.get(PoolRule, 1)
    assert row.max_owned == 8
    assert row.inactive_days == 25


def test_update_rejects_notice_not_less_than_inactive(db):
    with pytest.raises(InvalidValueError):
        update_pool_rules(
            db, PoolRulesUpdate(inactive_days=10, recycle_notice_days=10)
        )
    assert db.get(PoolRule, 1) is None


def test_update_allows_notice_zero(db):
    rules = update_pool_rules(db, PoolRulesUpdate(recycle_notice_days=0))
    assert rules.recycle_notice_days == 0
    assert rules.notice_threshold_days == 30


def test_update_rejects_explicit_null(db):
    """PATCH 显式传 null 不能变成“把 None 合并进规则”。

    schema 用 ``int | None`` 才能表达“未提供”；如果 service 不拦 ``None``，
    下一行 ``None >= int`` 会直接 500，而不是 400。
    """
    with pytest.raises(InvalidValueError):
        update_pool_rules(db, PoolRulesUpdate(inactive_days=None))
    assert db.get(PoolRule, 1) is None


def test_second_row_rejected_by_database(db):
    db.add(PoolRule(id=2))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_admin_can_read_and_patch_rules(client, db):
    _setup(db)
    headers = _auth(client, "admin")

    response = client.get("/api/v1/admin/pool-rules", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "inactive_days": 30,
        "max_owned": 50,
        "auto_recycle_enabled": True,
        "protect_with_open_deal": True,
        "protect_with_pending_followup": True,
        "recycle_notice_days": 3,
    }
    # GET 必须只读：默认值来自内存常量，不是路由顺手 upsert 出来的一行。
    # 这条断言放在 PATCH 之前，才能把任何“GET 里偷偷建行”的改动钉成红灯。
    assert db.get(PoolRule, 1) is None

    response = client.patch(
        "/api/v1/admin/pool-rules",
        headers=headers,
        json={"inactive_days": 15, "recycle_notice_days": 2, "max_owned": 8},
    )
    assert response.status_code == 200, response.text
    assert response.json()["inactive_days"] == 15
    assert response.json()["recycle_notice_days"] == 2
    assert response.json()["max_owned"] == 8

    response = client.get("/api/v1/admin/pool-rules", headers=headers)
    assert response.json()["inactive_days"] == 15


def test_non_admin_cannot_read_or_patch_rules(client, db):
    _setup(db)
    headers = _auth(client, "sales1")
    assert client.get("/api/v1/admin/pool-rules", headers=headers).status_code == 403
    assert (
        client.patch(
            "/api/v1/admin/pool-rules",
            headers=headers,
            json={"max_owned": 1},
        ).status_code
        == 403
    )


def test_patch_rejects_invalid_range(client, db):
    _setup(db)
    headers = _auth(client, "admin")
    response = client.patch(
        "/api/v1/admin/pool-rules",
        headers=headers,
        json={"inactive_days": 0},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_patch_rejects_notice_not_less_than_inactive_over_http(client, db):
    _setup(db)
    headers = _auth(client, "admin")
    response = client.patch(
        "/api/v1/admin/pool-rules",
        headers=headers,
        json={"inactive_days": 10, "recycle_notice_days": 10},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_value"


def test_patch_explicit_null_returns_400_not_500(client, db):
    _setup(db)
    headers = _auth(client, "admin")
    response = client.patch(
        "/api/v1/admin/pool-rules",
        headers=headers,
        json={"inactive_days": None},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_value"
