from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.db.models_registry  # noqa: F401
from alembic import context
from app.core.config import settings
from app.db.base import Base

config = context.config
# configparser 会解释 %，口令里的 % 必须转义成 %%，否则 alembic 抛 InterpolationSyntaxError
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    # disable_existing_loggers=False：编程方式调用（测试、运维脚本）时不能把宿主进程
    # 已配好的 logger 关掉，否则 caplog 这类日志断言会静默失效。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线迁移。

    优先用外部传入的连接（``config.attributes["connection"]``，alembic 官方的
    "共享连接" 模式）：测试要的就是把迁移打在 easycrm_test 上；不认这个属性时
    env.py 会自己按 settings.database_url 建连接，等于把迁移打到开发库，而测试库
    仍是空的——漂移守护测试会因此假红/假绿。外部连接的事务由调用方负责提交。
    """
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
