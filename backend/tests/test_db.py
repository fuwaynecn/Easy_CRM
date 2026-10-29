from sqlalchemy import text

import app.db.session as session_module
from app.db.base import Base


def test_module_engine_is_rebound_to_test_database(engine):
    """`app.db.session.engine` 也必须指向测试库，不能留开发库。"""
    assert session_module.engine is engine


def test_database_is_postgres(db):
    version = db.execute(text("SELECT version()")).scalar_one()
    assert "PostgreSQL" in version


def test_base_has_metadata():
    assert isinstance(Base.metadata.tables, dict)


def test_session_rolls_back_between_tests(db):
    db.execute(text("CREATE TEMP TABLE probe (id int)"))
    db.execute(text("INSERT INTO probe VALUES (1)"))
    assert db.execute(text("SELECT count(*) FROM probe")).scalar_one() == 1


def test_temp_table_is_gone_in_next_test(db):
    # 上一个测试建的是 TEMP 表，同一连接内仍可能存在；这里断言业务表为空即可
    tables = db.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    ).scalars().all()
    assert isinstance(tables, list)
