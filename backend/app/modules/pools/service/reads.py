"""公海池读取：列表与可分配用户。"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import ForbiddenError
from app.modules.customers.models import Customer
from app.modules.customers.service import list_customers
from app.modules.pools.rules import load_pool_rules
from app.modules.users.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_SALES,
    STATUS_ACTIVE,
    User,
)


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


@dataclass(frozen=True)
class AssignableUser:
    id: int
    display_name: str
    department_id: int
    department_name: str
    owned_count: int
    max_owned: int

    @property
    def quota_remaining(self) -> int:
        return max(0, self.max_owned - self.owned_count)


def list_assignable_users(db: Session, actor: User) -> list[AssignableUser]:
    """主管看本部门可分配对象；管理员看全部；销售 403。

    配额计数与领取/分配共用同一口径：所有未删除客户，含 lost/inactive（裁定 7）。
    """
    if actor.role not in (ROLE_ADMIN, ROLE_MANAGER):
        raise ForbiddenError("仅主管及以上可查看可分配用户")
    rules = load_pool_rules(db)
    counts = (
        select(
            Customer.owner_id.label("owner_id"),
            func.count().label("owned_count"),
        )
        .where(Customer.owner_id.is_not(None), Customer.deleted_at.is_(None))
        .group_by(Customer.owner_id)
        .subquery()
    )
    stmt = (
        select(User, func.coalesce(counts.c.owned_count, 0))
        .outerjoin(counts, counts.c.owner_id == User.id)
        .options(joinedload(User.department))
        .where(
            User.status == STATUS_ACTIVE,
            User.role.in_((ROLE_SALES, ROLE_MANAGER)),
            User.department_id.is_not(None),
        )
    )
    if actor.role == ROLE_MANAGER:
        stmt = stmt.where(User.department_id == actor.department_id)
    stmt = stmt.order_by(User.display_name.asc(), User.id.asc())
    return [
        AssignableUser(
            id=user.id,
            display_name=user.display_name,
            department_id=user.department_id,
            department_name=user.department.name if user.department else "",
            owned_count=int(owned_count),
            max_owned=rules.max_owned,
        )
        for user, owned_count in db.execute(stmt).all()
    ]
