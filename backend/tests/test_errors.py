from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import BaseModel, model_validator


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


def test_validation_error_returns_unified_422(db):
    app = create_app()

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
    app = create_app()

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