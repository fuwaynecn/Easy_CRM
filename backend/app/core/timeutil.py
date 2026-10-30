"""业务时区（Asia/Shanghai）的日界换算。

数据库与接口一律 UTC（spec §5），但「今天」「逾期」是业务概念，必须按上海时区
划天。这里只提供两个函数，避免每个模块各写一遍 ``datetime(y, m, d, tzinfo=...)``——
写错一处就会把凌晨 0–8 点的记录算到前一天。
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


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
