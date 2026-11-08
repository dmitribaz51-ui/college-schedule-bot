"""ШАГ 20: уведомления о новых изменениях."""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database import repository as repo
from app.utils import today_perm

logger = logging.getLogger(__name__)


async def _safe_send(
    bot: Bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
        return True
    except TelegramForbiddenError:
        logger.info("Пользователь %s заблокировал бота", chat_id)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        return await _safe_send(bot, chat_id, text, reply_markup=reply_markup)
    except TelegramAPIError as exc:
        logger.warning("Ошибка Telegram API для %s: %s", chat_id, exc)
    except Exception:
        logger.exception("Неожиданная ошибка отправки сообщения %s", chat_id)
    return False


async def notify_about_schedule_update(
    bot: Bot,
    *,
    schedule_date: date | None,
    groups: list[str],
    title: str,
    is_new: bool = False,
) -> int:
    """Пишет только тем, чья группа затронута и у кого включены уведомления."""
    if schedule_date is None or schedule_date < today_perm():
        logger.info(
            "Уведомление пропущено для неактуальной даты: %s",
            schedule_date,
        )
        return 0

    users = repo.get_users_by_groups(groups, only_enabled=True)
    if not users:
        return 0

    day = schedule_date.strftime("%d.%m.%Y") if schedule_date else "уточните дату"
    header = (
        "🗓 <b>Появилось новое расписание!</b>"
        if is_new
        else "⚠️ <b>Появились изменения в расписании!</b>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Посмотреть⬇️",
            callback_data=f"pickdate:{schedule_date.isoformat()}",
        )
    ]])
    sent = 0
    for user in users:
        text = (
            f"{header}\n"
            f"📅 На: {day}\n"
            f"👨‍🎓 Ваша группа: <b>{user.group_name}</b>\n\n"
            f"<i>{title}</i>\n"
            "Нажмите «Посмотреть⬇️», чтобы увидеть актуальное расписание."
        )
        if await _safe_send(bot, user.telegram_id, text, reply_markup=keyboard):
            sent += 1
        await asyncio.sleep(0.05)  # мягкий лимит Telegram

    event_name = "новом расписании" if is_new else "изменениях"
    logger.info("Уведомления о %s отправлены: %s", event_name, sent)
    return sent


def _week_label(start: date, end: date) -> str:
    """15.02–20.02.2027; при переходе через год — с годом у обеих дат."""
    if start.year == end.year:
        return f"{start:%d.%m}–{end:%d.%m.%Y}"
    return f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"


async def notify_about_zao_week(
    bot: Bot,
    *,
    week_start: date,
    week_end: date,
    groups: list[str],
    title: str,
    is_new: bool = False,
) -> int:
    """Пишет студентам заочки об опубликованной или изменённой неделе.

    В отличие от очного отделения, у заочки недельный PDF. Поэтому кнопка
    ведёт не на один день, а на весь недельный экран (``week:``), а полностью
    прошедшие недели не рассылаются.
    """
    if week_end < today_perm():
        logger.info(
            "Уведомление заочки пропущено для прошедшей недели %s–%s",
            week_start,
            week_end,
        )
        return 0

    users = repo.get_users_by_groups(groups, only_enabled=True)
    if not users:
        return 0

    label = _week_label(week_start, week_end)
    header = (
        "🗓 <b>Появилось расписание заочки!</b>"
        if is_new
        else "⚠️ <b>Изменения в расписании заочки!</b>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Посмотреть неделю⬇️",
            callback_data=f"week:{week_start.isoformat()}",
        )
    ]])
    sent = 0
    for user in users:
        text = (
            f"{header}\n"
            f"📅 Неделя: {label}\n"
            f"👨‍🎓 Ваша группа: <b>{escape(user.group_name or '')}</b>\n\n"
            f"<i>{escape(title or '')}</i>\n"
            "Нажмите «Посмотреть неделю⬇️», чтобы увидеть расписание."
        )
        if await _safe_send(bot, user.telegram_id, text, reply_markup=keyboard):
            sent += 1
        await asyncio.sleep(0.05)  # мягкий лимит Telegram

    event_name = "новой неделе заочки" if is_new else "изменениях заочки"
    logger.info("Уведомления о %s отправлены: %s", event_name, sent)
    return sent


async def notify_about_changes(
    bot: Bot, *, schedule_date: date | None, groups: list[str], title: str
) -> int:
    return await notify_about_schedule_update(
        bot,
        schedule_date=schedule_date,
        groups=groups,
        title=title,
        is_new=False,
    )


async def broadcast(bot: Bot, text: str) -> int:
    """Тестовая рассылка (админ-функция)."""
    users = repo.get_users_by_groups(repo.get_groups(), only_enabled=True)
    sent = 0
    for user in users:
        if await _safe_send(bot, user.telegram_id, text):
            sent += 1
        await asyncio.sleep(0.05)
    return sent
