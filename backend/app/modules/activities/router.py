"""跟进接口：列表、详情、创建、编辑、软删。

路由层不做权限判断：读取走 service 的作用域查询，写入走 service 的
get_customer + assert_customer_writable + 裁定 5 的权限判定。规则留在 service，
换一个入口（移动端悬浮按钮、将来的导入）才不会漏。

错误码口径（与 TL 拍定的一致）：业务规则 400 `invalid_value`、权限 403
`forbidden`、不存在或已软删 404 `not_found`、请求体白名单/参数范围 422
`validation_error`（由 pydantic/PageParams 在参数层挡住）。
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.pagination import Page, PageParams
from app.db.session import get_db
from app.modules.activities.schemas import (
    ActivityCreate,
    ActivityRead,
    ActivityUpdate,
)
from app.modules.activities.service import (
    create_activity,
    delete_activity,
    get_activity,
    list_activities,
    update_activity,
)

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", response_model=Page[ActivityRead])
def get_activities(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    page: Annotated[PageParams, Depends()],
    customer_id: int | None = None,
    type: str | None = None,
    created_by: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    items, total = list_activities(
        db,
        user,
        customer_id=customer_id,
        type=type,
        created_by=created_by,
        date_from=date_from,
        date_to=date_to,
        page=page.page,
        page_size=page.page_size,
    )
    return Page[ActivityRead](
        items=[ActivityRead.model_validate(item) for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("", response_model=ActivityRead, status_code=201)
def post_activity(
    payload: ActivityCreate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return create_activity(db, user, payload)


@router.get("/{activity_id}", response_model=ActivityRead)
def get_activity_detail(
    activity_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return get_activity(db, user, activity_id)


@router.patch("/{activity_id}", response_model=ActivityRead)
def patch_activity(
    activity_id: int,
    payload: ActivityUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return update_activity(db, user, activity_id, payload)


@router.delete("/{activity_id}", status_code=204)
def remove_activity(
    activity_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    delete_activity(db, user, activity_id)
    return Response(status_code=204)
