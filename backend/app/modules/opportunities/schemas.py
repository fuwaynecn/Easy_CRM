"""机会的请求/响应模型。

两道刻意的"拒绝"：

- ``extra="forbid"`` + **`OpportunityUpdate` 里没有 `stage`**：改阶段必须走
  ``POST /opportunities/{id}/stage``，这样 history 才不可能被绕过；
- ``OpportunityCreate`` 里没有 `owner_id`：机会负责人从客户负责人继承（§3.5），
  让请求体指定负责人等于允许"机会挂在别人名下"而客户不在。

``win_rate`` 在请求里可空：显式传 `null` 是"回到跟随阶段"，不传是"保持原值"
（PATCH 语义）——这两种"空"的区别靠 `model_dump(exclude_unset=True)` 表达。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.opportunities.models import STAGE_WIN_RATES

# 派生字段：不是 ORM 列，由 model_validator 从关系里补出来
_DERIVED_FIELDS = (
    "customer_name",
    "owner_name",
    "lost_reason_name",
    "win_rate",
    "win_rate_manual",
)


class OpportunityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    name: str = Field(min_length=1, max_length=200)
    stage: str = "contact"
    amount: Decimal = Field(
        default=Decimal(0), ge=0, max_digits=14, decimal_places=2
    )
    expected_close_date: date | None = None
    competitor: str | None = Field(default=None, max_length=200)
    win_rate: int | None = Field(default=None, ge=0, le=100)


class OpportunityUpdate(BaseModel):
    """PATCH 白名单。

    没有 stage（走 /stage）、没有 customer_id（机会不能搬家）、没有 owner_id
    （跟随客户负责人）、没有 won_at / lost_reason_id / lost_note（终态字段只能由
    阶段变更写）。这些字段被提交时 ``extra="forbid"`` 会直接 422。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    amount: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2
    )
    expected_close_date: date | None = None
    competitor: str | None = Field(default=None, max_length=200)
    win_rate: int | None = Field(default=None, ge=0, le=100)


class StageChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    # 重开终态时必须填（service 校验），平时可空；写进 stage_histories.note
    note: str | None = None
    lost_reason_id: int | None = None
    lost_note: str | None = None


class OpportunityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    customer_name: str
    name: str
    stage: str
    amount: Decimal
    expected_close_date: date | None
    competitor: str | None
    owner_id: int
    owner_name: str
    # 生效的赢单率：手工值为准，没填过就用阶段默认值
    win_rate: int
    # 前端据此决定编辑框是留空（跟随阶段）还是显示手工值
    win_rate_manual: bool
    lost_reason_id: int | None
    lost_reason_name: str | None
    lost_note: str | None
    won_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derive_joined_fields(cls, data: Any) -> Any:
        """把关系表上的姓名与"生效赢单率"摆成键。

        不用 ``computed_field``：``from_attributes`` 只按字段名读同一个对象的属性，
        读不到关系另一张表上的列。三个关系都是 ``lazy="raise"``，必须由服务层
        ``joinedload`` 预加载（漏了会立刻报错而不是静默 N+1）。

        派生字段（``customer_name`` / ``owner_name`` / ``lost_reason_name`` /
        ``win_rate`` / ``win_rate_manual``）必须先排除再 ``getattr``：它们在 ORM
        对象上根本不存在，逐个取值会 AttributeError。
        """
        if isinstance(data, (dict, cls)):
            return data
        customer = getattr(data, "customer", None)
        owner = getattr(data, "owner", None)
        lost_reason = getattr(data, "lost_reason", None)
        stored_win_rate = data.win_rate
        values = {
            field: getattr(data, field)
            for field in cls.model_fields
            if field not in _DERIVED_FIELDS
        }
        values["customer_name"] = customer.name if customer is not None else ""
        values["owner_name"] = owner.display_name if owner is not None else ""
        values["lost_reason_name"] = (
            lost_reason.name if lost_reason is not None else None
        )
        values["win_rate"] = (
            stored_win_rate
            if stored_win_rate is not None
            else STAGE_WIN_RATES[data.stage]
        )
        values["win_rate_manual"] = stored_win_rate is not None
        return values


class BoardColumnRead(BaseModel):
    stage: str
    total: int
    amount_total: Decimal
    items: list[OpportunityRead]


class BoardRead(BaseModel):
    columns: list[BoardColumnRead]
