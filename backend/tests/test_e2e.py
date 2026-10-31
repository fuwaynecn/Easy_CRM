"""跨角色的端到端冒烟（HTTP 级）：P1 客户/权限 + P2 机会生命周期。

一个测试文件覆盖最容易坏的路径，全部走真实 HTTP 与真实数据库，不打桩：

1. 销售建客户 → 加两个联系人 → 改第二个 → 设为主联系人 → 原主的标记被清掉；
2. 销售读同部门同事的客户是 403（不是空列表、不是 404）；
3. 公海客户：销售能读、PATCH 403，且库里的值一个都没变；
4. 主管看得到本部门全部 + 公海，看不到别部门客户详情（403），能改本部门客户；
5. 管理员能读任何人名下的客户，``owner_name`` 是真实姓名而不是「用户 #N」；
6. 重名先 409（``detail.similar`` 为 ``[{id, name}]``）→ ``force=true`` 成功 →
   紧接着再建同名仍 409（force 只对当次生效）；
7. （P2）机会从 contact 一路推到 won → 主管重开 → 标 lost，接口派生值、库里的
   终态字段、``stage_histories`` 的 from/to 序列三处必须互相对齐；
8. （P3）写跟进（带下次待办）→ 首页今日待办 → 完成待办（回写跟进）→ 时间线两条倒序 →
   首页清空，外加游标翻页不重不漏；
9. （P4）建公海客户 → 销售领取（owner/department 落地）→ 主管分配（已有 owner 记
   ``transfer``）→ 主管释放（owner/department 双空、客户重回公海）→ 时间线三条
   ``owner_change`` 倒序且 from/to 姓名与原因逐字段对齐。

与 brief 样例的差异（样例写于早期，按现状改写）：联系人改/删走扁平
``PATCH|DELETE /api/v1/contacts/{id}``（嵌套写路由已删除）；``create_user`` 要求
manager/sales 必须带 ``department_id``；登录名至少 3 个字符；``CustomerRead`` 多
返回 ``owner_name``（公海客户为 null）。

夹具刻意给华东部门放**两个**销售：只有一个销售时"本部门"与"自己名下"恰好重合，
把 sales 的作用域错写成"本部门 + 公海"也能全绿。
"""

from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.modules.contacts.models import Contact
from app.modules.customers.models import Customer
from app.modules.departments.service import create_department
from app.modules.lost_reasons.schemas import LostReasonCreate
from app.modules.lost_reasons.service import create_lost_reason
from app.modules.opportunities.models import Opportunity, StageHistory
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, ROLE_SALES
from app.modules.users.service import create_user
from scripts.seed import LOST_REASONS

PASSWORD = "Passw0rd!"


