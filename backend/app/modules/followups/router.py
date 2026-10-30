"""待办接口：列表、详情、创建、编辑、软删，外加唯一的完成入口 `/done`。

**完成必须走 `/done`**（裁定 10）：那条路径才会可选地生成跟进并刷新
`customers.last_activity_at`；`PATCH` 传 `status="done"` 由 service 直接 400。

错误码口径同 activities：业务 400、权限 403、不存在/已删 404、参数层 422。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.pagination import Page, PageParams
from app.db.session import get_db
from app.modules.activities.schemas import ActivityRead
from app.modules.followups.schemas import (
    FollowUpCreate,
    FollowUpDone,
    FollowUpDoneRead,
    FollowUpRead,
    FollowUpUpdate,
)
from app.modules.followups.service import (
    complete_follow_up,
    create_follow_up,
    delete_follow_up,
    get_follow_up,
    list_follow_ups,
    update_follow_up,
)

router = APIRouter(prefix="/follow_ups", tags=["follow_ups"])


@router.get("", response_model=Page[FollowUpRead])
def get_follow_ups(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    page: Annotated[PageParams, Depends()],
    customer_id: int | None = None,
    status: str | None = None,
    mine: bool = False,
):
    items, total = list_follow_ups(
        db,
        user,
        customer_id=customer_id,
        status=status,
        mine=mine,
        page=page.page,
        page_size=page.page_size,
    )
    return Page[FollowUpRead](
        items=[FollowUpRead.model_validate(item) for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("", response_model=FollowUpRead, status_code=201)
def post_follow_up(
    payload: FollowUpCreate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return create_follow_up(db, user, payload)


@router.get("/{follow_up_id}", response_model=FollowUpRead)
def get_follow_up_detail(
    follow_up_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return get_follow_up(db, user, follow_up_id)


@router.patch("/{follow_up_id}", response_model=FollowUpRead)
def patch_follow_up(
    follow_up_id: int,
    payload: FollowUpUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return update_follow_up(db, user, follow_up_id, payload)


@router.delete("/{follow_up_id}", status_code=204)
def remove_follow_up(
    follow_up_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    delete_follow_up(db, user, follow_up_id)
    return Response(status_code=204)


@router.post("/{follow_up_id}/done", response_model=FollowUpDoneRead)
def post_follow_up_done(
    follow_up_id: int,
    payload: FollowUpDone,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    result = complete_follow_up(db, user, follow_up_id, payload)
    return FollowUpDoneRead(
        follow_up=FollowUpRead.model_validate(result.follow_up),
        activity=(
            ActivityRead.model_validate(result.activity)
            if result.activity is not None
            else None
        ),
    )
