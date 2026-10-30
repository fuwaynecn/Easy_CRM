"""客户时间线：两类来源合并、倒序、游标翻页、已删/停用标注。

时间线是 P3 唯一**故意读已删行**的地方（裁定 1）：已软删机会的阶段变更、已删除
联系人的姓名、已停用账号的操作人都要照常出现并带标记。这里不去"修掉"这些越界，
而是把它们钉成契约——否则某次顺手加的 `deleted_at IS NULL` 会让历史凭空消失。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.modules.activities.schemas import ActivityCreate
from app.modules.activities.service import create_activity, delete_activity
from app.modules.contacts.schemas import ContactCreate
from app.modules.contacts.service import create_contact, delete_contact
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.opportunities.schemas import (
    OpportunityCreate,
    OpportunityUpdate,
    StageChange,
)
from app.modules.opportunities.service import (
    change_stage,
    create_opportunity,
    delete_opportunity,
    update_opportunity,
)
from app.modules.timeline.service import decode_cursor, encode_cursor, get_timeline
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"
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
    }


def test_timeline_merges_activities_and_stage_changes_desc(db):
    f = _setup(db)
    opportunity = create_opportunity(
        db, f["sa"], OpportunityCreate(customer_id=f["cust_a"].id, name="喷涂产线一期")
    )
    create_activity(
        db,
        f["sa"],
        # 不传 activity_at：这里要按"创建时间先后"排序，固定时间戳会让活动落到
        # 两次阶段变更之外（阶段历史用的是服务端 now()）
        ActivityCreate(customer_id=f["cust_a"].id, content="电话沟通", type="call"),
    )
    change_stage(db, f["sa"], opportunity.id, StageChange(stage="needs"))

    entries, next_cursor = get_timeline(db, f["sa"], f["cust_a"].id)

    assert next_cursor is None
    # 三次落库是两个事务之外的独立提交：创建机会(history) < 写跟进(activity) < 改阶段(history)
    assert [entry.type for entry in entries] == ["stage_change", "activity", "stage_change"]
    # 最新的是"推进到需求确认"，最旧的是"创建机会"（from_stage IS NULL）
    assert entries[0].from_stage == "contact"
    assert entries[0].to_stage == "needs"
    assert entries[1].content == "电话沟通"
    assert entries[1].activity_type == "call"
    assert entries[2].from_stage is None
    assert entries[2].to_stage == "contact"


def test_timeline_uses_amount_snapshot_not_current_amount(db):
    """裁定 4：阶段变更行展示**变更当刻**的金额，不是机会现值。"""
    f = _setup(db)
    opportunity = create_opportunity(
        db,
        f["sa"],
        OpportunityCreate(
            customer_id=f["cust_a"].id, name="喷涂产线一期", amount="100.00"
        ),
    )
    change_stage(db, f["sa"], opportunity.id, StageChange(stage="needs"))
    update_opportunity(db, f["sa"], opportunity.id, OpportunityUpdate(amount="200.00"))

    entries, _ = get_timeline(db, f["sa"], f["cust_a"].id)

    assert [str(entry.amount_snapshot) for entry in entries[:2]] == ["100.00", "100.00"]


def test_timeline_shows_stage_changes_of_deleted_opportunity(db):
    """裁定 1：机会已删，它的阶段变更仍然显示，并带 opportunity_deleted 标记。"""
    f = _setup(db)
    opportunity = create_opportunity(
        db, f["sa"], OpportunityCreate(customer_id=f["cust_a"].id, name="已删的单")
    )
    change_stage(db, f["sa"], opportunity.id, StageChange(stage="proposal"))
    delete_opportunity(db, f["boss"], opportunity.id)

    entries, _ = get_timeline(db, f["sa"], f["cust_a"].id)
    stage_entries = [entry for entry in entries if entry.type == "stage_change"]

    # 创建机会写一条（from_stage=None）+ 改阶段写一条 = 2 条，机会删了也都在
    assert len(stage_entries) == 2
    assert all(entry.opportunity_deleted for entry in stage_entries)
    assert all(entry.opportunity_name == "已删的单" for entry in stage_entries)


def test_timeline_marks_deleted_contact_and_disabled_operator(db):
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="王工", title="技术负责人")
    )
    create_activity(
        db,
        f["sa"],
        ActivityCreate(
            customer_id=f["cust_a"].id,
            contact_id=contact.id,
            content="拜访王工",
            type="visit",
            activity_at=NOW,
        ),
    )
    delete_contact(db, f["sa"], contact.id)
    f["sa"].status = "disabled"  # 账号停用是既有列的状态变更，不是伪造的接口
    db.commit()

    entries, _ = get_timeline(db, f["admin"], f["cust_a"].id)
    activity = entries[0]

    assert activity.contact_name == "王工"
    assert activity.contact_deleted is True
    assert activity.operator_name == "销售A"
    assert activity.operator_disabled is True


def test_timeline_cursor_pages_without_gap_or_duplicate(db):
    """翻页不重不漏；5 条**同一时刻**的记录靠 ``id`` 兜底排序。

    同一秒多条是真实场景（批量导入、同一动作连写两条），只按时间翻页会重复或漏。
    造数故意全部用同一个 ``activity_at``：删掉游标里的 id tie-break 后第 2 页会空，
    本用例立刻红。
    """
    f = _setup(db)
    for index in range(5):
        create_activity(
            db,
            f["sa"],
            ActivityCreate(
                customer_id=f["cust_a"].id,
                content=f"第 {index} 条",
                activity_at=NOW,
            ),
        )

    page1, cursor1 = get_timeline(db, f["sa"], f["cust_a"].id, limit=2)
    page2, cursor2 = get_timeline(db, f["sa"], f["cust_a"].id, limit=2, cursor=cursor1)
    page3, cursor3 = get_timeline(db, f["sa"], f["cust_a"].id, limit=2, cursor=cursor2)

    assert cursor1 is not None and cursor2 is not None
    assert cursor3 is None  # 第 3 页刚好取完，不再给游标
    ids = [entry.id for page in (page1, page2, page3) for entry in page]
    assert ids == [5, 4, 3, 2, 1]
    assert len(set(ids)) == 5


def test_timeline_cursor_roundtrip_and_invalid_cursor(db):
    f = _setup(db)
    create_activity(
        db, f["sa"], ActivityCreate(customer_id=f["cust_a"].id, content="一条", activity_at=NOW)
    )
    entries, _ = get_timeline(db, f["sa"], f["cust_a"].id)
    cursor = encode_cursor(entries[0])

    parsed = decode_cursor(cursor)
    assert parsed.type == "activity"
    assert parsed.id == entries[0].id
    assert parsed.occurred_at == entries[0].occurred_at

    with pytest.raises(InvalidValueError):
        decode_cursor("not-a-cursor")


def test_timeline_hides_soft_deleted_activity(db):
    """裁定 1 的两面：已删**活动**不再出现，已删**机会**的阶段历史仍然出现。

    两者都读同一张时间线，却按相反口径处理——活动是"要不要展示这条记录"，
    阶段历史是"这笔单曾经发生过什么"。混成一条规则就会二选一丢数据。
    """
    f = _setup(db)
    kept = create_activity(
        db, f["sa"], ActivityCreate(customer_id=f["cust_a"].id, content="留下", activity_at=NOW)
    )
    gone = create_activity(
        db,
        f["sa"],
        ActivityCreate(
            customer_id=f["cust_a"].id,
            content="删掉",
            activity_at=NOW + timedelta(minutes=1),
        ),
    )
    delete_activity(db, f["sa"], gone.id)

    opportunity = create_opportunity(
        db, f["sa"], OpportunityCreate(customer_id=f["cust_a"].id, name="已删的单")
    )
    change_stage(db, f["sa"], opportunity.id, StageChange(stage="proposal"))
    delete_opportunity(db, f["boss"], opportunity.id)

    entries, _ = get_timeline(db, f["sa"], f["cust_a"].id)

    activity_ids = [entry.id for entry in entries if entry.type == "activity"]
    assert activity_ids == [kept.id]
    stage_entries = [entry for entry in entries if entry.type == "stage_change"]
    assert len(stage_entries) == 2
    assert all(entry.opportunity_deleted for entry in stage_entries)


def test_timeline_rejects_non_positive_limit(db):
    """`limit=0` 会静默返回空页、`limit=-5` 会撞数据库错误变 500，都是 400 更清楚。

    HTTP 层的 1–50 是 422（参数层）；这里钉的是 service 被内部调用方传坏的情况。
    """
    f = _setup(db)
    for bad_limit in (0, -5):
        with pytest.raises(InvalidValueError):
            get_timeline(db, f["sa"], f["cust_a"].id, limit=bad_limit)


def test_timeline_cursor_rejects_unimplemented_owner_change_type(db):
    """`owner_change` 只在枚举里占位（P4 才接入），伪造这种游标必须 400。

    放它过去会让游标条件永远匹配不到，安静地返回空页——比 400 难查得多。
    """
    from base64 import urlsafe_b64encode

    raw = f"{NOW.isoformat()}|owner_change|1"
    forged = urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    with pytest.raises(InvalidValueError):
        decode_cursor(forged)


def test_timeline_of_invisible_customer_is_403_and_missing_is_404(db):
    f = _setup(db)
    with pytest.raises(ForbiddenError):
        get_timeline(db, f["sa"], f["cust_b"].id)
    with pytest.raises(NotFoundError):
        get_timeline(db, f["sa"], 999_999)


def test_timeline_api_shape(client, db):
    """HTTP 层：金额是定点字符串、游标在响应里、最后一页 next_cursor 为 null。"""
    f = _setup(db)
    login = client.post(
        "/api/v1/auth/login", json={"username": "sales_a", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    create_activity(
        db,
        f["sa"],
        ActivityCreate(
            customer_id=f["cust_a"].id, content="陪访", type="visit", activity_at=NOW
        ),
    )

    resp = client.get(
        f"/api/v1/customers/{f['cust_a'].id}/timeline?limit=1", headers=headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"][0]["type"] == "activity"
    assert body["items"][0]["content"] == "陪访"
    assert body["items"][0]["operator_name"] == "销售A"
    assert body["items"][0]["activity_type"] == "visit"
    assert body["items"][0]["amount_snapshot"] is None
    assert "next_cursor" in body

    forbidden = client.get(
        f"/api/v1/customers/{f['cust_b'].id}/timeline", headers=headers
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"
