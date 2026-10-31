"""客户 Excel 导入：模板、解析、校验、commit。

两阶段（design §4.6）：
1. 上传只解析与校验，落 `import_jobs` 与文件，状态 `validated` / `failed`；
2. commit 重新解析校验后单事务写客户，状态 `done`。

导入**不更新**已有客户：重名按 `normalize_name` 跳过，不覆盖（spec §4.6）。
"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import InvalidValueError, NotFoundError
from app.modules.customers.models import Customer
from app.modules.customers.service import normalize_name
from app.modules.imports.models import ImportJob
from app.modules.imports.schemas import ImportErrorRead
from app.modules.users.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    STATUS_ACTIVE,
    User,
)

# 列名顺序就是模板与解析的顺序；带 * 的是必填。改这里等于改导入契约。
CUSTOMER_TEMPLATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("客户名称*", "必填；与现有客户重名会被跳过，不会覆盖"),
    ("负责人*", "必填；填系统登录名 username，且必须已分配部门"),
    ("简称", "选填，最多 64 字"),
    ("编码", "选填，最多 64 字"),
    ("行业", "选填，最多 64 字"),
    ("地区", "选填，最多 64 字"),
    ("地址", "选填，最多 255 字"),
    ("网站", "选填，最多 255 字"),
    ("规模", "选填，最多 32 字"),
    ("等级", "A / B / C；留空按 C"),
    ("来源", "选填，最多 64 字"),
    ("采购方式", "招标 / 直采 / 框架 / 其他；留空不填"),
    ("预算状态", "已立项 / 待审批 / 无预算 / 未知；留空按未知"),
    ("客户状态", "活跃 / 停用 / 输单；留空按活跃"),
    ("备注", "选填"),
)

_TEMPLATE_HEADERS = tuple(name for name, _ in CUSTOMER_TEMPLATE_COLUMNS)
_TEMPLATE_EXAMPLE = (
    "杭州示例科技有限公司", "sales1", "示例科技", "", "制造业", "浙江", "", "",
    "50-200人", "B", "官网", "招标", "已立项", "活跃", "示例行，导入前请删除",
)


def build_customer_template() -> bytes:
    """生成客户导入模板：表头 + 一行示例 + 填写说明。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "客户"
    sheet.append(list(_TEMPLATE_HEADERS))
    sheet.append(list(_TEMPLATE_EXAMPLE))
    sheet.column_dimensions["A"].width = 32
    sheet.column_dimensions["B"].width = 18
    sheet.column_dimensions["O"].width = 36

    guide = workbook.create_sheet("填写说明")
    guide.append(["列名", "说明"])
    for name, note in CUSTOMER_TEMPLATE_COLUMNS:
        guide.append([name, note])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000

_FIELD_LIMITS = {
    "short_name": 64,
    "code": 64,
    "industry": 64,
    "region": 64,
    "address": 255,
    "website": 255,
    "scale": 32,
    "source": 64,
}

_LEVELS = {"A": "A", "B": "B", "C": "C", "a": "A", "b": "B", "c": "C"}
_PROCUREMENT = {
    "招标": "tender", "直采": "direct", "框架": "framework", "其他": "other",
    "tender": "tender", "direct": "direct", "framework": "framework", "other": "other",
}
_BUDGET = {
    "已立项": "approved", "待审批": "pending", "无预算": "none", "未知": "unknown",
    "approved": "approved", "pending": "pending", "none": "none", "unknown": "unknown",
}
_STATUS = {
    "活跃": "active", "停用": "inactive", "输单": "lost",
    "active": "active", "inactive": "inactive", "lost": "lost",
}


@dataclass(frozen=True)
class ParsedCustomerRow:
    row: int
    name: str
    owner_username: str
    fields: dict[str, str | None]


@dataclass
class ParseResult:
    rows: list[ParsedCustomerRow]
    errors: list[ImportErrorRead]
    total_rows: int


