from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import AdminUser, CurrentUser
from app.core.pagination import Page, PageParams
from app.core.timeutil import days_since_local
from app.db.session import get_db
from app.modules.customers.schemas import CustomerRead
from app.modules.pools.rules import as_read, load_pool_rules, update_pool_rules
from app.modules.pools.schemas import (
    AssignableUserRead,
    AssignRequest,
    PoolCustomerRead,
    PoolRuleRead,
    PoolRulesUpdate,
    ReleaseRequest,
)
from app.modules.pools.service import (
    assign_customer,
    claim_customer,
    list_assignable_users,
    list_pool,
    release_customer,
)

router = APIRouter(tags=["pools"])


@router.get("/admin/pool-rules", response_model=PoolRuleRead)
def get_pool_rules(
    db: Annotated[Session, Depends(get_db)],
    _admin: AdminUser,
):
    return as_read(load_pool_rules(db))


@router.patch("/admin/pool-rules", response_model=PoolRuleRead)
def patch_pool_rules(
    payload: PoolRulesUpdate,
    db: Annotated[Session, Depends(get_db)],
    _admin: AdminUser,
):
    return as_read(update_pool_rules(db, payload))


@router.get("/pool", response_model=Page[PoolCustomerRead])
def get_pool(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    page: Annotated[PageParams, Depends()],
    keyword: str | None = None,
):
    items, total = list_pool(
        db,
        user,
        keyword=keyword,
        page=page.page,
        page_size=page.page_size,
    )
    now = datetime.now(UTC)
    return Page[PoolCustomerRead](
        items=[
            PoolCustomerRead(
                id=item.id,
                name=item.name,
                short_name=item.short_name,
                industry=item.industry,
                level=item.level,
                region=item.region,
                status=item.status,
                last_activity_at=item.last_activity_at,
                days_inactive=(
                    days_since_local(item.last_activity_at, now=now)
                    if item.last_activity_at is not None
                    else None
                ),
                created_at=item.created_at,
            )
            for item in items
        ],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/pool/{customer_id}/claim", response_model=CustomerRead)
def claim(
    customer_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return claim_customer(db, user, customer_id)


@router.post("/customers/{customer_id}/assign", response_model=CustomerRead)
def assign(
    customer_id: int,
    payload: AssignRequest,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return assign_customer(
        db,
        user,
        customer_id,
        owner_id=payload.owner_id,
        reason=payload.reason,
    )


@router.post("/customers/{customer_id}/release", response_model=CustomerRead)
def release(
    customer_id: int,
    payload: ReleaseRequest,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return release_customer(db, user, customer_id, reason=payload.reason)


@router.get("/users/assignable", response_model=list[AssignableUserRead])
def get_assignable_users(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return [
        AssignableUserRead(
            id=item.id,
            display_name=item.display_name,
            department_id=item.department_id,
            department_name=item.department_name,
            owned_count=item.owned_count,
            max_owned=item.max_owned,
            quota_remaining=item.quota_remaining,
        )
        for item in list_assignable_users(db, user)
    ]
