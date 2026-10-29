"""机会表的结构性约束。

这里测的是**数据库层**能挡住什么，不是 service 的 400/403/409。分两层是因为
绕过 service 的写入路径一直存在（Excel 导入属 P5、运维手工 SQL、将来的批量操作）：
service 的校验挡得住请求，挡不住 `INSERT`。约束都显式命名，名字进首个迁移就固化。

同时钉住两件与后面任务相关的事实：
- 阶段默认值与赢单率映射逐字取自 design §3.6；
- 关系是 `lazy="raise"`：任何没预加载就访问关系的地方立刻报错（P1 的
  `Customer.owner` 同样处理，理由是不让它静默退化成 N+1）。

注意本文件的夹具走 `Base.metadata.create_all`，只能证明**模型声明**的约束与默认值；
迁移文件是否把它们原样落库（约束名、`server_default`）由 `test_migrations.py` 直接对
`command.upgrade` 的产物断言，别指望这里能发现迁移漂移。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm import Session

from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.lost_reasons.schemas import LostReasonCreate
from app.modules.lost_reasons.service import create_lost_reason
from app.modules.opportunities.models import (
    DEFAULT_STAGE,
    OPEN_STAGES,
    OPPORTUNITY_STAGES,
    STAGE_WIN_RATES,
    TERMINAL_STAGES,
    Opportunity,
    StageHistory,
)
from app.modules.users.models import ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"


def _owner(db: Session):
    department = create_department(db, "华东销售部")
    return create_user(
        db,
        username="sales_a",
        password=PASSWORD,
        display_name="销售A",
        role=ROLE_SALES,
        department_id=department.id,
    )


def _customer(db: Session, owner):
    return create_customer(db, owner, CustomerCreate(name="甲制造有限公司"))


def _opportunity(db: Session, owner, customer, **overrides) -> Opportunity:
    payload = {
        "customer_id": customer.id,
        "name": "喷涂产线一期",
        "owner_id": owner.id,
        **overrides,
    }
    opportunity = Opportunity(**payload)
    db.add(opportunity)
    db.flush()
    return opportunity


def test_stage_constants_match_design_3_6():
    assert OPPORTUNITY_STAGES == (
        "contact",
        "needs",
        "proposal",
        "negotiation",
        "won",
        "lost",
    )
    assert OPEN_STAGES + TERMINAL_STAGES == OPPORTUNITY_STAGES
    assert STAGE_WIN_RATES == {
        "contact": 10,
        "needs": 30,
        "proposal": 50,
        "negotiation": 70,
        "won": 100,
        "lost": 0,
    }
    assert DEFAULT_STAGE == "contact"


def test_defaults_and_column_types(db):
    owner = _owner(db)
    customer = _customer(db, owner)

    opportunity = _opportunity(db, owner, customer)
    db.refresh(opportunity)

    assert opportunity.stage == "contact"
    assert opportunity.amount == Decimal(0)
    assert opportunity.win_rate is None          # None = 跟随阶段
    assert opportunity.won_at is None
    assert opportunity.lost_reason_id is None
    assert opportunity.deleted_at is None
    # 金额列是 numeric(14,2)：入库后必须带两位小数，不能变成浮点
    assert str(opportunity.amount) == "0.00"


def test_amount_keeps_two_decimals(db):
    owner = _owner(db)
    customer = _customer(db, owner)

    opportunity = _opportunity(db, owner, customer, amount=Decimal("1234.5"))
    db.refresh(opportunity)

    assert str(opportunity.amount) == "1234.50"


def test_unknown_stage_is_rejected_by_database(db):
    owner = _owner(db)
    customer = _customer(db, owner)

    with pytest.raises(IntegrityError):
        _opportunity(db, owner, customer, stage="prospecting")
    db.rollback()


def test_win_rate_out_of_range_is_rejected(db):
    owner = _owner(db)
    customer = _customer(db, owner)

    with pytest.raises(IntegrityError):
        _opportunity(db, owner, customer, win_rate=101)
    db.rollback()


def test_negative_amount_is_rejected(db):
    owner = _owner(db)
    customer = _customer(db, owner)

    with pytest.raises(IntegrityError):
        _opportunity(db, owner, customer, amount=Decimal(-1))
    db.rollback()


def test_lost_requires_reason(db):
    owner = _owner(db)
    customer = _customer(db, owner)

    with pytest.raises(IntegrityError):
        _opportunity(db, owner, customer, stage="lost")
    db.rollback()


def test_lost_with_reason_is_accepted(db):
    owner = _owner(db)
    customer = _customer(db, owner)
    reason = create_lost_reason(db, LostReasonCreate(name="价格过高"))

    opportunity = _opportunity(
        db, owner, customer, stage="lost", lost_reason_id=reason.id
    )
    db.refresh(opportunity)

    assert opportunity.lost_reason_id == reason.id


def test_won_requires_won_at(db):
    owner = _owner(db)
    customer = _customer(db, owner)

    with pytest.raises(IntegrityError):
        _opportunity(db, owner, customer, stage="won")
    db.rollback()

    opportunity = _opportunity(
        db, owner, customer, stage="won", won_at=datetime.now(UTC)
    )
    db.refresh(opportunity)

    assert opportunity.stage == "won"
    assert opportunity.won_at is not None


def test_relations_are_lazy_raise(db):
    """没预加载就访问关系必须立刻报错，而不是偷偷回查一次 users。"""
    owner = _owner(db)
    customer = _customer(db, owner)
    opportunity = _opportunity(db, owner, customer)
    db.expire_all()
    fresh = db.get(Opportunity, opportunity.id)

    with pytest.raises(InvalidRequestError) as excinfo:
        _ = fresh.owner.display_name

    # 只断言异常类型（InvalidRequestError）；消息文案随 SQLAlchemy 版本变，别钉死
    assert "lazy" in str(excinfo.value).lower()


# --- 阶段历史（design §3.7） --------------------------------------------


def test_history_accepts_null_from_stage_and_snapshots_amount(db):
    """创建时的第一条历史：from_stage 为空、to_stage 是初始阶段、带金额快照。"""
    owner = _owner(db)
    customer = _customer(db, owner)
    opportunity = _opportunity(db, owner, customer, amount=Decimal(8800))

    history = StageHistory(
        opportunity_id=opportunity.id,
        from_stage=None,
        to_stage=opportunity.stage,
        amount_snapshot=opportunity.amount,
        operator_id=owner.id,
    )
    db.add(history)
    db.commit()
    db.refresh(history)

    assert history.id is not None
    assert history.from_stage is None
    assert history.to_stage == "contact"
    assert str(history.amount_snapshot) == "8800.00"
    assert history.changed_at is not None      # server_default now()


def test_history_rejects_unknown_stage(db):
    owner = _owner(db)
    customer = _customer(db, owner)
    opportunity = _opportunity(db, owner, customer)

    db.add(
        StageHistory(
            opportunity_id=opportunity.id,
            from_stage="contact",
            to_stage="prospecting",
            operator_id=owner.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_history_rejects_unknown_from_stage(db):
    """`from_stage` 为空的语义是"创建"，非法值不属于这个豁免。

    只测 ``to_stage`` 时，整条 ``from_stage`` CHECK 删掉都不会红：非法旧阶段会静默落库，
    之后按阶段分组的漏斗报表就多出一列没人认识的阶段。
    """
    owner = _owner(db)
    customer = _customer(db, owner)
    opportunity = _opportunity(db, owner, customer)

    db.add(
        StageHistory(
            opportunity_id=opportunity.id,
            from_stage="prospecting",
            to_stage="contact",
            operator_id=owner.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_history_index_exists(db):
    """`(opportunity_id, changed_at)` 是漏斗与成交周期报表的取数路径，索引必须真的建了。"""
    from sqlalchemy import inspect

    indexes = {
        index["name"] for index in inspect(db.get_bind()).get_indexes("stage_histories")
    }

    assert "ix_stage_histories_opportunity_id_changed_at" in indexes


def test_history_requires_existing_opportunity(db):
    owner = _owner(db)

    db.add(
        StageHistory(
            opportunity_id=999999,
            from_stage=None,
            to_stage="contact",
            operator_id=owner.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
