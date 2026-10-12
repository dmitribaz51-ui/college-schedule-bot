"""Хендлеры для просмотра звонков (кнопка «Звонки⏰»)."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.menu import BTN_BELLS, bells_back_keyboard, bells_keyboard
from app.database import repository as repo
from app.services.bells_data import (
    BELLS_CHERN_MONDAY_1,
    BELLS_CHERN_MONDAY_24,
    BELLS_CHERN_WEEKDAY_1,
    BELLS_CHERN_WEEKDAY_24,
    BELLS_PERMSKAYA_MONDAY,
    BELLS_PERMSKAYA_SATURDAY,
    BELLS_PERMSKAYA_WEEKDAY,
)
from app.utils import detect_faculty

logger = logging.getLogger(__name__)
router = Router(name="bells")

BELL_TEXTS = {
    "perm_mon": BELLS_PERMSKAYA_MONDAY,
    "perm_week": BELLS_PERMSKAYA_WEEKDAY,
    "perm_sat": BELLS_PERMSKAYA_SATURDAY,
    "chern_mon_1": BELLS_CHERN_MONDAY_1,
    "chern_mon_24": BELLS_CHERN_MONDAY_24,
    "chern_week_1": BELLS_CHERN_WEEKDAY_1,
    "chern_week_24": BELLS_CHERN_WEEKDAY_24,
}


async def cleanup_bells_message(message: Message, state: FSMContext) -> None:
    """Удаляет предыдущее сообщение со звонками, чтобы не засорять чат."""
    data = await state.get_data()
    bells_msg_id = data.get("bells_message_id")
    if bells_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=bells_msg_id)
        except TelegramBadRequest as exc:
            logger.info("Не удалось удалить сообщение звонков %s: %s", bells_msg_id, exc)
        await state.update_data(bells_message_id=None)


def _get_user_faculty_and_course(telegram_id: int) -> tuple[str, int | None]:
    user = repo.get_user(telegram_id)
    if not user:
        return "permskaya", None
    faculty = detect_faculty(user.group_name) if user.group_name else (user.faculty or "permskaya")
    return faculty, user.course


@router.message(F.text == BTN_BELLS)
@router.message(Command("bells"))
async def show_bells_menu(message: Message, state: FSMContext) -> None:
    """Открывает меню выбора дня для звонков."""
    # Сначала удаляем старое сообщение со звонками, если оно висит
    await cleanup_bells_message(message, state)
    await state.clear()

    faculty, course = _get_user_faculty_and_course(message.from_user.id)
    kb = bells_keyboard(faculty=faculty, course=course)

    text = (
        "⏰ <b>Расписание звонков</b>\n\n"
        "Выберите день недели:"
    )
    sent = await message.answer(text, reply_markup=kb)
    await state.update_data(bells_message_id=sent.message_id)


@router.callback_query(F.data.startswith("bells:"))
async def bells_callback(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.removeprefix("bells:")

    if action == "close":
        try:
            await callback.message.delete()
        except Exception:
            pass
        await state.update_data(bells_message_id=None)
        await callback.answer()
        return

    faculty, course = _get_user_faculty_and_course(callback.from_user.id)

    if action == "back":
        kb = bells_keyboard(faculty=faculty, course=course)
        text = "⏰ <b>Расписание звонков</b>\n\nВыберите день недели:"
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    text_to_show = BELL_TEXTS.get(action)
    if not text_to_show:
        await callback.answer("Расписание не найдено", show_alert=True)
        return

    try:
        await callback.message.edit_text(text_to_show, reply_markup=bells_back_keyboard())
    except Exception:
        pass
    await callback.answer()
