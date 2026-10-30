"""机会读取：可见性、列表、看板、单条。

作用域规则不在这里定义，而是组合 P1 的单一入口：可见机会 = 可见客户名下的机会。
写成 ``Opportunity.customer_id.in_(customer_query(actor).with_only_columns(Customer.id))``
而不是 join，是为了让"可见性只有一个真值源"在代码里一眼可见。
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.core.text import LIKE_ESCAPE, contains_pattern
from app.db.scope import customer_query
from app.modules.customers.models import Customer
from app.modules.customers.service import get_customer
from app.modules.opportunities.models import OPPORTUNITY_STAGES, Opportunity
from app.modules.users.models import User

_SORTABLE = {
    "created_at": Opportunity.created_at,
    "updated_at": Opportunity.updated_at,
    "amount": Opportunity.amount,
    "expected_close_date": Opportunity.expected_close_date,
}

# 看板每列最多返回多少条：`total` 是全量聚合（页头显示真实条数），items 只给前 N 条。
# 200 与分页上限一致，前端能明确说"显示前 200 条"。
BOARD_COLUMN_LIMIT = 200


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
