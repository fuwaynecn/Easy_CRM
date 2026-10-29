"""机会接口：列表、看板、详情、创建、编辑、软删、阶段变更。

路由层不做权限判断：读取经 service 的可见性查询，写入经 ``get_customer`` +
``assert_customer_writable``，阶段变更经 ``change_stage``。规则留在 service 里，
换一个入口（将来的批量操作、导入）才不会漏。

**声明顺序有讲究**：``/board`` 必须写在 ``/{opportunity_id}`` 之前。FastAPI 按声明
顺序匹配，写反了 ``GET /opportunities/board`` 会先命中 ``/{opportunity_id}``，
"board" 转 int 失败 → 422。
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.pagination import Page, PageParams
from app.db.session import get_db
from app.modules.opportunities.schemas import (
    BoardColumnRead,
    BoardRead,
    OpportunityCreate,
    OpportunityRead,
    OpportunityUpdate,
    StageChange,
)
from app.modules.opportunities.service import (
    board,
    change_stage,
    create_opportunity,
    delete_opportunity,
    get_opportunity,
    list_opportunities,
    update_opportunity,
)

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("", response_model=Page[OpportunityRead])
def get_opportunities(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    page: Annotated[PageParams, Depends()],
    keyword: str | None = None,
    stage: str | None = None,
    customer_id: int | None = None,
    owner_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "-created_at",
):
    items, total = list_opportunities(
        db,
        user,
        keyword=keyword,
        stage=stage,
        customer_id=customer_id,
        owner_id=owner_id,
        date_from=date_from,
        date_to=date_to,
        page=page.page,
        page_size=page.page_size,
        sort=sort,
    )
    return Page[OpportunityRead](
        items=[OpportunityRead.model_validate(item) for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/board", response_model=BoardRead)
def get_board(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    customer_id: int | None = None,
):
    columns = board(db, user, customer_id=customer_id)
    # 显式逐列装配，不能 ``BoardColumnRead.model_validate(column)``：``BoardColumn``
    # 是 service 的 dataclass，``BoardColumnRead`` 没有 ``from_attributes``（它是线上
    # 契约，不该为了内部类型打开属性读取），直接 validate 会 ValidationError。
    return BoardRead(
        columns=[
            BoardColumnRead(
                stage=column.stage,
                total=column.total,
                amount_total=column.amount_total,
                items=[OpportunityRead.model_validate(item) for item in column.items],
            )
            for column in columns
        ]
    )


@router.post("", response_model=OpportunityRead, status_code=201)
def post_opportunity(
    payload: OpportunityCreate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return create_opportunity(db, user, payload)


@router.get("/{opportunity_id}", response_model=OpportunityRead)
def get_opportunity_detail(
    opportunity_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return get_opportunity(db, user, opportunity_id)


@router.patch("/{opportunity_id}", response_model=OpportunityRead)
def patch_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return update_opportunity(db, user, opportunity_id, payload)


@router.delete("/{opportunity_id}", status_code=204)
def remove_opportunity(
    opportunity_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    delete_opportunity(db, user, opportunity_id)
    return Response(status_code=204)


@router.post("/{opportunity_id}/stage", response_model=OpportunityRead)
def post_stage(
    opportunity_id: int,
    payload: StageChange,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return change_stage(db, user, opportunity_id, payload)
