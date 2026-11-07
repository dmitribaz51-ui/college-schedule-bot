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

from app.bot.handlers.bells import cleanup_bells_message
from app.bot.keyboards.menu import (
    BTN_BELLS, BTN_PICK_DATE, BTN_TODAY, BTN_TOMORROW, main_menu,
    pick_date_keyboard, week_picker_keyboard,
)
from app.database import repository as repo
from app.services.schedule_service import (
    format_day_schedule,
    format_recent_changes,
    format_week_schedule,
    get_day_schedule,
)
from app.utils import (
    human_date,
    is_part_time_group,
    parse_user_date,
    today_perm,
    tomorrow_perm,
)

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


def _part_time_week_label(start, end) -> str:
    if start.year == end.year:
        return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m.%Y')}"
    return f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"


def _part_time_week(group: str, day, faculty: str, weeks=None):
    """Неделя заочки, в которую попадает день, либо ближайшая будущая.

    Прошедшую неделю автоматически не подставляем: 26.10 в ноябре — это
    уже история, а не «расписание на сегодня». История доступна через
    «Выбрать дату».
    """
    if weeks is None:
        weeks = repo.get_group_weeks(group, faculty)
    if not weeks:
        return None

    for start, end in weeks:
        if start <= day <= end:
            return start, end

    future = [week for week in weeks if week[0] > day]
    if future:
        return min(future, key=lambda week: week[0])
    return None


async def _send_part_time_day(
    message: Message,
    group: str,
    day,
    faculty: str,
    course: int | None,
) -> None:
    """Заочка: вместо одного дня показываем неделю и не цепляем Excel.

    Если на выбранный день недели нет, честно пишем это и открываем
    ближайшую будущую неделю. Если будущих недель нет, не показываем
    старую прошедшую, а говорим, что следующая сессия ещё не опубликована.
    Отдельные inline-кнопки для заочки не нужны: выбор другой недели
    остаётся в «Выбрать дату».
    """
    weeks = repo.get_group_weeks(group, faculty)
    week = _part_time_week(group, day, faculty, weeks=weeks)
    if week is None:
        if weeks:
            text = (
                f"На {human_date(day)} занятий нет.\n"
                "Следующая сессия заочки для вашей группы ещё не опубликована."
            )
        else:
            text = "Расписание заочки пока не загружено."
        await message.answer(
            f"📅 <b>Заочное отделение</b>\n\n{text}",
            reply_markup=main_menu(),
        )
        return

    start, end = week
    intro = None
    if not (start <= day <= end):
        intro = (
            f"На {human_date(day)} занятий нет.\n"
            f"Ближайшая доступная неделя заочки: {_part_time_week_label(start, end)}."
        )
    else:
        items, _ = get_day_schedule(group, day, faculty, course)
        if not items:
            intro = f"На {human_date(day)} занятий нет. Ниже — вся неделя."

    await _send_week_schedule(
        message,
        group,
        start,
        faculty,
        course,
        intro=intro,
    )


async def _send_schedule(message: Message, group: str, day, faculty: str, course: int | None) -> None:
    """Отправляет расписание и актуальный исходный файл (с кэшированием Telegram file_id), не обрезая текст."""
    # У заочки нет ежедневного Excel: только недельный PDF. Поэтому любые
    # «Сегодня/Завтра/дата» для группы «зо» ведут на недельный экран.
    if is_part_time_group(group):
        await _send_part_time_day(message, group, day, faculty, course)
        return

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


async def _send_week_schedule(
    message: Message,
    group: str,
    week_start,
    faculty: str,
    course: int | None,
    *,
    intro: str | None = None,
) -> None:
    """Заочка: одно сообщение на неделю + PDF недели."""
    schedule_text = format_week_schedule(group, week_start, faculty, course)
    if intro:
        schedule_text = f"{intro}\n\n{schedule_text}"
    schedule_file = repo.get_group_week_file(group, week_start, faculty)
    if not schedule_file or not schedule_file.local_path:
        await _send_schedule_text(message, schedule_text)
        return

    file_path = Path(schedule_file.local_path)
    if not file_path.exists():
        await _send_schedule_text(message, schedule_text)
        return

    cached_file_id = schedule_file.telegram_file_id
    doc_to_send = cached_file_id or FSInputFile(file_path)

    async def _send_doc(caption: str | None = None):
        nonlocal cached_file_id, doc_to_send
        try:
            sent = await message.answer_document(
                document=doc_to_send,
                caption=caption,
            )
            if sent and sent.document and not cached_file_id:
                repo.set_file_telegram_id(schedule_file.id, sent.document.file_id)
                cached_file_id = sent.document.file_id
            return sent
        except Exception as exc:
            if cached_file_id:
                logger.warning(
                    "Не удалось отправить недельный PDF по file_id %s: %s",
                    cached_file_id,
                    exc,
                )
                doc_to_send = FSInputFile(file_path)
                sent = await message.answer_document(
                    document=doc_to_send,
                    caption=caption,
                )
                if sent and sent.document:
                    repo.set_file_telegram_id(schedule_file.id, sent.document.file_id)
                    cached_file_id = sent.document.file_id
                return sent
            raise

    if _telegram_length(_plain_schedule_text(schedule_text)) <= TELEGRAM_CAPTION_LIMIT:
        try:
            await _send_doc(caption=schedule_text)
            return
        except Exception as exc:
            logger.warning("Не удалось отправить неделю с PDF: %s", exc)

    await _send_schedule_text(message, schedule_text)
    try:
        await _send_doc(caption=None)
    except Exception as exc:
        logger.warning("Не удалось отправить PDF недели %s: %s", file_path, exc)


