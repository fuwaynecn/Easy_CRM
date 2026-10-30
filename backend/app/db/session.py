from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """每个请求一个会话；**异常路径显式回滚**。

    服务层在业务校验失败时可能已经改过内存对象（完成冻结/取消冻结那类判定若顺序
    写错就是这种形态），不 commit 不代表会话里干净。这里不依赖 ``close()`` 的隐式
    回滚：显式 rollback 让"失败请求不落库"成为可读的约定，也保证连接回到池里时
    没有半截事务。
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
