from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class ImportErrorRead(BaseModel):
    row: int
    column: str
    message: str


class ImportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    file_name: str | None
    status: str
    total_rows: int
    success_rows: int
    skipped_rows: int
    error_rows: int
    errors: list[ImportErrorRead] = []
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _errors_from_json(cls, data: Any) -> Any:
        """ORM 上是 `errors_json`，wire 上是 `errors`；不要在响应里暴露 JSONB 列名。"""
        if isinstance(data, (dict, cls)):
            return data
        values = {
            field: getattr(data, field)
            for field in cls.model_fields
            if field != "errors"
        }
        values["errors"] = getattr(data, "errors_json", None) or []
        return values
