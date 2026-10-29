import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.modules.customers.models import (
    BUDGET_STATUSES,
    CUSTOMER_LEVELS,
    CUSTOMER_STATUSES,
    PROCUREMENT_TYPES,
    Customer,
)


def test_customer_defaults(db):
    customer = Customer(name="浙江甲制造有限公司", created_by=None)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    assert customer.level == "C"
    assert customer.status == "active"
    assert customer.budget_status == "unknown"
    assert customer.owner_id is None
    assert customer.deleted_at is None
    assert customer.created_at is not None


def test_customer_nullable_fields_accept_none(db):
    customer = Customer(
        name="浙江乙有限公司",
        owner_id=None,
        department_id=None,
        procurement_type=None,
        created_by=None,
    )
    db.add(customer)
    db.commit()
    stored = db.execute(select(Customer).where(Customer.name == "浙江乙有限公司")).scalar_one()
    assert stored.procurement_type is None
    assert stored.department_id is None


def test_customer_indexes_exist(db):
    """五条索引必须真实存在：迁移/建表漏建时这里直接红。"""
    names = {index["name"] for index in inspect(db.get_bind()).get_indexes("customers")}

    assert {
        "ix_customers_owner_id",
        "ix_customers_department_id",
        "ix_customers_last_activity_at",
        "ix_customers_status",
        "ix_customers_name",
    } <= names


def test_customer_server_defaults_apply_on_raw_insert(db):
    """裸 INSERT 不给默认列：必须由数据库自己填上 level/status/budget_status。"""
    db.execute(text("INSERT INTO customers (name) VALUES ('裸插客户')"))
    db.commit()

    row = db.execute(
        text(
            "SELECT level, status, budget_status, created_at, updated_at "
            "FROM customers WHERE name = '裸插客户'"
        )
    ).one()
    assert row.level == "C"
    assert row.status == "active"
    assert row.budget_status == "unknown"
    assert row.created_at is not None
    assert row.updated_at is not None


def test_customer_name_is_required(db):
    db.add(Customer(created_by=None))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_customer_enum_constants_match_spec():
    # 前端选项列表按这套值渲染，改动等于前后端契约变更
    assert PROCUREMENT_TYPES == ("tender", "direct", "framework", "other")
    assert BUDGET_STATUSES == ("approved", "pending", "none", "unknown")
    assert CUSTOMER_LEVELS == ("A", "B", "C")
    assert CUSTOMER_STATUSES == ("active", "lost", "inactive")
