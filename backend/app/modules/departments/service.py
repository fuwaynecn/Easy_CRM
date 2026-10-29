from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.modules.departments.models import Department


def list_departments(db: Session) -> list[Department]:
    stmt = select(Department).order_by(Department.id)
    return list(db.execute(stmt).scalars().all())


def create_department(db: Session, name: str) -> Department:
    cleaned = name.strip()
    existing = db.execute(
        select(Department).where(Department.name == cleaned)
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("部门名称已存在")
    dept = Department(name=cleaned)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


def get_department(db: Session, department_id: int) -> Department:
    dept = db.get(Department, department_id)
    if dept is None:
        raise NotFoundError("部门不存在")
    return dept