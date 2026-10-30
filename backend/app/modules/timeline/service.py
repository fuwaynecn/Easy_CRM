"""客户时间线：把跟进与阶段变更合并成一条倒序流。

三类来源里 P3 只有两类（`owner_change_logs` 属 P4，裁定 6）。合并放在 SQL 里做
（`UNION ALL` 后统一排序 + 游标过滤），而不是各查一页在 Python 里归并——后者在
"一页里全是活动"时会把另一路的数据整段挤掉，翻页出现空洞。

**游标是 `(occurred_at, type, id)`**（spec §5）：同一秒可能有多条，只按时间翻页会
重复或漏；把 type 与 id 一起放进游标，排序键与游标键严格一致，翻页天然不重不漏。
比较写成显式 `or_/and_` 而不是行值比较（PG 支持 row-value，但 SQLAlchemy 生成的
表达式在混合类型/时区上不稳，显式写法还能保证三列都走索引）。

时间线**故意不套软删过滤**（裁定 1）：已删机会的阶段变更、已删联系人的姓名都要
显示并带标记。这里读的是"发生过什么"，不是"现在还能操作什么"。
"""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, and_, literal, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import InvalidValueError
from app.modules.activities.models import Activity
from app.modules.contacts.models import Contact
from app.modules.customers.service import get_customer
from app.modules.opportunities.models import Opportunity, StageHistory
from app.modules.users.models import STATUS_ACTIVE, User

# owner_change 预留给 P4（裁定 6）：枚举先占位，P4 只加一路 UNION，不改前端契约
TIMELINE_TYPES = ("activity", "stage_change", "owner_change")
ACTIVITY_TIMELINE_TYPE = "activity"
STAGE_CHANGE_TIMELINE_TYPE = "stage_change"


@dataclass(frozen=True)
class TimelineCursor:
    occurred_at: datetime
    type: str
    id: int


@dataclass(frozen=True)
class TimelineEntry:
    type: str
    id: int
    occurred_at: datetime
    operator_id: int | None
    operator_name: str
    operator_disabled: bool
    activity_type: str | None
    content: str | None
    contact_id: int | None
    contact_name: str | None
    contact_deleted: bool
    opportunity_id: int | None
    opportunity_name: str | None
    opportunity_deleted: bool
    from_stage: str | None
    to_stage: str | None
    amount_snapshot: Decimal | None


