"""待办（follow_ups）的模型、作用域读取与「我的待办」口径。

spec §3.9 的关键结论：**待办不设负责人字段**，它跟着客户走。这里钉住的正是这条
——「我的待办」= join customers 后 `customers.owner_id = me`，而不是给待办加一列。
任何"顺手加个 owner_id"的改动都会让本文件里的 mine 用例失去意义。
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.errors import ForbiddenError, InvalidValueError, NotFoundError
from app.modules.activities.models import Activity
from app.modules.activities.schemas import ActivityCreate
from app.modules.activities.service import create_activity
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.followups.models import FOLLOW_UP_STATUSES, FollowUp
from app.modules.followups.schemas import FollowUpCreate, FollowUpDone, FollowUpUpdate
from app.modules.followups.service import (
    complete_follow_up,
    create_follow_up,
    delete_follow_up,
    get_follow_up,
    list_follow_ups,
    update_follow_up,
)
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


def _insert(db, actor, customer, *, content="下周回访", due_at=None, status="pending"):
    """造数走真实写接口；`status` 仅用于构造已完成/已取消的读侧场景。"""
    row = create_follow_up(
        db,
        actor,
        FollowUpCreate(
            customer_id=customer.id, due_at=due_at or NOW, content=content
        ),
    )
    if status != "pending":
        # 只改既有列，不模拟任何未实现的接口
        row.status = status
        row.done_at = datetime.now(UTC) if status == "done" else None
        db.commit()
    return row


def test_follow_up_table_is_registered_with_spec_columns():
    import app.db.models_registry  # noqa: F401
    from app.db.base import Base

    assert "follow_ups" in Base.metadata.tables
    columns = Base.metadata.tables["follow_ups"].columns
    assert columns["customer_id"].nullable is False
    assert columns["due_at"].nullable is False
    assert columns["content"].nullable is False
    assert columns["created_by"].nullable is False
    assert columns["done_at"].nullable is True
    # spec §3.9：待办没有负责人字段，这是设计结论，不是遗漏
    assert "owner_id" not in columns


def test_follow_up_statuses_match_spec():
    assert FOLLOW_UP_STATUSES == ("pending", "done", "cancelled")


def test_list_is_scoped_to_visible_customers(db):
    f = _setup(db)
    mine = _insert(db, f["sa"], f["cust_a"])
    _insert(db, f["sb"], f["cust_b"])

    items, total = list_follow_ups(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [mine.id]


def test_mine_filter_is_customer_owner_not_created_by(db):
    """「我的待办」按客户当前负责人判定，不是按创建者。

    主管给下属的客户建一条待办：在主管的「我的待办」里不该出现，在销售 A 的里该出现
    ——否则待办会跟着"谁写的"走，与 spec §3.9「客户转给谁，待办就归谁」相反。
    """
    f = _setup(db)
    by_boss = _insert(db, f["boss"], f["cust_a"], content="主管安排的待办")

    mine_sa, total_sa = list_follow_ups(db, f["sa"], mine=True)
    assert [item.id for item in mine_sa] == [by_boss.id]
    assert total_sa == 1

    mine_boss, total_boss = list_follow_ups(db, f["boss"], mine=True)
    assert mine_boss == []
    assert total_boss == 0

    # 不带 mine 时主管看得到本部门客户的待办（裁定 3：可见性与客户一致）
    all_boss, total_all = list_follow_ups(db, f["boss"])
    assert [item.id for item in all_boss] == [by_boss.id]
    assert total_all == 1


def test_list_filters_by_status_and_customer(db):
    f = _setup(db)
    pending = _insert(db, f["sa"], f["cust_a"], content="待办")
    _insert(db, f["sa"], f["cust_a"], content="已完成", status="done")
    # 真实写路径下销售写不了同事的客户，能写他人客户的只有本部门主管
    other = _insert(db, f["boss"], f["cust_a"], content="主管的待办")

    pending_items, pending_total = list_follow_ups(db, f["boss"], status="pending")
    assert pending_total == 2
    assert {item.id for item in pending_items} == {pending.id, other.id}

    _by_customer, total = list_follow_ups(
        db, f["boss"], customer_id=f["cust_a"].id
    )
    assert total == 3


def test_list_rejects_unknown_status(db):
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        list_follow_ups(db, f["sa"], status="someday")


def test_list_excludes_soft_deleted_from_items_and_total(db):
    f = _setup(db)
    kept = _insert(db, f["sa"], f["cust_a"], content="保留")
    gone = _insert(db, f["sa"], f["cust_a"], content="删除")
    gone.deleted_at = datetime.now(UTC)
    db.commit()

    items, total = list_follow_ups(db, f["sa"])

    assert total == 1
    assert [item.id for item in items] == [kept.id]


def test_list_orders_by_due_at_ascending(db):
    f = _setup(db)
    later = _insert(
        db, f["sa"], f["cust_a"], content="后天", due_at=NOW + timedelta(days=2)
    )
    sooner = _insert(db, f["sa"], f["cust_a"], content="今天", due_at=NOW)

    items, _ = list_follow_ups(db, f["sa"])

    assert [item.id for item in items] == [sooner.id, later.id]


def test_customer_name_is_part_of_the_read_shape(db):
    """首页与待办列表都要显示客户名，避免前端按 id 二次查名（N+1）。

    直接断言 wire 形状（若 read 不返回 customer_name，前端就得再发 N 个请求）。
    """
    from app.modules.followups.schemas import FollowUpRead

    f = _setup(db)
    row = _insert(db, f["sa"], f["cust_a"])
    loaded = get_follow_up(db, f["sa"], row.id)

    body = FollowUpRead.model_validate(loaded)

    assert body.customer_name == "甲制造有限公司"
    assert body.created_by_name == "销售A"


def test_get_other_departments_follow_up_is_403(db):
    f = _setup(db)
    foreign = _insert(db, f["sb"], f["cust_b"])
    with pytest.raises(ForbiddenError):
        get_follow_up(db, f["sa"], foreign.id)


def test_get_missing_follow_up_is_404(db):
    f = _setup(db)
    with pytest.raises(NotFoundError):
        get_follow_up(db, f["sa"], 999_999)


def test_get_soft_deleted_follow_up_is_404_not_403(db):
    """已删待办对外表现为不存在：自己的删了是 404，别部门的删了也不能变成 403。

    403 会泄漏"这条 id 曾经存在"。别部门 + 已删同时命中时，404 必须优先于 403，
    因此存在性探针同样要带 ``deleted_at IS NULL``。
    """
    f = _setup(db)
    mine = _insert(db, f["sa"], f["cust_a"])
    mine.deleted_at = datetime.now(UTC)
    foreign = _insert(db, f["sb"], f["cust_b"])
    foreign.deleted_at = datetime.now(UTC)
    db.commit()

    with pytest.raises(NotFoundError):
        get_follow_up(db, f["sa"], mine.id)
    with pytest.raises(NotFoundError):
        get_follow_up(db, f["sa"], foreign.id)


def test_list_by_invisible_customer_is_403(db):
    """筛一个看不见的客户是 403，不是空列表：空列表会被读成"这客户没有待办"。"""
    f = _setup(db)
    with pytest.raises(ForbiddenError):
        list_follow_ups(db, f["sa"], customer_id=f["cust_b"].id)


def test_same_due_at_orders_by_id_and_pages_stably(db):
    """同一到期时刻的三条按 id 升序（第二排序键），page_size=2 翻页不重不漏。

    待办按到期时间**升序**（最该先做的排最前），因此这里的 id 顺序也是升序；
    只按 ``due_at`` 排时同一时刻的顺序由数据库自由决定，翻页会漏行或重行。
    """
    f = _setup(db)
    first = _insert(db, f["sa"], f["cust_a"], content="一", due_at=NOW)
    second = _insert(db, f["sa"], f["cust_a"], content="二", due_at=NOW)
    third = _insert(db, f["sa"], f["cust_a"], content="三", due_at=NOW)

    page1, total1 = list_follow_ups(db, f["sa"], page=1, page_size=2)
    page2, total2 = list_follow_ups(db, f["sa"], page=2, page_size=2)

    assert (total1, total2) == (3, 3)
    assert [item.id for item in page1] == [first.id, second.id]
    assert [item.id for item in page2] == [third.id]


def test_mine_combines_with_status_filter(db):
    """`mine` 与 `status` 是 AND 叠加，不是互相覆盖。

    两条待办都写在销售 A 的客户上，但由**同部门主管**创建：因此本用例同时钉住
    「我的待办按客户负责人、不按创建人」——若 mine 改成 ``created_by == me``，
    这里一条都查不到。
    """
    f = _setup(db)
    pending = _insert(db, f["boss"], f["cust_a"], content="待办")
    done = _insert(db, f["boss"], f["cust_a"], content="已完成", status="done")

    done_items, done_total = list_follow_ups(db, f["sa"], mine=True, status="done")
    assert done_total == 1
    assert [item.id for item in done_items] == [done.id]

    all_items, all_total = list_follow_ups(db, f["sa"], mine=True)
    assert all_total == 2
    assert {item.id for item in all_items} == {pending.id, done.id}


def test_empty_status_filter_means_no_filter(db):
    """空串 = 不筛选：与 ``activities`` 的 ``type`` 筛选同一语义（见同名用例）。"""
    f = _setup(db)
    _insert(db, f["sa"], f["cust_a"])

    items, total = list_follow_ups(db, f["sa"], status="")

    assert total == 1
    assert len(items) == 1


def test_create_follow_up_keeps_last_activity_at_untouched(db):
    """建待办不是 §3.3 的同步点：不能让"安排了下周回访"把客户变新鲜。"""
    f = _setup(db)
    create_follow_up(
        db,
        f["sa"],
        FollowUpCreate(
            customer_id=f["cust_a"].id,
            due_at=NOW + timedelta(days=3),
            content="下周回访",
        ),
    )

    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at is None


def test_create_follow_up_on_pool_customer_is_403(db):
    f = _setup(db)
    with pytest.raises(ForbiddenError) as exc:
        create_follow_up(
            db,
            f["sa"],
            FollowUpCreate(customer_id=f["cust_pool"].id, due_at=NOW, content="回访"),
        )
    assert "公海" in str(exc.value)


def test_create_follow_up_rejects_opportunity_from_another_customer(db):
    f = _setup(db)
    opportunity = create_opportunity(
        db, f["sb"], OpportunityCreate(customer_id=f["cust_b"].id, name="乙公司机会")
    )
    with pytest.raises(InvalidValueError):
        create_follow_up(
            db,
            f["sa"],
            FollowUpCreate(
                customer_id=f["cust_a"].id, due_at=NOW, content="回访",
                opportunity_id=opportunity.id,
            ),
        )


def test_create_follow_up_rejects_naive_or_missing_due_at(db):
    """``due_at`` 必须带时区、且不能为空：两者都是 400，不是 500。

    naive 时间会被 session 时区静默解释（待办逾期判定全靠它）；``None`` 若放行会在
    flush 时撞 NOT NULL 变成 500。``model_construct`` 模拟绕过 schema 的调用方。
    """
    f = _setup(db)
    with pytest.raises(InvalidValueError) as exc:
        create_follow_up(
            db,
            f["sa"],
            FollowUpCreate(
                customer_id=f["cust_a"].id,
                due_at=datetime(2026, 11, 3, 1, 0),
                content="回访",
            ),
        )
    assert "时区" in str(exc.value)

    bypassed = FollowUpCreate.model_construct(
        customer_id=f["cust_a"].id,
        contact_id=None,
        opportunity_id=None,
        due_at=None,
        content="回访",
    )
    with pytest.raises(InvalidValueError) as exc:
        create_follow_up(db, f["sa"], bypassed)
    assert "不能为空" in str(exc.value)


def test_patch_cannot_change_customer(db):
    """待办跟客户走：PATCH 带 customer_id 是 422（schema extra=forbid）。"""
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])
    with pytest.raises(ValidationError):
        FollowUpUpdate(customer_id=f["cust_b"].id, content="换客户")
    assert todo.id is not None


def test_patch_status_done_is_rejected(db):
    """裁定 10：完成只能走 /done，PATCH 传 done 是 400 而不是默默完成。"""
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])
    with pytest.raises(InvalidValueError):
        update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(status="done"))


def test_patch_cancelled_then_reopen(db):
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])

    cancelled = update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(status="cancelled"))
    assert cancelled.status == "cancelled"
    assert cancelled.done_at is None

    reopened = update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(status="pending"))
    assert reopened.status == "pending"


def test_completed_follow_up_cannot_change_status(db):
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])
    complete_follow_up(db, f["sa"], todo.id, FollowUpDone())

    with pytest.raises(InvalidValueError):
        update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(status="pending"))


def test_completed_follow_up_cannot_change_content_or_due_at(db):
    """终态无条件冻结：不带 status 的 PATCH 也不能改写已完成的待办。

    原先的判断被嵌在 ``if "status" in changes:`` 里，只改 content/due_at 能绕过。
    """
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"], content="原内容")
    complete_follow_up(db, f["sa"], todo.id, FollowUpDone())

    with pytest.raises(InvalidValueError):
        update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(content="改内容"))
    with pytest.raises(InvalidValueError):
        update_follow_up(
            db, f["sa"], todo.id, FollowUpUpdate(due_at=NOW + timedelta(days=9))
        )

    db.expire_all()
    row = db.get(FollowUp, todo.id)
    assert row.content == "原内容"
    assert row.due_at == NOW


def test_cancelled_follow_up_cannot_change_content_but_can_reopen(db):
    """取消是"不做了"的留痕态：内容/时间冻结，但允许重开为待办。"""
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"], content="下周回访")
    update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(status="cancelled"))

    with pytest.raises(InvalidValueError):
        update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(content="改成别的"))

    db.expire_all()
    assert db.get(FollowUp, todo.id).content == "下周回访"

    reopened = update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(status="pending"))
    assert reopened.status == "pending"


def test_complete_sets_status_and_done_at(db):
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])

    result = complete_follow_up(db, f["sa"], todo.id, FollowUpDone())

    assert result.follow_up.status == "done"
    assert result.follow_up.done_at is not None
    assert result.activity is None
    # 不勾选"顺便写跟进"时不能刷新客户新鲜度
    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at is None


def test_complete_twice_is_400(db):
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])
    complete_follow_up(db, f["sa"], todo.id, FollowUpDone())

    with pytest.raises(InvalidValueError):
        complete_follow_up(db, f["sa"], todo.id, FollowUpDone())


def test_complete_with_activity_copies_content_and_bumps_last_activity(db):
    """spec §4.5：勾选"顺便写条跟进"→ 复制待办内容成 activity，并刷新 last_activity_at。"""
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"], content="把样册送到现场")

    result = complete_follow_up(
        db, f["sa"], todo.id, FollowUpDone(create_activity=True, activity_type="visit")
    )

    assert result.activity is not None
    activity = db.get(Activity, result.activity.id)
    assert activity.content == "把样册送到现场"
    assert activity.type == "visit"
    assert activity.customer_id == f["cust_a"].id
    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at is not None


def test_complete_without_ticking_but_passing_type_is_400(db):
    """不勾选却指定跟进方式是调用方意图矛盾，静默忽略会让用户以为记下了。

    而且必须**先校验再写库**：异常后待办状态与跟进行都不能有变化。
    """
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])

    with pytest.raises(InvalidValueError):
        complete_follow_up(
            db, f["sa"], todo.id, FollowUpDone(create_activity=False, activity_type="call")
        )

    db.expire_all()
    row = db.get(FollowUp, todo.id)
    assert row.status == "pending"
    assert row.done_at is None
    assert db.execute(select(func.count()).select_from(Activity)).scalar_one() == 0


def test_manager_can_edit_todo_on_subordinate_customer(db):
    """裁定 9：待办的「本人」= 客户当前负责人；主管建的待办，下属照样能改。"""
    f = _setup(db)
    todo = _insert(db, f["boss"], f["cust_a"], content="主管安排的回访")

    updated = update_follow_up(db, f["sa"], todo.id, FollowUpUpdate(content="销售A改期"))

    assert updated.content == "销售A改期"


def test_colleague_cannot_edit_or_delete_todo(db):
    f = _setup(db)
    # cust_a 属于销售 A；销售 C 同部门但既不是负责人也不是创建者
    todo = _insert(db, f["sa"], f["cust_a"])

    with pytest.raises(ForbiddenError):
        update_follow_up(db, f["sc"], todo.id, FollowUpUpdate(content="我改"))
    with pytest.raises(ForbiddenError):
        delete_follow_up(db, f["sc"], todo.id)


def test_manager_can_delete_department_todo_but_not_edit(db):
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"], content="销售自己的待办")

    with pytest.raises(ForbiddenError):
        update_follow_up(db, f["boss"], todo.id, FollowUpUpdate(content="主管改"))

    delete_follow_up(db, f["boss"], todo.id)
    db.expire_all()
    assert db.get(FollowUp, todo.id).deleted_at is not None


def test_complete_with_blank_content_after_bypass_is_400(db):
    """历史数据/运维改库可能留下全空白 content；完成时必须先清洗再落状态。

    API 写不进空白内容，所以这条守卫只能用"直接改 ORM 行"来触发；断言的重点是
    异常发生在 ``status='done'`` **之前**：待办仍是 pending、没有写出任何跟进。
    """
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"], content="原本有内容")
    todo.content = "   "
    db.commit()

    with pytest.raises(InvalidValueError):
        complete_follow_up(
            db,
            f["sa"],
            todo.id,
            FollowUpDone(create_activity=True, activity_type="visit"),
        )

    db.expire_all()
    row = db.get(FollowUp, todo.id)
    assert row.status == "pending"
    assert row.done_at is None
    assert db.execute(select(func.count()).select_from(Activity)).scalar_one() == 0


def test_manager_cannot_complete_subordinate_todo(db):
    """裁定 5：主管对下属的待办只能删，不能替人完成——否则等于绕过编辑权限。"""
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])

    with pytest.raises(ForbiddenError):
        complete_follow_up(db, f["boss"], todo.id, FollowUpDone())

    db.expire_all()
    row = db.get(FollowUp, todo.id)
    assert row.status == "pending"
    assert row.done_at is None


def test_creator_can_edit_todo_on_others_customer(db):
    """「创建者」分支：主管在别人客户上建的待办，自己仍然能改。

    与 ``test_manager_can_delete_department_todo_but_not_edit`` 一起钉住权限矩阵：
    改别人建的 → 403，改自己建的 → OK。
    """
    f = _setup(db)
    todo = _insert(db, f["boss"], f["cust_a"], content="主管安排的回访")

    updated = update_follow_up(
        db, f["boss"], todo.id, FollowUpUpdate(content="主管自己改")
    )

    assert updated.content == "主管自己改"


def test_delete_follow_up_is_soft_and_repeat_is_404(db):
    f = _setup(db)
    todo = _insert(db, f["sa"], f["cust_a"])

    delete_follow_up(db, f["sa"], todo.id)

    db.expire_all()
    row = db.get(FollowUp, todo.id)
    assert row is not None and row.deleted_at is not None
    # 软删不是 DELETE：物理行数不变，重复删除走 404（get_follow_up 的软删过滤）
    assert db.execute(select(func.count()).select_from(FollowUp)).scalar_one() == 1

    with pytest.raises(NotFoundError):
        delete_follow_up(db, f["sa"], todo.id)


def test_failed_next_follow_up_rolls_back_everything(db):
    """异常路径整体回滚：跟进已 flush、客户时间已推进，但待办建失败 → 三者都不留。

    ``next_follow_up.content`` 全空白能通过 schema 的 ``min_length=1``，在待办原语里
    才被 ``_clean_content`` 拒掉；此时 activity 已经 flush、last_activity_at 的 UPDATE
    也已发出，只有事务回滚才能保证"要么都成、要么都不成"。
    """
    f = _setup(db)
    with pytest.raises(InvalidValueError):
        create_activity(
            db,
            f["sa"],
            ActivityCreate(
                customer_id=f["cust_a"].id,
                content="这条跟进不该留下",
                activity_at=NOW,
                next_follow_up={"due_at": NOW + timedelta(days=1), "content": "   "},
            ),
        )

    db.rollback()

    assert db.execute(select(func.count()).select_from(Activity)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(FollowUp)).scalar_one() == 0
    db.expire_all()
    assert db.get(Customer, f["cust_a"].id).last_activity_at is None
