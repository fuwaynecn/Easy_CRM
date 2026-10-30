"""跟进记录（activities）的模型、作用域读取与写入规则。

可见性完全继承客户：这里测的是「客户可见 ⇒ 跟进可见」这条继承关系在每个入口
（列表 / 单条 / 不存在 / 已删）上是否成立，而不是再造一套作用域。
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.modules.activities.models import ACTIVITY_TYPES, Activity
from app.modules.activities.schemas import ActivityCreate, ActivityUpdate
from app.modules.activities.service import (
    create_activity,
    delete_activity,
    get_activity,
    list_activities,
    update_activity,
)
from app.modules.contacts.schemas import ContactCreate
from app.modules.contacts.service import create_contact
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.followups.models import FollowUp
from app.modules.opportunities.schemas import OpportunityCreate
from app.modules.opportunities.service import create_opportunity
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
    sc = create_user(
        db, username="sales_c", password=PASSWORD, display_name="销售C",
        role=ROLE_SALES, department_id=east.id,
    )
    sb = create_user(
        db, username="sales_b", password=PASSWORD, display_name="销售B",
        role=ROLE_SALES, department_id=south.id,
    )
    return {
        "admin": admin, "boss": boss, "sa": sa, "sc": sc, "sb": sb,
        "cust_a": create_customer(db, sa, CustomerCreate(name="甲制造有限公司")),
        "cust_b": create_customer(db, sb, CustomerCreate(name="乙制造有限公司")),
        "cust_pool": create_customer(db, admin, CustomerCreate(name="丙制造有限公司")),
    }


def test_activity_table_is_registered_in_metadata():
    """新表必须在 models_registry 里注册，否则测试夹具建表缺表、迁移比对也看不见。"""
    import app.db.models_registry  # noqa: F401
    from app.db.base import Base

    assert "activities" in Base.metadata.tables
    columns = Base.metadata.tables["activities"].columns
    assert columns["customer_id"].nullable is False
    assert columns["content"].nullable is False
    assert columns["activity_at"].nullable is False
    assert columns["created_by"].nullable is False
    assert columns["contact_id"].nullable is True
    assert columns["opportunity_id"].nullable is True


def _insert(db, actor, customer, *, content="电话沟通", type="call", activity_at=None, **overrides):
    """测试造数统一走真实写接口；`_insert` 只是新旧测试之间的兼容层。

    走真实写入而不是直接插表：待办联动、``last_activity_at``、内容清洗、权限都在
    ``create_activity`` 里，直接插表会让读侧测试在一个生产路径到不了的状态上做断言。
    """
    return create_activity(
        db,
        actor,
        ActivityCreate(
            customer_id=customer.id,
            content=content,
            type=type,
            activity_at=activity_at or NOW,
            **overrides,
        ),
    )


def test_activity_type_tuple_matches_spec():
    assert ACTIVITY_TYPES == ("call", "visit", "wechat", "email", "note", "other")


def test_list_is_scoped_to_visible_customers(db):
    """销售只看到自己客户的跟进；同部门同事与别部门都看不到。"""
    f = _setup(db)
    mine = _insert(db, f["sa"], f["cust_a"])
    _insert(db, f["sb"], f["cust_b"])

    items, total = list_activities(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [mine.id]


def test_manager_sees_department_activities_but_not_other_department(db):
    f = _setup(db)
    east = _insert(db, f["sa"], f["cust_a"])
    _insert(db, f["sb"], f["cust_b"])

    items, total = list_activities(db, f["boss"])

    assert total == 1
    assert [item.id for item in items] == [east.id]


def test_list_excludes_soft_deleted_rows_from_items_and_total(db):
    f = _setup(db)
    kept = _insert(db, f["sa"], f["cust_a"], content="保留")
    gone = _insert(db, f["sa"], f["cust_a"], content="删除")
    gone.deleted_at = datetime.now(UTC)
    db.commit()

    items, total = list_activities(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [kept.id]


def test_list_filters_by_customer_type_and_created_by(db):
    f = _setup(db)
    call = _insert(db, f["sa"], f["cust_a"], content="电话", type="call")
    _insert(db, f["sa"], f["cust_a"], content="拜访", type="visit")
    # 真实写路径下销售不能写同事的客户，能写他人客户的只有本部门主管（见 assert_customer_writable）
    other = _insert(db, f["boss"], f["cust_a"], content="主管的拜访", type="visit")

    # 只看 total：本用例关心的是「按客户筛选确实命中 3 条」而不是返回内容
    _by_customer, total = list_activities(
        db, f["boss"], customer_id=f["cust_a"].id
    )
    assert total == 3

    by_type, total = list_activities(db, f["boss"], type="call")
    assert total == 1
    assert [item.id for item in by_type] == [call.id]

    by_author, total = list_activities(db, f["boss"], created_by=f["boss"].id)
    assert total == 1
    assert [item.id for item in by_author] == [other.id]


def test_list_rejects_unknown_type(db):
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        list_activities(db, f["sa"], type="telepathy")


def test_list_on_invisible_customer_is_403(db):
    f = _setup(db)
    with pytest.raises(ForbiddenError):
        list_activities(db, f["sa"], customer_id=f["cust_b"].id)


def test_get_missing_activity_is_404(db):
    f = _setup(db)
    with pytest.raises(NotFoundError):
        get_activity(db, f["sa"], 999_999)


def test_get_other_departments_activity_is_403(db):
    f = _setup(db)
    foreign = _insert(db, f["sb"], f["cust_b"])
    with pytest.raises(ForbiddenError):
        get_activity(db, f["sa"], foreign.id)


def test_list_orders_by_activity_at_descending(db):
    f = _setup(db)
    older = _insert(
        db, f["sa"], f["cust_a"], content="早", activity_at=NOW - timedelta(days=2)
    )
    newer = _insert(db, f["sa"], f["cust_a"], content="晚", activity_at=NOW)

    items, _ = list_activities(db, f["sa"])

    assert [item.id for item in items] == [newer.id, older.id]


def test_get_soft_deleted_activity_is_404_not_403(db):
    """已删跟进对外表现为不存在：自己的删了是 404，别部门的删了也不能变成 403。

    403 会泄漏"这条 id 曾经存在"。已删记录的唯一语义是不认识它，因此存在性探针
    必须带 ``deleted_at IS NULL``——少了它，别部门已删的记录会先撞作用域给出 403。
    """
    f = _setup(db)
    mine = _insert(db, f["sa"], f["cust_a"])
    mine.deleted_at = datetime.now(UTC)
    foreign = _insert(db, f["sb"], f["cust_b"])
    foreign.deleted_at = datetime.now(UTC)
    db.commit()

    with pytest.raises(NotFoundError):
        get_activity(db, f["sa"], mine.id)
    # 别部门 + 已删：作用域与软删同时命中时，404 必须优先于 403
    with pytest.raises(NotFoundError):
        get_activity(db, f["sa"], foreign.id)


def test_list_rejects_inverted_date_range(db):
    """``date_from > date_to`` 是参数错误（400），不是空列表：空列表会被读成"没有跟进"。"""
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        list_activities(
            db, f["sa"], date_from=date(2026, 10, 31), date_to=date(2026, 10, 1)
        )


def test_same_instant_orders_by_id_desc_and_pages_stably(db):
    """同一时刻的三条按 id 倒序；page_size=2 翻两页不重不漏。

    第二排序键 ``id desc`` 与 offset 分页是一对：只按 ``activity_at`` 排时，
    同一时刻的记录顺序由数据库自由决定，翻页会漏行或重行。
    """
    f = _setup(db)
    first = _insert(db, f["sa"], f["cust_a"], content="一", activity_at=NOW)
    second = _insert(db, f["sa"], f["cust_a"], content="二", activity_at=NOW)
    third = _insert(db, f["sa"], f["cust_a"], content="三", activity_at=NOW)

    page1, total1 = list_activities(db, f["sa"], page=1, page_size=2)
    page2, total2 = list_activities(db, f["sa"], page=2, page_size=2)

    assert (total1, total2) == (3, 3)
    assert [item.id for item in page1] == [third.id, second.id]
    assert [item.id for item in page2] == [first.id]


def test_same_department_colleague_activity_is_visible(db):
    """可见性随客户、不随作者：同部门主管在销售的客户上写的跟进，销售看得见。

    作者取主管而不是同部门销售：真实写路径里 ``assert_customer_writable`` 只允许
    本人/本部门主管/管理员写，销售写不了同事的客户（直接插表的旧造数掩盖了这条）。
    """
    f = _setup(db)
    colleague = _insert(db, f["boss"], f["cust_a"], content="主管替我打的电话")

    items, total = list_activities(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [colleague.id]


def test_empty_type_filter_means_no_filter(db):
    """筛选参数的空串 = 不筛选（与 ``follow_ups`` 的 ``status`` 同一语义）。

    前端清空下拉会带 ``?type=``；若这里 400，同一套前端就要为两个同类参数写两种
    处理。写入路径的严格校验不受影响（Task 4 义务，见 service.list_activities 注释）。
    """
    f = _setup(db)
    _insert(db, f["sa"], f["cust_a"], type="call")

    items, total = list_activities(db, f["sa"], type="")

    assert total == 1
    assert len(items) == 1


def test_create_sets_last_activity_at(db):
    f = _setup(db)
    customer = db.get(Customer, f["cust_a"].id)
    assert customer.last_activity_at is None

    _insert(db, f["sa"], f["cust_a"], activity_at=NOW)

    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at == NOW


def test_backdated_activity_does_not_roll_back_last_activity_at(db):
    """补录历史跟进取 max(原值, activity_at)（spec §3.3 第 1 条）。"""
    f = _setup(db)
    _insert(db, f["sa"], f["cust_a"], activity_at=NOW)

    _insert(db, f["sa"], f["cust_a"], content="补录上周的拜访", activity_at=NOW - timedelta(days=7))

    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at == NOW


def test_backdated_only_activity_sets_past_time(db):
    f = _setup(db)
    _insert(db, f["sa"], f["cust_a"], activity_at=NOW - timedelta(days=3))

    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at == NOW - timedelta(days=3)


def test_create_activity_with_next_follow_up_creates_pending_todo(db):
    """spec §4.5：填了"下次跟进"就在同一个事务里建一条 pending 待办。"""
    f = _setup(db)
    activity = create_activity(
        db,
        f["sa"],
        ActivityCreate(
            customer_id=f["cust_a"].id,
            content="电话确认技术参数",
            type="call",
            activity_at=NOW,
            next_follow_up={
                "due_at": NOW + timedelta(days=3),
                "content": "把样册送到现场",
            },
        ),
    )

    todos = list(
        db.execute(
            select(FollowUp).where(
                FollowUp.customer_id == f["cust_a"].id, FollowUp.status == "pending"
            )
        ).scalars()
    )
    assert len(todos) == 1
    assert todos[0].content == "把样册送到现场"
    assert todos[0].due_at == NOW + timedelta(days=3)
    # 待办本身不推进最后跟进时间（§3.3 只认 activity / 领取 / 转移）
    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at == NOW
    assert activity.id is not None


def test_create_activity_on_pool_customer_is_403_for_sales(db):
    f = _setup(db)
    with pytest.raises(ForbiddenError) as exc:
        create_activity(
            db, f["sa"], ActivityCreate(customer_id=f["cust_pool"].id, content="打电话")
        )
    assert "公海" in str(exc.value)


def test_create_activity_rejects_unknown_type(db):
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        create_activity(
            db,
            f["sa"],
            ActivityCreate(customer_id=f["cust_a"].id, content="沟通", type="telepathy"),
        )


def test_create_activity_rejects_empty_type(db):
    """写侧严格、筛选侧宽松的分界线（T3 补测留下的义务）。

    ``list_activities(type="")`` 是"不筛选"，但 ``create_activity(type="")`` 是非法
    取值：空串落库只会剩下数据库 CHECK 兜底，变成一个前端选不中、报表也认不出的类型。
    """
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        create_activity(
            db,
            f["sa"],
            ActivityCreate(customer_id=f["cust_a"].id, content="沟通", type=""),
        )


def test_create_activity_requires_content_after_strip(db):
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        create_activity(
            db, f["sa"], ActivityCreate(customer_id=f["cust_a"].id, content="   ")
        )


def test_create_activity_rejects_contact_from_another_customer(db):
    f = _setup(db)
    foreign = create_contact(db, f["sb"], f["cust_b"].id, ContactCreate(name="外部联系人"))
    with pytest.raises(InvalidValueError) as exc:
        create_activity(
            db,
            f["sa"],
            ActivityCreate(
                customer_id=f["cust_a"].id, content="沟通", contact_id=foreign.id
            ),
        )
    assert "联系人" in str(exc.value)


def test_create_activity_rejects_missing_contact(db):
    f = _setup(db)
    with pytest.raises(NotFoundError):
        create_activity(
            db,
            f["sa"],
            ActivityCreate(customer_id=f["cust_a"].id, content="沟通", contact_id=999_999),
        )


def test_update_activity_never_touches_last_activity_at(db):
    """裁定 8：编辑（含把时间改到现在）一律不推进 last_activity_at。"""
    f = _setup(db)
    activity = _insert(db, f["sa"], f["cust_a"], activity_at=NOW - timedelta(days=5))

    update_activity(
        db, f["sa"], activity.id, ActivityUpdate(activity_at=NOW, content="补正内容")
    )

    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at == NOW - timedelta(days=5)
    assert db.get(Activity, activity.id).content == "补正内容"


def test_sales_cannot_edit_colleagues_activity(db):
    """销售改不了别人写的跟进，但删的权限是另一条规则（裁定 5）。

    作者取本部门主管而不是同部门销售：真实写路径里销售写不了同事的客户，
    "同事在我客户上写了跟进"只能由主管/管理员造成。
    """
    f = _setup(db)
    activity = _insert(db, f["boss"], f["cust_a"], content="主管写的")

    with pytest.raises(ForbiddenError):
        update_activity(db, f["sa"], activity.id, ActivityUpdate(content="我改"))

    # 但主管可以删（裁定 5），这是两条不同的权限
    delete_activity(db, f["boss"], activity.id)
    db.expire_all()
    assert db.get(Activity, activity.id).deleted_at is not None


def test_manager_cannot_edit_but_can_delete_department_activity(db):
    f = _setup(db)
    activity = _insert(db, f["sa"], f["cust_a"])

    with pytest.raises(ForbiddenError):
        update_activity(db, f["boss"], activity.id, ActivityUpdate(content="主管改"))

    delete_activity(db, f["boss"], activity.id)


def test_delete_activity_is_soft_and_repeat_is_404(db):
    f = _setup(db)
    activity = _insert(db, f["sa"], f["cust_a"])

    delete_activity(db, f["sa"], activity.id)
    db.expire_all()
    row = db.get(Activity, activity.id)
    assert row is not None and row.deleted_at is not None

    with pytest.raises(NotFoundError):
        delete_activity(db, f["sa"], activity.id)


def test_admin_can_delete_any_activity(db):
    f = _setup(db)
    activity = _insert(db, f["sb"], f["cust_b"])
    delete_activity(db, f["admin"], activity.id)


# --- 写路径不得污染客户行 / 拒 naive 时间 / 归属与软删（评审修复轮）--------


def test_creating_activity_does_not_touch_customer_updated_at(db):
    """写跟进只推进 ``last_activity_at``，不能把「最后修改时间」改成「最后跟进时间」。

    ``TimestampMixin`` 的列级 ``onupdate`` 会给任何 UPDATE 加 ``updated_at=now()``；
    不 pin 住的话，客户列表按 ``updated_at`` 排序/审计的语义会被跟进写入带偏，而且
    补录更早的跟进这种"空转写入"也会刷新它。
    """
    f = _setup(db)
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    db.execute(
        update(Customer)
        .where(Customer.id == f["cust_a"].id)
        .values(updated_at=fixed)
    )
    db.commit()

    _insert(db, f["sa"], f["cust_a"], activity_at=NOW)

    db.expire_all()
    customer = db.get(Customer, f["cust_a"].id)
    assert customer.updated_at == fixed
    assert customer.last_activity_at == NOW


def test_delete_activity_never_touches_last_activity_at(db):
    """裁定 8 的另一半：删除跟进也不推进 ``last_activity_at``（只有创建会）。"""
    f = _setup(db)
    activity = _insert(db, f["sa"], f["cust_a"], activity_at=NOW - timedelta(days=10))

    delete_activity(db, f["sa"], activity.id)

    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at == NOW - timedelta(days=10)


def test_create_activity_rejects_opportunity_from_another_customer(db):
    """``validate_child_links`` 的机会分支：跨客户挂机会必须 400（不是静默接受）。"""
    f = _setup(db)
    foreign = create_opportunity(
        db, f["sb"], OpportunityCreate(customer_id=f["cust_b"].id, name="乙公司机会")
    )
    with pytest.raises(InvalidValueError) as exc:
        create_activity(
            db,
            f["sa"],
            ActivityCreate(
                customer_id=f["cust_a"].id, content="沟通", opportunity_id=foreign.id
            ),
        )
    assert "机会" in str(exc.value)


def test_child_links_soft_deleted_is_not_found(db):
    """软删的联系人/机会对外不存在：挂到跟进上是 404（与"已删客户 404"同口径）。"""
    f = _setup(db)
    contact = create_contact(
        db, f["sa"], f["cust_a"].id, ContactCreate(name="已删联系人")
    )
    contact.deleted_at = datetime.now(UTC)
    opportunity = create_opportunity(
        db, f["sa"], OpportunityCreate(customer_id=f["cust_a"].id, name="已删机会")
    )
    opportunity.deleted_at = datetime.now(UTC)
    db.commit()

    with pytest.raises(NotFoundError):
        create_activity(
            db,
            f["sa"],
            ActivityCreate(
                customer_id=f["cust_a"].id, content="沟通", contact_id=contact.id
            ),
        )
    with pytest.raises(NotFoundError):
        create_activity(
            db,
            f["sa"],
            ActivityCreate(
                customer_id=f["cust_a"].id,
                content="沟通",
                opportunity_id=opportunity.id,
            ),
        )


def test_update_activity_rejects_empty_type(db):
    """写侧严格：PATCH 传空串同样是非法枚举（400），不能被"空串=不筛选"的宽松语义污染。"""
    f = _setup(db)
    activity = _insert(db, f["sa"], f["cust_a"])

    with pytest.raises(InvalidValueError):
        update_activity(db, f["sa"], activity.id, ActivityUpdate(type=""))


def test_create_activity_rejects_naive_activity_at(db):
    """naive 时间会被 DB session 时区静默解释（换环境偏 8 小时），必须 400。"""
    f = _setup(db)
    with pytest.raises(InvalidValueError) as exc:
        create_activity(
            db,
            f["sa"],
            ActivityCreate(
                customer_id=f["cust_a"].id,
                content="沟通",
                activity_at=datetime(2026, 10, 31, 1, 0),
            ),
        )
    assert "时区" in str(exc.value)


def test_next_follow_up_rejects_naive_due_at(db):
    """「下次跟进」的 ``due_at`` 同样必须带时区：待办逾期判定全靠这个时间。"""
    f = _setup(db)
    with pytest.raises(InvalidValueError) as exc:
        create_activity(
            db,
            f["sa"],
            ActivityCreate(
                customer_id=f["cust_a"].id,
                content="沟通",
                activity_at=NOW,
                next_follow_up={
                    "due_at": datetime(2026, 11, 3, 1, 0),
                    "content": "回访",
                },
            ),
        )
    assert "时区" in str(exc.value)
