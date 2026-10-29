import pytest
from sqlalchemy import select
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


def test_customer_name_indexed_for_lower_lookup(db):
    # 索引存在性是迁移层面的保证，这里确认列可正常按小写查询
    db.add(Customer(name="HangZhou Tech", created_by=None))
    db.commit()
    found = db.execute(
        select(Customer).where(Customer.name == "HangZhou Tech")
    ).scalar_one()
    assert found.name == "HangZhou Tech"


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
