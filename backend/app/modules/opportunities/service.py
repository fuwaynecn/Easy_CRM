"""机会服务。

作用域规则**不在这里定义**，而是组合 P1 的单一入口：可见机会 = 可见客户名下的
机会。写成 ``Opportunity.customer_id.in_(customer_query(actor).with_only_columns(Customer.id))``
而不是 join，是为了让"可见性只有一个真值源"在代码里一眼可见——评审能 grep 到
`customer_query`，而不是面对一个自己拼的 join 条件。

写入路径同理：先 ``get_customer``（403/404 分开），再
``assert_customer_writable``（公海只读）。机会模块不写第三套权限判断。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.core.text import LIKE_ESCAPE, contains_pattern
from app.db.scope import assert_customer_writable, customer_query
from app.modules.customers.models import Customer
from app.modules.customers.service import get_customer
from app.modules.opportunities.models import (
    OPPORTUNITY_STAGES,
    TERMINAL_STAGES,
    Opportunity,
    StageHistory,
)
from app.modules.opportunities.schemas import OpportunityCreate, OpportunityUpdate
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, User

_SORTABLE = {
    "created_at": Opportunity.created_at,
    "updated_at": Opportunity.updated_at,
    "amount": Opportunity.amount,
    "expected_close_date": Opportunity.expected_close_date,
}

# 看板每列最多返回多少条：`total` 是全量聚合（页头显示真实条数），items 只给前 N 条。
# 200 与分页上限一致，前端能明确说"显示前 200 条"。
BOARD_COLUMN_LIMIT = 200

# 库里的 NOT NULL 列：显式传 null 不能落库，按取值非法（400）而不是撞 500
_REQUIRED_FIELDS = {"name": "机会名称", "amount": "预计金额"}

# update 认的字段，与 schemas.OpportunityUpdate 一一对应（多一道白名单兜底）。
# **字面量而不是 `frozenset(OpportunityUpdate.model_fields)`**：从 schema 派生等于把
# 白名单变成 schema 的镜像，这道门就永远关不住任何东西（`unknown` 分支不可达），
# 而 Task 6 往 schema 加一个字段会**顺手**把这个字段放进可编辑集合。写成字面量后，
# 任何新增字段都必须在这里被有意识地加一次；两边不一致时下面的 `unknown` 会 400。
UPDATABLE_FIELDS = frozenset(
    {"name", "amount", "expected_close_date", "competitor", "win_rate"}
)


@dataclass(frozen=True)
class BoardColumn:
    """看板一列：全量聚合 + 前 N 条明细。"""

    stage: str
    total: int
    amount_total: Decimal
    items: list[Opportunity]


def _visible_conditions(
    actor: User, *, customer_id: int | None = None
) -> list[ColumnElement[bool]]:
    """机会可见性的唯一构造点：软删过滤 + 客户作用域（+ 可选的单客户过滤）。"""
    conditions: list[ColumnElement[bool]] = [
        Opportunity.deleted_at.is_(None),
        Opportunity.customer_id.in_(
            customer_query(actor).with_only_columns(Customer.id)
        ),
    ]
    if customer_id is not None:
        conditions.append(Opportunity.customer_id == customer_id)
    return conditions


def _visible_query(actor: User) -> Select:
    return select(Opportunity).where(*_visible_conditions(actor))


def _with_relations(stmt: Select) -> Select:
    """三个关系一律显式预加载（模型上是 lazy="raise"，漏了立刻报错）。"""
    return stmt.options(
        joinedload(Opportunity.customer),
        joinedload(Opportunity.owner),
        joinedload(Opportunity.lost_reason),
    )


def _load_full(db: Session, opportunity_id: int) -> Opportunity:
    """写完回读一次，带上三个关系。返回的对象可直接序列化成 OpportunityRead。"""
    return db.execute(
        _with_relations(select(Opportunity))
        .where(Opportunity.id == opportunity_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _validate_filter_stage(stage: str | None) -> None:
    """筛选取值非法是 400（不是空列表）：空列表会把"参数拼错"显示成"没有数据"。"""
    if stage and stage not in OPPORTUNITY_STAGES:
        raise InvalidValueError(f"未知阶段：{stage}")


def get_opportunity(db: Session, actor: User, opportunity_id: int) -> Opportunity:
    """按作用域取单个机会：不存在 404，存在但看不到 403。

    作用域内查不到时做一次**裸 select 的存在性探针**（只取 id、限定未删除），
    用来把 403 与 404 分开——与 ``customers.service.get_customer`` 同一手法。
    """
    opportunity = db.execute(
        _with_relations(_visible_query(actor)).where(Opportunity.id == opportunity_id)
    ).scalar_one_or_none()
    if opportunity is not None:
        return opportunity
    exists = db.execute(
        select(Opportunity.id).where(
            Opportunity.id == opportunity_id, Opportunity.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise NotFoundError("机会不存在")
    raise ForbiddenError("无权查看该机会")


def list_opportunities(
    db: Session,
    actor: User,
    *,
    keyword: str | None = None,
    stage: str | None = None,
    customer_id: int | None = None,
    owner_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str = "-created_at",
) -> tuple[list[Opportunity], int]:
    """作用域内的机会分页。

    ``keyword`` 命中机会名称、竞争对手、以及**所属客户名称**（spec §5 说机会字段的
    搜索"留给后续阶段"，P2 就是那个阶段）。``date_from`` / ``date_to`` 筛
    ``expected_close_date``：它是 date 列，整天闭区间即可，不像 timestamptz 那样要
    换算本地零点；没填成交日的机会在用了日期筛选时不会出现（NULL 比较结果为未知）。
    """
    _validate_filter_stage(stage)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidValueError("开始日期不能晚于结束日期")
    if customer_id is not None:
        # 筛一个看不见的客户应当 403，而不是返回空列表：空列表会被读成"这客户没机会"
        get_customer(db, actor, customer_id)

    stmt = select(Opportunity).where(
        *_visible_conditions(actor, customer_id=customer_id)
    )
    if keyword and keyword.strip():
        pattern = contains_pattern(keyword.strip())
        stmt = stmt.where(
            or_(
                Opportunity.name.ilike(pattern, escape=LIKE_ESCAPE),
                Opportunity.competitor.ilike(pattern, escape=LIKE_ESCAPE),
                Opportunity.customer_id.in_(
                    customer_query(actor)
                    .with_only_columns(Customer.id)
                    .where(Customer.name.ilike(pattern, escape=LIKE_ESCAPE))
                ),
            )
        )
    if stage:
        stmt = stmt.where(Opportunity.stage == stage)
    if owner_id is not None:
        stmt = stmt.where(Opportunity.owner_id == owner_id)
    if date_from is not None:
        stmt = stmt.where(Opportunity.expected_close_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Opportunity.expected_close_date <= date_to)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    stmt = _with_relations(stmt)
    normalized = sort.strip() or "-created_at"
    descending = normalized.startswith("-")
    field = normalized.lstrip("-")
    column = _SORTABLE.get(field)
    if column is None:
        raise InvalidValueError(f"不支持的排序字段：{field}")
    # 第二排序键 id 保证翻页稳定（金额/日期相同的机会不会漏页或重页）
    stmt = stmt.order_by(
        column.desc() if descending else column.asc(), Opportunity.id
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return list(db.execute(stmt).scalars().all()), int(total)


def board(
    db: Session, actor: User, *, customer_id: int | None = None
) -> list[BoardColumn]:
    """看板数据：固定六列，每列全量聚合 + 前 ``BOARD_COLUMN_LIMIT`` 条明细。

    不按分页拉全量再前端分组：机会数过 200 之后，前端分组会静默丢掉第 201 条以后
    的机会，而看板恰恰是用来看"有多少单在谈"的。列内按预计成交日升序、未填的排最后
    （P2 没有逾期概念，最早该成交的单排最前最有用）。
    """
    if customer_id is not None:
        get_customer(db, actor, customer_id)
    conditions = _visible_conditions(actor, customer_id=customer_id)

    stats = {
        stage: (int(total), Decimal(amount_total))
        for stage, total, amount_total in db.execute(
            select(
                Opportunity.stage,
                func.count(),
                func.coalesce(func.sum(Opportunity.amount), 0),
            )
            .where(*conditions)
            .group_by(Opportunity.stage)
        ).all()
    }

    columns: list[BoardColumn] = []
    for stage in OPPORTUNITY_STAGES:
        total, amount_total = stats.get(stage, (0, Decimal(0)))
        if total == 0:
            columns.append(
                BoardColumn(stage=stage, total=0, amount_total=Decimal(0), items=[])
            )
            continue
        items = list(
            db.execute(
                _with_relations(select(Opportunity).where(*conditions))
                .where(Opportunity.stage == stage)
                .order_by(
                    Opportunity.expected_close_date.asc().nulls_last(),
                    Opportunity.id,
                )
                .limit(BOARD_COLUMN_LIMIT)
            )
            .scalars()
            .all()
        )
        columns.append(
            BoardColumn(
                stage=stage, total=total, amount_total=amount_total, items=items
            )
        )
    return columns


def _clean_name(name: str | None) -> str:
    """strip 后不能为空：空名字在列表里就是一行看不见的空白。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidValueError("机会名称不能为空")
    return cleaned


