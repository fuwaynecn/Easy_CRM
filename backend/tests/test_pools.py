"""公海池服务层：列表、领取、行锁、配额、机会同步。

全部打真实 PostgreSQL：`FOR UPDATE`、advisory lock、`NULLS FIRST` 这些行为在
SQLite 上没有意义，替身测试会给出假绿。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
)
from app.db.session import SessionLocal
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer, get_customer
from app.modules.departments.service import create_department
from app.modules.lost_reasons.schemas import LostReasonCreate
from app.modules.lost_reasons.service import create_lost_reason
from app.modules.opportunities.schemas import OpportunityCreate
from app.modules.opportunities.service import create_opportunity
from app.modules.pools.rules import update_pool_rules
from app.modules.pools.schemas import PoolRulesUpdate
from app.modules.pools.service import claim_customer, list_pool
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"
NOW = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def _setup(db):
    """华东：主管 boss、销售 sales1/sales2；华南：销售 outsider；管理员 admin。"""
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
    boss = create_user(
        db,
        username="boss",
        password=PASSWORD,
        display_name="东区主管",
        role=ROLE_MANAGER,
        department_id=east.id,
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
    outsider = create_user(
        db,
        username="outsider",
        password=PASSWORD,
        display_name="华南销售",
        role=ROLE_SALES,
        department_id=west.id,
    )
    pool = create_customer(
        db,
        admin,
        CustomerCreate(name="苏州新能源电池科技有限公司"),
    )
    owned = create_customer(
        db,
        admin,
        CustomerCreate(name="杭州智造装备有限公司", owner_id=sales1.id),
    )
    return {
        "admin": admin,
        "boss": boss,
        "sales1": sales1,
        "sales2": sales2,
        "outsider": outsider,
        "east": east,
        "west": west,
        "pool": pool,
        "owned": owned,
    }


def _pool_customer(db, admin, name: str, *, last_activity_at=None):
    customer = create_customer(db, admin, CustomerCreate(name=name))
    if last_activity_at is not None:
        customer.last_activity_at = last_activity_at
        db.commit()
    return customer


def test_list_pool_returns_only_unowned_customers(db):
    ctx = _setup(db)
    items, total = list_pool(db, ctx["sales1"])
    assert total == 1
    assert [item.id for item in items] == [ctx["pool"].id]


def test_list_pool_keyword_matches_name_and_short_name(db):
    ctx = _setup(db)
    create_customer(
        db,
        ctx["admin"],
        CustomerCreate(name="宁波海天涂装有限公司", short_name="宁波海天"),
    )
    items, total = list_pool(db, ctx["sales1"], keyword="海天")
    assert total == 1
    assert items[0].short_name == "宁波海天"
    items, total = list_pool(db, ctx["sales1"], keyword="不存在的客户")
    assert total == 0
    assert items == []


def test_list_pool_orders_stale_first_with_nulls_first(db):
    ctx = _setup(db)
    stale = _pool_customer(
        db, ctx["admin"], "久未跟进客户", last_activity_at=NOW - timedelta(days=10)
    )
    never = _pool_customer(db, ctx["admin"], "从未跟进客户")
    items, _ = list_pool(db, ctx["sales1"])
    # NULL（从未跟进）排最前，同一 last_activity_at 再按 id 升序；
    # ctx["pool"] 与 never 都是 NULL，pool 先建，所以 id 更小。
    assert [item.id for item in items] == [ctx["pool"].id, never.id, stale.id]


def test_list_pool_paginates(db):
    ctx = _setup(db)
    for index in range(3):
        _pool_customer(db, ctx["admin"], f"公海客户{index}")
    items, total = list_pool(db, ctx["sales1"], page=1, page_size=2)
    assert total == 4
    assert len(items) == 2
    items, total = list_pool(db, ctx["sales1"], page=2, page_size=2)
    assert total == 4
    assert len(items) == 2


def test_claim_sets_owner_department_and_resets_last_activity(db):
    ctx = _setup(db)
    claimed = claim_customer(db, ctx["sales1"], ctx["pool"].id, now=NOW)
    assert claimed.owner_id == ctx["sales1"].id
    assert claimed.department_id == ctx["east"].id
    assert claimed.last_activity_at == NOW
    row = db.get(Customer, ctx["pool"].id)
    assert row.owner_id == ctx["sales1"].id
    assert row.department_id == ctx["east"].id
    assert row.last_activity_at == NOW


def test_claim_syncs_open_opportunities_and_keeps_terminal_owner(db):
    ctx = _setup(db)
    open_deal = create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=ctx["owned"].id, name="在谈机会"),
    )
    won_deal = create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=ctx["owned"].id, name="已赢机会"),
    )
    won_deal.stage = "won"
    won_deal.won_at = NOW
    db.commit()
    # 模拟“被释放/回收后进入公海”：客户无主，但历史机会还挂在 sales1
    ctx["owned"].owner_id = None
    ctx["owned"].department_id = None
    db.commit()

    claim_customer(db, ctx["sales2"], ctx["owned"].id, now=NOW)

    db.refresh(open_deal)
    db.refresh(won_deal)
    assert open_deal.owner_id == ctx["sales2"].id
    assert won_deal.owner_id == ctx["sales1"].id


def test_claim_writes_owner_change_and_audit_logs(db):
    from app.modules.audit.models import AuditLog
    from app.modules.pools.models import OwnerChangeLog

    ctx = _setup(db)
    claim_customer(db, ctx["sales1"], ctx["pool"].id, now=NOW)
    change = db.execute(
        select(OwnerChangeLog).where(OwnerChangeLog.customer_id == ctx["pool"].id)
    ).scalar_one()
    assert change.action == "claim"
    assert change.from_owner_id is None
    assert change.to_owner_id == ctx["sales1"].id
    assert change.operator_id == ctx["sales1"].id
    audit = db.execute(
        select(AuditLog).where(AuditLog.entity_id == ctx["pool"].id)
    ).scalar_one()
    assert audit.entity == "customers"
    assert audit.action == "transfer"
    assert audit.before_json == {"owner_id": None, "department_id": None}
    assert audit.after_json == {
        "owner_id": ctx["sales1"].id,
        "department_id": ctx["east"].id,
    }


def test_claim_rejects_already_owned_customer(db):
    ctx = _setup(db)
    # 用主管而不是 sales2：销售只看自己名下 + 公海，sales2 看不到
    # 同部门 sales1 名下的客户（get_customer 先 403）；主管能看到部门内
    # 客户，才会走到“owner 非空”的 409 分支。
    with pytest.raises(ConflictError, match="已被他人领取"):
        claim_customer(db, ctx["boss"], ctx["owned"].id, now=NOW)


def test_claim_other_department_owned_customer_is_403_not_409(db):
    ctx = _setup(db)
    with pytest.raises(ForbiddenError):
        claim_customer(db, ctx["outsider"], ctx["owned"].id, now=NOW)


def test_claim_admin_is_forbidden(db):
    ctx = _setup(db)
    with pytest.raises(ForbiddenError, match="仅销售或主管"):
        claim_customer(db, ctx["admin"], ctx["pool"].id, now=NOW)


def test_claim_without_department_is_400(db):
    ctx = _setup(db)
    ctx["sales1"].department_id = None
    db.commit()
    with pytest.raises(InvalidValueError, match="未分配部门"):
        claim_customer(db, ctx["sales1"], ctx["pool"].id, now=NOW)


def test_claim_quota_is_enforced(db):
    ctx = _setup(db)
    update_pool_rules(db, PoolRulesUpdate(max_owned=1))
    # sales1 已经有 owned 一个客户，再领取公海客户就超限
    with pytest.raises(ConflictError, match="上限"):
        claim_customer(db, ctx["sales1"], ctx["pool"].id, now=NOW)


def test_claim_quota_counts_lost_customer(db):
    """配额口径是“名下未删除客户”，lost 不豁免（裁定 7）。"""
    ctx = _setup(db)
    lost = create_customer(
        db,
        ctx["admin"],
        CustomerCreate(name="已流失客户有限公司", owner_id=ctx["sales2"].id),
    )
    lost.status = "lost"
    db.commit()
    update_pool_rules(db, PoolRulesUpdate(max_owned=1))
    # sales2 名下只有这条 lost 客户，仍占满 1 个配额。
    with pytest.raises(ConflictError, match="上限"):
        claim_customer(db, ctx["sales2"], ctx["pool"].id, now=NOW)


def test_claim_quota_ignores_soft_deleted_customer(db):
    """软删客户对外不存在，也不占配额。"""
    ctx = _setup(db)
    removed = create_customer(
        db,
        ctx["admin"],
        CustomerCreate(name="已删除客户有限公司", owner_id=ctx["sales2"].id),
    )
    removed.deleted_at = datetime.now(UTC)
    db.commit()
    update_pool_rules(db, PoolRulesUpdate(max_owned=1))
    claimed = claim_customer(db, ctx["sales2"], ctx["pool"].id, now=NOW)
    assert claimed.owner_id == ctx["sales2"].id


def test_claim_unknown_customer_is_404(db):
    ctx = _setup(db)
    with pytest.raises(NotFoundError):
        claim_customer(db, ctx["sales1"], 999_999, now=NOW)


def test_claim_rechecks_owner_after_lock(db):
    """并发赢家提交后，锁查询必须重新读行，而不是用 identity map 的旧值。

    场景：本会话已把公海客户读进 identity map（owner_id=None）；另一个真实会话在
    ``SELECT ... FOR UPDATE`` 之前提交了归属变更。``_lock_customer`` 带
    ``populate_existing=True`` 时能读到新 owner 并 409；去掉它就会拿旧值继续写，
    覆盖并发赢家（本测试转红）。
    """
    ctx = _setup(db)
    # 先入 identity map，且此刻确实是公海（owner_id=None）。
    assert get_customer(db, ctx["sales2"], ctx["pool"].id).owner_id is None

    fired = False

    def claim_by_other_before_lock(
        conn, cursor, statement, parameters, context, executemany
    ):
        nonlocal fired
        upper = statement.upper()
        if fired or "FOR UPDATE" not in upper or "FROM CUSTOMERS" not in upper:
            return
        fired = True
        # 第二个真实会话：模拟并发赢家先改归属并提交。
        with SessionLocal() as other:
            customer = other.get(Customer, ctx["pool"].id)
            customer.owner_id = ctx["sales1"].id
            customer.department_id = ctx["east"].id
            other.commit()

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", claim_by_other_before_lock)
    try:
        with pytest.raises(ConflictError, match="已被他人领取"):
            claim_customer(db, ctx["sales2"], ctx["pool"].id, now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", claim_by_other_before_lock)

    assert fired, "行锁前没有触发并发归属变更，测试前提不成立"
    # 输家不落库：归属仍是并发赢家 sales1。
    db.rollback()
    assert db.get(Customer, ctx["pool"].id).owner_id == ctx["sales1"].id


def test_claim_issues_for_update_lock(db):
    ctx = _setup(db)
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        claim_customer(db, ctx["sales1"], ctx["pool"].id, now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    upper = [statement.upper() for statement in statements]
    # 客户行锁：复查 owner 的前提。
    assert any("FROM CUSTOMERS" in s and "FOR UPDATE" in s for s in upper)
    # 目标用户行锁：并发领取同一销售的配额判定必须串行；只断言“有 FOR UPDATE”
    # 会被客户行锁满足，删掉用户行锁也照样绿。
    assert any("FROM USERS" in s and "FOR UPDATE" in s for s in upper)


# ---------------------------------------------------------------------------
# Task 4：分配 / 释放 / 可分配用户
# ---------------------------------------------------------------------------


def _open_and_won_opportunities(db, ctx):
    open_deal = create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=ctx["owned"].id, name="在谈机会"),
    )
    won_deal = create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=ctx["owned"].id, name="已赢机会"),
    )
    won_deal.stage = "won"
    won_deal.won_at = NOW
    lost_deal = create_opportunity(
        db,
        ctx["sales1"],
        OpportunityCreate(customer_id=ctx["owned"].id, name="已输机会"),
    )
    lost_reason = create_lost_reason(db, LostReasonCreate(name="价格过高"))
    lost_deal.stage = "lost"
    lost_deal.lost_reason_id = lost_reason.id
    db.commit()
    return open_deal, won_deal, lost_deal


def test_assign_by_admin_moves_owner_without_touching_last_activity(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    ctx["owned"].last_activity_at = NOW - timedelta(days=10)
    db.commit()
    updated = assign_customer(
        db,
        ctx["admin"],
        ctx["owned"].id,
        owner_id=ctx["sales2"].id,
        reason="区域调整",
    )
    assert updated.owner_id == ctx["sales2"].id
    assert updated.department_id == ctx["east"].id
    assert updated.last_activity_at == NOW - timedelta(days=10)


def test_assign_pool_by_admin_uses_assign_action(db):
    from app.modules.pools.models import OwnerChangeLog
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    assign_customer(db, ctx["admin"], ctx["pool"].id, owner_id=ctx["sales1"].id)
    change = db.execute(
        select(OwnerChangeLog).where(OwnerChangeLog.customer_id == ctx["pool"].id)
    ).scalar_one()
    assert change.action == "assign"
    assert change.from_owner_id is None
    assert change.to_owner_id == ctx["sales1"].id


def test_assign_from_owner_uses_transfer_action(db):
    from app.modules.audit.models import AuditLog
    from app.modules.pools.models import OwnerChangeLog
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    assign_customer(db, ctx["admin"], ctx["owned"].id, owner_id=ctx["sales2"].id)
    change = db.execute(
        select(OwnerChangeLog).where(OwnerChangeLog.customer_id == ctx["owned"].id)
    ).scalar_one()
    assert change.action == "transfer"
    assert change.from_owner_id == ctx["sales1"].id
    assert change.to_owner_id == ctx["sales2"].id
    # 审计行与业务行同事务：删掉 assign 里的 write_audit_log 必须转红。
    audit = db.execute(
        select(AuditLog).where(AuditLog.entity_id == ctx["owned"].id)
    ).scalar_one()
    assert audit.entity == "customers"
    assert audit.action == "transfer"
    assert audit.before_json == {
        "owner_id": ctx["sales1"].id,
        "department_id": ctx["east"].id,
    }
    assert audit.after_json == {
        "owner_id": ctx["sales2"].id,
        "department_id": ctx["east"].id,
    }
    assert audit.operator_id == ctx["admin"].id


def test_assign_syncs_open_opportunities_and_keeps_terminal(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    open_deal, won_deal, lost_deal = _open_and_won_opportunities(db, ctx)
    assign_customer(db, ctx["admin"], ctx["owned"].id, owner_id=ctx["sales2"].id)
    db.refresh(open_deal)
    db.refresh(won_deal)
    db.refresh(lost_deal)
    assert open_deal.owner_id == ctx["sales2"].id
    assert won_deal.owner_id == ctx["sales1"].id
    assert lost_deal.owner_id == ctx["sales1"].id


def test_assign_rejects_overlong_reason_without_mutating(db):
    """原因校验必须在改归属之前：否则异常时会话里已是「已换人」的脏状态。"""
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    customer = get_customer(db, ctx["admin"], ctx["owned"].id)
    with pytest.raises(InvalidValueError, match="200"):
        assign_customer(
            db,
            ctx["admin"],
            ctx["owned"].id,
            owner_id=ctx["sales2"].id,
            reason="x" * 201,
        )
    assert customer.owner_id == ctx["sales1"].id
    assert customer.department_id == ctx["east"].id


def test_manager_cannot_assign_pool_customer(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    with pytest.raises(ForbiddenError, match="公海客户"):
        assign_customer(db, ctx["boss"], ctx["pool"].id, owner_id=ctx["sales1"].id)


def test_manager_cannot_assign_across_departments(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    with pytest.raises(ForbiddenError, match="本部门"):
        assign_customer(db, ctx["boss"], ctx["owned"].id, owner_id=ctx["outsider"].id)


def test_manager_can_assign_within_department(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    updated = assign_customer(
        db, ctx["boss"], ctx["owned"].id, owner_id=ctx["sales2"].id
    )
    assert updated.owner_id == ctx["sales2"].id


def test_assign_rejects_inactive_target(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    ctx["sales2"].status = "disabled"
    db.commit()
    with pytest.raises(InvalidValueError, match="已停用"):
        assign_customer(db, ctx["admin"], ctx["owned"].id, owner_id=ctx["sales2"].id)


def test_assign_rejects_target_without_department(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    ctx["sales2"].department_id = None
    db.commit()
    with pytest.raises(InvalidValueError, match="未分配部门"):
        assign_customer(db, ctx["admin"], ctx["owned"].id, owner_id=ctx["sales2"].id)


def test_assign_rejects_admin_target(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    with pytest.raises(InvalidValueError, match="销售或主管"):
        assign_customer(db, ctx["admin"], ctx["owned"].id, owner_id=ctx["admin"].id)


def test_assign_same_owner_conflicts(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    with pytest.raises(ConflictError, match="已属于"):
        assign_customer(
            db, ctx["admin"], ctx["owned"].id, owner_id=ctx["sales1"].id
        )


def test_assign_quota_is_enforced(db):
    from app.modules.pools.service import assign_customer

    ctx = _setup(db)
    # sales2 先有一个客户；上限 1；把 sales1 的客户分配给 sales2 必须 409
    create_customer(
        db, ctx["admin"], CustomerCreate(name="销售二现有客户", owner_id=ctx["sales2"].id)
    )
    update_pool_rules(db, PoolRulesUpdate(max_owned=1))
    with pytest.raises(ConflictError, match="上限"):
        assign_customer(db, ctx["admin"], ctx["owned"].id, owner_id=ctx["sales2"].id)


def test_release_requires_reason(db):
    from app.modules.pools.service import release_customer

    ctx = _setup(db)
    with pytest.raises(InvalidValueError, match="释放原因"):
        release_customer(db, ctx["boss"], ctx["owned"].id, reason="   ")


def test_release_clears_owner_and_department_but_keeps_opportunity_owner(db):
    from app.modules.audit.models import AuditLog
    from app.modules.pools.models import OwnerChangeLog
    from app.modules.pools.service import release_customer

    ctx = _setup(db)
    open_deal, _, _ = _open_and_won_opportunities(db, ctx)
    ctx["owned"].last_activity_at = NOW - timedelta(days=10)
    db.commit()
    updated = release_customer(
        db, ctx["boss"], ctx["owned"].id, reason="客户长期不回复"
    )
    assert updated.owner_id is None
    assert updated.department_id is None
    # 释放不是跟进动作：不许把 last_activity_at 刷成“刚刚联系过”（裁定 5）。
    assert updated.last_activity_at == NOW - timedelta(days=10)
    db.refresh(open_deal)
    assert open_deal.owner_id == ctx["sales1"].id
    change = db.execute(
        select(OwnerChangeLog).where(OwnerChangeLog.customer_id == ctx["owned"].id)
    ).scalar_one()
    assert change.action == "release"
    assert change.from_owner_id == ctx["sales1"].id
    assert change.to_owner_id is None
    assert change.operator_id == ctx["boss"].id
    assert change.reason == "客户长期不回复"
    # 审计行与业务行同事务：删掉 release 里的 write_audit_log 必须转红。
    audit = db.execute(
        select(AuditLog).where(AuditLog.entity_id == ctx["owned"].id)
    ).scalar_one()
    assert audit.entity == "customers"
    assert audit.action == "transfer"
    assert audit.before_json == {
        "owner_id": ctx["sales1"].id,
        "department_id": ctx["east"].id,
    }
    assert audit.after_json == {"owner_id": None, "department_id": None}
    assert audit.operator_id == ctx["boss"].id


def test_release_rechecks_department_after_lock(db):
    """锁后只比 owner 会漏掉“只换部门”的并发变更（owner 不变）。

    场景：本会话已读到客户在华东（主管可释放）；另一个真实会话在
    ``SELECT ... FOR UPDATE`` 之前只把 department_id 改到华南并提交。只复查
    owner 时旧部门主管会成功释放一条已迁出的客户；同时复查 department 才能 409。
    """
    from app.modules.pools.service import release_customer

    ctx = _setup(db)
    assert get_customer(db, ctx["boss"], ctx["owned"].id).department_id == ctx["east"].id

    fired = False

    def move_department_before_lock(
        conn, cursor, statement, parameters, context, executemany
    ):
        nonlocal fired
        upper = statement.upper()
        if fired or "FOR UPDATE" not in upper or "FROM CUSTOMERS" not in upper:
            return
        fired = True
        # 第二个真实会话：只调部门，owner 保持 sales1（模拟 users.service 的调岗同步）。
        with SessionLocal() as other:
            customer = other.get(Customer, ctx["owned"].id)
            customer.department_id = ctx["west"].id
            other.commit()

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", move_department_before_lock)
    try:
        with pytest.raises(ConflictError, match="归属已变化"):
            release_customer(db, ctx["boss"], ctx["owned"].id, reason="并发调岗")
    finally:
        event.remove(engine, "before_cursor_execute", move_department_before_lock)

    assert fired, "行锁前没有触发并发部门变更，测试前提不成立"
    # 输家不落库：归属仍是 sales1 / 华南。
    db.rollback()
    customer = db.get(Customer, ctx["owned"].id)
    assert customer.owner_id == ctx["sales1"].id
    assert customer.department_id == ctx["west"].id


def test_release_pool_customer_is_400(db):
    from app.modules.pools.service import release_customer

    ctx = _setup(db)
    with pytest.raises(InvalidValueError, match="已在公海"):
        release_customer(db, ctx["boss"], ctx["pool"].id, reason="无")


def test_release_by_sales_is_forbidden(db):
    from app.modules.pools.service import release_customer

    ctx = _setup(db)
    with pytest.raises(ForbiddenError, match="仅主管及以上"):
        release_customer(db, ctx["sales1"], ctx["owned"].id, reason="无")


def test_release_cross_department_is_forbidden(db):
    from app.modules.pools.service import release_customer

    ctx = _setup(db)
    outsider_boss = create_user(
        db,
        username="outsider_boss",
        password=PASSWORD,
        display_name="华南主管",
        role=ROLE_MANAGER,
        department_id=ctx["west"].id,
    )
    with pytest.raises(ForbiddenError):
        release_customer(db, outsider_boss, ctx["owned"].id, reason="跨区")


def test_assignable_users_manager_sees_only_own_department(db):
    from app.modules.pools.service import list_assignable_users

    ctx = _setup(db)
    users = list_assignable_users(db, ctx["boss"])
    assert {user.id for user in users} == {
        ctx["boss"].id,
        ctx["sales1"].id,
        ctx["sales2"].id,
    }
    sales1 = next(user for user in users if user.id == ctx["sales1"].id)
    assert sales1.owned_count == 1
    assert sales1.max_owned == 50
    assert sales1.quota_remaining == 49
    assert sales1.department_name == "华东销售部"


def test_assignable_users_admin_sees_all_departments(db):
    from app.modules.pools.service import list_assignable_users

    ctx = _setup(db)
    users = list_assignable_users(db, ctx["admin"])
    assert {user.id for user in users} == {
        ctx["boss"].id,
        ctx["sales1"].id,
        ctx["sales2"].id,
        ctx["outsider"].id,
    }


def test_assignable_users_sales_is_forbidden(db):
    from app.modules.pools.service import list_assignable_users

    ctx = _setup(db)
    with pytest.raises(ForbiddenError):
        list_assignable_users(db, ctx["sales1"])
