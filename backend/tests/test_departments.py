import pytest
from app.core.errors import ConflictError
from app.modules.departments.service import create_department, get_department


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