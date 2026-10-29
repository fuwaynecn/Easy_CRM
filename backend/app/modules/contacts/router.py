"""联系人接口：列表、新增、修改、软删除。

路由层不做任何权限判断——读取经 service 里的 ``get_customer``，写入经
``assert_customer_writable``。规则留在 service 里，换一个入口（导入、前端批量操作）
才不会漏。

路径只有一套：

- ``GET/POST /customers/{customer_id}/contacts``：列表与新增必须带父 id；
- ``PATCH/DELETE /contacts/{contact_id}``：联系人的 id 全局唯一，改/删不需要父 id
  （spec §5 ``CRUD /contacts``）。

``/customers/{customer_id}/contacts/{contact_id}`` 的嵌套写路由曾作为别名存在，
现已删除：同一条规则维护两份实现只会让两边的校验慢慢漂移。
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