@dataclass
class ValidationResult:
    valid: list[ParsedCustomerRow]
    errors: list[ImportErrorRead]
    skipped: list[ParsedCustomerRow]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _enum(
    value: str,
    mapping: dict[str, str],
    *,
    row: int,
    column: str,
    default: str | None,
    errors: list[ImportErrorRead],
) -> str | None:
    if not value:
        return default
    resolved = mapping.get(value)
    if resolved is None:
        errors.append(
            ImportErrorRead(
                row=row, column=column, message=f"不支持的值：{value}"
            )
        )
        return default
    return resolved


def parse_customer_workbook(content: bytes) -> ParseResult:
    """解析客户 xlsx；只返回结构合法的行，行级错误单独收集。"""
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # 第三方库异常类型不稳定，统一转 400
        raise InvalidValueError("无法读取 Excel 文件，请确认是 .xlsx 格式") from exc

    sheet = workbook.active
    if sheet is None:
        workbook.close()
        raise InvalidValueError("Excel 文件没有工作表")

    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header_values = next(rows_iter)
    except StopIteration as exc:
        workbook.close()
        raise InvalidValueError("Excel 文件是空的") from exc

    headers = [_text(value) for value in header_values]
    missing = [name for name in ("客户名称*", "负责人*") if name not in headers]
    if missing:
        workbook.close()
        return ParseResult(
            rows=[],
            errors=[
                ImportErrorRead(
                    row=1, column="表头", message="缺少列：" + "、".join(missing)
                )
            ],
            total_rows=0,
        )

    positions = {
        name: index for index, name in enumerate(headers) if name
    }

    def cell(cells: list[str], header: str) -> str:
        """按列名取单元格；缺列或行尾单元格缺失时给空串。

        定义在行循环外面并显式传入 `cells`：写在循环里会闭包捕获循环变量
        （ruff B023），虽然同步调用不会出错，但没必要留这个坑。
        """
        position = positions.get(header)
        return cells[position] if position is not None and position < len(cells) else ""

    parsed: list[ParsedCustomerRow] = []
    errors: list[ImportErrorRead] = []
    total_rows = 0
    for row_number, values in enumerate(rows_iter, start=2):
        cells = [_text(value) for value in values]
        if not any(cells):
            continue
        total_rows += 1
        if total_rows > MAX_IMPORT_ROWS:
            errors.append(
                ImportErrorRead(
                    row=row_number,
                    column="",
                    message=f"单次最多导入 {MAX_IMPORT_ROWS} 行",
                )
            )
            break

        name = cell(cells, "客户名称*")
        owner = cell(cells, "负责人*")
        row_errors_before = len(errors)
        if not name:
            errors.append(
                ImportErrorRead(row=row_number, column="客户名称*", message="必填")
            )
        elif len(name) > 200:
            errors.append(
                ImportErrorRead(
                    row=row_number, column="客户名称*", message="不能超过 200 字"
                )
            )
        if not owner:
            errors.append(
                ImportErrorRead(row=row_number, column="负责人*", message="必填")
            )
        elif len(owner) > 64:
            errors.append(
                ImportErrorRead(
                    row=row_number, column="负责人*", message="不能超过 64 字"
                )
            )

        fields: dict[str, str | None] = {}
        for header, field in (
            ("简称", "short_name"),
            ("编码", "code"),
            ("行业", "industry"),
            ("地区", "region"),
            ("地址", "address"),
            ("网站", "website"),
            ("规模", "scale"),
            ("来源", "source"),
            ("备注", "remark"),
        ):
            value = cell(cells, header) or None
            limit = _FIELD_LIMITS.get(field)
            if value is not None and limit is not None and len(value) > limit:
                errors.append(
                    ImportErrorRead(
                        row=row_number, column=header, message=f"不能超过 {limit} 字"
                    )
                )
            fields[field] = value

        fields["level"] = _enum(
            cell(cells, "等级"), _LEVELS, row=row_number, column="等级",
            default="C", errors=errors,
        )
        fields["procurement_type"] = _enum(
            cell(cells, "采购方式"), _PROCUREMENT, row=row_number, column="采购方式",
            default=None, errors=errors,
        )
        fields["budget_status"] = _enum(
            cell(cells, "预算状态"), _BUDGET, row=row_number, column="预算状态",
            default="unknown", errors=errors,
        )
        fields["status"] = _enum(
            cell(cells, "客户状态"), _STATUS, row=row_number, column="客户状态",
            default="active", errors=errors,
        )
        if len(errors) > row_errors_before:
            continue
        parsed.append(
            ParsedCustomerRow(
                row=row_number,
                name=name,
                owner_username=owner,
                fields=fields,
            )
        )
    workbook.close()
    return ParseResult(rows=parsed, errors=errors, total_rows=total_rows)


