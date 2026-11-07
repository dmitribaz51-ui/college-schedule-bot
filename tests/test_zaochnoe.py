"""Тесты заочки: группы, PDF-ссылки, разбор текстовых PDF и неделя."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User as TelegramUser

from app.bot.handlers import schedule as schedule_handler
from app.bot.handlers.start import choose_course
from app.bot.keyboards.menu import week_picker_keyboard
from app.database import database as db
from app.database import repository as repo
from app.parser.pdf_parser import parse_pdf_file
from app.parser.website_parser import parse_pdf_links_from_html
from app.services.schedule_service import format_week_schedule
from app.utils import course_from_group, is_part_time_group, normalize_group

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def memory_db():
    db.init_db("sqlite:///:memory:")


def test_normalize_part_time_group():
    assert normalize_group("ТД-24-9 зо") == "ТД-24-9 зо"
    assert normalize_group("тд 24 9 зо") == "ТД-24-9 зо"
    assert normalize_group("ТД-24-9-ЗО") == "ТД-24-9 зо"
    assert normalize_group("ТД-24-9") == "ТД-24-9"
    assert normalize_group("просто текст") is None

    assert is_part_time_group("ТД-24-9 зо")
    assert not is_part_time_group("ТД-24-9")
    assert not is_part_time_group(None)


def test_course_from_group():
    assert course_from_group("Л-26-11 зо", date(2026, 11, 9)) == 1
    assert course_from_group("Л-25-9 зо", date(2026, 11, 9)) == 2
    assert course_from_group("Л-24-11 зо", date(2026, 11, 9)) == 3
    assert course_from_group("Л-23-9 зо", date(2026, 11, 9)) == 4
    # февраль 2027 — тот же учебный год 2026/2027
    assert course_from_group("Л-24-11 зо", date(2027, 2, 15)) == 3


def test_startup_migration_fixes_trade_automation_subject(tmp_path):
    """Старые заочные строки «горно» должны стать «торгово», очные — нет."""
    db_file = tmp_path / "bot.db"
    url = f"sqlite:///{db_file.as_posix()}"
    db.init_db(url)
    day = date(2026, 10, 26)
    repo.replace_group_lessons(
        schedule_date=day,
        group_name="ТД-25-11 зо",
        source_type="schedule",
        course=2,
        lessons=[
            {
                "number": 4,
                "subject": "Автоматизация горно-технологических процессов",
            }
        ],
    )
    repo.replace_group_lessons(
        schedule_date=day,
        group_name="ТД-25-11",
        source_type="schedule",
        course=2,
        lessons=[
            {
                "number": 4,
                "subject": "Автоматизация горно-технологических процессов",
            }
        ],
    )

    db.init_db(url)

    zao = repo.get_lessons(day, "ТД-25-11 зо", faculty="permskaya")
    full_time = repo.get_lessons(day, "ТД-25-11", faculty="permskaya")
    assert zao[0].subject == "Автоматизация торгово-технологических процессов"
    assert full_time[0].subject == "Автоматизация горно-технологических процессов"


def test_pdf_links_parser():
    html = """
    <a href="/files/zaochnoe/09.11.pdf">
      Расписание 09.11-14.11.2026 заочная форма обучения
      (группы Л-26-11 зо, Ф-26-11 зо, ТД-26-11 зо) 1 неделя
    </a>
    <a href="/files/zaochnoe/gup.pdf">График учебного процесса на 2026-2027</a>
    <a href="/files/zaochnoe/15.02.pdf">Расписание 15.02-20.02.2027 (1 неделя)</a>
    """
    links = parse_pdf_links_from_html(
        html, "https://pkps-perm.ru/students/zaochnoe-otdelenie/"
    )
    assert len(links) == 2
    first, second = links
    assert first.schedule_date == date(2026, 11, 9)
    assert first.date_end == date(2026, 11, 14)
    assert first.groups == ["Л-26-11 зо", "Ф-26-11 зо", "ТД-26-11 зо"]
    assert second.schedule_date == date(2027, 2, 15)
    assert second.date_end == date(2027, 2, 20)
    assert second.groups == []


def test_parse_text_pdf_09_11():
    schedules = parse_pdf_file(FIXTURES / "zaochnoe_09.11.pdf")
    groups = {item.group for item in schedules}
    assert groups == {
        "Л-26-11 зо",
        "Ф-26-11 зо",
        "ТД-26-11 зо",
        "Л-25-9 зо",
        "Ф-25-9 зо",
        "ТД-25-9 зо",
    }
    assert len(schedules) == 36

    monday = next(
        item
        for item in schedules
        if item.group == "Л-26-11 зо" and item.schedule_date == date(2026, 11, 9)
    )
    assert monday.course == 1
    org = next(lesson for lesson in monday.lessons if lesson.number == 0)
    assert org.subject == "ОРГ. СОБРАНИЕ"
    assert org.start_time == "14:10"
    assert org.room == "314"

    lesson = next(lesson for lesson in monday.lessons if lesson.number == 4)
    assert lesson.start_time == "14:45"
    assert lesson.end_time == "16:10"
    assert "История России" in lesson.subject
    assert "Ширинкин" in lesson.teacher
    assert lesson.room == "209"


def test_parse_text_pdf_12_10_merged_columns_and_courses():
    schedules = parse_pdf_file(FIXTURES / "zaochnoe_12.10.pdf")
    assert {item.group for item in schedules} == {
        "Л-23-9 зо",
        "К-23-9 зо",
        "Л-24-11 зо",
        "ТД-24-11 зо",
    }
    assert len(schedules) == 24
    first = next(
        item
        for item in schedules
        if item.group == "Л-24-11 зо" and item.schedule_date == date(2026, 10, 12)
    )
    assert first.course == 3
    assert first.lessons[0].start_time == "14:45"
    assert first.lessons[0].end_time == "16:10"
    assert "Логистика" in first.lessons[0].subject

    fourth = next(
        item
        for item in schedules
        if item.group == "Л-23-9 зо" and item.schedule_date == date(2026, 10, 12)
    )
    assert fourth.course == 4
    # Первая колонка объединяет Л-23-9 и Л-24-11 — пары должны совпадать
    assert [l.subject for l in first.lessons] == [l.subject for l in fourth.lessons]

    friday = next(
        item
        for item in schedules
        if item.group == "Л-24-11 зо" and item.schedule_date == date(2026, 10, 16)
    )
    org = next(lesson for lesson in friday.lessons if lesson.number == 0)
    assert org.start_time == "14:05"
    assert org.room == "305"


def test_week_registry_and_message(memory_db):
    schedules = parse_pdf_file(FIXTURES / "zaochnoe_09.11.pdf")
    for item in schedules:
        repo.replace_group_lessons(
            schedule_date=item.schedule_date,
            group_name=item.group,
            source_type="schedule",
            course=item.course,
            lessons=[lesson.to_dict() for lesson in item.lessons],
            faculty="permskaya",
        )
        repo.upsert_groups([(item.group, item.course)], faculty="permskaya")

    # Реестр не зависит от пар: группа видна даже без занятий
    repo.upsert_groups([("Л-24-9 зо", 3)], faculty="permskaya")
    assert "Л-26-11 зо" in repo.get_groups(1, "permskaya")
    assert "Л-24-9 зо" in repo.get_groups(3, "permskaya")
    assert "Л-24-9 зо" not in repo.get_groups(1, "permskaya")

    weeks = repo.get_group_weeks("Л-26-11 зо", "permskaya")
    assert weeks == [(date(2026, 11, 9), date(2026, 11, 14))]

    text = format_week_schedule("Л-26-11 зо", date(2026, 11, 9))
    assert "Заочка, 09.11–14.11.2026" in text
    assert "Напоминание из расписания" in text
    assert "ОРГ. СОБРАНИЕ" in text
    assert "14:45-16:10" in text
    assert "История России" in text
    assert "Ширинкин" in text


def test_week_picker_shows_two_weeks_and_arrows():
    weeks = [
        (date(2026, 11, 9), date(2026, 11, 14)),
        (date(2027, 2, 15), date(2027, 2, 20)),
        (date(2027, 2, 22), date(2027, 2, 27)),
    ]
    keyboard = week_picker_keyboard(weeks, 0)
    rows = keyboard.inline_keyboard
    assert rows[0][0].callback_data == "week:2026-11-09"
    assert rows[2][0].callback_data == "weekpage:1"
    assert rows[0][0].text == "09.11–14.11.2026"


@pytest.mark.anyio
async def test_course_selection_explains_part_time_groups(memory_db):
    repo.upsert_groups(
        [("Л-26-11 зо", 1), ("Ф-26-11 зо", 1), ("ТД-26-11 зо", 1)],
        faculty="permskaya",
    )
    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=1, chat_id=123, user_id=123),
    )
    await state.update_data(faculty="permskaya")
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(
        data="course:1",
        from_user=TelegramUser(id=123, is_bot=False, first_name="Student"),
        message=message,
        answer=AsyncMock(),
    )

    await choose_course(callback, state)

    text = message.edit_text.call_args.args[0]
    assert "заочное отделение" in text
    assert "ТД-24-9 зо" in text
    buttons = [
        button.text
        for row in message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert "Л-26-11 зо" in buttons


@pytest.mark.anyio
async def test_part_time_today_opens_week_without_excel(memory_db, monkeypatch):
    repo.replace_group_lessons(
        schedule_date=date(2026, 11, 9),
        group_name="Л-25-11 зо",
        source_type="schedule",
        course=2,
        lessons=[
            {
                "number": 4,
                "subject": "Гражданское право",
                "teacher": "Канюкова Сусанна Рауфовна",
                "room": "308",
                "start_time": "14:45",
                "end_time": "16:10",
            }
        ],
        faculty="permskaya",
    )
    # Если этот метод вызовется, значит для заочки снова пытаются прикрепить Excel.
    monkeypatch.setattr(
        schedule_handler.repo,
        "get_schedule_file_for_group",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Excel для зо")),
    )
    message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())

    await schedule_handler._send_schedule(
        message, "Л-25-11 зо", date(2026, 11, 9), "permskaya", 2
    )

    message.answer_document.assert_not_awaited()
    text = message.answer.call_args.args[0]
    assert "Заочка, 09.11–14.11.2026" in text
    assert "Гражданское право" in text
    assert "Пн 09.11" in text


@pytest.mark.anyio
async def test_part_time_missing_day_shows_nearest_week(memory_db, monkeypatch):
    repo.replace_group_lessons(
        schedule_date=date(2026, 11, 9),
        group_name="Л-25-11 зо",
        source_type="schedule",
        course=2,
        lessons=[
            {
                "number": 4,
                "subject": "Гражданское право",
                "start_time": "14:45",
                "end_time": "16:10",
            }
        ],
        faculty="permskaya",
    )
    monkeypatch.setattr(
        schedule_handler.repo,
        "get_schedule_file_for_group",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Excel для зо")),
    )
    message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())

    # Воскресенье 08.11: текущей недели заочки нет, ближайшая — 09.11.
    await schedule_handler._send_schedule(
        message, "Л-25-11 зо", date(2026, 11, 8), "permskaya", 2
    )

    message.answer_document.assert_not_awaited()
    text = message.answer.call_args.args[0]
    assert "занятий нет" in text
    assert "Ближайшая доступная неделя заочки" in text
    assert "09.11–14.11.2026" in text


@pytest.mark.anyio
async def test_part_time_does_not_show_stale_past_week(memory_db, monkeypatch):
    repo.replace_group_lessons(
        schedule_date=date(2026, 10, 26),
        group_name="Л-25-11 зо",
        source_type="schedule",
        course=2,
        lessons=[
            {
                "number": 4,
                "subject": "Гражданское право",
                "start_time": "14:45",
                "end_time": "16:10",
            }
        ],
        faculty="permskaya",
    )
    monkeypatch.setattr(
        schedule_handler.repo,
        "get_schedule_file_for_group",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Excel для зо")),
    )
    message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())

    # 08.11, а последняя загруженная неделя — 26.10. Показывать её как
    # «расписание на сегодня» нельзя: следующая сессия ещё не опубликована.
    await schedule_handler._send_schedule(
        message, "Л-25-11 зо", date(2026, 11, 8), "permskaya", 2
    )

    message.answer_document.assert_not_awaited()
    text = message.answer.call_args.args[0]
    assert "занятий нет" in text
    assert "Следующая сессия заочки" in text
    assert "26.10" not in text
    assert "Последняя загруженная" not in text


@pytest.mark.anyio
async def test_part_time_without_weeks_says_not_loaded(memory_db):
    repo.upsert_groups([("Л-24-9 зо", 3)], faculty="permskaya")
    message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())

    await schedule_handler._send_schedule(
        message, "Л-24-9 зо", date(2026, 11, 9), "permskaya", 3
    )

    message.answer_document.assert_not_awaited()
    text = message.answer.call_args.args[0]
    assert "Расписание заочки пока не загружено" in text
    assert message.answer.call_args.kwargs["reply_markup"] is not None