def _setup(db) -> dict[str, object]:
    """两个部门、一个管理员、两个主管、三个销售。"""
    east = create_department(db, "华东销售部")
    west = create_department(db, "华南销售部")
    admin = create_user(
        db,
        username="admin",
        password=PASSWORD,
        display_name="系统管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )
    boss = create_user(
        db,
        username="boss",
        password=PASSWORD,
        display_name="李建国",
        role=ROLE_MANAGER,
        department_id=east.id,
    )
    sales1 = create_user(
        db,
        username="sales1",
        password=PASSWORD,
        display_name="张明",
        role=ROLE_SALES,
        department_id=east.id,
    )
    sales2 = create_user(
        db,
        username="sales2",
        password=PASSWORD,
        display_name="王丽",
        role=ROLE_SALES,
        department_id=east.id,
    )
    boss_south = create_user(
        db,
        username="boss_south",
        password=PASSWORD,
        display_name="陈晓峰",
        role=ROLE_MANAGER,
        department_id=west.id,
    )
    sales3 = create_user(
        db,
        username="sales3",
        password=PASSWORD,
        display_name="赵强",
        role=ROLE_SALES,
        department_id=west.id,
    )
    return {
        "east": east,
        "west": west,
        "admin": admin,
        "boss": boss,
        "sales1": sales1,
        "sales2": sales2,
        "boss_south": boss_south,
        "sales3": sales3,
    }


def _auth(client, username: str) -> dict[str, str]:
    """登录拿 access token；access token 只走请求头，refresh cookie 不参与断言。"""
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_customer(client, headers, name: str, **extra) -> dict:
    resp = client.post(
        "/api/v1/customers", headers=headers, json={"name": name, **extra}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _names(body: dict) -> set[str]:
    return {item["name"] for item in body["items"]}


def _history(db, opportunity_id: int) -> list[StageHistory]:
    """按 id 升序读某条机会的阶段历史。

    先 ``expire_all()``：测试里的 ``db`` 与 ``client`` 共用同一个会话，不 expire 会读到
    缓存里的旧状态，"403 之后 history 没多"这种断言就变成了永远为真。
    """
    db.expire_all()
    return list(
        db.execute(
            select(StageHistory)
            .where(StageHistory.opportunity_id == opportunity_id)
            .order_by(StageHistory.id)
        )
        .scalars()
        .all()
    )


def test_sales_creates_customer_and_primary_contact_moves(client, db):
    """销售建客户 → 加联系人 → 改联系人 → 设主联系人 → 原主的标记被清掉。"""
    _setup(db)
    headers = _auth(client, "sales1")
    customer = _create_customer(
        client,
        headers,
        "杭州智造装备有限公司",
        level="A",
        budget_status="approved",
    )
    customer_id = customer["id"]
    assert customer["owner_name"] == "张明"
    assert customer["owner_id"] is not None

    first = client.post(
        f"/api/v1/customers/{customer_id}/contacts",
        headers=headers,
        json={"name": "王工", "phone": "13800000001", "is_primary": True},
    )
    assert first.status_code == 201, first.text
    assert first.json()["is_primary"] is True

    second = client.post(
        f"/api/v1/customers/{customer_id}/contacts",
        headers=headers,
        json={"name": "刘总", "title": "总经理", "decision_role": "decision_maker"},
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]

    # 修改 + 设为主联系人：扁平路径，联系人 id 全局唯一，不需要父客户 id。
    # 嵌套写路由 /customers/{cid}/contacts/{id} 已删除，用它只会拿到 404。
    patched = client.patch(
        f"/api/v1/contacts/{second_id}",
        headers=headers,
        json={"name": "刘建国", "phone": "13800000002", "is_primary": True},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "刘建国"
    assert patched.json()["is_primary"] is True

    listed = client.get(f"/api/v1/customers/{customer_id}/contacts", headers=headers)
    assert listed.status_code == 200
    primaries = {item["name"]: item["is_primary"] for item in listed.json()}
    assert primaries == {"刘建国": True, "王工": False}

    # 库里也不能残留两条 primary：接口返回对、库里错是最难发现的一种坏
    db.expire_all()
    rows = (
        db.execute(select(Contact).where(Contact.customer_id == customer_id))
        .scalars()
        .all()
    )
    assert [row.name for row in rows if row.is_primary] == ["刘建国"]


def test_sales_cannot_read_same_department_colleague_customer(client, db):
    """同部门同事的客户是 403，不是空列表、不是 404。"""
    _setup(db)
    colleague = _auth(client, "sales2")
    customer = _create_customer(client, colleague, "宁波海天涂装有限公司")

    headers = _auth(client, "sales1")
    detail = client.get(f"/api/v1/customers/{customer['id']}", headers=headers)
    assert detail.status_code == 403, detail.text
    assert detail.json()["code"] == "forbidden"
    assert detail.json()["message"] == "无权查看该客户"

    listed = client.get("/api/v1/customers", headers=headers)
    assert listed.status_code == 200
    assert _names(listed.json()) == set()
    assert listed.json()["total"] == 0

    # 联系人读取继承客户作用域：绕道 /contacts 也拿不到数据
    contacts = client.get(
        f"/api/v1/customers/{customer['id']}/contacts", headers=headers
    )
    assert contacts.status_code == 403


def test_sales_patch_pool_customer_forbidden_and_data_unchanged(client, db):
    """公海只读：销售能读、PATCH 403，且库里的值一个都没变。"""
    _setup(db)
    pool = _create_customer(
        client,
        _auth(client, "admin"),
        "苏州新能源电池科技有限公司",
        level="B",
        budget_status="unknown",
        remark="官网留资，尚未分配负责人",
    )
    assert pool["owner_id"] is None
    assert pool["owner_name"] is None

    headers = _auth(client, "sales1")
    readable = client.get(f"/api/v1/customers/{pool['id']}", headers=headers)
    assert readable.status_code == 200
    assert readable.json()["name"] == "苏州新能源电池科技有限公司"

    denied = client.patch(
        f"/api/v1/customers/{pool['id']}",
        headers=headers,
        json={"level": "A", "remark": "被改写"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "forbidden"
    assert "公海" in denied.json()["message"]

    # 403 之后库里必须原封不动：拒绝写入却已落库是最坏的失败方式
    db.expire_all()
    row = db.get(Customer, pool["id"])
    assert row.level == "B"
    assert row.remark == "官网留资，尚未分配负责人"
    assert row.owner_id is None


def test_manager_sees_department_only_and_can_edit_it(client, db):
    """主管看到本部门全部 + 公海；别部门客户详情 403；本部门客户可改。"""
    f = _setup(db)
    east_a = _create_customer(client, _auth(client, "sales1"), "杭州智造装备有限公司")
    east_b = _create_customer(client, _auth(client, "sales2"), "宁波海天涂装有限公司")
    west = _create_customer(
        client, _auth(client, "sales3"), "广州鼎盛汽车零部件有限公司"
    )
    pool = _create_customer(
        client, _auth(client, "admin"), "苏州新能源电池科技有限公司"
    )

    boss = _auth(client, "boss")
    listed = client.get("/api/v1/customers", headers=boss)
    assert listed.status_code == 200
    body = listed.json()
    assert _names(body) == {
        "杭州智造装备有限公司",
        "宁波海天涂装有限公司",
        "苏州新能源电池科技有限公司",
    }
    assert west["name"] not in _names(body)
    assert body["total"] == 3

    other = client.get(f"/api/v1/customers/{west['id']}", headers=boss)
    assert other.status_code == 403
    assert other.json()["code"] == "forbidden"

    # 主管可写本部门全部（含下属名下），写响应里的 owner_name 是下属姓名
    patched = client.patch(
        f"/api/v1/customers/{east_a['id']}", headers=boss, json={"level": "B"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["level"] == "B"
    assert patched.json()["owner_name"] == "张明"

    # 公海对主管同样只读
    denied = client.patch(
        f"/api/v1/customers/{pool['id']}", headers=boss, json={"level": "A"}
    )
    assert denied.status_code == 403
    assert east_b["owner_id"] == f["sales2"].id


def test_admin_reads_any_customer_with_real_owner_name(client, db):
    """管理员能读任何人名下的客户，且 owner_name 是真实姓名而不是「用户 #N」。"""
    _setup(db)
    _create_customer(client, _auth(client, "sales3"), "广州鼎盛汽车零部件有限公司")
    _create_customer(client, _auth(client, "sales1"), "杭州智造装备有限公司")
    _create_customer(client, _auth(client, "admin"), "苏州新能源电池科技有限公司")

    admin = _auth(client, "admin")
    listed = client.get("/api/v1/customers", headers=admin)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 3
    owners = {item["name"]: item["owner_name"] for item in body["items"]}
    assert owners == {
        "广州鼎盛汽车零部件有限公司": "赵强",
        "杭州智造装备有限公司": "张明",
        "苏州新能源电池科技有限公司": None,
    }

    # 详情页同样带姓名：前端 ownerLabel 退化到「用户 #id」说明这里丢了字段
    west_id = next(
        item["id"] for item in body["items"] if item["name"] == "广州鼎盛汽车零部件有限公司"
    )
    detail = client.get(f"/api/v1/customers/{west_id}", headers=admin)
    assert detail.status_code == 200, detail.text
    assert detail.json()["owner_name"] == "赵强"
    assert "用户 #" not in detail.text
    assert "用户名" not in detail.text


def test_duplicate_name_conflict_then_force_is_one_shot(client, db):
    """重名 409（detail.similar 是 [{id, name}]）→ force 成功 → 再建仍 409。"""
    _setup(db)
    headers = _auth(client, "sales1")
    first = _create_customer(client, headers, "浙江甲制造有限公司")

    # 归一化后同名（多一个空格）也判重：前端按 detail.similar 渲染候选列表
    duplicate = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江 甲制造 有限公司"}
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "conflict"
    assert duplicate.json()["detail"]["similar"] == [
        {"id": first["id"], "name": "浙江甲制造有限公司"}
    ]

    forced = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "浙江甲制造有限公司", "force": True},
    )
    assert forced.status_code == 201, forced.text
    forced_id = forced.json()["id"]
    assert forced_id != first["id"]

    # force 只对当次生效：紧接着的同名请求仍要经过查重，两条都出现在候选里
    again = client.post(
        "/api/v1/customers", headers=headers, json={"name": "浙江甲制造有限公司"}
    )
    assert again.status_code == 409, again.text
    assert {item["id"] for item in again.json()["detail"]["similar"]} == {
        first["id"],
        forced_id,
    }


@pytest.mark.parametrize("username", ["admin", "boss", "sales1", "sales2", "sales3"])
def test_every_seed_style_account_can_login(client, db, username):
    """所有角色的账号都能登录：夹具的部门必修课没有把某类账号挤掉。"""
    _setup(db)
    assert _auth(client, username)


def test_opportunity_lifecycle_end_to_end(client, db):
    """P2 冒烟：建客户 → 建机会 → 推进 → 赢单 → 主管重开 → 输单，全程真实 HTTP。

    与 `test_opportunities_api.py` 的分工：那边逐条钉单点规则（422/403/400），这里只
    走"一条机会从生到死"的完整路径，并在**同一个测试里**对齐三件容易互相矛盾的东西：
    接口返回的派生值（`win_rate` 跟随阶段）、库里的终态字段、`stage_histories` 的
    from/to 序列。history 是漏斗报表的唯一数据来源，序列错一位报表就会把"重开"算成
    两次赢单。

    重开与输单都用主管账号：重开限 `manager`/`admin`（销售试一次必须 403，并且 403
    之后 `stage` 与 history 都不能变，这一条也钉在这里）。测试库不跑种子，所以输单
    原因按 `scripts.seed.LOST_REASONS` 的**同一个元组**用服务层建出来（不硬编码中文，
    种子改名这里跟着变），再用 `GET /lost-reasons` 核对读接口返回的就是这一套口径——
    下拉框的数据源与测试用的字典必须同源，否则"测试里能输单、界面上选不到原因"
    这种错会被漏掉。
    """
    people = _setup(db)
    sales1_id = people["sales1"].id
    boss_id = people["boss"].id
    sales = _auth(client, "sales1")
    customer = _create_customer(client, sales, "端到端制造有限公司")

    # 测试库不跑种子：按种子常量建出整套字典，并核对读接口（下拉框的数据源）口径一致。
    reasons = [
        create_lost_reason(db, LostReasonCreate(name=name)) for name in LOST_REASONS
    ]
    dictionary = client.get("/api/v1/lost-reasons", headers=sales)
    assert dictionary.status_code == 200, dictionary.text
    assert [item["name"] for item in dictionary.json()] == list(LOST_REASONS)
    reason = reasons[0]

    created = client.post(
        "/api/v1/opportunities",
        headers=sales,
        json={
            "customer_id": customer["id"],
            "name": "端到端机会",
            "amount": "123456.78",
            "expected_close_date": "2026-12-31",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["stage"] == "contact"
    assert body["amount"] == "123456.78"
    assert body["owner_name"] == "张明"          # 继承客户负责人
    assert body["customer_name"] == "端到端制造有限公司"
    assert body["win_rate"] == 10                 # 跟随阶段默认值
    opportunity_id = body["id"]

    # 阶段只能经 /stage：PATCH 带 stage 在 schema 层就 422，`stage_histories` 写不全
    # 这件事在 HTTP 层就不可能发生。
    bypass = client.patch(
        f"/api/v1/opportunities/{opportunity_id}",
        headers=sales,
        json={"stage": "needs"},
    )
    assert bypass.status_code == 422, bypass.text
    assert bypass.json()["code"] == "validation_error"

    for stage in ("needs", "proposal"):
        resp = client.post(
            f"/api/v1/opportunities/{opportunity_id}/stage",
            headers=sales,
            json={"stage": stage},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stage"] == stage

    # 在 proposal 之后 PATCH 改一次金额：这是证明 `amount_snapshot` 是"变更当刻的值"
    # 而不是"join 回 opportunities 的现值"的唯一办法——不改金额时六行快照全一样，
    # 两种实现都绿。改在这里让前三行旧值、后三行新值。
    repriced = client.patch(
        f"/api/v1/opportunities/{opportunity_id}",
        headers=sales,
        json={"amount": "200000.00"},
    )
    assert repriced.status_code == 200, repriced.text
    assert repriced.json()["amount"] == "200000.00"

    won = client.post(
        f"/api/v1/opportunities/{opportunity_id}/stage",
        headers=sales,
        json={"stage": "won"},
    )
    assert won.status_code == 200, won.text
    assert won.json()["won_at"] is not None
    assert won.json()["win_rate"] == 100
    assert won.json()["lost_reason_id"] is None
    assert won.json()["amount"] == "200000.00"

    # 销售不能重开自己赢的单，且 403 不能让状态变化
    denied = client.post(
        f"/api/v1/opportunities/{opportunity_id}/stage",
        headers=sales,
        json={"stage": "negotiation", "note": "我自己重开"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "forbidden"
    assert denied.json()["message"] == "仅主管及以上可重开已关闭的机会"

    # 403 不能让状态变化：阶段仍是 won、history 也没有多出一行。失败请求写过半条历史
    # 是最难查的一类 bug（卡片没动，报表却多了一次"重开"）。
    assert [row.to_stage for row in _history(db, opportunity_id)] == [
        "contact",
        "needs",
        "proposal",
        "won",
    ]
    denied_after = db.get(Opportunity, opportunity_id)
    assert denied_after is not None
    assert denied_after.stage == "won"
    assert denied_after.won_at is not None

    boss = _auth(client, "boss")
    # 重开原因必填：缺了是 400（不是悄悄写一条 note 为空的历史）
    missing_note = client.post(
        f"/api/v1/opportunities/{opportunity_id}/stage",
        headers=boss,
        json={"stage": "negotiation"},
    )
    assert missing_note.status_code == 400, missing_note.text
    assert missing_note.json()["code"] == "invalid_value"
    assert missing_note.json()["message"] == "重开机会必须填写重开原因"

    reopen_note = "客户追加预算，重新评估技术方案"
    reopened = client.post(
        f"/api/v1/opportunities/{opportunity_id}/stage",
        headers=boss,
        json={"stage": "negotiation", "note": reopen_note},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["stage"] == "negotiation"
    assert reopened.json()["won_at"] is None       # 重开必须清掉赢单时间
    assert reopened.json()["lost_reason_id"] is None
    assert reopened.json()["win_rate"] == 70       # 回到自动值（跟随后阶段）

    lost = client.post(
        f"/api/v1/opportunities/{opportunity_id}/stage",
        headers=boss,
        json={
            "stage": "lost",
            "lost_reason_id": reason.id,
            "lost_note": "客户把预算挪到明年",
        },
    )
    assert lost.status_code == 200, lost.text
    assert lost.json()["stage"] == "lost"
    assert lost.json()["won_at"] is None
    assert lost.json()["lost_reason_id"] == reason.id
    assert lost.json()["lost_reason_name"] == LOST_REASONS[0]
    assert lost.json()["win_rate"] == 0

    # 看板口径与单条记录一致：赢单列空、输单列一条且金额是变更当刻的值
    board = client.get("/api/v1/opportunities/board", headers=boss)
    assert board.status_code == 200, board.text
    columns = {column["stage"]: column for column in board.json()["columns"]}
    assert columns["won"]["total"] == 0
    assert columns["lost"]["total"] == 1
    assert columns["lost"]["amount_total"] == "200000.00"
    assert columns["lost"]["items"][0]["id"] == opportunity_id
    assert columns["lost"]["items"][0]["amount"] == "200000.00"
    assert columns["contact"]["total"] == 0

    history = _history(db, opportunity_id)
    assert [(row.from_stage, row.to_stage) for row in history] == [
        (None, "contact"),
        ("contact", "needs"),
        ("needs", "proposal"),
        ("proposal", "won"),
        ("won", "negotiation"),
        ("negotiation", "lost"),
    ]
    # 重开原因落在**重开那一行**上；终态转换与创建都不带 note
    assert [row.note for row in history] == [None, None, None, None, reopen_note, None]
    # 操作人逐行钉住：创建与三次推进是 sales1，重开与输单是 boss。只钉重开那一行会漏掉
    # "创建/推进的 operator 写成了别人"这种错——报表按操作人统计时它会把业绩算错人。
    assert [row.operator_id for row in history] == [
        sales1_id,
        sales1_id,
        sales1_id,
        sales1_id,
        boss_id,
        boss_id,
    ]
    # 金额快照记的是**变更当刻**的值：前三行是 PATCH 之前的 123456.78，后三行是
    # PATCH 成 200000.00 之后的。若实现改成 join 回 opportunities 的现值，
    # 这六行会全变成 200000.00，报表的历史成交额就被现值篡改了。
    assert [str(row.amount_snapshot) for row in history] == [
        "123456.78",
        "123456.78",
        "123456.78",
        "200000.00",
        "200000.00",
        "200000.00",
    ]

    opportunity = db.get(Opportunity, opportunity_id)
    assert opportunity is not None
    assert opportunity.stage == "lost"
    assert opportunity.won_at is None
    assert opportunity.lost_reason_id == reason.id
    assert opportunity.lost_note == "客户把预算挪到明年"
    assert str(opportunity.amount) == "200000.00"


def test_p3_follow_up_loop(client, db):
    """P3 主链路：写跟进（顺手排待办）→ 首页今日待办 → 完成并回写跟进 → 时间线两条 → 首页清空。

    这条用例存在的意义是**跨模块**：activity 写侧、follow_ups 写侧、home 聚合、timeline
    合并四处各自单测都绿，但"待办完成后首页消失、时间线多一条"只有连起来跑才成立。

    自带夹具，不复用 `_setup` 的内部字典键——那边服务于 P2 的权限矩阵，按键名耦合会让
    两边一改就互相绊倒。

    待办到期时间取「此刻」而不是「明天」：`/home` 的今日块是半开区间
    `[上海零点, 次日零点)`，此刻必然落在其中，因此这条断言不依赖测试运行的具体时刻；
    按裁定「今天已过点仍算今日待办」，它是今日待办而不是逾期。
    """
    from datetime import UTC, datetime

    from app.modules.departments.service import create_department
    from app.modules.users.models import ROLE_SALES
    from app.modules.users.service import create_user

    department = create_department(db, "P3 验收部")
    create_user(
        db,
        username="p3_sales",
        password=PASSWORD,
        display_name="P3 销售",
        role=ROLE_SALES,
        department_id=department.id,
    )
    headers = _auth(client, "p3_sales")

    created_customer = client.post(
        "/api/v1/customers", headers=headers, json={"name": "P3 贯通客户有限公司"}
    )
    assert created_customer.status_code == 201, created_customer.text
    customer_id = created_customer.json()["id"]
    assert created_customer.json()["last_activity_at"] is None

    # 1) 写跟进 + 顺手排一条今天到期的待办
    now = datetime.now(UTC)
    created = client.post(
        "/api/v1/activities",
        headers=headers,
        json={
            "customer_id": customer_id,
            "type": "call",
            "content": "确认技术参数",
            "next_follow_up": {"due_at": now.isoformat(), "content": "寄样册"},
        },
    )
    assert created.status_code == 201, created.text
    activity_id = created.json()["id"]
    # 写活动会推进客户新鲜度（design §3.3）：否则首页"即将进公海"会立刻把新客户算进去
    detail = client.get(f"/api/v1/customers/{customer_id}", headers=headers).json()
    assert detail["last_activity_at"] is not None

    # 2) 首页：今日待办里有它，逾期与公海预警都没有
    home = client.get("/api/v1/home", headers=headers)
    assert home.status_code == 200, home.text
    home_body = home.json()
    assert set(home_body) == {"today", "overdue", "pool_notice"}
    today = home_body["today"]
    assert today["total"] == 1
    assert [item["content"] for item in today["items"]] == ["寄样册"]
    assert today["items"][0]["customer_name"] == "P3 贯通客户有限公司"
    assert home_body["overdue"]["total"] == 0
    # 刚写过的客户不可能进预警区间：27 天瞬时阈值
    assert home_body["pool_notice"]["total"] == 0

    # 3) 「我的待办」列表里也是同一条（首页与列表必须同源）
    todos = client.get("/api/v1/follow_ups?mine=true", headers=headers)
    assert todos.status_code == 200, todos.text
    assert todos.json()["total"] == 1
    todo_id = todos.json()["items"][0]["id"]
    assert todos.json()["items"][0]["status"] == "pending"

    # 4) 完成待办并顺便写跟进
    done = client.post(
        f"/api/v1/follow_ups/{todo_id}/done",
        headers=headers,
        json={"create_activity": True, "activity_type": "visit"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["follow_up"]["status"] == "done"
    assert done.json()["follow_up"]["done_at"] is not None
    assert done.json()["activity"]["content"] == "寄样册"
    assert done.json()["activity"]["type"] == "visit"

    # 5) 首页不再有它：两个块都空（完成 ≠ 逾期，也不是被藏起来）
    home_after = client.get("/api/v1/home", headers=headers).json()
    assert home_after["today"]["total"] == 0
    assert home_after["overdue"]["total"] == 0
    assert home_after["today"]["items"] == []

    # 6) 时间线两条，倒序（完成待办产生的那条在前）；没有阶段变更
    timeline = client.get(f"/api/v1/customers/{customer_id}/timeline", headers=headers)
    assert timeline.status_code == 200, timeline.text
    timeline_body = timeline.json()
    assert all(item["type"] == "activity" for item in timeline_body["items"])
    assert [item["content"] for item in timeline_body["items"]] == ["寄样册", "确认技术参数"]
    assert [item["activity_type"] for item in timeline_body["items"]] == ["visit", "call"]
    # 单页装得下 → 不发行游标，前端拿到 next_cursor=null 就不会显示「加载更多」
    assert timeline_body["next_cursor"] is None

    # 7) 分页契约：limit=1 时给游标，翻页不重不漏
    first_page = client.get(
        f"/api/v1/customers/{customer_id}/timeline?limit=1", headers=headers
    ).json()
    assert len(first_page["items"]) == 1
    assert first_page["next_cursor"] is not None
    second_page = client.get(
        f"/api/v1/customers/{customer_id}/timeline",
        headers=headers,
        params={"limit": 1, "cursor": first_page["next_cursor"]},
    )
    assert second_page.status_code == 200, second_page.text
    second_body = second_page.json()
    assert len(second_body["items"]) == 1
    assert second_body["items"][0]["id"] != first_page["items"][0]["id"]
    assert second_body["next_cursor"] is None
    # 第二页就是最初那条跟进：activity 主键与时间线条目的 id 同源
    assert second_body["items"][0]["id"] == activity_id
    assert second_body["items"][0]["content"] == "确认技术参数"


def test_p4_pool_claim_assign_release_and_timeline(client, db):
    """P4 主链路：公海列表 → 领取 → 主管分配 → 主管释放 → 时间线三条 owner_change。"""
    f = _setup(db)
    admin = _auth(client, "admin")
    sales1 = _auth(client, "sales1")
    boss = _auth(client, "boss")

    pool = _create_customer(client, admin, "苏州新能源电池科技有限公司")

    listed = client.get("/api/v1/pool", headers=sales1)
    assert listed.status_code == 200, listed.text
    assert pool["id"] in [item["id"] for item in listed.json()["items"]]

    claimed = client.post(f"/api/v1/pool/{pool['id']}/claim", headers=sales1)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["owner_id"] == f["sales1"].id
    assert claimed.json()["department_id"] == f["east"].id

    assigned = client.post(
        f"/api/v1/customers/{pool['id']}/assign",
        headers=boss,
        json={"owner_id": f["sales2"].id, "reason": "华东内部调整"},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["owner_id"] == f["sales2"].id

    released = client.post(
        f"/api/v1/customers/{pool['id']}/release",
        headers=boss,
        json={"reason": "客户长期不回复"},
    )
    assert released.status_code == 200, released.text
    assert released.json()["owner_id"] is None
    assert released.json()["department_id"] is None

    listed_again = client.get("/api/v1/pool", headers=sales1)
    assert pool["id"] in [item["id"] for item in listed_again.json()["items"]]

    timeline = client.get(
        f"/api/v1/customers/{pool['id']}/timeline", headers=boss
    )
    assert timeline.status_code == 200, timeline.text
    actions = [
        item["owner_change_action"]
        for item in timeline.json()["items"]
        if item["type"] == "owner_change"
    ]
    assert actions == ["release", "transfer", "claim"]
    release_item = next(
        item for item in timeline.json()["items"] if item["type"] == "owner_change"
    )
    assert release_item["from_owner_name"] == "王丽"
    assert release_item["to_owner_name"] is None
    assert release_item["content"] == "客户长期不回复"


def _xlsx_rows(names: list[str], owner: str) -> bytes:
    """按模板表头拼一份最小客户导入文件（只填必填的两列）。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "客户"
    sheet.append([
        "客户名称*", "负责人*", "简称", "编码", "行业", "地区", "地址", "网站",
        "规模", "等级", "来源", "采购方式", "预算状态", "客户状态", "备注",
    ])
    for name in names:
        sheet.append([name, owner, "", "", "", "", "", "", "", "", "", "", "", "", ""])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _upload(client, headers: dict[str, str], content: bytes):
    return client.post(
        "/api/v1/imports",
        headers=headers,
        data={"kind": "customers"},
        files={
            "file": (
                "e2e.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def test_p5_import_then_report_e2e(client, db):
    """导入 3 个客户 → 客户列表可见 → 建机会 → 漏斗能看到。"""
    _setup(db)
    headers = _auth(client, "admin")

    upload = _upload(
        client, headers, _xlsx_rows(["E2E甲公司", "E2E乙公司", "E2E丙公司"], "sales1")
    )
    assert upload.status_code == 201, upload.text
    job = upload.json()
    assert job["status"] == "validated"

    commit = client.post(f"/api/v1/imports/{job['id']}/commit", headers=headers)
    assert commit.status_code == 200, commit.text
    assert commit.json()["success_rows"] == 3

    listed = client.get("/api/v1/customers?keyword=E2E", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 3

    customer_id = listed.json()["items"][0]["id"]
    created = client.post(
        "/api/v1/opportunities",
        headers=headers,
        json={"customer_id": customer_id, "name": "E2E机会", "amount": "1000.00"},
    )
    assert created.status_code == 201, created.text

    funnel = client.get("/api/v1/reports/funnel", headers=headers)
    assert funnel.status_code == 200, funnel.text
    assert sum(row["count"] for row in funnel.json()["stages"]) >= 1


def test_p5_import_500_rows(client, db):
    """验收要求：500 行单次导入成功。"""
    _setup(db)
    headers = _auth(client, "admin")
    names = [f"批量客户{i:04d}" for i in range(500)]

    upload = _upload(client, headers, _xlsx_rows(names, "sales1"))
    assert upload.status_code == 201, upload.text
    job = upload.json()
    assert job["total_rows"] == 500
    assert job["error_rows"] == 0

    commit = client.post(f"/api/v1/imports/{job['id']}/commit", headers=headers)
    assert commit.status_code == 200, commit.text
    assert commit.json()["success_rows"] == 500