def _clean_optional(value: str | None) -> str | None:
    """竞争对手这类自由文本：全空白按没填处理，避免前端把空串写进库。"""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _validate_stage_for_create(stage: str) -> None:
    """创建时只允许四个推进阶段。

    赢单要 `won_at`、输单要原因，这两个字段只能由阶段变更产生（Task 6）。允许建单
    即终态等于给了一条绕过规则的旁路，而且"新建一个已经输掉的机会"本身也没有
    业务含义。
    """
    if stage not in OPPORTUNITY_STAGES:
        raise InvalidValueError(f"未知阶段：{stage}")
    if stage in TERMINAL_STAGES:
        raise InvalidValueError(
            "新建机会只能从推进阶段开始；赢单/输单必须通过阶段变更产生"
        )


def _writable_customer(db: Session, actor: User, customer_id: int) -> Customer:
    """写入前必经：先按作用域取客户（403/404 分开），再判可写（公海只读）。"""
    customer = get_customer(db, actor, customer_id)
    assert_customer_writable(customer, actor)
    return customer


def create_opportunity(
    db: Session, actor: User, payload: OpportunityCreate
) -> Opportunity:
    """创建机会。

    负责人**只能**从客户继承（design §3.5「机会负责人与客户负责人一致」），请求体
    里没有 owner_id 这个字段（schema `extra="forbid"`）。因此公海客户（没有负责人）
    建不了机会——这不是遗漏，是 owner_id NOT NULL 与一致性规则的必然结果。

    创建本身也是一次"阶段从无到有"，所以同一事务里写第一条 history
    （`from_stage=None`）：否则「漏斗里有多少单进了初步接触」就少了一整段。
    """
    name = _clean_name(payload.name)
    _validate_stage_for_create(payload.stage)
    customer = _writable_customer(db, actor, payload.customer_id)
    # 下面这条 400 只有在**调用方对该客户可写**时才能走到：sales/manager 遇到公海客户
    # 已经在 `assert_customer_writable` 里拿到 403「公海客户只读」，admin 才走到这里。
    # 两层不是重复——403 说的是"你对这个客户没有写权限"，400 说的是"这个客户没有负责人，
    # 建不了机会"；将来若公海对某个角色放开写权限，400 仍然成立。
    if customer.owner_id is None:
        raise InvalidValueError("客户未分配负责人，请先领取或分配后再新建机会")

    opportunity = Opportunity(
        customer_id=customer.id,
        name=name,
        stage=payload.stage,
        amount=payload.amount,
        expected_close_date=payload.expected_close_date,
        competitor=_clean_optional(payload.competitor),
        owner_id=customer.owner_id,
        win_rate=payload.win_rate,
        created_by=actor.id,
    )
    db.add(opportunity)
    db.flush()  # 拿到自增 id，history 需要它
    db.add(
        StageHistory(
            opportunity_id=opportunity.id,
            from_stage=None,
            to_stage=opportunity.stage,
            amount_snapshot=opportunity.amount,
            note=None,
            operator_id=actor.id,
        )
    )
    db.commit()
    return _load_full(db, opportunity.id)


