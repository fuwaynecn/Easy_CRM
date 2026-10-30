"""业务时区（Asia/Shanghai）的日界换算。

数据库与接口一律 UTC（spec §5），但「今天」「逾期」是业务概念，必须按上海时区
划天。这里只提供两个函数，避免每个模块各写一遍 ``datetime(y, m, d, tzinfo=...)``——
写错一处就会把凌晨 0–8 点的记录算到前一天。
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.errors import InvalidValueError

SHANGHAI = ZoneInfo("Asia/Shanghai")


def ensure_aware(value: datetime | None, label: str) -> datetime | None:
    """拒收 naive 时间：没有时区的时间会被数据库 session 时区静默解释。

    同一条 ``2026-10-31 01:00`` 在 UTC 库与上海时区库上差 8 小时，而且不报错——
    属于"错得无声"的一类，写进库以后只能靠事后核对才能发现。入参为 ``None`` 时原样
    返回（调用方自己决定"不传 = 现在"）。
    """
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise InvalidValueError(f"{label}必须带时区")
    return value


def day_start(day: date) -> datetime:
    """把日期解释成 Asia/Shanghai 当天 00:00（带偏移的 aware datetime）。"""
    return datetime(day.year, day.month, day.day, tzinfo=SHANGHAI)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """``[当天 00:00, 次日 00:00)`` 半开区间：右端用半开区间才不会漏掉当天下午。"""
    start = day_start(day)
    return start, start + timedelta(days=1)


def today_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """「现在」所在的上海时区自然日的 ``[00:00, 次日 00:00)``。"""
    local = (now or datetime.now(UTC)).astimezone(SHANGHAI)
    return day_bounds(local.date())
