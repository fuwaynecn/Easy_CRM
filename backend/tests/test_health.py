from app.core.errors import ConflictError
from app.main import create_app
from fastapi.testclient import TestClient


def test_health_returns_ok():
    with TestClient(create_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_app_error_uses_unified_body():
    app = create_app()

    @app.get("/__boom")
    def _boom():
        raise ConflictError("名称已存在", detail={"similar": ["甲公司"]})

    with TestClient(app) as client:
        resp = client.get("/__boom")
    assert resp.status_code == 409
    assert resp.json() == {
        "code": "conflict",
        "message": "名称已存在",
        "detail": {"similar": ["甲公司"]},
    }
