"""ШАГ 20: уведомления о новых изменениях."""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

from app.database import repository as repo

logger = logging.getLogger(__name__)


async def _safe_send(bot: Bot, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id, text)
        return True
    except TelegramForbiddenError:
        logger.info("Пользователь %s заблокировал бота", chat_id)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        return await _safe_send(bot, chat_id, text)
    except TelegramAPIError as exc:
        logger.warning("Ошибка Telegram API для %s: %s", chat_id, exc)
    except Exception:
        logger.exception("Неожиданная ошибка отправки сообщения %s", chat_id)
    return False


async def notify_about_changes(
    bot: Bot, *, schedule_date: date | None, groups: list[str], title: str
) -> int:
    """Пишет только тем, чья группа затронута и у кого включены уведомления."""
    users = repo.get_users_by_groups(groups, only_enabled=True)
    if not users:
        return 0

    day = schedule_date.strftime("%d.%m.%Y") if schedule_date else "уточните дату"
    sent = 0
    for user in users:
        text = (
            "⚠️ <b>Появились изменения в расписании!</b>\n"
            f"📅 На: {day}\n"
            f"👨‍🎓 Ваша группа: <b>{user.group_name}</b>\n\n"
            f"<i>{title}</i>\n"
            "Нажмите «📍 Сегодня» или «📆 Завтра», чтобы увидеть актуальное расписание."
        )
        if await _safe_send(bot, user.telegram_id, text):
            sent += 1
        await asyncio.sleep(0.05)  # мягкий лимит Telegram

    logger.info("Уведомления об изменениях отправлены: %s", sent)
    return sent


async def broadcast(bot: Bot, text: str) -> int:
    """Тестовая рассылка (админ-функция)."""
    users = repo.get_users_by_groups(repo.get_groups(), only_enabled=True)
    sent = 0
    for user in users:
        if await _safe_send(bot, user.telegram_id, text):
            sent += 1
        await asyncio.sleep(0.05)
    return sent