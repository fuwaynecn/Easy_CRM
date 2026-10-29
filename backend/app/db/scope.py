"""客户数据作用域：可见性与可写性的唯一真值源。

规则见 design §2.2，结构性保证见 §2.3：

- 可见性只在 :func:`customer_scope` 里判定，客户读取统一走 :func:`customer_query`；
  路由与服务层不要自己拼 ``select(Customer)``，否则漏一处就是一处越权或漏数据。
- 公海客户（``owner_id IS NULL``）对 sales 与 manager 一律只读，判定写在
  :func:`customer_writable_scope` / :func:`can_write_customer` 里，不交给路由判断。

``customer_writable_scope``（SQL 条件）与 ``can_write_customer``（对象判定）必须给出
同一集合，``tests/test_scope.py::test_writable_scope_matches_can_write_customer``
盯着这条：两套规则一旦漂移，列表能改的和接口允许改的就会错位。
"""

from sqlalchemy import ColumnElement, Select, and_, false, or_, select, true

from app.core.errors import ForbiddenError
from app.modules.customers.models import Customer
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, User


def _public_pool() -> ColumnElement[bool]:
    """公海：没有归属销售。销售与主管都能看见它，但都不能直接改。"""
    return Customer.owner_id.is_(None)


def customer_scope(user: User) -> ColumnElement[bool]:
    """客户可见性条件，拼进 WHERE 使用。

    - admin：全部；
    - manager：本部门 + 公海；
    - sales：自己名下 + 公海（公海只读，见 :func:`can_write_customer`）。
    """
    if user.role == ROLE_ADMIN:
        return true()
    if user.role == ROLE_MANAGER:
        # 主管没有部门时不能退化成"部门字段为空即本部门"：``department_id = NULL``
        # 在 SQLAlchemy 里会被渲染成 IS NULL，等于把所有无部门客户都放给他。
        if user.department_id is None:
            return _public_pool()
        return or_(Customer.department_id == user.department_id, _public_pool())
    # 未识别的角色按最低权限处理（等价 sales），不静默放宽
    return or_(Customer.owner_id == user.id, _public_pool())


def customer_writable_scope(user: User) -> ColumnElement[bool]:
    """客户可写性条件，SQL 侧的 :func:`can_write_customer`。"""
    if user.role == ROLE_ADMIN:
        return true()
    # 公海只读是硬约束：先排除 owner_id IS NULL，再谈部门/归属
    owned = Customer.owner_id.is_not(None)
    if user.role == ROLE_MANAGER:
        if user.department_id is None:
            return false()
        return and_(owned, Customer.department_id == user.department_id)
    return and_(owned, Customer.owner_id == user.id)


def customer_query(user: User) -> Select[tuple[Customer]]:
    """客户读取的唯一入口：已软删的记录不可见，可见性由作用域条件决定。

    路由与服务层从这里起步再挂业务过滤（关键字、状态、分页），不要另起
    ``select(Customer)``。
    """
    return select(Customer).where(
        Customer.deleted_at.is_(None), customer_scope(user)
    )


def can_write_customer(customer: Customer, user: User) -> bool:
    """能否修改该客户。公海对 sales / manager 一律 False。"""
    if user.role == ROLE_ADMIN:
        return True
    if customer.owner_id is None:
        return False
    if user.role == ROLE_MANAGER:
        return (
            user.department_id is not None
            and customer.department_id == user.department_id
        )
    return customer.owner_id == user.id


def assert_customer_writable(customer: Customer, user: User) -> None:
    """越权写操作抛 403（不是 404、也不是空结果）。

    404 会让前端以为"记录没了"，空列表会让它以为"没数据"；这两种状态都必须和
    "有数据但没权限" 区分开。
    """
    if can_write_customer(customer, user):
        return
    if customer.owner_id is None:
        raise ForbiddenError("公海客户只读，请先领取后再修改")
    raise ForbiddenError("无权修改该客户")
