from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import AdminUser, ManagerOrAdminUser
from app.db.session import get_db
from app.modules.departments.schemas import DepartmentCreate, DepartmentRead
from app.modules.departments.service import create_department, list_departments

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("", response_model=list[DepartmentRead])
def get_departments(
    db: Annotated[Session, Depends(get_db)],
    _user: ManagerOrAdminUser,
):
    """列表：主管与管理员可读（销售不可读部门全表）。"""
    return list_departments(db)


@router.post("", response_model=DepartmentRead, status_code=201)
def post_department(
    payload: DepartmentCreate,
    db: Annotated[Session, Depends(get_db)],
    _user: AdminUser,
):
    """新建：仅管理员。"""
    return create_department(db, payload.name)
