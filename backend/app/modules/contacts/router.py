"""联系人接口：列表、新增、修改、软删除。

路由层不做任何权限判断——读取经 service 里的 ``get_customer``，写入经
``assert_customer_writable``。规则留在 service 里，换一个入口（导入、前端批量操作）
才不会漏。

路径有两套写法，语义完全一致：

- ``GET/POST /customers/{customer_id}/contacts``：联系人挂在客户下，新增时必须
  带父 id，这是 brief/spec 的写法；
- ``PATCH/DELETE /contacts/{contact_id}``：扁平写法（spec §5 ``CRUD /contacts``），
  联系人的 id 全局唯一，改/删不需要父 id；
- ``PATCH/DELETE /customers/{customer_id}/contacts/{contact_id}``：派单要求的嵌套
  写法，作为扁平路由的别名保留，并且会校验 ``customer_id`` 确实是该联系人的父记录
  （对不上 404），免得"用 A 客户的路径改到 B 客户的联系人"。

两种写法共用一个 service 函数，不存在两套逻辑。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.db.session import get_db
from app.modules.contacts.schemas import ContactCreate, ContactRead, ContactUpdate
from app.modules.contacts.service import (
    create_contact,
    delete_contact,
    list_contacts,
    update_contact,
)

router = APIRouter(tags=["contacts"])


@router.get("/customers/{customer_id}/contacts", response_model=list[ContactRead])
def get_contacts(
    customer_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return list_contacts(db, user, customer_id)


@router.post(
    "/customers/{customer_id}/contacts",
    response_model=ContactRead,
    status_code=201,
)
def post_contact(
    customer_id: int,
    payload: ContactCreate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return create_contact(db, user, customer_id, payload)


@router.patch("/contacts/{contact_id}", response_model=ContactRead)
def patch_contact(
    contact_id: int,
    payload: ContactUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return update_contact(db, user, contact_id, payload)


@router.delete("/contacts/{contact_id}", status_code=204)
def remove_contact(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    delete_contact(db, user, contact_id)
    return Response(status_code=204)


@router.patch(
    "/customers/{customer_id}/contacts/{contact_id}", response_model=ContactRead
)
def patch_nested_contact(
    customer_id: int,
    contact_id: int,
    payload: ContactUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    return update_contact(db, user, contact_id, payload, customer_id=customer_id)


@router.delete("/customers/{customer_id}/contacts/{contact_id}", status_code=204)
def remove_nested_contact(
    customer_id: int,
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
):
    delete_contact(db, user, contact_id, customer_id=customer_id)
    return Response(status_code=204)
