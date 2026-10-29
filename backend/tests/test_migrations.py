"""迁移与模型的漂移守护。

测试夹具用 ``Base.metadata.create_all`` 建表，生产用 Alembic 迁移，这是两个真值源：
迁移漏了某列时全部测试照样通过，只有线上才撞 NOT NULL / UndefinedColumn。本用例把
测试库清空后用 Alembic 从零升到 head，再用 autogenerate 比对模型，差异非空即失败。
"""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Engine

import app.db.models_registry  # noqa: F401  注册全部模型到 Base.metadata
from alembic import command
from app.db.base import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def _render(diff: list[object]) -> str:
    return "\n".join(f"  - {item}" for item in diff)


def test_migrations_match_models(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    config = Config(str(ALEMBIC_INI))
    # 外部连接模式：env.py 优先用 config.attributes["connection"]，不再自己建引擎，
    # 保证迁移落在 easycrm_test 而不是开发库。engine.begin() 负责提交/回滚。
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True}
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], (
        "Alembic 迁移与 SQLAlchemy 模型不一致（共 "
        f"{len(diff)} 项），请补一条迁移或修正模型：\n{_render(diff)}"
    )
