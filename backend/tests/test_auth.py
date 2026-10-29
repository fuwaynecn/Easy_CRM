from pydantic import SecretStr

from app.modules.auth.schemas import LoginRequest
from app.modules.departments.service import create_department
from app.modules.users.service import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_SALES,
    create_user,
    set_user_status,
)


def _make_user(
    db,
    *,
    username="zhangsan",
    password="Passw0rd!",
    role=ROLE_SALES,
    dept_name="华东销售部",
):
    dept = create_department(db, dept_name)
    return create_user(
        db,
        username=username,
        password=password,
        display_name=username,
        role=role,
        department_id=dept.id,
    )


def _login(client, username="zhangsan", password="Passw0rd!"):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


def _cookie_flags(resp) -> set[str]:
    return {part.strip().lower() for part in resp.headers["set-cookie"].split(";")}


def _refresh_cookie_value(resp) -> str:
    return resp.headers["set-cookie"].split(";")[0].split("=", 1)[1]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_returns_access_token_and_user(client, db):
    _make_user(db)
    resp = _login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["user"]["username"] == "zhangsan"
    assert "password_hash" not in body["user"]


def test_login_sets_http_only_refresh_cookie(client, db):
    _make_user(db)
    resp = _login(client)
    set_cookie = resp.headers["set-cookie"]
    assert "refresh_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


def test_refresh_cookie_is_scoped_and_not_secure_by_default(client, db):
    _make_user(db)
    flags = _cookie_flags(_login(client))
    assert "path=/api/v1/auth" in flags
    assert "secure" not in flags


def test_refresh_cookie_gets_secure_flag_when_configured(client, db, monkeypatch):
    from app.core.config import settings

    _make_user(db)
    monkeypatch.setattr(settings, "cookie_secure", True)
    flags = _cookie_flags(_login(client))
    assert "secure" in flags


def test_access_token_is_not_written_to_cookie(client, db):
    _make_user(db)
    resp = _login(client)
    assert resp.json()["access_token"] not in resp.headers["set-cookie"]


def test_login_wrong_password_returns_401(client, db):
    _make_user(db)
    resp = _login(client, password="wrong")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_unknown_user_looks_identical_to_wrong_password(client, db):
    _make_user(db)
    missing = _login(client, username="nobody")
    wrong = _login(client, password="wrong")
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["message"] == wrong.json()["message"]
    assert missing.json()["code"] == wrong.json()["code"]


def test_disabled_user_login_returns_403(client, db):
    user = _make_user(db)
    set_user_status(db, user.id, "disabled")
    resp = _login(client)
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"
    assert "停用" in resp.json()["message"]


def test_locked_user_login_returns_423_even_with_correct_password(client, db):
    _make_user(db)
    for _ in range(5):
        assert _login(client, password="wrong").status_code == 401
    resp = _login(client)
    assert resp.status_code == 423
    body = resp.json()
    assert body["code"] == "locked"
    assert "锁定" in body["message"]
    assert "分钟" in body["message"]


def test_login_with_oversized_password_never_echoes_plaintext(client, db):
    """口令字段不能被校验失败回显：SecretStr 且不加长度约束。"""
    _make_user(db)
    sentinel = "Pa55-" + "LeakMePlease" * 30
    resp = _login(client, password=sentinel)
    assert sentinel not in resp.text
    assert resp.status_code == 401


def test_login_request_password_field_is_secret_str():
    assert LoginRequest.model_fields["password"].annotation is SecretStr


def test_me_without_token_returns_401(client):
    assert client.get("/api/v1/me").status_code == 401


def test_me_with_token_returns_current_user(client, db):
    _make_user(db)
    token = _login(client).json()["access_token"]
    resp = client.get("/api/v1/me", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json()["username"] == "zhangsan"


def test_me_rejects_non_bearer_scheme(client, db):
    _make_user(db)
    token = _login(client).json()["access_token"]
    resp = client.get("/api/v1/me", headers={"Authorization": f"Basic {token}"})
    assert resp.status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client, db):
    _make_user(db)
    resp = _login(client)
    refresh = _refresh_cookie_value(resp)
    me = client.get("/api/v1/me", headers=_bearer(refresh))
    assert me.status_code == 401


def test_refresh_issues_new_access_token(client, db):
    _make_user(db)
    _login(client)
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_refresh_without_cookie_returns_401(client, db):
    _make_user(db)
    client.cookies.clear()
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_with_garbage_cookie_returns_401(client, db):
    _make_user(db)
    client.cookies.set("refresh_token", "not-a-jwt", path="/api/v1/auth")
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


def test_logout_clears_refresh_cookie(client, db):
    _make_user(db)
    _login(client)
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_disabled_user_token_is_rejected(client, db):
    user = _make_user(db)
    token = _login(client).json()["access_token"]
    set_user_status(db, user.id, "disabled")
    resp = client.get("/api/v1/me", headers=_bearer(token))
    assert resp.status_code == 401


def test_expired_token_is_rejected(client, db, monkeypatch):
    from app.core import security

    _make_user(db)
    token = _login(client).json()["access_token"]
    monkeypatch.setattr(security.settings, "access_token_ttl_minutes", -1)
    expired, _ = security.create_access_token(1)
    resp = client.get("/api/v1/me", headers=_bearer(expired))
    assert resp.status_code == 401
    assert token


def test_departments_list_allows_manager(client, db):
    _make_user(db, username="lisi", role=ROLE_MANAGER, dept_name="华东销售部")
    token = _login(client, username="lisi").json()["access_token"]
    resp = client.get("/api/v1/departments", headers=_bearer(token))
    assert resp.status_code == 200
    assert [d["name"] for d in resp.json()] == ["华东销售部"]


def test_departments_list_forbids_sales(client, db):
    _make_user(db)
    token = _login(client).json()["access_token"]
    resp = client.get("/api/v1/departments", headers=_bearer(token))
    assert resp.status_code == 403


def test_departments_create_allows_admin(client, db):
    _make_user(db, username="admin1", role=ROLE_ADMIN, dept_name="管理部")
    token = _login(client, username="admin1").json()["access_token"]
    resp = client.post(
        "/api/v1/departments", json={"name": "华南销售部"}, headers=_bearer(token)
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "华南销售部"


def test_departments_create_forbids_manager(client, db):
    _make_user(db, username="lisi", role=ROLE_MANAGER, dept_name="华东销售部")
    token = _login(client, username="lisi").json()["access_token"]
    resp = client.post(
        "/api/v1/departments", json={"name": "华南销售部"}, headers=_bearer(token)
    )
    assert resp.status_code == 403
