from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """每个请求一个会话，收尾只有 ``close()``。

    **会话由 `close()` 负责回滚未提交事务，别删 try/finally**：服务层在业务校验
    失败时可能已经改过内存对象，这些改动不能落库；``Session.close()`` 会回滚活动
    事务并把连接还池。这里不需要、也不应再补一次 rollback（补了是死代码，反而让人
    以为"没有它就漏数据"）。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