def update_opportunity(
    db: Session, actor: User, opportunity_id: int, payload: OpportunityUpdate
) -> Opportunity:
    """编辑机会字段。

    `stage` / `owner_id` / `won_at` / `lost_reason_id` 都不在白名单里（schema 已经
    `extra="forbid"`，这里再挡一次是给绕过 schema 的调用方留同一道门）。
    改金额**不**写 history：§3.7 的 history 只记阶段变更，金额的演变由报表按
    `amount_snapshot` 的时点快照体现，不靠 history 行。
    """
    opportunity = get_opportunity(db, actor, opportunity_id)
    _writable_customer(db, actor, opportunity.customer_id)

    changes = payload.model_dump(exclude_unset=True)
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise InvalidValueError(f"不可更新的字段：{'、'.join(sorted(unknown))}")
    # 显式 null 与"未提交"是两件事：name/amount 是 NOT NULL 列，显式 null 必须在这里
    # 变成 400，否则要等到 flush 时撞约束、以 IntegrityError 冒成 500。
    for field, label in _REQUIRED_FIELDS.items():
        if field in changes and changes[field] is None:
            raise InvalidValueError(f"{label}不能为空")
    if "name" in changes:
        changes["name"] = _clean_name(changes["name"])
    if "competitor" in changes:
        changes["competitor"] = _clean_optional(changes["competitor"])
    # win_rate 显式传 null 是合法动作：回到"跟随阶段默认值"，所以不在上面的必填检查里
    for field, value in changes.items():
        setattr(opportunity, field, value)
    db.commit()
    return _load_full(db, opportunity.id)


