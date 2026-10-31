"""导入接口。

模板是两段路径 `/{kind}/template`，和后面的单段 `/{job_id}` 不会冲突；仍然先声明，
读代码时按「模板 → 上传 → 查询 → commit」的顺序走。
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import ManagerOrAdminUser
from app.core.errors import InvalidValueError
from app.db.session import get_db
from app.modules.imports.schemas import ImportJobRead
from app.modules.imports.service import (
    MAX_IMPORT_BYTES,
    build_customer_template,
    create_import_job,
    get_import_job,
)

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/{kind}/template")
def download_template(
    kind: str,
    db: Annotated[Session, Depends(get_db)],
    user: ManagerOrAdminUser,
):
    if kind != "customers":
        raise InvalidValueError("P5 只支持客户导入模板")
    return Response(
        content=build_customer_template(),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": 'attachment; filename="customers-template.xlsx"'
        },
    )


@router.post("", response_model=ImportJobRead, status_code=201)
async def upload_import(
    db: Annotated[Session, Depends(get_db)],
    user: ManagerOrAdminUser,
    # Annotated 而非 `file: UploadFile = File(...)`：ruff B008 不允许在默认值里调函数
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "customers",
):
    if kind != "customers":
        raise InvalidValueError("P5 只支持客户导入")
    if not file.filename:
        raise InvalidValueError("请选择文件")
    if Path(file.filename).suffix.lower() != ".xlsx":
        raise InvalidValueError("只支持 .xlsx 文件")
    content = await file.read()
    if len(content) > MAX_IMPORT_BYTES:
        raise InvalidValueError("文件不能超过 5MB")
    return create_import_job(db, user, file.filename, content)


@router.get("/{job_id}", response_model=ImportJobRead)
def read_import(
    job_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: ManagerOrAdminUser,
):
    return get_import_job(db, user, job_id)
