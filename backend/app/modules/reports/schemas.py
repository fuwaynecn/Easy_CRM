"""报表响应模型。

金额用 `Decimal`（Pydantic v2 在 JSON 里序列化成定点字符串，与既有机会接口一致）；
比率与天数用 `float`，`total=0` 时给 `null`，前端显示「—」而不是 0%。
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class PeriodReport(BaseModel):
    date_from: date
    date_to: date


class FunnelStageRead(BaseModel):
    stage: str
    count: int
    amount: Decimal


class FunnelReport(BaseModel):
    as_of: datetime
    stages: list[FunnelStageRead]


class DepartmentRateRead(BaseModel):
    department_id: int | None
    department_name: str
    won: int
    lost: int
    total: int
    rate: float | None


class WinRateReport(PeriodReport):
    won: int
    lost: int
    total: int
    rate: float | None
    by_department: list[DepartmentRateRead]


class CycleOwnerRead(BaseModel):
    owner_id: int
    owner_name: str
    won_count: int
    avg_days: float | None


class CycleMonthRead(BaseModel):
    month: str
    won_count: int
    avg_days: float | None


class CycleReport(PeriodReport):
    won_count: int
    avg_days: float | None
    by_owner: list[CycleOwnerRead]
    by_month: list[CycleMonthRead]


class PerformanceOwnerRead(BaseModel):
    owner_id: int
    owner_name: str
    won_count: int
    amount: Decimal


class PerformanceMonthRead(BaseModel):
    month: str
    won_count: int
    amount: Decimal


class PerformanceReport(PeriodReport):
    won_count: int
    total_amount: Decimal
    by_owner: list[PerformanceOwnerRead]
    by_month: list[PerformanceMonthRead]


class ActivityOwnerRead(BaseModel):
    user_id: int
    user_name: str
    count: int


class ActivityBucketRead(BaseModel):
    bucket: str
    count: int


class ActivityReport(PeriodReport):
    granularity: str
    total: int
    by_owner: list[ActivityOwnerRead]
    series: list[ActivityBucketRead]


class LostReasonRead(BaseModel):
    lost_reason_id: int
    lost_reason_name: str
    count: int
    amount: Decimal


class LostReasonReport(PeriodReport):
    total_count: int
    total_amount: Decimal
    items: list[LostReasonRead]


class CompetitorRead(BaseModel):
    competitor: str
    count: int
    amount: Decimal
    won: int
    lost: int
    rate: float | None


class CompetitorReport(PeriodReport):
    total_opportunities: int
    items: list[CompetitorRead]
