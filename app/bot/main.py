"""Сборка и запуск бота."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import admin, schedule, settings, start
from app.config import get_config
from app.database.database import init_db
from app.scheduler.jobs import setup_scheduler
from app.utils import setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    config = get_config()
    setup_logging(config.log_level)
    init_db(config.database_url)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(schedule.router)
    dp.include_router(settings.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()

    try:
        me = await bot.get_me()
        logger.info("Бот запущен: @%s", me.username)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":  # запуск напрямую: python -m app.bot.main
    import asyncio

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную")