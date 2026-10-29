"""用户管理接口（仅管理员）。

全部断言走真实 HTTP：权限、错误体、口令通路都以 client 请求为准，
避免"service 层测过了、路由接线错了"这类静默失败。
"""

import pytest

from app.core.security import verify_password
from app.modules.departments.service import create_department
from app.modules.users.models import User
from app.modules.users.service import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_SALES,
    create_user,
    get_user_by_username,
)

PASSWORD = "Passw0rd!"
NEW_PASSWORD = "NewPass!23"


def _token(client, username: str, password: str = PASSWORD) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(db, *, username: str = "admin") -> User:
    return create_user(
        db,
        username=username,
        password=PASSWORD,
        display_name="管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )


def _sales(db, *, username: str = "sales1", display_name: str = "销售一") -> User:
    dept = create_department(db, "华东销售部")
    return create_user(
        db,
        username=username,
        password=PASSWORD,
        display_name=display_name,
        role=ROLE_SALES,
        department_id=dept.id,
    )


def _bootstrap(db) -> int:
    """管理员 + 主管 + 销售各一，返回销售部 id。"""
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="admin",
        password=PASSWORD,
        display_name="管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )
    create_user(
        db,
        username="boss",
        password=PASSWORD,
        display_name="主管",
        role=ROLE_MANAGER,
        department_id=dept.id,
    )
    create_user(
        db,
        username="sales1",
        password=PASSWORD,
        display_name="销售一",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    return dept.id


def _new_user_payload(**overrides) -> dict:
    payload = {
        "username": "sales2",
        "password": PASSWORD,
        "display_name": "销售二",
        "role": ROLE_SALES,
        "department_id": None,
    }
    payload.update(overrides)
    return payload


def _user_id(db, username: str) -> int:
    user = get_user_by_username(db, username)
    assert user is not None, f"查询不到用户 {username}"
    return user.id


# --- 列表 ---------------------------------------------------------------


def test_admin_can_list_users(client, db):
    _bootstrap(db)
    headers = _auth(_token(client, "admin"))

    resp = client.get("/api/v1/users", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert {u["username"] for u in body["items"]} == {"admin", "boss", "sales1"}


def test_list_users_filters_by_keyword(client, db):
    _bootstrap(db)
    headers = _auth(_token(client, "admin"))

    by_username = client.get(
        "/api/v1/users", headers=headers, params={"keyword": "BOSS"}
    ).json()
    by_display_name = client.get(
        "/api/v1/users", headers=headers, params={"keyword": "销售"}
    ).json()

    assert [u["username"] for u in by_username["items"]] == ["boss"]
    assert [u["username"] for u in by_display_name["items"]] == ["sales1"]
    assert by_display_name["total"] == 1


def test_list_users_paginates(client, db):
    _bootstrap(db)
    headers = _auth(_token(client, "admin"))

    first = client.get(
        "/api/v1/users", headers=headers, params={"page": 1, "page_size": 2}
    ).json()
    second = client.get(
        "/api/v1/users", headers=headers, params={"page": 2, "page_size": 2}
    ).json()

    assert [u["username"] for u in first["items"]] == ["admin", "boss"]
    assert [u["username"] for u in second["items"]] == ["sales1"]
    assert first["total"] == second["total"] == 3


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": 999_999_999}, {"page_size": 0}, {"page_size": 201}],
    ids=["page-zero", "page-too-large", "page-size-zero", "page-size-too-large"],
)
def test_list_users_rejects_invalid_paging(client, db, params):
    _bootstrap(db)
    headers = _auth(_token(client, "admin"))

    resp = client.get("/api/v1/users", headers=headers, params=params)

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_list_users_requires_authentication(client, db):
    _bootstrap(db)
    resp = client.get("/api/v1/users")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


# --- 新建 ---------------------------------------------------------------


def test_admin_can_create_user(client, db):
    dept_id = _bootstrap(db)
    headers = _auth(_token(client, "admin"))

    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json=_new_user_payload(
            username="  sales2  ", display_name=" 销售二 ", department_id=dept_id
        ),
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] > 0
    assert body["username"] == "sales2"
    assert body["display_name"] == "销售二"
    assert body["role"] == ROLE_SALES
    assert body["department_id"] == dept_id
    assert body["status"] == "active"


def test_create_rejects_internal_fields(client, db):
    """创建 schema 也必须拒绝未知字段：password_hash 被静默丢弃会让人以为写入了。"""
    _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json=_new_user_payload(password_hash="deadbeef"),
    )

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"
    assert get_user_by_username(db, "sales2") is None


def test_created_user_can_login_with_real_password(client, db):
    """口令必须按明文原样哈希：brief 的 router 会把 SecretStr 掩码写进库里。"""
    _bootstrap(db)
    headers = _auth(_token(client, "admin"))
    resp = client.post(
        "/api/v1/users", headers=headers, json=_new_user_payload(username="sales2")
    )
    assert resp.status_code == 201

    stored = get_user_by_username(db, "sales2")
    assert stored is not None
    assert verify_password(PASSWORD, stored.password_hash)
    assert not verify_password("**********", stored.password_hash)

    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login", json={"username": "sales2", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text

    masked = client.post(
        "/api/v1/auth/login", json={"username": "sales2", "password": "**********"}
    )
    assert masked.status_code == 401


def test_username_is_stripped_on_create_and_login(client, db):
    """建号 strip、查询 strip：带空格建出来的账号必须两端都能登进去。"""
    _admin(db)
    headers = _auth(_token(client, "admin"))
    resp = client.post(
        "/api/v1/users", headers=headers, json=_new_user_payload(username="  spacy  ")
    )
    assert resp.status_code == 201
    assert resp.json()["username"] == "spacy"
    assert get_user_by_username(db, " spacy ") is not None

    client.cookies.clear()
    for username in ("spacy", "  spacy  "):
        login = client.post(
            "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert login.status_code == 200, f"{username!r} 登录失败：{login.text}"


@pytest.mark.parametrize(
    ("field", "value"),
    [("username", "  a  "), ("display_name", "   ")],
    ids=["username-after-strip", "display-name-blank"],
)
def test_create_validates_after_strip(client, db, field, value):
    """strip 之后的最终值才参与非空/长度校验，不能存出 1 字符登录名或空显示名。"""
    _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json=_new_user_payload(**{field: value}),
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"
    db.expire_all()
    assert get_user_by_username(db, "sales2") is None
    assert get_user_by_username(db, "a") is None


def test_weak_password_returns_400_without_echo(client, db):
    """口令过短必须是 400 业务错误（不是 422），且响应体不能带明文。"""
    _admin(db)
    headers = _auth(_token(client, "admin"))
    plain = "Ab123"

    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json=_new_user_payload(username="shorty", password=plain),
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "weak_password"
    assert "密码" in body["message"]
    assert plain not in resp.text
    assert get_user_by_username(db, "shorty") is None


def test_duplicate_username_returns_409(client, db):
    _bootstrap(db)
    headers = _auth(_token(client, "admin"))

    resp = client.post(
        "/api/v1/users", headers=headers, json=_new_user_payload(username="sales1")
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"
    assert "已存在" in resp.json()["message"]


def test_unknown_role_returns_400(client, db):
    _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.post(
        "/api/v1/users", headers=headers, json=_new_user_payload(role="owner")
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"
    assert "角色" in resp.json()["message"]
    assert get_user_by_username(db, "sales2") is None


def test_unknown_department_returns_404(client, db):
    """坏 department_id 必须是 404 业务错误，不能撞外键抛 500。"""
    _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json=_new_user_payload(department_id=999_999),
    )

    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    assert "部门" in resp.json()["message"]
    assert get_user_by_username(db, "sales2") is None


def test_user_payloads_never_leak_internal_fields(client, db):
    _bootstrap(db)
    headers = _auth(_token(client, "admin"))
    created = client.post("/api/v1/users", headers=headers, json=_new_user_payload())
    listed = client.get("/api/v1/users", headers=headers)
    patched = client.patch(
        f"/api/v1/users/{_user_id(db, 'sales2')}",
        headers=headers,
        json={"display_name": "销售二号"},
    )

    for resp in (created, listed, patched):
        assert resp.status_code in (200, 201), resp.text
        lowered = resp.text.lower()
        assert "password" not in lowered
        assert "hash" not in lowered
        assert "failed_login_count" not in resp.text
        assert "locked_until" not in resp.text


# --- 修改 ---------------------------------------------------------------


def test_admin_can_update_user_fields(client, db):
    _admin(db)
    target = _sales(db)
    other_dept = create_department(db, "华南销售部")
    headers = _auth(_token(client, "admin"))

    resp = client.patch(
        f"/api/v1/users/{target.id}",
        headers=headers,
        json={
            "display_name": " 销售一号 ",
            "role": ROLE_MANAGER,
            "department_id": other_dept.id,
            "status": "disabled",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == target.id
    assert body["display_name"] == "销售一号"
    assert body["role"] == ROLE_MANAGER
    assert body["department_id"] == other_dept.id
    assert body["status"] == "disabled"

    db.refresh(target)
    assert target.role == ROLE_MANAGER
    assert target.status == "disabled"


def test_update_without_a_field_keeps_it(client, db):
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))
    # 在请求前把期望值抄成普通整数：若只和同一个 ORM 对象比较，
    # 请求共用的 Session 会把 target 一起改掉，断言就恒真了。
    assert target.department_id is not None
    expected_department_id = target.department_id

    resp = client.patch(
        f"/api/v1/users/{target.id}", headers=headers, json={"display_name": "新名字"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "新名字"
    assert body["role"] == ROLE_SALES
    assert body["status"] == "active"
    assert body["department_id"] == expected_department_id


def test_patch_rejects_password_hash_field(client, db):
    """未知字段必须 422：默认 extra="ignore" 会静默丢弃并返回 200。"""
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))
    original_hash = target.password_hash

    resp = client.patch(
        f"/api/v1/users/{target.id}",
        headers=headers,
        json={"display_name": "改名", "password_hash": "deadbeef"},
    )

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"
    db.refresh(target)
    assert target.password_hash == original_hash
    assert target.display_name == "销售一"


def test_update_user_can_clear_department(client, db):
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))

    resp = client.patch(
        f"/api/v1/users/{target.id}", headers=headers, json={"department_id": None}
    )

    assert resp.status_code == 200
    assert resp.json()["department_id"] is None


