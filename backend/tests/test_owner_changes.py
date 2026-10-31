"""负责人变更日志与审计日志。

这两张表是 P4 的“事实来源”：客户 owner 变了但日志没写，时间线和审计都会说谎。
测试直接查库，不通过接口——本任务还没有接口。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import InvalidValueError
from app.modules.audit.models import AuditLog
from app.modules.audit.service import write_audit_log
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import create_customer
from app.modules.departments.service import create_department
from app.modules.pools.models import OwnerChangeLog
from app.modules.pools.service import record_owner_change
from app.modules.users.models import ROLE_ADMIN, ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"


def _setup(db):
    east = create_department(db, "华东销售部")
    admin = create_user(
        db,
        username="admin",
        password=PASSWORD,
        display_name="管理员",
        role=ROLE_ADMIN,
        department_id=None,
    )
    sales = create_user(
        db,
        username="sales1",
        password=PASSWORD,
        display_name="销售一",
        role=ROLE_SALES,
        department_id=east.id,
    )
    customer = create_customer(
        db,
        admin,
        CustomerCreate(name="杭州智造装备有限公司", owner_id=sales.id),
    )
    return {"admin": admin, "sales": sales, "customer": customer}


def test_record_owner_change_persists_all_fields(db):
    ctx = _setup(db)
    log = record_owner_change(
        db,
        customer_id=ctx["customer"].id,
        action="transfer",
        from_owner_id=ctx["sales"].id,
        to_owner_id=ctx["admin"].id,
        operator_id=ctx["admin"].id,
        reason="组织调整",
    )
    # 契约定死在这里：helper 只 add，不 flush/commit——事务归调用方。
    # 内部 commit 会让行离开 db.new；内部 flush 会分配 id。两条断言缺一不可。
    assert log in db.new
    assert log.id is None
    db.commit()
    row = db.execute(select(OwnerChangeLog).where(OwnerChangeLog.id == log.id)).scalar_one()
    assert row.customer_id == ctx["customer"].id
    assert row.action == "transfer"
    assert row.from_owner_id == ctx["sales"].id
    assert row.to_owner_id == ctx["admin"].id
    assert row.operator_id == ctx["admin"].id
    assert row.reason == "组织调整"
    assert row.created_at is not None


def test_recycle_can_have_null_operator(db):
    ctx = _setup(db)
    log = record_owner_change(
        db,
        customer_id=ctx["customer"].id,
        action="recycle",
        from_owner_id=ctx["sales"].id,
        to_owner_id=None,
        operator_id=None,
        reason="连续 30 天无跟进，系统自动回收",
    )
    assert log in db.new
    assert log.id is None
    db.commit()
    row = db.execute(select(OwnerChangeLog).where(OwnerChangeLog.id == log.id)).scalar_one()
    assert row.operator_id is None
    assert row.to_owner_id is None


def test_record_owner_change_normalizes_blank_reason(db):
    """纯空白 reason 落 NULL：时间线上不该出现“空白原因”。"""
    ctx = _setup(db)
    log = record_owner_change(
        db,
        customer_id=ctx["customer"].id,
        action="assign",
        from_owner_id=None,
        to_owner_id=ctx["sales"].id,
        operator_id=ctx["admin"].id,
        reason="   ",
    )
    assert log.reason is None
    assert log in db.new
    assert log.id is None
    db.commit()
    row = db.execute(select(OwnerChangeLog).where(OwnerChangeLog.id == log.id)).scalar_one()
    assert row.reason is None


def test_record_owner_change_rejects_unknown_action(db):
    ctx = _setup(db)
    with pytest.raises(InvalidValueError):
        record_owner_change(
            db,
            customer_id=ctx["customer"].id,
            action="stolen",
            from_owner_id=None,
            to_owner_id=ctx["sales"].id,
            operator_id=ctx["admin"].id,
        )


def test_write_audit_log_persists_json_snapshots(db):
    ctx = _setup(db)
    log = write_audit_log(
        db,
        entity="customers",
        entity_id=ctx["customer"].id,
        action="transfer",
        before={"owner_id": ctx["sales"].id, "department_id": ctx["sales"].department_id},
        after={"owner_id": ctx["admin"].id, "department_id": None},
        operator_id=ctx["admin"].id,
    )
    # 同上：审计写入也只 add，flush 由调用方触发。
    assert log in db.new
    assert log.id is None
    db.commit()
    row = db.execute(select(AuditLog).where(AuditLog.id == log.id)).scalar_one()
    assert row.entity == "customers"
    assert row.entity_id == ctx["customer"].id
    assert row.action == "transfer"
    assert row.before_json == {
        "owner_id": ctx["sales"].id,
        "department_id": ctx["sales"].department_id,
    }
    assert row.after_json == {"owner_id": ctx["admin"].id, "department_id": None}
    assert row.operator_id == ctx["admin"].id


def test_write_audit_log_copies_caller_dicts(db):
    """写入边界拷贝：调用方复用并改写同一个 dict，不能篡改历史快照。"""
    ctx = _setup(db)
    before = {"owner_id": ctx["sales"].id}
    after = {"owner_id": ctx["admin"].id}
    log = write_audit_log(
        db,
        entity="customers",
        entity_id=ctx["customer"].id,
        action="transfer",
        before=before,
        after=after,
        operator_id=ctx["admin"].id,
    )
    before["owner_id"] = -1
    after["owner_id"] = -1
    db.commit()
    row = db.execute(select(AuditLog).where(AuditLog.id == log.id)).scalar_one()
    assert row.before_json == {"owner_id": ctx["sales"].id}
    assert row.after_json == {"owner_id": ctx["admin"].id}


def test_write_audit_log_rejects_unknown_action(db):
    ctx = _setup(db)
    with pytest.raises(InvalidValueError):
        write_audit_log(
            db,
            entity="customers",
            entity_id=ctx["customer"].id,
            action="hacked",
            before=None,
            after=None,
            operator_id=ctx["admin"].id,
        )


def test_owner_change_action_check_constraint_blocks_unknown(db):
    ctx = _setup(db)
    db.add(
        OwnerChangeLog(
            customer_id=ctx["customer"].id,
            action="stolen",
            from_owner_id=None,
            to_owner_id=ctx["sales"].id,
            operator_id=ctx["admin"].id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_audit_action_check_constraint_blocks_unknown(db):
    ctx = _setup(db)
    db.add(
        AuditLog(
            entity="customers",
            entity_id=ctx["customer"].id,
            action="hacked",
            operator_id=ctx["admin"].id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
