"""联系人服务。

**权限完全继承客户，本模块不另写一套规则**：

- 读取：先 :func:`app.modules.customers.service.get_customer`（作用域内才可见，
  越权 403、不存在 404），确认能读客户之后再读它名下的联系人；
- 写入：再过 :func:`app.db.scope.assert_customer_writable`（公海客户只读 403）。

联系人自己**没有**独立的可见性判断。原因很直接：客户的作用域规则已经在
``app/db/scope.py`` 里实现了两遍（SQL 条件与对象判定）并用测试对齐，联系人再抄
一份就等于第三份，任何一处改动漏掉一处就会漂移，而漂移的表现是"能看见客户却
看不见联系人"或更糟的"看不见客户却能改联系人"。

``last_activity_at`` 的维护规则见 design §3.3：只有写 activity、领取、转移三个
同步点，**改联系人不更新**。这一条不写注释最容易被"有交互就刷新"顺手改坏，而
后果是客户永远达不到回收条件、公海形同虚设。
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import InvalidValueError, NotFoundError
from app.db.scope import assert_customer_writable
from app.modules.contacts.models import DECISION_ROLES, Contact
from app.modules.contacts.schemas import ContactCreate, ContactUpdate
from app.modules.customers.service import get_customer
from app.modules.users.models import User

# 库里的 NOT NULL 列：显式传 null 不能落库，按取值非法（400）处理而不是撞 500
_REQUIRED_FIELDS = {"name": "联系人姓名", "is_primary": "是否主联系人"}


def _validate_decision_role(role: str | None) -> None:
    """取值不在字典里是 400（同客户模块的枚举口径）。

    不用 409：409 表示"与现有数据冲突"（重名、已被领取），取值非法只是参数错。
    """
    if role is not None and role not in DECISION_ROLES:
        raise InvalidValueError(f"未知决策角色：{role}")


def _clean_name(name: str | None) -> str:
    """姓名去空白后不能为空。

    schema 的 ``min_length=1`` 拦不住全空格（``"   "`` 长度是 3），而空姓名在
    列表里就是一行看不见的空白，必须在服务层再判一次。
    """
    if name is None:
        raise InvalidValueError("联系人姓名不能为空")
    cleaned = name.strip()
    if not cleaned:
        raise InvalidValueError("联系人姓名不能为空")
    return cleaned


def _validate_required(changes: dict[str, object]) -> None:
    """显式传 null 给库里的 NOT NULL 列是 400，不是撞约束的 500。

    ``ContactUpdate`` 的每个字段都可空（PATCH 语义），但 ``name`` 与 ``is_primary``
    在库里是 NOT NULL：``{"is_primary": null}`` 会让 setattr 把 None 写进列，直接
    撞数据库约束变成 500。与客户模块的 ``_REQUIRED_FIELDS`` 同一口径。
    """
    for field, label in _REQUIRED_FIELDS.items():
        if field in changes and changes[field] is None:
            raise InvalidValueError(f"{label}不能为空")


def _clear_other_primaries(db: Session, customer_id: int, keep_id: int | None) -> None:
    """把同客户下其它未删除的主联系人置 false。

    与"设新主联系人"必须落在同一个事务里（本函数不做 commit），否则中间态会出现
    两条 primary：数据库的部分唯一索引会直接把这次写入顶回去。
    """
    stmt = (
        update(Contact)
        .where(
            Contact.customer_id == customer_id,
            Contact.deleted_at.is_(None),
            Contact.is_primary.is_(True),
        )
        .values(is_primary=False)
    )
    if keep_id is not None:
        stmt = stmt.where(Contact.id != keep_id)
    db.execute(stmt)


def list_contacts(db: Session, actor: User, customer_id: int) -> list[Contact]:
    """客户可见才返回其联系人；主联系人排在最前（详情页直接用这个顺序）。"""
    get_customer(db, actor, customer_id)
    stmt = (
        select(Contact)
        .where(Contact.customer_id == customer_id, Contact.deleted_at.is_(None))
        .order_by(Contact.is_primary.desc(), Contact.id)
    )
    return list(db.execute(stmt).scalars().all())


def create_contact(
    db: Session, actor: User, customer_id: int, payload: ContactCreate
) -> Contact:
    customer = get_customer(db, actor, customer_id)
    assert_customer_writable(customer, actor)
    _validate_decision_role(payload.decision_role)
    name = _clean_name(payload.name)
    if payload.is_primary:
        _clear_other_primaries(db, customer_id, keep_id=None)

    contact = Contact(
        customer_id=customer_id,
        name=name,
        title=payload.title,
        phone=payload.phone,
        wechat=payload.wechat,
        email=payload.email,
        is_primary=payload.is_primary,
        decision_role=payload.decision_role,
        remark=payload.remark,
    )
    db.add(contact)
    # 客户本身不在这里刷新：design §3.3 的三个同步点不含联系人操作
    db.commit()
    db.refresh(contact)
    return contact


def _get_owned_contact(
    db: Session, actor: User, contact_id: int, customer_id: int | None = None
) -> Contact:
    """取一条可写联系人：先过客户作用域，再返回。

    ``customer_id`` 只在嵌套路由（``/customers/{cid}/contacts/{id}``）传入，用于
    确认路径里的客户确实是这个联系人的父记录；对不上按"该客户下不存在此联系人"
    处理（404），避免出现"用 A 客户的路径改到 B 客户的联系人"。
    """
    contact = db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    ).scalar_one_or_none()
    if contact is None or (
        customer_id is not None and contact.customer_id != customer_id
    ):
        raise NotFoundError("联系人不存在")
    customer = get_customer(db, actor, contact.customer_id)
    assert_customer_writable(customer, actor)
    return contact


def update_contact(
    db: Session,
    actor: User,
    contact_id: int,
    payload: ContactUpdate,
    *,
    customer_id: int | None = None,
) -> Contact:
    contact = _get_owned_contact(db, actor, contact_id, customer_id)
    changes = payload.model_dump(exclude_unset=True)
    _validate_required(changes)
    if "decision_role" in changes:
        _validate_decision_role(changes["decision_role"])
    if "name" in changes:
        changes["name"] = _clean_name(changes["name"])
    if changes.get("is_primary") is True:
        _clear_other_primaries(db, contact.customer_id, keep_id=contact.id)
    for field, value in changes.items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(
    db: Session, actor: User, contact_id: int, *, customer_id: int | None = None
) -> None:
    """软删除。已删除的再删一次是 404（不是幂等的 200，也不是 500）。"""
    contact = _get_owned_contact(db, actor, contact_id, customer_id)
    contact.deleted_at = datetime.now(UTC)
    db.commit()