def test_disabled_user_cannot_login_after_update(client, db):
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))
    assert (
        client.patch(
            f"/api/v1/users/{target.id}", headers=headers, json={"status": "disabled"}
        ).status_code
        == 200
    )

    client.cookies.clear()
    resp = client.post(
        "/api/v1/auth/login", json={"username": "sales1", "password": PASSWORD}
    )

    assert resp.status_code == 403
    assert "停用" in resp.json()["message"]


@pytest.mark.parametrize(
    "payload", [{"role": "owner"}, {"status": "deleted"}], ids=["role", "status"]
)
def test_update_rejects_unknown_enum_values(client, db, payload):
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))

    resp = client.patch(
        f"/api/v1/users/{target.id}", headers=headers, json=payload
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"
    db.refresh(target)
    assert target.role == ROLE_SALES
    assert target.status == "active"


def test_update_with_unknown_department_returns_404(client, db):
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))

    resp = client.patch(
        f"/api/v1/users/{target.id}",
        headers=headers,
        json={"department_id": 999_999},
    )

    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    assert resp.json()["message"] == "部门不存在"
    db.refresh(target)
    assert target.department_id is not None


def test_update_missing_user_returns_404(client, db):
    _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.patch(
        "/api/v1/users/999999", headers=headers, json={"display_name": "幽灵"}
    )

    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    # 走业务错误而不是框架 404：框架文案是"资源不存在"
    assert resp.json()["message"] == "用户不存在"


def test_admin_cannot_disable_self(client, db):
    """唯一的 admin 停用自己就再也没人能管系统了。"""
    admin = _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.patch(
        f"/api/v1/users/{admin.id}", headers=headers, json={"status": "disabled"}
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "forbidden"
    assert "自己" in body["message"]
    db.refresh(admin)
    assert admin.status == "active"


def test_admin_cannot_strip_own_admin_role(client, db):
    """降级自己与停用自己是同一条自锁死通道。"""
    admin = _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.patch(
        f"/api/v1/users/{admin.id}", headers=headers, json={"role": ROLE_MANAGER}
    )

    assert resp.status_code == 403
    assert "自己" in resp.json()["message"]
    db.refresh(admin)
    assert admin.role == ROLE_ADMIN


@pytest.mark.parametrize(
    "payload", [{"role": "owner"}, {"status": "deleted"}], ids=["role", "status"]
)
def test_admin_self_patch_with_invalid_enum_returns_400(client, db, payload):
    """非法枚举值必须先报 400，不能被自锁死守卫抢答成 403。"""
    admin = _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.patch(f"/api/v1/users/{admin.id}", headers=headers, json=payload)

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_value"
    db.refresh(admin)
    assert admin.role == ROLE_ADMIN
    assert admin.status == "active"


def test_admin_can_disable_another_admin(client, db):
    """自锁死拦截只针对本人，不能把"停用别的管理员"一起拦掉。"""
    _admin(db)
    other = create_user(
        db,
        username="admin2",
        password=PASSWORD,
        display_name="管理员二",
        role=ROLE_ADMIN,
        department_id=None,
    )
    headers = _auth(_token(client, "admin"))

    resp = client.patch(
        f"/api/v1/users/{other.id}", headers=headers, json={"status": "disabled"}
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


# --- 重置口令 -----------------------------------------------------------


def test_reset_password_returns_204_and_new_password_works(client, db):
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))

    resp = client.post(
        f"/api/v1/users/{target.id}/reset-password",
        headers=headers,
        json={"new_password": NEW_PASSWORD},
    )

    assert resp.status_code == 204
    assert resp.text == ""
    db.refresh(target)
    assert verify_password(NEW_PASSWORD, target.password_hash)
    assert not verify_password("**********", target.password_hash)

    client.cookies.clear()
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "sales1", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "sales1", "password": NEW_PASSWORD}
        ).status_code
        == 200
    )