def encode_cursor(entry: TimelineEntry) -> str:
    """把排序键编成不透明字符串（客户端不得解析，裁定 14）。"""
    raw = f"{entry.occurred_at.isoformat()}|{entry.type}|{entry.id}"
    return urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(raw: str) -> TimelineCursor:
    """非法游标是 400 而不是 500：伪造/过期的 cursor 只该让客户端从头开始。"""
    try:
        decoded = urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        occurred_at_raw, type_raw, id_raw = decoded.split("|")
        if type_raw not in TIMELINE_TYPES:
            raise ValueError(type_raw)
        occurred_at = datetime.fromisoformat(occurred_at_raw)
        # 伪造的游标可能不带时区：放过去会在 SQL 里变成 naive 比较，500 而不是 400
        if occurred_at.tzinfo is None:
            raise ValueError("naive cursor")
        return TimelineCursor(
            occurred_at=occurred_at, type=type_raw, id=int(id_raw)
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidValueError("时间线游标无效，请重新加载") from exc


def _page_rows(
    db: Session, customer_id: int, cursor: TimelineCursor | None, limit: int
) -> list:
    """取一页排序键：UNION ALL 后按 (occurred_at, type, id) 倒序。

    三列都倒序，游标比较才能用同一组排序键（混合升降序需要逐字段拼条件，容易漏分支）。
    """
    activities = select(
        literal(ACTIVITY_TIMELINE_TYPE, type_=String).label("type"),
        Activity.id.label("id"),
        Activity.activity_at.label("occurred_at"),
    ).where(
        Activity.customer_id == customer_id, Activity.deleted_at.is_(None)
    )

    # 注意：这里**不过滤 Opportunity.deleted_at**（裁定 1）：机会软删后
    # stage_histories 的行仍在，历史必须照常出现在时间线上。
    histories = (
        select(
            literal(STAGE_CHANGE_TIMELINE_TYPE, type_=String).label("type"),
            StageHistory.id.label("id"),
            StageHistory.changed_at.label("occurred_at"),
        )
        .join(Opportunity, Opportunity.id == StageHistory.opportunity_id)
        .where(Opportunity.customer_id == customer_id)
    )

    merged = activities.union_all(histories).subquery()
    stmt = select(merged.c.type, merged.c.id, merged.c.occurred_at)
    if cursor is not None:
        # 字典序倒序：a < x 或 (a == x 且 b < y) 或 (a == x 且 b == y 且 c < z)
        stmt = stmt.where(
            or_(
                merged.c.occurred_at < cursor.occurred_at,
                and_(
                    merged.c.occurred_at == cursor.occurred_at,
                    merged.c.type < cursor.type,
                ),
                and_(
                    merged.c.occurred_at == cursor.occurred_at,
                    merged.c.type == cursor.type,
                    merged.c.id < cursor.id,
                ),
            )
        )
    stmt = stmt.order_by(
        merged.c.occurred_at.desc(), merged.c.type.desc(), merged.c.id.desc()
    ).limit(limit)
    return list(db.execute(stmt).all())


def _assemble(db: Session, rows: list) -> list[TimelineEntry]:
    """按页内的 id 批量取回两张表的行，再按排序顺序装配（3 条查询，无 N+1）。"""
    activity_ids = [row.id for row in rows if row.type == ACTIVITY_TIMELINE_TYPE]
    history_ids = [row.id for row in rows if row.type == STAGE_CHANGE_TIMELINE_TYPE]

    activities: dict[int, Activity] = {}
    if activity_ids:
        for activity in db.execute(
            select(Activity)
            .options(
                joinedload(Activity.operator),
                joinedload(Activity.opportunity),
            )
            .where(Activity.id.in_(activity_ids))
        ).scalars():
            activities[activity.id] = activity

    histories: dict[int, StageHistory] = {}
    if history_ids:
        for history in db.execute(
            select(StageHistory)
            .options(
                joinedload(StageHistory.operator),
                joinedload(StageHistory.opportunity),
            )
            .where(StageHistory.id.in_(history_ids))
        ).scalars():
            histories[history.id] = history

    # 联系人单独按 id 取，**故意不带 deleted_at 过滤**（裁定 1）：已删联系人的姓名
    # 仍要显示在历史行上。这里不复用任何带软删过滤的读函数，避免哪天过滤被"统一"进来。
    contact_ids = {
        activity.contact_id
        for activity in activities.values()
        if activity.contact_id is not None
    }
    contacts: dict[int, Contact] = {}
    if contact_ids:
        for contact in db.execute(
            select(Contact).where(Contact.id.in_(contact_ids))
        ).scalars():
            contacts[contact.id] = contact

    entries: list[TimelineEntry] = []
    for row in rows:
        if row.type == ACTIVITY_TIMELINE_TYPE:
            activity = activities[row.id]
            operator = activity.operator
            contact = contacts.get(activity.contact_id) if activity.contact_id else None
            opportunity = activity.opportunity
            entries.append(
                TimelineEntry(
                    type=ACTIVITY_TIMELINE_TYPE,
                    id=activity.id,
                    occurred_at=activity.activity_at,
                    operator_id=activity.created_by,
                    operator_name=operator.display_name if operator else "",
                    operator_disabled=bool(
                        operator is not None and operator.status != STATUS_ACTIVE
                    ),
                    activity_type=activity.type,
                    content=activity.content,
                    contact_id=activity.contact_id,
                    contact_name=contact.name if contact is not None else None,
                    contact_deleted=bool(
                        contact is not None and contact.deleted_at is not None
                    ),
                    opportunity_id=activity.opportunity_id,
                    opportunity_name=opportunity.name if opportunity else None,
                    opportunity_deleted=bool(
                        opportunity is not None and opportunity.deleted_at is not None
                    ),
                    from_stage=None,
                    to_stage=None,
                    amount_snapshot=None,
                )
            )
        else:
            history = histories[row.id]
            operator = history.operator
            opportunity = history.opportunity
            entries.append(
                TimelineEntry(
                    type=STAGE_CHANGE_TIMELINE_TYPE,
                    id=history.id,
                    occurred_at=history.changed_at,
                    operator_id=history.operator_id,
                    operator_name=operator.display_name if operator else "",
                    operator_disabled=bool(
                        operator is not None and operator.status != STATUS_ACTIVE
                    ),
                    activity_type=None,
                    content=history.note,
                    contact_id=None,
                    contact_name=None,
                    contact_deleted=False,
                    opportunity_id=history.opportunity_id,
                    opportunity_name=opportunity.name if opportunity else None,
                    opportunity_deleted=bool(
                        opportunity is not None and opportunity.deleted_at is not None
                    ),
                    from_stage=history.from_stage,
                    to_stage=history.to_stage,
                    amount_snapshot=history.amount_snapshot,
                )
            )
    return entries


def get_timeline(
    db: Session,
    actor: User,
    customer_id: int,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> tuple[list[TimelineEntry], str | None]:
    """客户可见才返回时间线（403/404 与其它入口同口径）。"""
    get_customer(db, actor, customer_id)
    parsed = decode_cursor(cursor) if cursor else None

    # 多取一条判断是否还有下一页，返回时再截断
    rows = _page_rows(db, customer_id, parsed, limit + 1)
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    entries = _assemble(db, page_rows)
    next_cursor = encode_cursor(entries[-1]) if has_more and entries else None
    return entries, next_cursor
