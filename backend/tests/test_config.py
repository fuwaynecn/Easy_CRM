import logging

import pytest

import app.main as main_module
from app.core.config import DEV_JWT_SECRET_PLACEHOLDER, settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _fresh_startup_state(monkeypatch):
    """启动告警是进程级"只打一次"；每个用例前复位标记与相关配置，断言才互不污染。"""
    monkeypatch.setattr(main_module, "_warned_startup_issues", set())
    monkeypatch.setattr(settings, "jwt_secret", DEV_JWT_SECRET_PLACEHOLDER)
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "environment", "dev")


def _messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.main"]


def test_dev_jwt_secret_placeholder_meets_hmac_length():
    assert len(DEV_JWT_SECRET_PLACEHOLDER.encode("utf-8")) >= 48


def test_create_app_warns_when_jwt_secret_is_placeholder(caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
    messages = _messages(caplog)
    assert any("JWT 密钥" in message and "占位值" in message for message in messages)
    assert all(settings.jwt_secret not in message for message in messages)


def test_create_app_warns_only_once_per_process(caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
        create_app()
    assert len([m for m in _messages(caplog) if "占位值" in m]) == 1


def test_create_app_does_not_warn_with_custom_jwt_secret(monkeypatch, caplog):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 48)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
    assert _messages(caplog) == []


@pytest.mark.parametrize("environment", ["prod", "production", "staging"])
def test_create_app_warns_when_cookie_secure_is_off_outside_dev(
    monkeypatch, caplog, environment
):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 48)
    monkeypatch.setattr(settings, "environment", environment)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
    messages = _messages(caplog)
    assert any("COOKIE_SECURE" in message for message in messages)
    assert all("test-secret-" not in message for message in messages)


@pytest.mark.parametrize("environment", ["dev", "test", "local", "TESTING"])
def test_no_cookie_secure_warning_in_dev_or_test(monkeypatch, caplog, environment):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 48)
    monkeypatch.setattr(settings, "environment", environment)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
    assert _messages(caplog) == []


def test_no_cookie_secure_warning_when_secure_is_on(monkeypatch, caplog):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 48)
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "cookie_secure", True)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
    assert _messages(caplog) == []


def test_cookie_secure_warning_is_emitted_once(monkeypatch, caplog):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 48)
    monkeypatch.setattr(settings, "environment", "prod")
    with caplog.at_level(logging.WARNING, logger="app.main"):
        create_app()
        create_app()
    assert len([m for m in _messages(caplog) if "COOKIE_SECURE" in m]) == 1