def validate_customer_rows(
    db: Session, actor: User, rows: list[ParsedCustomerRow]
) -> ValidationResult:
    """按负责人与全库重名做业务校验；重名进 `skipped`，不是错误。"""
    users = db.execute(
        select(User).where(User.status == STATUS_ACTIVE)
    ).scalars().all()
    by_username = {user.username: user for user in users}
    existing = {
        normalize_name(name)
        for (name,) in db.execute(
            select(Customer.name).where(Customer.deleted_at.is_(None))
        ).all()
    }
    errors: list[ImportErrorRead] = []
    valid: list[ParsedCustomerRow] = []
    skipped: list[ParsedCustomerRow] = []
    seen_in_file: set[str] = set()
    for row in rows:
        owner = by_username.get(row.owner_username)
        if owner is None:
            errors.append(
                ImportErrorRead(
                    row=row.row,
                    column="负责人*",
                    message=f"找不到启用的用户：{row.owner_username}",
                )
            )
            continue
        if actor.role == ROLE_MANAGER and owner.department_id != actor.department_id:
            errors.append(
                ImportErrorRead(
                    row=row.row,
                    column="负责人*",
                    message="只能导入本部门负责人",
                )
            )
            continue
        if owner.department_id is None:
            errors.append(
                ImportErrorRead(
                    row=row.row,
                    column="负责人*",
                    message="负责人未分配部门，无法导入",
                )
            )
            continue
        key = normalize_name(row.name)
        if not key:
            errors.append(
                ImportErrorRead(
                    row=row.row, column="客户名称*", message="名称规范化后为空"
                )
            )
            continue
        if key in existing or key in seen_in_file:
            skipped.append(row)
            continue
        seen_in_file.add(key)
        valid.append(row)
    return ValidationResult(valid=valid, errors=errors, skipped=skipped)


def _job_path(job_id: int) -> Path:
    return Path(settings.import_dir) / f"{job_id}.xlsx"


def create_import_job(
    db: Session, actor: User, file_name: str, content: bytes
) -> ImportJob:
    """上传阶段：解析 + 校验 + 落文件；不写客户。"""
    if not content:
        raise InvalidValueError("文件是空的")
    if len(content) > MAX_IMPORT_BYTES:
        raise InvalidValueError("文件不能超过 5MB")

    job = ImportJob(
        kind="customers",
        file_name=Path(file_name).name[:255] or None,
        status="validating",
        created_by=actor.id,
    )
    db.add(job)
    db.flush()

    parsed = parse_customer_workbook(content)
    result = validate_customer_rows(db, actor, parsed.rows)
    errors = [*parsed.errors, *result.errors]

    path = _job_path(job.id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError as exc:
        db.rollback()
        raise InvalidValueError("导入文件保存失败，请重试") from exc

    job.total_rows = parsed.total_rows
    job.success_rows = 0
    job.skipped_rows = len(result.skipped)
    # error_rows 是“出错的行数”，不是错误条数：一行可能同时缺名称和负责人
    job.error_rows = len({error.row for error in errors})
    job.errors_json = [error.model_dump() for error in errors] or None
    job.status = "validated" if not errors else "failed"
    db.commit()
    db.refresh(job)
    return job


def get_import_job(db: Session, actor: User, job_id: int) -> ImportJob:
    """任务可见性：admin 全部，manager 仅自己创建的；越权按 404 处理。"""
    job = db.get(ImportJob, job_id)
    if job is None:
        raise NotFoundError("导入任务不存在")
    if actor.role != ROLE_ADMIN and job.created_by != actor.id:
        raise NotFoundError("导入任务不存在")
    return job
