"""客户服务。

读取一律从 :func:`app.db.scope.customer_query` 起步（design §2.3），写前一律
:func:`app.db.scope.assert_customer_writable`；本模块只叠加业务过滤，不自拼
``select(Customer)``。**只有两处故意绕过作用域**，结构审计时必须一起看：

1. :func:`find_similar_customers`：跨作用域重名软查重——别人建过、当前用户看不见
   的同名客户也要命中，否则"看不见就查不出重复"，软查重等于没做；
2. :func:`get_customer`：作用域内查不到时的存在性探针——只取 ``id`` 且限定未删除，
   用来把 403（有记录但无权）与 404（记录不存在）分开。

两处都不返回越权数据，但它们是 ``customer_query`` 之外仅有的读取入口。
``last_activity_at`` 的三条维护规则见 design §3.3。P1 还没有 activity 表，也没有
领取/分配/转移接口（属后续阶段），所以这里只落"其余编辑一律不更新"这一半：
创建不写该字段，编辑不碰它。
"""

import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
)
from app.core.text import LIKE_ESCAPE, contains_pattern
from app.db.scope import assert_customer_writable, customer_query
from app.modules.customers.models import (
    BUDGET_STATUSES,
    CUSTOMER_LEVELS,
    CUSTOMER_STATUSES,
    PROCUREMENT_TYPES,
    Customer,
)
from app.modules.customers.schemas import (
    CUSTOMER_UPDATE_FIELDS,
    CustomerCreate,
    CustomerUpdate,
)
from app.modules.users.models import User
from app.modules.users.service import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLES,
    STATUS_ACTIVE,
    get_user,
)

# 名称归一化时丢掉的噪声：空白、半/全角括号、书名号、间隔号、常见标点与斜杠
_NOISE = re.compile(r"[\s（）()【】\[\]·,，.。\-—_/\\]")

_SORTABLE = {
    "name": Customer.name,
    "created_at": Customer.created_at,
    "updated_at": Customer.updated_at,
    "last_activity_at": Customer.last_activity_at,
    "level": Customer.level,
}

# 库里的 NOT NULL 列：显式传 null 不能落库，按取值非法（400）处理而不是撞 500
_REQUIRED_FIELDS = {
    "name": "客户名称",
    "level": "客户等级",
    "budget_status": "预算状态",
    "status": "客户状态",
}


def normalize_name(name: str) -> str:
    """宽度归一化后去噪声字符并转小写，供软查重比较（design §4.4）。

    先做 NFKC 而不是直接 ``strip().lower()``：Excel 导出的客户名里全角字母数字极
    常见（"杭州ＸＸ公司" vs "杭州XX公司"），不归一化就会被判成两家不同客户，软查重
    等于漏掉最常见的一类重复。NFKC 顺带把全角空格、全角括号折成半角，正好落进
    ``_NOISE`` 的清理范围。
    """
    return _NOISE.sub("", unicodedata.normalize("NFKC", name)).lower()


def find_similar_customers(db: Session, name: str, limit: int = 5) -> list[Customer]:
    """跨作用域的重名软查重（design §4.4）。

    这里**故意**不走 ``customer_query``：查重必须对全库生效——别人建过、当前用户
    看不到的同名客户也要命中，否则"看不见就查不出重复"，软查重等于没做。已软删的
    记录不参与，删掉再建同名是合法操作。

    匹配分两档：归一化后完全相同优先，其次是互相包含（"XX 集团" vs "XX 集团分公司"
    这类需要人工判断）。P1 数据量在万级以内，全表取名字在应用层比较即满足设计；
    design §3.3 明确不建 ``lower(name)`` 函数索引。
    """
    target = normalize_name(name)
    if not target:
        return []
    rows = (
        db.execute(select(Customer).where(Customer.deleted_at.is_(None)))
        .scalars()
        .all()
    )
    exact: list[Customer] = []
    fuzzy: list[Customer] = []
    for row in rows:
        candidate = normalize_name(row.name)
        if candidate == target:
            exact.append(row)
        elif target in candidate or candidate in target:
            fuzzy.append(row)
    return (exact + fuzzy)[:limit]


def _validate_enums(payload: CustomerCreate | CustomerUpdate) -> None:
    """枚举取值非法是 400（与 409"与现有数据冲突"区分），见 core.errors。"""
    if payload.level is not None and payload.level not in CUSTOMER_LEVELS:
        raise InvalidValueError(f"未知客户等级：{payload.level}")
    if (
        payload.procurement_type is not None
        and payload.procurement_type not in PROCUREMENT_TYPES
    ):
        raise InvalidValueError(f"未知采购方式：{payload.procurement_type}")
    if (
        payload.budget_status is not None
        and payload.budget_status not in BUDGET_STATUSES
    ):
        raise InvalidValueError(f"未知预算状态：{payload.budget_status}")
    status = getattr(payload, "status", None)
    if status is not None and status not in CUSTOMER_STATUSES:
        raise InvalidValueError(f"未知客户状态：{status}")


