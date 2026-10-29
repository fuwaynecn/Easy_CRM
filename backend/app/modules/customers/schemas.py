from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    short_name: str | None = Field(default=None, max_length=64)
    code: str | None = Field(default=None, max_length=64)
    industry: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    scale: str | None = Field(default=None, max_length=32)
    level: str = "C"
    source: str | None = Field(default=None, max_length=64)
    procurement_type: str | None = None
    budget_status: str = "unknown"
    owner_id: int | None = None
    remark: str | None = None
    force: bool = False


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    short_name: str | None = Field(default=None, max_length=64)
    code: str | None = Field(default=None, max_length=64)
    industry: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    scale: str | None = Field(default=None, max_length=32)
    level: str | None = None
    source: str | None = Field(default=None, max_length=64)
    procurement_type: str | None = None
    budget_status: str | None = None
    status: str | None = None
    remark: str | None = None


class CustomerOwnerUpdate(BaseModel):
    owner_id: int | None = None
    reason: str | None = None


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    short_name: str | None
    code: str | None
    industry: str | None
    region: str | None
    address: str | None
    website: str | None
    scale: str | None
    level: str
    source: str | None
    procurement_type: str | None
    budget_status: str
    status: str
    owner_id: int | None
    department_id: int | None
    last_activity_at: datetime | None
    remark: str | None
    created_at: datetime
    updated_at: datetime


class CustomerBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
