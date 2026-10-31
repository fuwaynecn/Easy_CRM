"""首页三块的查询口径。

三件事各自有陷阱：
1. 「今天/逾期」是按 **Asia/Shanghai** 自然日切分，不是 UTC 日（凌晨 0–8 点的记录
   会整批错位）；
2. 「我的待办」按客户负责人，不按创建者（裁定 3）；
3. 「即将进公海」的阈值是 27 天（30 − 3），并且有两条豁免（在谈机会 / 未完成待办）
   ——写死 30 天或漏掉豁免，首页会把在谈客户推到"即将被收走"里（spec §4.2）。
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.followups.schemas import FollowUpCreate
from app.modules.followups.service import create_follow_up
from app.modules.home.service import days_since_local, summary
from app.modules.opportunities.schemas import OpportunityCreate
from app.modules.opportunities.service import create_opportunity
from app.modules.pools.rules import (
    INACTIVE_DAYS,
    NOTICE_THRESHOLD_DAYS,
    RECYCLE_NOTICE_DAYS,
    update_pool_rules,
)
from app.modules.pools.schemas import PoolRulesUpdate
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"
# 2026-10-31 01:00 UTC = 2026-10-31 09:00 Asia/Shanghai
NOW = datetime(2026, 10, 31, 1, 0, tzinfo=UTC)


def _setup(db):
    east = create_department(db, "华东销售部")
    south = create_department(db, "华南销售部")
    admin = create_user(
        db, username="admin", password=PASSWORD, display_name="管理员",
        role=ROLE_ADMIN, department_id=None,
    )
    boss = create_user(
        db, username="boss", password=PASSWORD, display_name="东区主管",
        role=ROLE_MANAGER, department_id=east.id,
    )
    sa = create_user(
        db, username="sales_a", password=PASSWORD, display_name="销售A",
        role=ROLE_SALES, department_id=east.id,
    )
    sb = create_user(
        db, username="sales_b", password=PASSWORD, display_name="销售B",
        role=ROLE_SALES, department_id=south.id,
    )
    return {
        "admin": admin, "boss": boss, "sa": sa, "sb": sb,
        "cust_a": create_customer(db, sa, CustomerCreate(name="甲制造有限公司")),
        "cust_b": create_customer(db, sb, CustomerCreate(name="乙制造有限公司")),
        "cust_pool": create_customer(db, admin, CustomerCreate(name="丙制造有限公司")),
    }


def _todo(db, actor, customer, *, due_at, content="回访"):
    return create_follow_up(
        db,
        actor,
        FollowUpCreate(customer_id=customer.id, due_at=due_at, content=content),
    )


def _age_customer(db, customer, *, days):
    """把最后跟进时间调到 N 天前（既有列，不是伪造接口）。"""
    customer.last_activity_at = NOW - timedelta(days=days)
    db.commit()


def test_rules_are_spec_derived():
    assert INACTIVE_DAYS == 30
    assert RECYCLE_NOTICE_DAYS == 3
    assert NOTICE_THRESHOLD_DAYS == 27


def test_today_and_overdue_split_by_shanghai_midnight(db):
    f = _setup(db)
    # 上海时间 2026-10-31 23:59 与 2026-10-30 23:59
    today = datetime(2026, 10, 31, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    overdue = datetime(2026, 10, 30, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    today_todo = _todo(db, f["sa"], f["cust_a"], due_at=today, content="今天的")
    overdue_todo = _todo(db, f["sa"], f["cust_a"], due_at=overdue, content="逾期的")

    result = summary(db, f["sa"], now=NOW)

    assert [item.id for item in result.today] == [today_todo.id]
    assert result.today_total == 1
    assert [item.id for item in result.overdue] == [overdue_todo.id]
    assert result.overdue_total == 1


def test_utc_day_boundary_is_not_used(db):
    """UTC 10-31 01:00 在上海已是 10-31 09:00；UTC 10-30 17:00 是上海 10-31 01:00。

    这条专门防"用 UTC 日切分"：按 UTC 算，后一条会被判成"昨天→逾期"。
    """
    f = _setup(db)
    shanghai_early = datetime(2026, 10, 31, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    todo = _todo(db, f["sa"], f["cust_a"], due_at=shanghai_early)

    result = summary(db, f["sa"], now=NOW)

    assert [item.id for item in result.today] == [todo.id]
    assert result.overdue == []


def test_only_my_todos(db):
    """裁定 3：首页的"我的待办"= 我名下客户的全部未完成待办。

    注意这里**故意**包含主管在我客户上建的待办：待办跟客户走，不跟创建者走
    （spec §3.9）。若哪天有人把过滤条件改成 `created_by = me`，这条会红。
    别部门的待办不能出现。
    """
    f = _setup(db)
    mine = _todo(db, f["sa"], f["cust_a"], due_at=NOW)
    by_boss = _todo(db, f["boss"], f["cust_a"], due_at=NOW, content="主管安排")
    other = _todo(db, f["sb"], f["cust_b"], due_at=NOW, content="别部门的")

    result = summary(db, f["sa"], now=NOW)
    ids = [item.id for item in result.today]

    assert set(ids) == {mine.id, by_boss.id}
    assert result.today_total == 2
    assert other.id not in ids

    # 主管自己的首页没有这条：cust_a 的负责人是销售 A，不是主管
    boss_result = summary(db, f["boss"], now=NOW)
    assert boss_result.today == []
    assert boss_result.today_total == 0


def test_pool_notice_uses_27_day_threshold(db):
    f = _setup(db)
    just_over = create_customer(db, f["sa"], CustomerCreate(name="刚好超过"))
    not_yet = create_customer(db, f["sa"], CustomerCreate(name="还没到"))
    _age_customer(db, just_over, days=28)
    _age_customer(db, not_yet, days=26)

    result = summary(db, f["sa"], now=NOW)
    ids = [customer.id for customer in result.pool_notice]

    assert ids == [just_over.id]
    assert not_yet.id not in ids


def test_pool_notice_excludes_customers_with_open_deal(db):
    f = _setup(db)
    risky = create_customer(db, f["sa"], CustomerCreate(name="有在谈机会"))
    _age_customer(db, risky, days=60)
    opportunity = create_opportunity(
        db, f["sa"], OpportunityCreate(customer_id=risky.id, name="在谈的单")
    )

    result = summary(db, f["sa"], now=NOW)

    assert risky.id not in [customer.id for customer in result.pool_notice]
    assert opportunity.id is not None


def test_pool_notice_excludes_customers_with_pending_todo(db):
    f = _setup(db)
    planned = create_customer(db, f["sa"], CustomerCreate(name="有未完成待办"))
    _age_customer(db, planned, days=60)
    _todo(db, f["sa"], planned, due_at=NOW + timedelta(days=3))

    result = summary(db, f["sa"], now=NOW)

    assert planned.id not in [customer.id for customer in result.pool_notice]


def test_pool_notice_excludes_pool_inactive_and_never_followed(db):
    f = _setup(db)
    never = create_customer(db, f["sa"], CustomerCreate(name="从未跟进"))
    leftover = create_customer(db, f["sa"], CustomerCreate(name="已流失"))
    _age_customer(db, leftover, days=60)
    leftover.status = "lost"
    db.commit()

    result = summary(db, f["sa"], now=NOW)
    ids = [customer.id for customer in result.pool_notice]

    # 从未跟进（last_activity_at 为 NULL）不进预警：spec §4.2 的条件就是
    # `last_activity_at < ...`，NULL 不满足（裁定 13）
    assert never.id not in ids
    assert leftover.id not in ids
    assert f["cust_pool"].id not in ids


def test_pool_notice_is_scoped_to_visible_customers(db):
    f = _setup(db)
    foreign = create_customer(db, f["sb"], CustomerCreate(name="别部门客户"))
    _age_customer(db, foreign, days=60)

    result = summary(db, f["sa"], now=NOW)

    assert foreign.id not in [customer.id for customer in result.pool_notice]


def test_days_since_local_uses_shanghai_days():
    moment = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)  # 上海 10-29 04:00
    assert days_since_local(moment, now=NOW) == 2  # 上海 10-29 → 10-31


def test_days_since_local_uses_day_diff():
    """23 小时也能跨一天：按 24 小时整除会得到 0，按自然日必须是 1。

    「昨天 23:00 建的，今天 22:00 看」只差 23 小时，但业务上确实过了一天——
    首页显示"今天/逾期"、公海显示"已 N 天没跟进"，都用自然日口径。
    """
    last = datetime(2026, 10, 30, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    now = datetime(2026, 10, 31, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert (now - last).days == 0  # 24 小时口径：不足一天
    assert days_since_local(last, now=now) == 1  # 自然日口径：跨了一天


def test_day_bounds_are_half_open(db):
    """`[今天 00:00, 明天 00:00)`：左闭右开，边界各归一边、且没有重叠。"""
    f = _setup(db)
    today_start = datetime(2026, 10, 31, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    tomorrow_start = today_start + timedelta(days=1)
    at_midnight = _todo(db, f["sa"], f["cust_a"], due_at=today_start, content="零点")
    next_midnight = _todo(
        db, f["sa"], f["cust_a"], due_at=tomorrow_start, content="明天零点"
    )

    result = summary(db, f["sa"], now=NOW)

    # 今天 00:00 属于今天（左端闭）
    assert at_midnight.id in [item.id for item in result.today]
    # 明天 00:00 不属于今天，也不该被算成逾期（右端开）
    assert next_midnight.id not in [item.id for item in result.today]
    assert next_midnight.id not in [item.id for item in result.overdue]


def test_home_block_limit_truncates(db):
    """块上限只截 items，`total` 仍是真实条数（否则页面会显示"最多 50 条"）。"""
    from app.modules.home.service import HOME_BLOCK_LIMIT

    f = _setup(db)
    for index in range(HOME_BLOCK_LIMIT + 1):
        _todo(db, f["sa"], f["cust_a"], due_at=NOW, content=f"待办 {index}")

    result = summary(db, f["sa"], now=NOW)

    assert len(result.today) == HOME_BLOCK_LIMIT == 50
    assert result.today_total == HOME_BLOCK_LIMIT + 1 == 51


def test_home_api_shape(client, db):
    f = _setup(db)
    # 走真实 HTTP，服务端用真实当前时间；这里必须写"上海今天"，不能固定 2026-10-31。
    today = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=1, minute=0, second=0, microsecond=0
    )
    _todo(db, f["sa"], f["cust_a"], due_at=today, content="今天的待办")
    login = client.post(
        "/api/v1/auth/login", json={"username": "sales_a", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.get("/api/v1/home", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["content"] for item in body["today"]["items"]] == ["今天的待办"]
    assert body["today"]["items"][0]["customer_name"] == "甲制造有限公司"
    assert body["overdue"]["total"] == 0
    assert body["pool_notice"]["rule"] == {
        "inactive_days": 30,
        "recycle_notice_days": 3,
        "threshold_days": 27,
    }

    anonymous = client.get("/api/v1/home")
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "unauthorized"


def test_admin_home_today_and_overdue_include_all_owners(db):
    f = _setup(db)
    todo = _todo(db, f["sa"], f["cust_a"], due_at=NOW)

    admin_result = summary(db, f["admin"], now=NOW)

    assert [item.id for item in admin_result.today] == [todo.id]
    assert admin_result.today_total == 1
    # 主管仍只看自己名下：cust_a 的负责人是销售 A，不是主管
    assert summary(db, f["boss"], now=NOW).today_total == 0


def test_home_pool_notice_uses_configured_rules(db):
    f = _setup(db)
    aged = create_customer(db, f["sa"], CustomerCreate(name="9 天没跟进"))
    _age_customer(db, aged, days=9)
    update_pool_rules(
        db, PoolRulesUpdate(inactive_days=10, recycle_notice_days=2)
    )

    result = summary(db, f["sa"], now=NOW)

    assert aged.id in [customer.id for customer in result.pool_notice]


def test_home_pool_notice_respects_protect_flags(db):
    f = _setup(db)
    with_deal = create_customer(db, f["sa"], CustomerCreate(name="有在谈机会"))
    _age_customer(db, with_deal, days=28)
    create_opportunity(
        db,
        f["sa"],
        OpportunityCreate(customer_id=with_deal.id, name="在谈机会"),
    )

    # 默认保护：不预警
    assert with_deal.id not in [
        customer.id for customer in summary(db, f["sa"], now=NOW).pool_notice
    ]

    update_pool_rules(db, PoolRulesUpdate(protect_with_open_deal=False))
    assert with_deal.id in [
        customer.id for customer in summary(db, f["sa"], now=NOW).pool_notice
    ]


def test_home_pool_notice_respects_pending_followup_flag(db):
    """未完成待办的保护也必须是开关，不能写死。

    镜像 `test_home_pool_notice_respects_protect_flags`：把待办那条保护关掉后，
    同一个客户必须立刻进预警。若哪天有人把 `if rules.protect_with_pending_followup:`
    改成常量真值或整段删掉，这条会红。
    """
    f = _setup(db)
    planned = create_customer(db, f["sa"], CustomerCreate(name="有待办但保护可关"))
    _age_customer(db, planned, days=28)
    _todo(db, f["sa"], planned, due_at=NOW + timedelta(days=3))

    # 默认保护：不预警
    assert planned.id not in [
        customer.id for customer in summary(db, f["sa"], now=NOW).pool_notice
    ]

    update_pool_rules(db, PoolRulesUpdate(protect_with_pending_followup=False))
    assert planned.id in [
        customer.id for customer in summary(db, f["sa"], now=NOW).pool_notice
    ]


def test_home_api_returns_rule_from_database(client, db):
    # 只需要客户/用户基线；接口用 sales_a 登录，这里不直接用返回的夹具。
    _setup(db)
    update_pool_rules(
        db, PoolRulesUpdate(inactive_days=12, recycle_notice_days=2)
    )
    login = client.post(
        "/api/v1/auth/login", json={"username": "sales_a", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.get("/api/v1/home", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["pool_notice"]["rule"] == {
        "inactive_days": 12,
        "recycle_notice_days": 2,
        "threshold_days": 10,
    }
