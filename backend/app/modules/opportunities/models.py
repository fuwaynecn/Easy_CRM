"""机会模型（design §3.5）。

字段与阶段枚举逐字取自 spec：阶段固定四推进 + 两终态，不做可配置（§3.6）。

`win_rate` 可空，语义是「NULL = 跟随阶段默认值」：spec 只说"默认由阶段推导"，
手工值要不要允许曾是待决项，P2 的裁定是允许手改、改阶段不覆盖手改值。用 NULL
而不是另加 `win_rate_manual` 列，是因为"有没有手改过"恰好等于"这一列是不是空"，
再加一列就有两个真值源。

终态字段（`won_at` / `lost_reason_id` / `lost_note`）只由阶段变更写，并有三条
数据库约束兜底：`stage='lost'` 必须有原因、`stage='won'` 必须有 `won_at`、
金额非负、赢单率 0–100、阶段取值合法。约束显式命名，名字进首个迁移即固化。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.modules.customers.models import Customer
    from app.modules.lost_reasons.models import LostReason
    from app.modules.users.models import User

# design §3.6：四个推进阶段 + 两个终态，顺序就是看板的列顺序
OPPORTUNITY_STAGES = ("contact", "needs", "proposal", "negotiation", "won", "lost")
OPEN_STAGES = ("contact", "needs", "proposal", "negotiation")
TERMINAL_STAGES = ("won", "lost")
DEFAULT_STAGE = "contact"

# design §3.6 的「默认赢单率」列。手工值存在 `win_rate` 列里，为空时读这里。
STAGE_WIN_RATES = {
    "contact": 10,
    "needs": 30,
    "proposal": 50,
    "negotiation": 70,
    "won": 100,
    "lost": 0,
}

# CHECK 约束里的取值列表用这个片段拼，避免三处各写一遍字符串
_STAGE_SQL = "(" + ", ".join(f"'{stage}'" for stage in OPPORTUNITY_STAGES) + ")"


class Opportunity(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    stage: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DEFAULT_STAGE, server_default=DEFAULT_STAGE
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        default=Decimal(0),
        server_default=text("0"),
    )
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    competitor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 机会负责人与客户负责人一致（§3.5），NOT NULL：公海客户不能建机会，
    # 由 service 层给出 400，而不是靠这一列撞约束变成 500。
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    # NULL = 跟随阶段默认值；非空 = 手工值，改阶段不覆盖
    win_rate: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    lost_reason_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("lost_reasons.id"), nullable=True
    )
    lost_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    won_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )

    # 三个关系一律 lazy="raise"：`OpportunityRead` 要 customer_name / owner_name /
    # lost_reason_name，服务层的读取入口统一 joinedload 预加载；漏预加载会立刻报错，
    # 不会静默退化成逐条回查（与 P1 的 Customer.owner 同一口径）。
    customer: Mapped["Customer"] = relationship("Customer", lazy="raise")
    owner: Mapped["User"] = relationship(
        "User", foreign_keys=[owner_id], lazy="raise"
    )
    lost_reason: Mapped["LostReason | None"] = relationship(
        "LostReason", lazy="raise"
    )

    # 命名约定里的 ix 模板是 %(column_0_label)s，表达式/部分索引会撞名，因此全部显式命名
    __table_args__ = (
        Index("ix_opportunities_customer_id", "customer_id"),
        Index("ix_opportunities_owner_id", "owner_id"),
        Index("ix_opportunities_stage", "stage"),
        Index("ix_opportunities_expected_close_date", "expected_close_date"),
        CheckConstraint(f"stage IN {_STAGE_SQL}", name="stage"),
        CheckConstraint(
            "win_rate IS NULL OR (win_rate >= 0 AND win_rate <= 100)",
            name="win_rate_range",
        ),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint(
            "stage <> 'lost' OR lost_reason_id IS NOT NULL",
            name="lost_reason_required",
        ),
        CheckConstraint(
            "stage <> 'won' OR won_at IS NOT NULL", name="won_at_required"
        ),
    )
