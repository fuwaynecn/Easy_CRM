from datetime import UTC, datetime, timedelta

import jwt
from pydantic import SecretStr
from sqlalchemy import text

from app.core import security
from app.core.config import settings
from app.modules.auth.schemas import LoginRequest
from app.modules.departments.service import create_department
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import (
    create_user,
    update_user,
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


def _claims(token: str, *, verify_exp: bool = True) -> dict:
    """解出令牌 claims：断言具体属性（sub/type），不要只断言"token 非空"。"""
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": verify_exp},
    )


def _use_refresh_cookie(client, value: str) -> None:
    """先清空 cookie jar 再放目标值：同名 cookie 残留两条时，服务端取哪条不可控。"""
    client.cookies.clear()
    client.cookies.set("refresh_token", value, path="/api/v1/auth")


def test_login_returns_access_token_and_user(client, db):
    user = _make_user(db)
    resp = _login(client)
    assert resp.status_code == 200
    body = resp.json()
    claims = _claims(body["access_token"])
    assert claims["sub"] == str(user.id)
    assert claims["type"] == "access"
    assert body["expires_in"] == settings.access_token_ttl_minutes * 60
    assert body["user"]["id"] == user.id
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
    update_user(db, user.id, {"status": "disabled"})
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


def test_login_succeeds_after_lock_expires_and_resets_counter(client, db):
    """锁定到期后必须能正常登录，且失败计数清零（否则用户永久进不来）。"""
    user = _make_user(db)
    for _ in range(5):
        assert _login(client, password="wrong").status_code == 401

    # 不用 sleep：直接把锁定到期时间拨到过去。同时留一个非零计数，
    # 这样"登录成功后计数为 0"才是真的验证了清零，而不是验证本来就没计数。
    db.refresh(user)
    assert user.locked_until is not None
    user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    user.failed_login_count = 4
    db.commit()

    resp = _login(client)
    assert resp.status_code == 200
    db.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


def test_login_with_oversized_password_never_echoes_plaintext(client, db):
    """口令字段不能被校验失败回显：SecretStr 且不加长度约束。"""
    _make_user(db)
    sentinel = "Pa55-" + "LeakMePlease" * 30
    resp = _login(client, password=sentinel)
    assert sentinel not in resp.text
    assert resp.status_code == 401


def test_validation_error_drops_input_and_never_echoes_password(client):
    """422 一律不带 detail[].input：非字符串口令也不能在响应里露面。"""
    resp = client.post(
        "/api/v1/auth/login", json={"username": "zhangsan", "password": 12345}
    )
    assert resp.status_code == 422
    assert "12345" not in resp.text
    body = resp.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["detail"], list)
    assert body["detail"]
    assert all("input" not in item for item in body["detail"])
    assert all("loc" in item and "msg" in item for item in body["detail"])


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
    user = _make_user(db)
    _login(client)
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    claims = _claims(resp.json()["access_token"])
    assert claims["sub"] == str(user.id)
    assert claims["type"] == "access"


def test_refresh_without_cookie_returns_401(client, db):
    _make_user(db)
    client.cookies.clear()
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_without_cookie_still_clears_stale_cookie(client, db):
    _make_user(db)
    client.cookies.clear()
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert "max-age=0" in resp.headers["set-cookie"].lower()


def test_refresh_with_garbage_cookie_returns_401(client, db):
    _make_user(db)
    _use_refresh_cookie(client, "not-a-jwt")
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


def test_refresh_failure_clears_refresh_cookie(client, db):
    """失败响应必须真的带清 cookie 的指令：只断言 401 会漏掉"看起来清了其实没清"。"""
    _make_user(db)
    _use_refresh_cookie(client, "not-a-jwt")
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    set_cookie = resp.headers["set-cookie"].lower()
    assert "refresh_token=" in set_cookie
    assert "max-age=0" in set_cookie
    assert "path=/api/v1/auth" in set_cookie


def test_disabled_user_refresh_is_rejected(client, db):
    user = _make_user(db)
    refresh = _refresh_cookie_value(_login(client))
    update_user(db, user.id, {"status": "disabled"})
    _use_refresh_cookie(client, refresh)
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_access_token_cannot_be_used_as_refresh_token(client, db):
    _make_user(db)
    access = _login(client).json()["access_token"]
    # 先证明这个 token 本身有效，否则下面的 401 可能只是因为它坏了
    assert client.get("/api/v1/me", headers=_bearer(access)).status_code == 200
    _use_refresh_cookie(client, access)
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"
    assert "令牌类型" in resp.json()["message"]


def test_logout_clears_refresh_cookie(client, db):
    _make_user(db)
    _login(client)
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_disabled_user_token_is_rejected(client, db):
    user = _make_user(db)
    token = _login(client).json()["access_token"]
    update_user(db, user.id, {"status": "disabled"})
    resp = client.get("/api/v1/me", headers=_bearer(token))
    assert resp.status_code == 401


def test_expired_token_is_rejected(client, db, monkeypatch):
    user = _make_user(db)
    _login(client)
    monkeypatch.setattr(security.settings, "access_token_ttl_minutes", -1)
    expired, _ = security.create_access_token(user.id)
    claims = _claims(expired, verify_exp=False)
    assert claims["sub"] == str(user.id)
    assert claims["type"] == "access"
    resp = client.get("/api/v1/me", headers=_bearer(expired))
    assert resp.status_code == 401


def test_unknown_role_is_rejected_by_auth_dependency(client, db):
    """数据库里的角色被手工改成未知值时，已签发的 token 也必须立刻失效（403）。

    作用域层虽然 fail-closed，但让未知角色通过鉴权会把"账号异常"伪装成"没有数据"，
    排查时看不出根因。鉴权入口直接拒绝。
    """
    user = _make_user(db)
    token = _login(client).json()["access_token"]
    db.execute(
        text("UPDATE users SET role = 'guest' WHERE id = :user_id"),
        {"user_id": user.id},
    )
    db.commit()
    # 测试与接口共用一个 Session：裸 SQL 改了库，身份映射里还是旧 role，必须过期
    db.expire_all()

    resp = client.get("/api/v1/me", headers=_bearer(token))

    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"
    assert resp.json()["message"] == "账号角色异常，请联系管理员"


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


def test_departments_create_duplicate_name_returns_409(client, db):
    """重名走真实 HTTP：409 + 统一错误体（原先只有 service 层的间接证据）。"""
    _make_user(db, username="admin1", role=ROLE_ADMIN, dept_name="华东销售部")
    token = _login(client, username="admin1").json()["access_token"]
    resp = client.post(
        "/api/v1/departments", json={"name": "华东销售部"}, headers=_bearer(token)
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"
    assert "已存在" in resp.json()["message"]