def test_admin_reset_unlocks_locked_account_immediately(client, db):
    """锁定 → 管理员重置 → 新口令立刻登录 200：整条 HTTP 链路必须闭合。"""
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))

    client.cookies.clear()
    for _ in range(5):
        failed = client.post(
            "/api/v1/auth/login", json={"username": "sales1", "password": "wrong"}
        )
        assert failed.status_code == 401
    db.refresh(target)
    assert target.locked_until is not None
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "sales1", "password": PASSWORD}
        ).status_code
        == 423
    )

    reset = client.post(
        f"/api/v1/users/{target.id}/reset-password",
        headers=headers,
        json={"new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 204

    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login", json={"username": "sales1", "password": NEW_PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.json()["access_token"]


def test_reset_password_does_not_reenable_disabled_account(client, db):
    """重置只清失败计数与锁定，不解除停用；恢复账号是独立的管理动作。"""
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))
    assert (
        client.patch(
            f"/api/v1/users/{target.id}", headers=headers, json={"status": "disabled"}
        ).status_code
        == 200
    )

    reset = client.post(
        f"/api/v1/users/{target.id}/reset-password",
        headers=headers,
        json={"new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 204

    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login", json={"username": "sales1", "password": NEW_PASSWORD}
    )
    assert login.status_code == 403
    assert "停用" in login.json()["message"]
    db.refresh(target)
    assert target.status == "disabled"


def test_reset_password_rejects_short_password(client, db):
    _admin(db)
    target = _sales(db)
    headers = _auth(_token(client, "admin"))
    old_hash = target.password_hash

    resp = client.post(
        f"/api/v1/users/{target.id}/reset-password",
        headers=headers,
        json={"new_password": "Ab123"},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "weak_password"
    assert "Ab123" not in resp.text
    db.refresh(target)
    assert target.password_hash == old_hash


def test_reset_password_missing_user_returns_404(client, db):
    _admin(db)
    headers = _auth(_token(client, "admin"))

    resp = client.post(
        "/api/v1/users/999999/reset-password",
        headers=headers,
        json={"new_password": NEW_PASSWORD},
    )

    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    assert resp.json()["message"] == "用户不存在"


# --- 权限（表驱动） -----------------------------------------------------

ADMIN_ONLY_ENDPOINTS = [
    pytest.param("GET", "/api/v1/users", None, id="list"),
    pytest.param("POST", "/api/v1/users", "create", id="create"),
    pytest.param("PATCH", "/api/v1/users/{user_id}", "update", id="update"),
    pytest.param(
        "POST", "/api/v1/users/{user_id}/reset-password", "reset", id="reset"
    ),
]


def _request_payload(kind: str | None, target_id: int) -> dict:
    if kind == "create":
        return _new_user_payload(username="sneaky")
    if kind == "update":
        return {"display_name": "越权改名", "role": ROLE_MANAGER, "status": "disabled"}
    if kind == "reset":
        return {"new_password": NEW_PASSWORD}
    return {}


@pytest.mark.parametrize(("method", "path", "kind"), ADMIN_ONLY_ENDPOINTS)
@pytest.mark.parametrize("actor", ["sales1", "boss"])
def test_non_admin_gets_403_with_unified_error_body(
    client, db, actor, method, path, kind
):
    """sales 与 manager 对全部管理员接口一律 403，且错误体是统一的 {code,...}。"""
    _bootstrap(db)
    target_id = _user_id(db, "sales1")
    headers = _auth(_token(client, actor))
    body = _request_payload(kind, target_id)

    resp = client.request(
        method,
        path.format(user_id=target_id),
        headers=headers,
        json=body or None,
    )

    assert resp.status_code == 403, resp.text
    error = resp.json()
    assert error["code"] == "forbidden"
    assert isinstance(error["message"], str)
    assert error["message"]
    assert "detail" in error

    # 越权请求不得产生任何副作用
    db.expire_all()
    victim = get_user_by_username(db, "sales1")
    assert victim is not None
    assert victim.display_name == "销售一"
    assert victim.role == ROLE_SALES
    assert victim.status == "active"
    assert verify_password(PASSWORD, victim.password_hash)
    assert get_user_by_username(db, "sneaky") is None
