from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import AdminUser
from app.db.session import get_db
from app.modules.pools.rules import as_read, load_pool_rules, update_pool_rules
from app.modules.pools.schemas import PoolRuleRead, PoolRulesUpdate

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
