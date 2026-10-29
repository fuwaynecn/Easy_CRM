from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CustomerCreate(BaseModel):
    # extra="forbid"：owner_id 之外的归属字段（department_id、created_by、
    # last_activity_at）必须报 422，而不是被静默丢弃后让人以为写入生效了。
    model_config = ConfigDict(extra="forbid")

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
    # owner_id / department_id / last_activity_at 一律不在更新范围内：归属变更走
    # 领取、分配、转移，last_activity_at 只由 activity 与领取/转移维护（design §3.3）。
    model_config = ConfigDict(extra="forbid")

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


# 请求体字段白名单：创建与更新**故意不对称**，两处都显式列出，别再靠散落在
# 代码里的 if 分支维持口径。
# - 创建可传 owner_id：管理员/主管建客户时直接指定负责人，范围由 _resolve_owner 校验；
# - 更新不可传 owner_id：转移客户必须写 owner_change_logs 并同步 department_id，
#   走专门的领取/分配/转移流程（design §4.3）。若允许 PATCH 顺手改归属，
#   既绕开审计日志，也会让两个冗余归属字段失去同步点。
CUSTOMER_CREATE_FIELDS = frozenset(CustomerCreate.model_fields)
CUSTOMER_UPDATE_FIELDS = frozenset(CustomerUpdate.model_fields)


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
    # 负责人姓名：只有能读到该客户的人才看得到，权限不因为多返回一个名字而变化。
    # 公海客户（owner_id IS NULL）为 None。值来自 User.display_name，不额外开放
    # /users 名录——销售/主管本来就读不到那张表。
    owner_name: str | None = None
    department_id: int | None
    last_activity_at: datetime | None
    remark: str | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derive_owner_name(cls, data: Any) -> Any:
        """从 ORM 的 ``owner`` 关系补 ``owner_name``。

        不用 ``computed_field``：``from_attributes`` 只按字段名读同一个对象的属性，
        读不到关系另一张表上的列，必须在验证前把姓名摆成一个键。``owner`` 由服务层
        用 ``joinedload`` 预加载（关系是 ``lazy="raise"``，漏预加载会立刻报错而不是
        静默 N+1）。
        """
        if isinstance(data, (dict, cls)):
            return data
        owner = getattr(data, "owner", None)
        values = {
            field: getattr(data, field)
            for field in cls.model_fields
            if field != "owner_name"
        }
        values["owner_name"] = owner.display_name if owner is not None else None
        return values
