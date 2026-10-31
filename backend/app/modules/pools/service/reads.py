"""公海池读取：列表与可分配用户（后者在 Task 4 追加）。"""

from sqlalchemy.orm import Session

from app.modules.customers.models import Customer
from app.modules.customers.service import list_customers
from app.modules.users.models import User


def list_pool(
    db: Session,
    actor: User,
    *,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Customer], int]:
    """公海列表：作用域内 owner_id IS NULL，按最久没跟进排序。

    复用 ``list_customers`` 的 keyword/分页/软删逻辑，避免出现第二套搜索口径；
    ``sort="last_activity_at"`` 在 customers service 里被解释为 ASC NULLS FIRST。
    """
    return list_customers(
        db,
        actor,
        keyword=keyword,
        pool_only=True,
        page=page,
        page_size=page_size,
        sort="last_activity_at",
    )
