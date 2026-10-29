import pytest

from app.core.errors import ConflictError
from app.modules.departments.service import (
    create_department,
    get_department,
    list_departments,
)


def test_create_department(db):
    dept = create_department(db, "华东销售部")
    assert dept.id is not None
    assert dept.name == "华东销售部"


def test_department_name_must_be_unique(db):
    create_department(db, "华东销售部")
    with pytest.raises(ConflictError):
        create_department(db, "华东销售部")


def test_get_department_raises_when_missing(db):
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        get_department(db, 9999)


def test_list_departments_returns_all_sorted_by_name(db):
    for name in ("Gamma", "Alpha", "Beta"):
        create_department(db, name)
    departments = list_departments(db)
    assert [d.name for d in departments] == ["Alpha", "Beta", "Gamma"]


def test_get_department_returns_matching_entity(db):
    create_department(db, "华东销售部")
    target = create_department(db, "华南销售部")
    found = get_department(db, target.id)
    assert found.id == target.id
    assert found.name == "华南销售部"
