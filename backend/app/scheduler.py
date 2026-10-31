"""每日定时任务。

APScheduler 3.x BackgroundScheduler；进程内单实例。多 worker 的重复执行由
`recycle_customers` 的 advisory lock 兜底——scheduler 层不做分布式选主。
"""

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.db.session import SessionLocal
from app.modules.pools.service import recycle_customers, sync_department_consistency

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_recycle_job() -> None:
    with SessionLocal() as db:
        try:
            count = recycle_customers(db)
            logger.info("每日回收完成：%d 个客户", count)
        except Exception:
            db.rollback()
            logger.exception("每日回收失败")


def _run_consistency_job() -> None:
    with SessionLocal() as db:
        try:
            count = sync_department_consistency(db)
            logger.info("部门一致性检查完成：修复 %d 条", count)
        except Exception:
            db.rollback()
            logger.exception("部门一致性检查失败")


def build_scheduler() -> BackgroundScheduler:
    tz = ZoneInfo(settings.scheduler_timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        _run_recycle_job,
        CronTrigger(hour=2, minute=0, timezone=tz),
        id="daily_recycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_consistency_job,
        CronTrigger(hour=2, minute=10, timezone=tz),
        id="department_consistency",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    return scheduler


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = build_scheduler()
    _scheduler.start()
    logger.info("定时任务已启动（时区 %s）", settings.scheduler_timezone)


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
