"""Периодическая проверка сайта (APScheduler)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_config
from app.services.update_service import check_for_updates

logger = logging.getLogger(__name__)


async def check_updates_job(bot: Bot) -> None:
    try:
        report = await check_for_updates(bot=bot)
        logger.info("Проверка сайта завершена:\n%s", report.summary())
    except Exception:
        # одна ошибка не должна ломать бота
        logger.exception("Ошибка в задаче проверки сайта")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    config = get_config()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_updates_job,
        trigger="interval",
        minutes=config.check_interval_minutes,
        args=[bot],
        id="check_schedule_site",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        next_run_time=datetime.now() + timedelta(seconds=15),  # первая проверка сразу
    )
    logger.info("Планировщик: проверка каждые %s мин.", config.check_interval_minutes)
    return scheduler