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
    PoolCustomerRead,
    PoolRuleRead,
    PoolRulesUpdate,
)
from app.modules.pools.service import claim_customer, list_pool

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
