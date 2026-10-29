import logging

from app.core.config import DEV_JWT_SECRET_PLACEHOLDER, settings
from app.main import create_app


def test_dev_jwt_secret_placeholder_meets_hmac_length():
    assert len(DEV_JWT_SECRET_PLACEHOLDER.encode("utf-8")) >= 48


def test_create_app_warns_when_jwt_secret_is_placeholder(caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
    messages = [record.getMessage() for record in caplog.records if record.name == "app.main"]
    assert any("JWT 密钥" in message and "占位值" in message for message in messages)
    assert all(settings.jwt_secret not in message for message in messages)


def test_create_app_does_not_warn_with_custom_jwt_secret(monkeypatch, caplog):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 48)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
    assert [record for record in caplog.records if record.name == "app.main"] == []
