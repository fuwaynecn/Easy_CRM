from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import require_admin
from app.db.session import get_db
from app.modules.departments.schemas import DepartmentCreate, DepartmentRead
from app.modules.departments.service import create_department, list_departments

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("", response_model=list[DepartmentRead])
def get_departments(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[object, Depends(require_admin)],
):
    return list_departments(db)


@router.post("", response_model=DepartmentRead, status_code=201)
def post_department(
    payload: DepartmentCreate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[object, Depends(require_admin)],
):
    return create_department(db, payload.name)