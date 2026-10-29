"""机会表的结构性约束。

这里测的是**数据库层**能挡住什么，不是 service 的 400/403/409。分两层是因为
绕过 service 的写入路径一直存在（Excel 导入属 P5、运维手工 SQL、将来的批量操作）：
service 的校验挡得住请求，挡不住 `INSERT`。约束都显式命名，名字进首个迁移就固化。

同时钉住两件与后面任务相关的事实：
- 阶段默认值与赢单率映射逐字取自 design §3.6；
- 关系是 `lazy="raise"`：任何没预加载就访问关系的地方立刻报错（P1 的
  `Customer.owner` 同样处理，理由是不让它静默退化成 N+1）。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
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


def test_server_defaults_fill_bare_insert(db):
    """裸 INSERT 不能因为漏写 stage/amount 就撞 NOT NULL。

    绕开 ORM 的写入路径一直存在（P5 的 Excel 导入、运维手工 SQL），Python 侧的
    ``default=`` 在那条路径上根本不执行；能兜底的只有 ``server_default``。
    """
    owner = _owner(db)
    customer = _customer(db, owner)

    row = db.execute(
        text(
            "INSERT INTO opportunities (customer_id, name, owner_id) "
            "VALUES (:customer_id, :name, :owner_id) "
            "RETURNING id, stage, amount"
        ),
        {"customer_id": customer.id, "name": "裸插入机会", "owner_id": owner.id},
    ).one()
    inserted_id, stage, amount = row

    try:
        assert stage == "contact"
        # numeric(14,2) 的 server_default 0 落库后是两位小数，不是浮点 0.0
        assert amount == Decimal("0.00")
    finally:
        # 这条用例绕过了夹具的 ORM 事务，必须自己清掉，否则污染同库后面的用例
        db.execute(
            text("DELETE FROM opportunities WHERE id = :id"), {"id": inserted_id}
        )
        db.commit()


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


def test_check_constraint_names_are_frozen(db):
    """CHECK 约束名必须真的进了数据库。

    `test_migrations.py` 的 ``compare_metadata`` 不比较 CHECK 约束（也没开
    ``compare_server_default``），所以约束在迁移里写成匿名、或者名字与模型不一致，
    整套测试都发现不了——只有线上某个裸 INSERT 才暴露。这里直接查 ``pg_constraint``
    钉住模型声明的名字；断言"包含"而不是"相等"，将来新增约束不该让这条变脆，
    但已经固化的名字丢了必须炸。
    """
    expected = {
        "opportunities": {
            "ck_opportunities_stage",
            "ck_opportunities_win_rate_range",
            "ck_opportunities_amount_non_negative",
            "ck_opportunities_lost_reason_required",
            "ck_opportunities_won_at_required",
        },
        "stage_histories": {
            "ck_stage_histories_from_stage",
            "ck_stage_histories_to_stage",
        },
    }

    for table_name, wanted in expected.items():
        names = {
            row[0]
            for row in db.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = CAST(:table_name AS regclass) "
                    "AND contype = 'c'"
                ),
                {"table_name": table_name},
            )
        }
        assert wanted <= names, f"{table_name} 缺少 CHECK 约束：{wanted - names}"


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
