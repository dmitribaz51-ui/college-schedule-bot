"""Моя группа, уведомления, информация."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.menu import (
    BTN_GROUP, BTN_INFO, BTN_NOTIFICATIONS, notifications_keyboard,
)
from app.bot.handlers.start import ask_faculty
from app.config import get_config
from app.database import repository as repo

router = Router(name="settings")


@router.message(F.text == BTN_GROUP)
async def my_group(message: Message, state: FSMContext) -> None:
    user = repo.get_user(message.from_user.id)
    current = user.group_name if user and user.group_name else "не выбрана"
    await message.answer(f"⚙️ Ваша группа: <b>{current}</b>\n\nХотите изменить?")
    await ask_faculty(message, state)


@router.message(F.text == BTN_NOTIFICATIONS)
async def notifications(message: Message) -> None:
    user = repo.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    status = "включены ✅" if user.notifications_enabled else "отключены 🔕"
    await message.answer(
        f"🔔 Уведомления об изменениях: <b>{status}</b>",
        reply_markup=notifications_keyboard(user.notifications_enabled),
    )


@router.callback_query(F.data.startswith("notify:"))
async def toggle_notifications(callback: CallbackQuery) -> None:
    enabled = callback.data.split(":")[1] == "on"
    repo.set_notifications(callback.from_user.id, enabled)
    status = "включены ✅" if enabled else "отключены 🔕"
    await callback.message.edit_text(
        f"🔔 Уведомления об изменениях: <b>{status}</b>",
        reply_markup=notifications_keyboard(enabled),
    )
    await callback.answer("Сохранено")


@router.message(F.text == BTN_INFO)
async def info(message: Message) -> None:
    config = get_config()
    await message.answer(
        "ℹ️ <b>О боте</b>\n\n"
        "Я автоматически проверяю страницу расписания ПКПС, скачиваю новые "
        "Excel-файлы («Расписание на …» и «Изменения на …») и показываю "
        "расписание вашей группы.\n\n"
        f"⏱ Интервал проверки: каждые {config.check_interval_minutes} мин.\n"
        "🌐 Источник: <a href=\"https://pkps-perm.ru/students/raspisanie/\">сайт колледжа</a>\n\n"
        "Команды: /start, /today, /tomorrow",
        disable_web_page_preview=True,
    )
