"""初始化开发环境的种子数据。

**可重复执行**：按自然键判重（部门名、登录名、客户全称、客户下的联系人姓名），
已存在即跳过并计 0。这个脚本会被反复跑在同一个开发库上，重复执行既不报错也不
产生重复数据，后一条比前一条更难做到——所以判重一律落在自然键上，不用自增 id。

种子数据刻意贴近真实业务：中文公司全称、真实部门划分、A/B/C 三档等级、四种预算
状态，并**必须包含一条公海客户**（``owner_id IS NULL``）——前端验收要"管理员能
看到含无负责人客户"，种子里没有这条就没法验收。

所有账号口令统一为 :data:`SEED_PASSWORD`，只用于本地开发/演示，不要带到任何运行
环境。入口：``cd backend && python -m scripts.seed``。
"""

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.modules.contacts.models import Contact
from app.modules.contacts.schemas import ContactCreate
from app.modules.contacts.service import create_contact
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.models import Department
from app.modules.departments.service import create_department
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES, User
from app.modules.users.service import create_user, get_user_by_username

# 本地开发口令。满足 create_user 的强度校验（≥8 位），报告里会列给人工验收用。
SEED_PASSWORD = "Passw0rd!"

DEPARTMENTS = ("华东销售部", "华南销售部")


@dataclass(frozen=True)
class SeedUser:
    """一个种子账号。``department`` 是部门名，``None`` 表示不归属部门（仅管理员）。"""

    username: str
    display_name: str
    role: str
    department: str | None


@dataclass(frozen=True)
class SeedContact:
    name: str
    title: str
    phone: str
    decision_role: str
    is_primary: bool = False


@dataclass(frozen=True)
class SeedCustomer:
    """一条种子客户。``owner`` 是负责人的登录名，``None`` 表示进公海。"""

    name: str
    short_name: str
    industry: str
    region: str
    level: str
    budget_status: str
    procurement_type: str | None
    source: str | None
    owner: str | None
    remark: str | None = None
    contacts: tuple[SeedContact, ...] = ()


USERS = (
    SeedUser("admin", "系统管理员", ROLE_ADMIN, None),
    SeedUser("boss", "李建国", ROLE_MANAGER, "华东销售部"),
    SeedUser("sales1", "张明", ROLE_SALES, "华东销售部"),
    SeedUser("boss_south", "陈晓峰", ROLE_MANAGER, "华南销售部"),
    SeedUser("sales2", "王丽", ROLE_SALES, "华南销售部"),
)

CUSTOMERS = (
    SeedCustomer(
        name="杭州智造装备有限公司",
        short_name="杭州智造",
        industry="专用设备制造",
        region="浙江杭州",
        level="A",
        budget_status="approved",
        procurement_type="tender",
        source="老客户转介绍",
        owner="sales1",
        remark="喷涂产线改造，预算已批，Q4 完成技术方案",
        contacts=(
            SeedContact("王工", "技术负责人", "13800000001", "tech", is_primary=True),
            SeedContact("刘总", "总经理", "13800000002", "decision_maker"),
            SeedContact("陈经理", "采购经理", "13800000003", "purchase"),
        ),
    ),
    SeedCustomer(
        name="宁波海天涂装有限公司",
        short_name="宁波海天",
        industry="金属制品",
        region="浙江宁波",
        level="B",
        budget_status="pending",
        procurement_type="direct",
        source="行业展会",
        owner="sales2",
        remark="现有产线自动化改造，预算待批",
        contacts=(SeedContact("周工", "设备工程师", "13900000001", "tech"),),
    ),
    SeedCustomer(
        name="上海精工机器人系统有限公司",
        short_name="上海精工",
        industry="工业机器人",
        region="上海",
        level="A",
        budget_status="approved",
        procurement_type="framework",
        source="合作伙伴推荐",
        owner="boss",
        remark="框架协议客户，按项目分批下单",
        contacts=(
            SeedContact("徐总", "总经理", "13700000001", "decision_maker", is_primary=True),
            SeedContact("孙工", "项目经理", "13700000002", "user"),
        ),
    ),
    SeedCustomer(
        name="广州鼎盛汽车零部件有限公司",
        short_name="广州鼎盛",
        industry="汽车零部件",
        region="广东广州",
        level="C",
        budget_status="none",
        procurement_type="other",
        source="电话开发",
        owner="sales2",
        remark="暂无明确项目，保持季度回访",
        contacts=(SeedContact("何工", "工艺工程师", "13600000001", "user"),),
    ),
    SeedCustomer(
        name="苏州新能源电池科技有限公司",
        short_name="苏州新能",
        industry="电池制造",
        region="江苏苏州",
        level="B",
        budget_status="unknown",
        procurement_type=None,
        source="官网询盘",
        # 公海客户：owner 为空，销售与主管只读，由管理员分配或销售领取后归属
        owner=None,
        remark="官网留资，尚未分配负责人",
    ),
)