async def ask_week(message: Message, state: FSMContext, selected: tuple) -> None:
    """Экран выбора доступной недели для заочной группы."""
    group, faculty, _course = selected
    weeks = repo.get_group_weeks(group, faculty)
    await state.clear()
    if not weeks:
        await message.answer(
            "Расписание заочного отделения пока не загружено.",
            reply_markup=main_menu(),
        )
        return

    today = today_perm()
    index = next(
        (i for i, (_start, end) in enumerate(weeks) if end >= today),
        len(weeks) - 1,
    )
    await message.answer(
        "📅 <b>Заочное отделение</b>\nВыберите неделю:",
        reply_markup=week_picker_keyboard(weeks, index),
    )


@router.callback_query(F.data.startswith("weekpage:"))
async def week_page(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        index = int((callback.data or "").removeprefix("weekpage:"))
    except ValueError:
        await callback.answer("Некорректная страница", show_alert=True)
        return

    selected = _user_group(callback.from_user.id)
    if not selected or not is_part_time_group(selected[0]):
        await callback.answer("Сначала выберите заочную группу — /start.", show_alert=True)
        return

    weeks = repo.get_group_weeks(selected[0], selected[1])
    if not weeks:
        await callback.answer("Недели пока не загружены", show_alert=True)
        return
    index = max(0, min(index, len(weeks) - 1))
    await state.clear()
    if callback.message is not None:
        await callback.message.edit_text(
            "📅 <b>Заочное отделение</b>\nВыберите неделю:",
            reply_markup=week_picker_keyboard(weeks, index),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("week:"))
async def open_week(callback: CallbackQuery, state: FSMContext) -> None:
    from datetime import date as date_type

    raw = (callback.data or "").removeprefix("week:")
    try:
        week_start = date_type.fromisoformat(raw)
    except ValueError:
        await callback.answer("Некорректная неделя", show_alert=True)
        return

    selected = _user_group(callback.from_user.id)
    if not selected or not is_part_time_group(selected[0]):
        await callback.answer("Сначала выберите заочную группу — /start.", show_alert=True)
        return

    await state.clear()
    await callback.answer()
    if callback.message is not None:
        await _send_week_schedule(
            callback.message,
            selected[0],
            week_start,
            selected[1],
            selected[2],
        )


@router.message(F.text == BTN_TODAY)
@router.message(Command("today"))
async def show_today(message: Message, state: FSMContext) -> None:
    await cleanup_bells_message(message, state)
    await state.clear()  # Сбрасываем режим выбора даты
    group = await _require_group(message)
    if group:
        await _send_schedule(message, group[0], today_perm(), group[1], group[2])


@router.message(F.text == BTN_TOMORROW)
@router.message(Command("tomorrow"))
async def show_tomorrow(message: Message, state: FSMContext) -> None:
    await cleanup_bells_message(message, state)
    await state.clear()  # Сбрасываем режим выбора даты
    group = await _require_group(message)
    if group:
        await _send_schedule(message, group[0], tomorrow_perm(), group[1], group[2])


@router.message(Command("date"))
async def cmd_pick_date(message: Message, state: FSMContext) -> None:
    """Командный shortcut кнопки «Выбрать дату»."""
    await cleanup_bells_message(message, state)
    await ask_date(message, state)


@router.message(Command("changes"))
async def cmd_changes(message: Message, state: FSMContext) -> None:
    """Последние файлы изменений — только для админа (студентам недоступно)."""
    from app.bot.handlers.broadcast_common import is_admin

    if not is_admin(message.from_user.id):
        return  # для студентов команды не существует
    await cleanup_bells_message(message, state)
    await state.clear()
    selected = _user_group(message.from_user.id)
    await message.answer(format_recent_changes(selected[0] if selected else None))


@router.message(F.text == BTN_PICK_DATE)
async def ask_date(message: Message, state: FSMContext) -> None:
    await cleanup_bells_message(message, state)
    group = await _require_group(message)
    if not group:
        return
    # Заочка выбирает не дату, а доступную неделю; очное — как раньше.
    if is_part_time_group(group[0]):
        await ask_week(message, state, group)
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
    ~F.text.in_([BTN_TODAY, BTN_TOMORROW, BTN_PICK_DATE, BTN_BELLS])
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
