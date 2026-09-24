"""Точка входа. Запуск: python run.py"""
import asyncio
import logging

from app.bot.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Бот остановлен вручную")