def _validate_filter_enums(
    *,
    level: str | None,
    status: str | None,
    procurement_type: str | None,
    budget_status: str | None,
) -> None:
    """列表筛选的枚举取值非法是 400（不是空列表），取值以 design §3.3 为准。

    返回空列表会让前端把"参数拼错了"显示成"没有数据"，与越权返回空列表是同一类
    错误；空串按"未筛选"处理（前端清空下拉时会带 ``?level=``）。
    """
    for value, allowed, label in (
        (level, CUSTOMER_LEVELS, "客户等级"),
        (status, CUSTOMER_STATUSES, "客户状态"),
        (procurement_type, PROCUREMENT_TYPES, "采购方式"),
        (budget_status, BUDGET_STATUSES, "预算状态"),
    ):
        if value and value not in allowed:
            raise InvalidValueError(f"未知{label}：{value}")


def _resolve_owner(
    db: Session, actor: User, requested_owner_id: int | None
) -> User | None:
    """创建时的归属（design §2.2 / §3.3）。

    销售自建归自己；主管可指定本部门成员（可写范围是本部门全部）；管理员可指定
    任何人。返回 None 表示进公海——只有管理员不指定负责人时才会这样。

    未知角色在这里就拒绝，不能退化成"等价 sales"：那会让手工改坏的角色建出客户，
    随后按作用域又看不见它（未知角色只见公海），留下谁都找不到的孤儿数据。
    """
    if actor.role not in ROLES:
        raise ForbiddenError("账号角色异常，请联系管理员")
    if requested_owner_id is None:
        return None if actor.role == ROLE_ADMIN else actor
    if requested_owner_id == actor.id:
        return actor
    if actor.role not in (ROLE_ADMIN, ROLE_MANAGER):
        raise ForbiddenError("无权为他人创建客户")

    owner = get_user(db, requested_owner_id)
    if owner is None:
        raise NotFoundError("指定的负责人不存在")
    if owner.status != STATUS_ACTIVE:
        raise InvalidValueError("指定的负责人已停用")
    if actor.role == ROLE_MANAGER and owner.department_id != actor.department_id:
        raise ForbiddenError("主管只能把客户分配给本部门成员")
    return owner


