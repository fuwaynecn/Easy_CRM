from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import get_db
from app.main import create_app
from app.modules.departments.service import create_department
from app.modules.users.models import ROLE_ADMIN
from app.modules.users.service import create_user


def _app_with_db(db):
    """局部 app 必须 override get_db，否则测试路由一旦触库会打到开发库。"""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return app


def test_local_app_routes_share_the_fixture_session(db):
    app = _app_with_db(db)

    @app.get("/__db")
    def _db_route(session: Annotated[Session, Depends(get_db)]) -> dict:
        return {"same_session": session is db, "one": session.scalar(text("SELECT 1"))}

    with TestClient(app) as client:
        resp = client.get("/__db")
    assert resp.json() == {"same_session": True, "one": 1}


def test_unknown_path_returns_unified_404(client):
    resp = client.get("/api/v1/__missing__")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "not_found"
    assert body["message"] == "资源不存在"
    assert isinstance(body["detail"], str)


def test_method_not_allowed_returns_unified_405(client):
    resp = client.post("/health")
    assert resp.status_code == 405
    body = resp.json()
    assert body["code"] == "method_not_allowed"
    assert body["message"] == "请求方法不被允许"
    assert isinstance(body["detail"], str)
    assert resp.headers["allow"] == "GET"


def test_validation_error_returns_unified_422(db):
    app = _app_with_db(db)

    @app.get("/__paged")
    def _paged(page: int):
        return {"page": page}

    with TestClient(app) as client:
        resp = client.get("/__paged", params={"page": "abc"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "请求参数校验失败"
    assert isinstance(body["detail"], list)
    assert body["detail"][0]["loc"] == ["query", "page"]


def test_validation_error_with_exception_in_ctx_is_serializable(db):
    app = _app_with_db(db)

    class Payload(BaseModel):
        x: int

        @model_validator(mode="after")
        def _reject(self):
            raise ValueError("非法值")

    @app.post("/__payload")
    def _payload(payload: Payload):
        return payload.model_dump()

    with TestClient(app) as client:
        resp = client.post("/__payload", json={"x": 1})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert body["detail"][0]["ctx"]["error"] == {}


def test_app_error_detail_with_datetime_and_exception_is_serializable(db):
    app = _app_with_db(db)

    @app.get("/__app_error")
    def _boom():
        raise AppError(
            "内部错误", detail={"at": datetime.now(UTC), "err": RuntimeError("坏掉了")}
        )

    with TestClient(app) as client:
        resp = client.get("/__app_error")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "bad_request"
    assert body["message"] == "内部错误"
    assert isinstance(body["detail"]["at"], str)
    assert isinstance(body["detail"]["err"], dict)


# 未处理异常的线上契约。
#
# P3 已知限制 #7：未处理异常走 Starlette 默认 500，响应是纯文本 "Internal Server Error"，
# 前端 errorMessage 拿不到 {code,message,detail}。P4 统一成 JSON，且不把异常文本回显给
# 客户端（可能含 SQL、路径、密钥片段）。与上面 404/405/422/AppError 属同一错误体契约，
# 故并入本文件。

PASSWORD = "Passw0rd!"


def _setup(db):
    east = create_department(db, "华东销售部")
    create_user(
        db,
        username="admin",
        password=PASSWORD,
        display_name="管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )
    return {"east": east}


def _auth(client) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_unhandled_exception_returns_json_500(client, db, monkeypatch, caplog):
    _setup(db)
    headers = _auth(client)

    def boom(*args, **kwargs):
        raise RuntimeError("数据库口令不能出现在响应里")

    monkeypatch.setattr("app.modules.home.router.summary", boom)

    with TestClient(client.app, raise_server_exceptions=False) as raw:
        response = raw.get("/api/v1/home", headers=headers)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "code": "internal_error",
        "message": "服务器内部错误",
        "detail": None,
    }
    assert "数据库口令" not in response.text
    assert any("数据库口令" in record.getMessage() or record.exc_info for record in caplog.records)