def _get_or_create_department(db: Session, name: str) -> tuple[Department, int]:
    existing = db.execute(
        select(Department).where(Department.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, 0
    return create_department(db, name), 1


def _get_or_create_user(
    db: Session, spec: SeedUser, departments: dict[str, Department]
) -> tuple[User, int]:
    """按登录名判重。

    已存在时不改它的部门/角色：脚本只负责把数据补齐，不负责纠正被人手工改过的
    账号——静默覆盖别人的调整是更危险的失败方式。
    """
    existing = get_user_by_username(db, spec.username)
    if existing is not None:
        return existing, 0
    department = departments.get(spec.department) if spec.department else None
    user = create_user(
        db,
        username=spec.username,
        password=SEED_PASSWORD,
        display_name=spec.display_name,
        role=spec.role,
        department_id=department.id if department is not None else None,
    )
    return user, 1


def _find_customer(db: Session, name: str) -> Customer | None:
    """按客户全称**精确**判重。

    刻意不用 ``find_similar_customers``：那是模糊软查重（"XX 集团" 与 "XX 集团
    分公司" 互相包含），用来判幂等会把一条本来没建过的客户当成"已存在"跳过。
    软删记录不参与：删掉再建同名是合法操作。
    """
    return db.execute(
        select(Customer).where(Customer.name == name, Customer.deleted_at.is_(None))
    ).scalar_one_or_none()


def _get_or_create_customer(
    db: Session, spec: SeedCustomer, users: dict[str, User], admin: User
) -> tuple[Customer, int]:
    existing = _find_customer(db, spec.name)
    if existing is not None:
        return existing, 0
    # owner 为空即公海：用管理员身份建、不指定负责人，_resolve_owner 会落成 NULL。
    # 其余客户由负责人本人建（销售自建归自己、主管自建归自己），不需要额外指定。
    actor = users[spec.owner] if spec.owner is not None else admin
    customer = create_customer(
        db,
        actor,
        CustomerCreate(
            name=spec.name,
            short_name=spec.short_name,
            industry=spec.industry,
            region=spec.region,
            level=spec.level,
            budget_status=spec.budget_status,
            procurement_type=spec.procurement_type,
            source=spec.source,
            remark=spec.remark,
        ),
    )
    return customer, 1


def _create_contact_if_missing(
    db: Session, actor: User, customer: Customer, spec: SeedContact
) -> int:
    """按（客户，联系人姓名）判重。

    联系人不建唯一约束（design §3.4 允许同名/同号），所以幂等只能靠这一层；
    已存在即跳过，避免第二次执行把主联系人插成两条撞上部分唯一索引。
    """
    existing = db.execute(
        select(Contact.id).where(
            Contact.customer_id == customer.id,
            Contact.name == spec.name,
            Contact.deleted_at.is_(None),
        )
    ).first()
    if existing is not None:
        return 0
    create_contact(
        db,
        actor,
        customer.id,
        ContactCreate(
            name=spec.name,
            title=spec.title,
            phone=spec.phone,
            decision_role=spec.decision_role,
            is_primary=spec.is_primary,
        ),
    )
    return 1


def _seed(
    db: Session,
) -> tuple[dict[str, int], dict[str, list[str]], dict[str, list[str]]]:
    """真正的播种逻辑：返回（各表新建数量、新建明细、跳过明细）。"""
    counts = {"departments": 0, "users": 0, "customers": 0, "contacts": 0}
    created: dict[str, list[str]] = {key: [] for key in counts}
    skipped: dict[str, list[str]] = {key: [] for key in counts}

    def _record(key: str, label: str, was_created: int) -> None:
        counts[key] += was_created
        (created if was_created else skipped)[key].append(label)

    departments: dict[str, Department] = {}
    for name in DEPARTMENTS:
        department, was_created = _get_or_create_department(db, name)
        departments[name] = department
        _record("departments", name, was_created)

    users: dict[str, User] = {}
    for user_spec in USERS:
        user, was_created = _get_or_create_user(db, user_spec, departments)
        users[user_spec.username] = user
        _record("users", user_spec.username, was_created)

    admin = users["admin"]
    for customer_spec in CUSTOMERS:
        customer, was_created = _get_or_create_customer(db, customer_spec, users, admin)
        _record("customers", customer_spec.name, was_created)
        for contact_spec in customer_spec.contacts:
            contact_created = _create_contact_if_missing(
                db, admin, customer, contact_spec
            )
            _record(
                "contacts", f"{customer_spec.name}/{contact_spec.name}", contact_created
            )

    return counts, created, skipped


def seed(db: Session) -> dict[str, int]:
    """播种种子数据，返回各表**本次新建**数量（已存在的计 0）。"""
    counts, _, _ = _seed(db)
    return counts


_LABELS = {
    "departments": "部门",
    "users": "用户",
    "customers": "客户",
    "contacts": "联系人",
}

_ROLE_LABELS = {ROLE_ADMIN: "管理员", ROLE_MANAGER: "主管", ROLE_SALES: "销售"}


def _join(names: list[str]) -> str:
    return "、".join(names) if names else "无"


def _print_summary(
    counts: dict[str, int],
    created: dict[str, list[str]],
    skipped: dict[str, list[str]],
) -> None:
    print("种子数据执行完成。")
    print(f"本次新建 {sum(counts.values())} 条记录：")
    for key, label in _LABELS.items():
        print(f"  {label}：新建 {counts[key]} 条（{_join(created[key])}）")
        if skipped[key]:
            print(f"  {label}：跳过 {len(skipped[key])} 条（{_join(skipped[key])}）")
    print()
    print(f"种子账号（口令统一为 {SEED_PASSWORD}）：")
    for user_spec in USERS:
        department = user_spec.department or "无部门"
        print(
            f"  {user_spec.username:<10} {_ROLE_LABELS[user_spec.role]}"
            f"  {user_spec.display_name}  {department}"
        )
    print()
    pool = [spec.name for spec in CUSTOMERS if spec.owner is None]
    if pool:
        print(f"公海客户（owner 为空，管理员列表可见）：{_join(pool)}")


# 只有开发/测试环境允许播种：种子口令是固定且公开的（见文件头），误在生产执行
# 等于给系统装上 5 个口令已知的账号，其中 admin 还是管理员。
_ALLOWED_ENVIRONMENTS = frozenset({"dev", "test"})


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化开发环境的种子数据")
    parser.add_argument(
        "--force",
        action="store_true",
        help="在非 dev/test 环境强制执行（危险：会创建口令已知的账号）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：环境守卫放在这里，``seed(db)`` 本身保持可测、不读全局配置。"""
    args = _parse_args(argv)
    if settings.environment not in _ALLOWED_ENVIRONMENTS:
        if not args.force:
            print(
                "拒绝执行种子脚本：当前 environment="
                f"{settings.environment!r}，只允许在 dev/test 环境运行。"
                "种子账号口令固定且公开，误在生产执行会造出 5 个口令已知的账号"
                "（其中 admin 是管理员）。如确需执行，请显式传 --force。",
                file=sys.stderr,
            )
            return 1
        print(
            f"警告：正在 {settings.environment!r} 环境使用 --force 执行种子脚本，"
            "将创建口令已知的账号，请确认这是你想要的。",
            file=sys.stderr,
        )

    db = SessionLocal()
    try:
        counts, created, skipped = _seed(db)
    finally:
        db.close()
    _print_summary(counts, created, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