def create_customer(db: Session, actor: User, payload: CustomerCreate) -> Customer:
    name = payload.name.strip()
    if not name:
        raise InvalidValueError("客户名称不能为空")
    _validate_enums(payload)
    if not payload.force:
        similar = find_similar_customers(db, name)
        if similar:
            raise ConflictError(
                "客户名称疑似重复",
                detail={"similar": [{"id": c.id, "name": c.name} for c in similar]},
            )

    owner = _resolve_owner(db, actor, payload.owner_id)
    # 负责人必须有部门：department_id 只能从 owner 同步（下面那行），owner 没部门就
    # 只能落成 NULL，于是这条客户既不在公海、也不在任何部门作用域里——owner 自己
    # 都看不到，库里留下一条"谁都看不见"的孤儿行。无部门主管自建（None != None 放行）
    # 与管理员把客户指定给无部门账号（如另一个 admin）都走这条路径，所以判在这里，
    # 而不是分散在 _resolve_owner 的两个分支里。
    if owner is not None and owner.department_id is None:
        raise InvalidValueError("负责人未分配部门，无法归属客户")
    customer = Customer(
        name=name,
        short_name=payload.short_name,
        code=payload.code,
        industry=payload.industry,
        region=payload.region,
        address=payload.address,
        website=payload.website,
        scale=payload.scale,
        level=payload.level,
        source=payload.source,
        procurement_type=payload.procurement_type,
        budget_status=payload.budget_status,
        owner_id=owner.id if owner is not None else None,
        # department_id 是冗余字段，同步点之一就是创建：直接取负责人的当前部门，
        # 不接受请求体传入（schema 已拒绝），避免出现两套归属口径。
        department_id=owner.department_id if owner is not None else None,
        remark=payload.remark,
        created_by=actor.id,
        # last_activity_at 故意不写：它只由 activity 写入与领取/转移维护（§3.3），
        # 创建客户不是同步点。
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def list_customers(
    db: Session,
    actor: User,
    *,
    keyword: str | None = None,
    level: str | None = None,
    status: str | None = None,
    procurement_type: str | None = None,
    budget_status: str | None = None,
    owner_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str = "-created_at",
) -> tuple[list[Customer], int]:
    """作用域内的客户分页：返回当前页与总数（前端分页器要的是 total 不是本页数）。"""
    _validate_filter_enums(
        level=level,
        status=status,
        procurement_type=procurement_type,
        budget_status=budget_status,
    )
    stmt: Select = customer_query(actor)
    if keyword and keyword.strip():
        # % 与 _ 按字面量匹配：搜索 "%" 不能变成"列出全部客户"
        pattern = contains_pattern(keyword.strip())
        stmt = stmt.where(
            or_(
                Customer.name.ilike(pattern, escape=LIKE_ESCAPE),
                Customer.short_name.ilike(pattern, escape=LIKE_ESCAPE),
                Customer.code.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    if level:
        stmt = stmt.where(Customer.level == level)
    if status:
        stmt = stmt.where(Customer.status == status)
    if procurement_type:
        stmt = stmt.where(Customer.procurement_type == procurement_type)
    if budget_status:
        stmt = stmt.where(Customer.budget_status == budget_status)
    if owner_id is not None:
        stmt = stmt.where(Customer.owner_id == owner_id)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    normalized = sort.strip() or "-created_at"
    descending = normalized.startswith("-")
    field = normalized.lstrip("-")
    column = _SORTABLE.get(field)
    if column is None:
        raise InvalidValueError(f"不支持的排序字段：{field}")
    # 第二排序键 id 保证翻页稳定：created_at 相同（批量导入）时不会漏页/重页
    stmt = stmt.order_by(column.desc() if descending else column.asc(), Customer.id)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return list(db.execute(stmt).scalars().all()), int(total)


def get_customer(db: Session, actor: User, customer_id: int) -> Customer:
    """按作用域取单个客户：不存在 404，存在但无权限 403（design §5）。

    作用域内查不到时，下面还有一个**裸 select 的存在性探针**——本模块两处故意
    绕过 ``customer_query`` 的第二处（第一处是 :func:`find_similar_customers`）。
    它只取 ``id``、限定 ``deleted_at IS NULL``，不返回任何业务字段；存在的意义是
    把 403 与 404 分开，前端才能区分"没权限"与"记录没了"。
    """
    customer = db.execute(
        customer_query(actor).where(Customer.id == customer_id)
    ).scalar_one_or_none()
    if customer is not None:
        return customer
    exists = db.execute(
        select(Customer.id).where(
            Customer.id == customer_id, Customer.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise NotFoundError("客户不存在")
    raise ForbiddenError("无权查看该客户")


def update_customer(
    db: Session, actor: User, customer_id: int, payload: CustomerUpdate
) -> Customer:
    """编辑客户字段。

    不触发软查重（design §4.4 只约束创建）：改名撞到别人的名字不该被 409 卡住，
    现实里同名/改名很常见，且已有 ``force`` 让创建侧自行决定。
    """
    customer = get_customer(db, actor, customer_id)
    assert_customer_writable(customer, actor)
    _validate_enums(payload)

    changes = payload.model_dump(exclude_unset=True)
    # 白名单兜底：schema 已经 extra="forbid"，这里再挡一次是给绕过 schema 的调用方
    # （脚本、后续新增路由）留同一道门，owner_id 尤其不能从这里溜进更新路径。
    unknown = set(changes) - CUSTOMER_UPDATE_FIELDS
    if unknown:
        raise InvalidValueError(f"不可更新的字段：{'、'.join(sorted(unknown))}")
    for field, label in _REQUIRED_FIELDS.items():
        if field in changes and changes[field] is None:
            raise InvalidValueError(f"{label}不能为空")
    if "name" in changes:
        name = changes["name"].strip()
        if not name:
            raise InvalidValueError("客户名称不能为空")
        changes["name"] = name
    for field, value in changes.items():
        setattr(customer, field, value)
    db.commit()
    db.refresh(customer)
    return customer


def delete_customer(db: Session, actor: User, customer_id: int) -> None:
    """软删除：仅主管及以上，且必须在自己的可写范围内（公海客户连主管也不能删）。"""
    if actor.role not in (ROLE_ADMIN, ROLE_MANAGER):
        raise ForbiddenError("仅主管及以上可删除客户")
    customer = get_customer(db, actor, customer_id)
    assert_customer_writable(customer, actor)
    customer.deleted_at = datetime.now(UTC)
    db.commit()
