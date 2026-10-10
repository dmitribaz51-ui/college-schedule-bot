"""Показ расписания: сегодня, завтра, произвольная дата, изменения."""
from __future__ import annotations

import logging
from html.parser import HTMLParser
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.bot.keyboards.menu import (
    BTN_CHANGES, BTN_PICK_DATE, BTN_TODAY, BTN_TOMORROW, main_menu,
    pick_date_keyboard,
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


TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_LIMIT = 4096


class _ScheduleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_schedule_text(text: str) -> str:
    parser = _ScheduleTextParser()
    parser.feed(text)
    return "".join(parser.parts)


def _telegram_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


async def _send_schedule_text(message: Message, text: str) -> None:
    plain = _plain_schedule_text(text)
    if _telegram_length(plain) <= TELEGRAM_TEXT_LIMIT:
        await message.answer(text)
        return

    # В редком случае очень длинного расписания сохраняем весь текст,
    # разбивая его без HTML, чтобы не разорвать теги форматирования.
    chunk: list[str] = []
    size = 0
    for char in plain:
        char_size = _telegram_length(char)
        if size + char_size > TELEGRAM_TEXT_LIMIT:
            await message.answer("".join(chunk), parse_mode=None)
            chunk = []
            size = 0
        chunk.append(char)
        size += char_size
    if chunk:
        await message.answer("".join(chunk), parse_mode=None)


async def _send_schedule(message: Message, group: str, day, faculty: str, course: int | None) -> None:
    """Отправляет расписание и актуальный исходный файл (с кэшированием Telegram file_id), не обрезая текст."""
    schedule_text = format_day_schedule(group, day, faculty, course)
    schedule_file = repo.get_schedule_file_for_group(day, group, faculty)

    if schedule_file and schedule_file.local_path:
        file_path = Path(schedule_file.local_path)
        if file_path.exists():
            # Если есть сохранённый telegram_file_id, отправляем по нему без повторной загрузки.
            cached_file_id = schedule_file.telegram_file_id
            doc_to_send = cached_file_id or FSInputFile(file_path)

            async def _send_doc(caption: str | None = None) -> Message | None:
                nonlocal cached_file_id, doc_to_send
                try:
                    sent = await message.answer_document(
                        document=doc_to_send,
                        caption=caption,
                    )
                    # Если отправляли новый файл с диска и получили file_id — сохраняем в кэш
                    if sent and sent.document and not cached_file_id:
                        repo.set_file_telegram_id(schedule_file.id, sent.document.file_id)
                        cached_file_id = sent.document.file_id
                    return sent
                except Exception as e:
                    # Если отправка по кэшированному file_id упала — пробуем отправить файл с диска
                    if cached_file_id:
                        logger.warning(
                            "Не удалось отправить по cached file_id %s, пробуем FSInputFile: %s",
                            cached_file_id, e
                        )
                        doc_to_send = FSInputFile(file_path)
                        try:
                            sent = await message.answer_document(
                                document=doc_to_send,
                                caption=caption,
                            )
                            if sent and sent.document:
                                repo.set_file_telegram_id(schedule_file.id, sent.document.file_id)
                                cached_file_id = sent.document.file_id
                            return sent
                        except Exception as e2:
                            logger.warning("Повторная отправка с диска также не удалась: %s", e2)
                            raise e2
                    raise e

            # В коротком расписании документ и весь текст помещаются в одно сообщение.
            if _telegram_length(_plain_schedule_text(schedule_text)) <= TELEGRAM_CAPTION_LIMIT:
                try:
                    await _send_doc(caption=schedule_text)
                    return
                except Exception as e:
                    logger.warning(
                        "Не удалось отправить расписание вместе с файлом %s: %s",
                        file_path,
                        e,
                    )

            # Длинный текст нельзя помещать в Telegram caption: сначала отправляем
            # расписание целиком, затем файл отдельным сообщением.
            await _send_schedule_text(message, schedule_text)
            try:
                await _send_doc(caption=None)
            except Exception as e:
                logger.warning("Не удалось отправить файл %s: %s", file_path, e)
            return

    # Если файла нет, отправляем расписание как обычно.
    await _send_schedule_text(message, schedule_text)


@router.message(F.text == BTN_TODAY)
@router.message(Command("today"))
async def show_today(message: Message, state: FSMContext) -> None:
    await state.clear()  # Сбрасываем режим выбора даты
    group = await _require_group(message)
    if group:
        await _send_schedule(message, group[0], today_perm(), group[1], group[2])


@router.message(F.text == BTN_TOMORROW)
@router.message(Command("tomorrow"))
async def show_tomorrow(message: Message, state: FSMContext) -> None:
    await state.clear()  # Сбрасываем режим выбора даты
    group = await _require_group(message)
    if group:
        await _send_schedule(message, group[0], tomorrow_perm(), group[1], group[2])


@router.message(Command("date"))
async def cmd_pick_date(message: Message, state: FSMContext) -> None:
    """Командный shortcut кнопки «Выбрать дату»."""
    await ask_date(message, state)


@router.message(Command("changes"))
async def cmd_changes(message: Message, state: FSMContext) -> None:
    """Командный shortcut кнопки «Последние изменения»."""
    await state.clear()  # Сбрасываем режим выбора даты
    selected = _user_group(message.from_user.id)
    await message.answer(format_recent_changes(selected[0] if selected else None))


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
    kb = pick_date_keyboard(dates)
    await message.answer(
        "Введите дату в формате <code>ДД.ММ.ГГГГ</code> (пример <code>10.09</code>)." + hint,
        reply_markup=kb,
    )


@router.message(F.text == BTN_CHANGES)
async def show_changes(message: Message, state: FSMContext) -> None:
    await state.clear()  # Сбрасываем режим выбора даты
    selected = _user_group(message.from_user.id)
    await message.answer(format_recent_changes(selected[0] if selected else None))


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message, state: FSMContext) -> None:
    """Неизвестная команда: выходит из режима выбора даты, датой не считается."""
    await state.clear()
    await message.answer(
        "Неизвестная команда. Используйте меню ниже 👇",
        reply_markup=main_menu(),
    )


@router.message(
    ScheduleStates.waiting_date,
    ~F.text.in_([BTN_TODAY, BTN_TOMORROW, BTN_PICK_DATE, BTN_CHANGES])
)
async def show_by_date(message: Message, state: FSMContext) -> None:
    from app.bot.keyboards.menu import BTN_GROUP, BTN_NOTIFICATIONS, BTN_INFO

    # Если это кнопка меню — игнорируем, пусть обработают другие хендлеры
    if message.text in [BTN_GROUP, BTN_NOTIFICATIONS, BTN_INFO]:
        return

    day = parse_user_date(message.text or "")
    if day is None:
        await message.answer("Не понял дату 🤔 Пример: <code>10.09.2026</code>")
        return
    await state.clear()
    group = await _require_group(message)
    if group:
        await _send_schedule(message, group[0], day, group[1], group[2])


@router.callback_query(F.data.startswith("pickdate:"))
async def pick_date(callback: CallbackQuery, state: FSMContext) -> None:
    from datetime import date as date_type

    raw = (callback.data or "").removeprefix("pickdate:")
    try:
        day = date_type.fromisoformat(raw)
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return

    await state.clear()  # ВАЖНО: выходим из режима waiting_date

    selected = _user_group(callback.from_user.id)
    if not selected:
        await callback.answer("Сначала выберите группу — отправьте /start.", show_alert=True)
        return

    await callback.answer()  # закрыть «часики» Telegram
    if callback.message is not None:
        await _send_schedule(callback.message, selected[0], day, selected[1], selected[2])
