"""输单原因字典（design §3.10）。

三条规则容易被写错，都用测试钉住：

- 名称首尾空白必须 strip，全空白是 400 而不是落库成一行看不见的空白；
- 重名是 409（与现有数据冲突），不是静默合并；数据库的唯一约束是第二道防线；
- 停用的原因不出现在选择器里，但记录必须留着——历史机会还指着它，
  硬删会让报表的「输单原因」分组整块消失。
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, InvalidValueError, NotFoundError
from app.modules.departments.service import create_department
from app.modules.lost_reasons.models import LostReason
from app.modules.lost_reasons.schemas import LostReasonCreate
from app.modules.lost_reasons.service import (
    create_lost_reason,
    get_lost_reason,
    list_lost_reasons,
)
from app.modules.users.models import ROLE_SALES
from app.modules.users.service import create_user

PASSWORD = "Passw0rd!"


def _sales(db):
    department = create_department(db, "华东销售部")
    return create_user(
        db,
        username="sales_a",
        password=PASSWORD,
        display_name="销售A",
        role=ROLE_SALES,
        department_id=department.id,
    )


def _token(client, username: str = "sales_a") -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# --- 服务层 -------------------------------------------------------------


def test_create_strips_name_and_uses_defaults(db):
    reason = create_lost_reason(db, LostReasonCreate(name="  价格过高  "))

    assert reason.id is not None
    assert reason.name == "价格过高"
    assert reason.sort_order == 0
    assert reason.active is True


def test_blank_name_is_invalid_value(db):
    """schema 的 min_length=1 拦不住全空格（"   " 长度是 3），服务层必须再判一次。"""
    with pytest.raises(InvalidValueError):
        create_lost_reason(db, LostReasonCreate(name="   "))


def test_duplicate_name_is_conflict(db):
    create_lost_reason(db, LostReasonCreate(name="价格过高"))

    with pytest.raises(ConflictError) as excinfo:
        create_lost_reason(db, LostReasonCreate(name="价格过高"))

    assert excinfo.value.status_code == 409


def test_unique_constraint_rejects_raw_duplicate(db):
    """绕过 service 直接插入也必须被数据库挡住：字典重名是数据问题，不是提示问题。"""
    create_lost_reason(db, LostReasonCreate(name="价格过高"))

    db.add(LostReason(name="价格过高"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_list_filters_inactive_and_orders_by_sort_order(db):
    create_lost_reason(db, LostReasonCreate(name="第二", sort_order=20))
    create_lost_reason(db, LostReasonCreate(name="第一", sort_order=10))
    create_lost_reason(
        db, LostReasonCreate(name="停用的", sort_order=30, active=False)
    )

    names = [reason.name for reason in list_lost_reasons(db)]
    assert names == ["第一", "第二"]

    all_names = [reason.name for reason in list_lost_reasons(db, include_inactive=True)]
    assert all_names == ["第一", "第二", "停用的"]


def test_get_missing_reason_is_not_found(db):
    with pytest.raises(NotFoundError):
        get_lost_reason(db, 999)


# --- 接口层 -------------------------------------------------------------


def test_api_requires_authentication(client, db):
    resp = client.get("/api/v1/lost-reasons")

    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_api_lists_active_reasons_for_any_role(client, db):
    _sales(db)
    create_lost_reason(db, LostReasonCreate(name="价格过高", sort_order=10))
    create_lost_reason(db, LostReasonCreate(name="已停用的", sort_order=20, active=False))
    headers = {"Authorization": f"Bearer {_token(client)}"}

    resp = client.get("/api/v1/lost-reasons", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == [
        {"id": 1, "name": "价格过高", "sort_order": 10, "active": True}
    ]
