"""客户接口：列表、详情、创建、编辑、软删除。

权限判断不在路由层：读取经 service 里的 ``customer_query``，写入经
``assert_customer_writable``。路由只做参数解包与分页体拼装——规则一旦散到路由，
换一个入口（导入、报表）就会漏。
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.pagination import Page, PageParams
from app.db.session import get_db
from app.modules.customers.schemas import (
    CustomerCreate,
    CustomerRead,
    CustomerUpdate,
)
from app.modules.customers.service import (
    create_customer,
    delete_customer,
    get_customer,
    list_customers,
    update_customer,
)

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=Page[CustomerRead])
def get_customers(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    page: Annotated[PageParams, Depends()],
    keyword: str | None = None,
    level: str | None = None,
    status: str | None = None,
    procurement_type: str | None = None,
    budget_status: str | None = None,
    owner_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "-created_at",
):
    items, total = list_customers(
        db,
        user,
        keyword=keyword,
        level=level,
        status=status,
        procurement_type=procurement_type,
        budget_status=budget_status,
        owner_id=owner_id,
        date_from=date_from,
        date_to=date_to,
        page=page.page,
        page_size=page.page_size,
        sort=sort,
    )
    return Page[CustomerRead](
        items=[CustomerRead.model_validate(item) for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("", response_model=CustomerRead, status_code=201)
def post_customer(
    payload: CustomerCreate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return create_customer(db, user, payload)


@router.get("/{customer_id}", response_model=CustomerRead)
def get_customer_detail(
    customer_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return get_customer(db, user, customer_id)


@router.patch("/{customer_id}", response_model=CustomerRead)
def patch_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return update_customer(db, user, customer_id, payload)


@router.delete("/{customer_id}", status_code=204)
def remove_customer(
    customer_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    delete_customer(db, user, customer_id)
    return Response(status_code=204)
