import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

import app.db.models_registry  # noqa: F401
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app


def _urls() -> tuple[str, str]:
    """推导管理连接与测试库连接。

    TEST_DATABASE_URL 只覆盖连接信息（主机、端口、账号、认证方式），库名是固定的：
    管理连接用 postgres，测试库用 easycrm_test；URL 里自带的库名会被忽略。
    未设置时沿用 settings.database_url 的连接信息。
    """
    override = os.getenv("TEST_DATABASE_URL")
    base = make_url(override or settings.database_url)
    test = base.set(database="easycrm_test").render_as_string(hide_password=False)
    admin = base.set(database="postgres").render_as_string(hide_password=False)
    return admin, test


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    admin_url, test_url = _urls()
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'easycrm_test'")
            ).scalar()
            if not exists:
                conn.execute(text("CREATE DATABASE easycrm_test"))
    finally:
        admin.dispose()

    eng = create_engine(test_url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session", autouse=True)
def _bind_sessionlocal(engine: Engine) -> Iterator[None]:
    """把常驻的 SessionLocal 也绑到测试库，避免绕过 get_db 的代码写开发库。"""
    from app.db.session import SessionLocal

    SessionLocal.configure(bind=engine)
    yield


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    # 重建 schema 而不是 drop_all/create_all：前者能清掉孤儿表，后者对同名旧表是跳过
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
