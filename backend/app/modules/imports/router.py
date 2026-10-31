"""导入接口。

模板是两段路径 `/{kind}/template`，和后面的单段 `/{job_id}` 不会冲突；仍然先声明，
读代码时按「模板 → 上传 → 查询 → commit」的顺序走。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import ManagerOrAdminUser
from app.core.errors import InvalidValueError
from app.db.session import get_db
from app.modules.imports.service import build_customer_template

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
