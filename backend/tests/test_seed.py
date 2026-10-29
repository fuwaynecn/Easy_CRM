"""种子数据脚本。

三条硬要求：

1. **可重复执行**：这个脚本会被反复跑在开发库上，按自然键（部门名、登录名、客户
   全称）判重，第二次执行各表新增必须是 0、总行数不变，且不能报错；
2. **必须有一条公海客户**（``owner_id IS NULL``）：前端验收要"管理员能看到含无
   负责人客户"，种子里没有这条，验收就没法做；
3. **种子账号能用**：登录名 ≥3 字符、口令过 ``create_user`` 的强度校验，测试直接
   验口令哈希，避免"报告里写着能登、实际登不进去"。

另加一条数据质量约束：种子客户不能出现"有负责人、没部门"的孤儿行（那类记录不在
公海、不在任何部门作用域里，owner 自己都看不到）。
"""

from sqlalchemy import func, select

from app.core.security import verify_password
from app.modules.contacts.models import Contact
from app.modules.customers.models import Customer
from app.modules.departments.models import Department
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES, User
from scripts.seed import SEED_PASSWORD, seed


def _count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def _counts(db) -> dict[str, int]:
    return {
        "departments": _count(db, Department),
        "users": _count(db, User),
        "customers": _count(db, Customer),
        "contacts": _count(db, Contact),
    }


def test_seed_creates_baseline_data(db):
    result = seed(db)

    # 返回值是"各表新建数量"，空库上必须与库里的实际行数一致
    assert result == _counts(db)
    assert result["departments"] >= 2
    assert result["users"] >= 4
    assert result["customers"] >= 3
    assert result["contacts"] >= 2


def test_seed_is_idempotent(db):
    """第二次执行全部跳过：不报错、不重复插数据、总行数不变。"""
    seed(db)
    before = _counts(db)

    result = seed(db)

    assert result == {"departments": 0, "users": 0, "customers": 0, "contacts": 0}
    assert _counts(db) == before


def test_seed_creates_exactly_one_admin(db):
    seed(db)
    admins = db.execute(select(User).where(User.role == ROLE_ADMIN)).scalars().all()

    assert [user.username for user in admins] == ["admin"]


def test_seed_covers_all_roles(db):
    seed(db)
    roles = {user.role for user in db.execute(select(User)).scalars().all()}

    assert roles == {ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES}


def test_seed_creates_public_pool_customer(db):
    """前端验收第 3 条依赖它：管理员列表里能看到无负责人的客户。"""
    seed(db)
    pool = (
        db.execute(select(Customer).where(Customer.owner_id.is_(None))).scalars().all()
    )

    assert pool
    # 公海不给部门：给了部门会让主管以为这条算本部门业绩
    assert all(customer.department_id is None for customer in pool)


def test_seed_leaves_no_orphan_customer(db):
    """有负责人却没部门的客户谁都看不见：种子里一条都不许有。"""
    seed(db)
    orphans = (
        db.execute(
            select(Customer).where(
                Customer.owner_id.is_not(None), Customer.department_id.is_(None)
            )
        )
        .scalars()
        .all()
    )

    assert orphans == []


def test_seed_customer_department_matches_owner(db):
    """``department_id`` 是随 owner 同步的冗余字段，种子不能把它写成别的部门。"""
    seed(db)
    rows = db.execute(
        select(Customer, User).join(User, Customer.owner_id == User.id)
    ).all()

    assert rows
    for customer, owner in rows:
        assert customer.department_id == owner.department_id


def test_seed_customer_data_is_business_like(db):
    """种子要能直接当验收数据用：A/B/C 三档与多种预算状态都得有。"""
    seed(db)
    customers = db.execute(select(Customer)).scalars().all()

    assert {customer.level for customer in customers} == {"A", "B", "C"}
    assert len({customer.budget_status for customer in customers}) >= 3


def test_seed_accounts_are_usable(db):
    """账号要真能登录：登录名合规，口令哈希能用 ``Passw0rd!`` 验过。"""
    seed(db)
    users = db.execute(select(User)).scalars().all()

    assert len(users) >= 4
    for user in users:
        assert len(user.username) >= 3
        assert verify_password(SEED_PASSWORD, user.password_hash)


def test_seed_creates_contacts_for_demo_customer(db):
    seed(db)
    customer = db.execute(
        select(Customer).where(Customer.name == "杭州智造装备有限公司")
    ).scalar_one()

    assert customer.owner_id is not None
    contacts = (
        db.execute(select(Contact).where(Contact.customer_id == customer.id))
        .scalars()
        .all()
    )
    assert len(contacts) >= 2
    # 一个客户只能有一个主联系人（部分唯一索引盯着这条）
    assert sum(1 for contact in contacts if contact.is_primary) == 1
