"""每日回收与部门一致性。

回收四条条件（spec §4.1）：有 owner、active、超过 inactive_days 没跟进、无未关闭
机会、无 pending 待办。后两条受规则开关控制。测试用固定 now，避免凌晨边界漂移。

凡涉及“保护”的用例都同时放一个**该被回收**的客户：只有两个客户并存，才能证明
EXISTS 是关联到外层客户身上的；只有一个客户时，把 ``customer_id = customers.id``
删掉也照样绿。
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select, text

from app.modules.audit.models import AuditLog
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.followups.models import FollowUp
from app.modules.opportunities.schemas import OpportunityCreate
from app.modules.opportunities.service import create_opportunity
from app.modules.pools.models import OwnerChangeLog
from app.modules.pools.rules import update_pool_rules
from app.modules.pools.schemas import PoolRulesUpdate
from app.modules.pools.service import (
    RECYCLE_LOCK_KEY,
    recycle_customers,
    sync_department_consistency,
)
from app.modules.users.models import ROLE_ADMIN, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"
NOW = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def _setup(db):
    east = create_department(db, "华东销售部")
    west = create_department(db, "华南销售部")
    admin = create_user(
        db,
        username="admin",
        password=PASSWORD,
        display_name="管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )
    sales1 = create_user(
        db,
        username="sales1",
        password=PASSWORD,
        display_name="销售一",
        role=ROLE_SALES,
        department_id=east.id,
    )
    sales2 = create_user(
        db,
        username="sales2",
        password=PASSWORD,
        display_name="销售二",
        role=ROLE_SALES,
        department_id=east.id,
    )
    return {
        "admin": admin,
        "sales1": sales1,
        "sales2": sales2,
        "east": east,
        "west": west,
    }


def _customer(db, ctx, name, *, owner=None, age_days=None, status="active"):
    customer = create_customer(
        db,
        ctx["admin"],
        CustomerCreate(name=name, owner_id=(owner or ctx["sales1"]).id),
    )
    customer.status = status
    if age_days is not None:
        customer.last_activity_at = NOW - timedelta(days=age_days)
    db.commit()
    return customer


def _age(db, customer, days):
    customer.last_activity_at = NOW - timedelta(days=days)
    db.commit()


def test_recycle_hits_four_conditions_and_writes_logs(db):
    ctx = _setup(db)
    stale = _customer(db, ctx, "31 天没跟进", age_days=31)
    fresh = _customer(db, ctx, "29 天没跟进", age_days=29)
    lost = _customer(db, ctx, "输单客户", age_days=40, status="lost")
    pool = create_customer(db, ctx["admin"], CustomerCreate(name="公海客户"))
    never = _customer(db, ctx, "从未跟进")

    count = recycle_customers(db, now=NOW)

    assert count == 1
    db.refresh(stale)
    assert stale.owner_id is None
    assert stale.department_id is None
    for untouched in (fresh, lost, pool, never):
        db.refresh(untouched)
    assert fresh.owner_id == ctx["sales1"].id
    assert lost.owner_id == ctx["sales1"].id
    assert pool.owner_id is None
    assert never.owner_id == ctx["sales1"].id

    change = db.execute(
        select(OwnerChangeLog).where(OwnerChangeLog.customer_id == stale.id)
    ).scalar_one()
    assert change.action == "recycle"
    assert change.operator_id is None
    assert change.from_owner_id == ctx["sales1"].id
    audit = db.execute(
        select(AuditLog).where(AuditLog.entity_id == stale.id)
    ).scalar_one()
    assert audit.action == "transfer"
    assert audit.operator_id is None


def test_recycle_commits_once_and_writes_audit_payload(db):
    """两个客户只提交一次：审计与归属变更必须与回收同成同败。

    ``before_commit`` 钩子只在 setup 完成后挂上，因此计数里不含建客户的那几次提交。
    实现若改成“每回收一个客户提交一次”，这里立刻变 2。
    """
    ctx = _setup(db)
    first = _customer(db, ctx, "回收一", age_days=31)
    second = _customer(db, ctx, "回收二", age_days=31)
    commits: list[bool] = []

    def _count_commit(_session):
        commits.append(True)

    event.listen(db, "before_commit", _count_commit)
    try:
        assert recycle_customers(db, now=NOW) == 2
    finally:
        event.remove(db, "before_commit", _count_commit)

    assert len(commits) == 1
    db.refresh(first)
    db.refresh(second)
    assert first.owner_id is None and second.owner_id is None

    # 逐行点人数：只给第一个客户写日志的实现也能让上面的断言全绿（first 照样齐全），
    # 所以这里按 customer/entity_id 数到 2，且必须恰好是这两个客户。
    changes = (
        db.execute(
            select(OwnerChangeLog).where(
                OwnerChangeLog.customer_id.in_([first.id, second.id])
            )
        )
        .scalars()
        .all()
    )
    assert len(changes) == 2
    assert {change.customer_id for change in changes} == {first.id, second.id}
    audits = (
        db.execute(
            select(AuditLog).where(
                AuditLog.entity == "customers",
                AuditLog.entity_id.in_([first.id, second.id]),
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 2
    assert {audit.entity_id for audit in audits} == {first.id, second.id}

    audit = db.execute(
        select(AuditLog).where(
            AuditLog.entity == "customers",
            AuditLog.entity_id == first.id,
        )
    ).scalar_one()
    assert audit.before_json == {
        "owner_id": ctx["sales1"].id,
        "department_id": ctx["east"].id,
    }
    assert audit.after_json == {"owner_id": None, "department_id": None}
    assert audit.operator_id is None

    change = db.execute(
        select(OwnerChangeLog).where(OwnerChangeLog.customer_id == first.id)
    ).scalar_one()
    assert change.to_owner_id is None
    assert change.reason is not None
    assert "30 天无跟进" in change.reason


def test_recycle_disabled_returns_zero(db):
    ctx = _setup(db)
    _customer(db, ctx, "31 天没跟进", age_days=31)
    update_pool_rules(db, PoolRulesUpdate(auto_recycle_enabled=False))
    assert recycle_customers(db, now=NOW) == 0


def test_open_deal_protects_customer(db):
    """保护必须只作用于“有在谈机会的那个客户”。

    旁边那个同样陈旧的客户没有任何机会，必须被回收：否则把 EXISTS 里的
    ``Opportunity.customer_id == Customer.id`` 删掉（变成“存在任意在谈机会”）
    测试仍然全绿。
    """
    ctx = _setup(db)
    protected = _customer(db, ctx, "有在谈机会", age_days=31)
    unprotected = _customer(db, ctx, "无在谈机会", age_days=31)
    create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=protected.id, name="在谈机会"),
    )

    assert recycle_customers(db, now=NOW) == 1
    db.refresh(protected)
    db.refresh(unprotected)
    assert protected.owner_id == ctx["sales1"].id
    assert unprotected.owner_id is None


def test_terminal_deal_does_not_protect_customer(db):
    ctx = _setup(db)
    customer = _customer(db, ctx, "只有已赢机会", age_days=31)
    won = create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=customer.id, name="已赢机会"),
    )
    won.stage = "won"
    won.won_at = NOW
    db.commit()
    assert recycle_customers(db, now=NOW) == 1


def test_soft_deleted_open_deal_does_not_protect(db):
    """已删除的在谈机会不算保护：EXISTS 必须带 ``deleted_at IS NULL``。"""
    ctx = _setup(db)
    customer = _customer(db, ctx, "机会已删除", age_days=31)
    deal = create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=customer.id, name="已删机会"),
    )
    deal.deleted_at = datetime.now(UTC)
    db.commit()

    assert recycle_customers(db, now=NOW) == 1
    db.refresh(customer)
    assert customer.owner_id is None


def test_pending_followup_protects_customer(db):
    """与在谈机会同形：保护的只能是“有待办的那个”，旁边的裸陈旧客户要回收。"""
    ctx = _setup(db)
    protected = _customer(db, ctx, "有待办", age_days=31)
    unprotected = _customer(db, ctx, "无待办", age_days=31)
    db.add(
        FollowUp(
            customer_id=protected.id,
            due_at=NOW + timedelta(days=1),
            content="下周回访",
            status="pending",
            created_by=ctx["sales1"].id,
        )
    )
    db.commit()

    assert recycle_customers(db, now=NOW) == 1
    db.refresh(protected)
    db.refresh(unprotected)
    assert protected.owner_id == ctx["sales1"].id
    assert unprotected.owner_id is None


def test_done_followup_does_not_protect_customer(db):
    ctx = _setup(db)
    customer = _customer(db, ctx, "已完成待办", age_days=31)
    db.add(
        FollowUp(
            customer_id=customer.id,
            due_at=NOW - timedelta(days=1),
            content="已完成",
            status="done",
            done_at=NOW - timedelta(days=1),
            created_by=ctx["sales1"].id,
        )
    )
    db.commit()
    assert recycle_customers(db, now=NOW) == 1


def test_soft_deleted_pending_followup_does_not_protect(db):
    """已删除的待办不算保护：EXISTS 必须带 ``deleted_at IS NULL``。"""
    ctx = _setup(db)
    customer = _customer(db, ctx, "待办已删除", age_days=31)
    db.add(
        FollowUp(
            customer_id=customer.id,
            due_at=NOW + timedelta(days=1),
            content="已删待办",
            status="pending",
            deleted_at=datetime.now(UTC),
            created_by=ctx["sales1"].id,
        )
    )
    db.commit()

    assert recycle_customers(db, now=NOW) == 1
    db.refresh(customer)
    assert customer.owner_id is None


def test_protect_flags_off_allows_recycle_and_keeps_opportunity_owner(db):
    ctx = _setup(db)
    customer = _customer(db, ctx, "有在谈机会但关闭保护", age_days=31)
    deal = create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=customer.id, name="在谈机会"),
    )
    db.add(
        FollowUp(
            customer_id=customer.id,
            due_at=NOW + timedelta(days=1),
            content="待办",
            status="pending",
            created_by=ctx["sales1"].id,
        )
    )
    db.commit()
    update_pool_rules(
        db,
        PoolRulesUpdate(
            protect_with_open_deal=False,
            protect_with_pending_followup=False,
        ),
    )
    assert recycle_customers(db, now=NOW) == 1
    db.refresh(customer)
    db.refresh(deal)
    assert customer.owner_id is None
    # 裁定 2：回收不清机会 owner
    assert deal.owner_id == ctx["sales1"].id


def test_custom_inactive_days_is_used(db):
    ctx = _setup(db)
    _customer(db, ctx, "6 天没跟进", age_days=6)
    _customer(db, ctx, "4 天没跟进", age_days=4)
    update_pool_rules(db, PoolRulesUpdate(inactive_days=5))
    assert recycle_customers(db, now=NOW) == 1


def test_soft_deleted_customer_is_not_recycled(db):
    """已删除客户即使陈旧也不回收：列表里看不到它，回收会写出一条幽灵审计。"""
    ctx = _setup(db)
    gone = _customer(db, ctx, "已删除客户", age_days=31)
    gone.deleted_at = datetime.now(UTC)
    db.commit()

    assert recycle_customers(db, now=NOW) == 0
    db.refresh(gone)
    assert gone.owner_id == ctx["sales1"].id
    assert gone.department_id == ctx["east"].id


def test_advisory_lock_held_skips_recycle(db):
    ctx = _setup(db)
    _customer(db, ctx, "31 天没跟进", age_days=31)
    from app.db.session import SessionLocal

    lock_session = SessionLocal()
    try:
        got = lock_session.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": RECYCLE_LOCK_KEY},
        ).scalar()
        assert got is True
        assert recycle_customers(db, now=NOW) == 0
    finally:
        lock_session.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": RECYCLE_LOCK_KEY},
        )
        lock_session.commit()
        lock_session.close()


def test_sync_department_consistency_fixes_owned_customer(db, caplog):
    ctx = _setup(db)
    customer = _customer(db, ctx, "部门不一致客户", age_days=1)
    customer.department_id = ctx["west"].id
    db.commit()
    with caplog.at_level(logging.WARNING, logger="app.modules.pools.service.recycle"):
        fixed = sync_department_consistency(db)
    assert fixed == 1
    db.refresh(customer)
    assert customer.department_id == ctx["east"].id
    assert any("部门" in record.getMessage() for record in caplog.records)


def test_sync_department_consistency_clears_pool_department(db):
    ctx = _setup(db)
    pool = create_customer(db, ctx["admin"], CustomerCreate(name="公海带部门"))
    pool.department_id = ctx["east"].id
    db.commit()
    assert sync_department_consistency(db) == 1
    db.refresh(pool)
    assert pool.department_id is None


def test_sync_department_consistency_is_noop_when_consistent(db):
    ctx = _setup(db)
    _customer(db, ctx, "一致客户", age_days=1)
    assert sync_department_consistency(db) == 0


def test_sync_department_consistency_skips_soft_deleted(db):
    """已删除客户不参与部门校正：否则会改写一条谁也看不到的行。"""
    ctx = _setup(db)
    gone = _customer(db, ctx, "已删除部门不一致", age_days=1)
    gone.department_id = ctx["west"].id
    gone.deleted_at = datetime.now(UTC)
    db.commit()

    assert sync_department_consistency(db) == 0
    db.refresh(gone)
    assert gone.department_id == ctx["west"].id


def test_build_scheduler_registers_two_jobs():
    from app.scheduler import (
        _run_consistency_job,
        _run_recycle_job,
        build_scheduler,
    )

    scheduler = build_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"daily_recycle", "department_consistency"}
    assert jobs["daily_recycle"].func is _run_recycle_job
    assert jobs["department_consistency"].func is _run_consistency_job
    assert "minute='0'" in str(jobs["daily_recycle"].trigger)
    assert "minute='10'" in str(jobs["department_consistency"].trigger)
    assert str(jobs["daily_recycle"].trigger.timezone) == "Asia/Shanghai"
    assert str(jobs["department_consistency"].trigger.timezone) == "Asia/Shanghai"


def test_config_scheduler_defaults():
    """默认值必须来自 Settings 定义，而不是 conftest 改过的运行时值。"""
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["scheduler_enabled"].default is True
    assert fields["scheduler_timezone"].default == "Asia/Shanghai"


def test_lifespan_starts_and_stops_scheduler_when_enabled(monkeypatch):
    """开启开关时 lifespan 按 start → body → shutdown 顺序执行。

    ``start_scheduler`` / ``shutdown_scheduler`` 都被替换掉，测试不会真的起线程。
    """
    from fastapi.testclient import TestClient

    from app import main as main_module
    from app.main import create_app

    events: list[str] = []
    monkeypatch.setattr(main_module.settings, "scheduler_enabled", True)
    monkeypatch.setattr(main_module, "start_scheduler", lambda: events.append("start"))
    monkeypatch.setattr(
        main_module, "shutdown_scheduler", lambda: events.append("shutdown")
    )

    with TestClient(create_app()) as client:
        events.append("body")
        assert client.get("/health").json() == {"status": "ok"}

    assert events == ["start", "body", "shutdown"]


def test_lifespan_skips_scheduler_when_disabled(monkeypatch):
    """关闭开关时 lifespan 既不 start 也不 shutdown。

    没有这条反向用例，把 ``lifespan`` 里两处 ``if settings.scheduler_enabled``
    删掉也全绿：开启用例只证明“开着时动了”，证明不了“关着时没动”。
    """
    from fastapi.testclient import TestClient

    from app import main as main_module
    from app.main import create_app

    events: list[str] = []
    monkeypatch.setattr(main_module.settings, "scheduler_enabled", False)
    monkeypatch.setattr(main_module, "start_scheduler", lambda: events.append("start"))
    monkeypatch.setattr(
        main_module, "shutdown_scheduler", lambda: events.append("shutdown")
    )

    with TestClient(create_app()) as client:
        events.append("body")
        assert client.get("/health").json() == {"status": "ok"}

    assert events == ["body"]


def test_scheduler_disabled_in_test_environment():
    from app.core.config import settings

    assert settings.scheduler_enabled is False
