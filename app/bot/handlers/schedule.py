"""Показ расписания: сегодня, завтра, произвольная дата, изменения."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.bot.keyboards.menu import (
    BTN_CHANGES, BTN_PICK_DATE, BTN_TODAY, BTN_TOMORROW, main_menu,
)
from app.database import repository as repo
from app.services.schedule_service import format_day_schedule, format_recent_changes
from app.utils import parse_user_date, today_perm, tomorrow_perm

logger = logging.getLogger(__name__)
router = Router(name="schedule")


class ScheduleStates(StatesGroup):
    waiting_date = State()


def _user_group(telegram_id: int) -> tuple[str, str, int | None] | None:
    from app.utils import detect_faculty
    user = repo.get_user(telegram_id)
    if not user or not user.group_name:
        return None
    # Автоопределение факультета по префиксу группы (приоритет над сохранённым)
    faculty = detect_faculty(user.group_name)
    return (user.group_name, faculty, user.course)


async def _require_group(message: Message) -> str | None:
    selected = _user_group(message.from_user.id)
    if not selected:
        await message.answer(
            "Сначала выберите группу — отправьте /start.", reply_markup=main_menu()
        )
        return None
    return selected


@router.message(F.text == BTN_TODAY)
@router.message(Command("today"))
async def show_today(message: Message) -> None:
    group = await _require_group(message)
    if group:
        await message.answer(format_day_schedule(group[0], today_perm(), group[1], group[2]))


@router.message(F.text == BTN_TOMORROW)
@router.message(Command("tomorrow"))
async def show_tomorrow(message: Message) -> None:
    group = await _require_group(message)
    if group:
        await message.answer(format_day_schedule(group[0], tomorrow_perm(), group[1], group[2]))


@router.message(F.text == BTN_PICK_DATE)
async def ask_date(message: Message, state: FSMContext) -> None:
    group = await _require_group(message)
    if not group:
        return
    dates = repo.available_dates(group[0], faculty=group[1])
    hint = ""
    if dates:
        hint = "\n\nЕсть данные на: " + ", ".join(d.strftime("%d.%m") for d in dates)
    await state.set_state(ScheduleStates.waiting_date)
    await message.answer(
        "Введите дату в формате <code>ДД.ММ.ГГГГ</code> (можно <code>10.09</code>)." + hint
    )


@router.message(ScheduleStates.waiting_date)
async def show_by_date(message: Message, state: FSMContext) -> None:
    day = parse_user_date(message.text or "")
    if day is None:
        await message.answer("Не понял дату 🤔 Пример: <code>10.09.2026</code>")
        return
    await state.clear()
    group = await _require_group(message)
    if group:
        await message.answer(format_day_schedule(group[0], day, group[1], group[2]))


@router.message(F.text == BTN_CHANGES)
async def show_changes(message: Message) -> None:
    selected = _user_group(message.from_user.id)
    await message.answer(format_recent_changes(selected[0] if selected else None))