def delete_opportunity(db: Session, actor: User, opportunity_id: int) -> None:
    """软删除：仅主管及以上，且必须在自己的可写范围内。

    软删而不是硬删：stage_histories 是漏斗与成交周期报表的唯一数据来源，硬删会让
    报表里整段历史凭空消失。已删的机会对接口表现为不存在（重复删除 404）。
    """
    if actor.role not in (ROLE_ADMIN, ROLE_MANAGER):
        raise ForbiddenError("仅主管及以上可删除机会")
    opportunity = get_opportunity(db, actor, opportunity_id)
    _writable_customer(db, actor, opportunity.customer_id)
    opportunity.deleted_at = datetime.now(UTC)
    db.commit()


def sync_opportunity_owner(db: Session, *, customer_id: int, owner_id: int) -> int:
    """把某客户名下**未关闭**机会的负责人改到新负责人。返回实际受影响行数。

    design §3.5：客户的领取、分配、转移在同一个事务内把该客户名下未关闭
    （`stage NOT IN ('won','lost')`）的机会一并改过去；已赢/已输的机会保留原负责人
    作为历史业绩归属——否则把三年前的成单算到新人头上，业绩报表就失真了。

    **本函数不 commit**：调用方（P4 的领取/分配/转移）必须与客户归属变更放在同一个
    事务里，否则会出现"客户已换人、在谈的机会还挂在旧人"的中间态。

    P2 没有调用方：P1 尚未实现领取/分配/转移（属 P4）。这里先落地并测试，是为了让
    P4 直接调用，而不是届时再凭记忆重写一遍规则（重写必然漏掉"未关闭"这半句）。
    """
    result = db.execute(
        update(Opportunity)
        .where(
            Opportunity.customer_id == customer_id,
            Opportunity.deleted_at.is_(None),
            Opportunity.stage.not_in(TERMINAL_STAGES),
            Opportunity.owner_id.is_distinct_from(owner_id),
        )
        .values(owner_id=owner_id)
    )
    return int(result.rowcount or 0)
