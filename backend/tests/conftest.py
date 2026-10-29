import os
from collections.abc import Iterator

import app.db.models_registry  # noqa: F401
import pytest
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker


def _urls() -> tuple[str, str]:
    """从 settings.database_url 推导管理库与测试库地址。

    TEST_DATABASE_URL 可整体覆盖数据库地址；这里只替换库名，主机、端口、账号与
    认证方式全部沿用 DATABASE_URL，避免把本机端口固化进仓库。
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
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'easycrm_test'")
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE easycrm_test"))
    admin.dispose()

    eng = create_engine(test_url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    Base.metadata.drop_all(engine)
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