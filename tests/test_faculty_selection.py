"""Тест выбора факультета и отображения соответствующих групп."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User as TelegramUser

from app.bot.handlers.start import choose_course, choose_faculty, Registration
from app.database import database as db
from app.database import repository as repo


@pytest.fixture
def memory_db():
    db.init_db("sqlite:///:memory:")


@pytest.mark.anyio
async def test_choose_faculty_preserves_faculty_for_course_selection(memory_db):
    """При выборе факультета и последующем выборе курса должны возвращаться группы выбранного факультета."""
    # Создаем тестовые группы для обоих факультетов
    repo.replace_group_lessons(
        schedule_date=date(2026, 10, 12),
        group_name="ТД-26-9",
        source_type="schedule",
        course=1,
        lessons=[{"number": 1, "subject": "Математика"}],
        source_file_id=1,
        faculty="permskaya",
    )
    repo.replace_group_lessons(
        schedule_date=date(2026, 10, 12),
        group_name="Д-26-11К",
        source_type="schedule",
        course=1,
        lessons=[{"number": 1, "subject": "Рисунок"}],
        source_file_id=2,
        faculty="chernyshevskogo",
    )

    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=1, chat_id=123, user_id=123),
    )

    user = TelegramUser(id=123, is_bot=False, first_name="Student")
    msg = SimpleNamespace(
        answer=AsyncMock(),
        edit_text=AsyncMock(),
    )

    # 1. Пользователь выбирает Чернышевского
    cb_faculty = SimpleNamespace(
        from_user=user,
        message=msg,
        data="faculty:chernyshevskogo",
        answer=AsyncMock(),
    )

    await choose_faculty(cb_faculty, state)

    data = await state.get_data()
    assert data.get("faculty") == "chernyshevskogo"

    # 2. Пользователь выбирает 1 курс
    cb_course = SimpleNamespace(
        from_user=user,
        message=msg,
        data="course:1",
        answer=AsyncMock(),
    )
    await choose_course(cb_course, state)

    # Проверяем, что в разметке клавиатуры группы с Чернышевского (Д-26-11К), а не с Пермской (ТД-26-9)
    assert msg.edit_text.called
    call_args = msg.edit_text.call_args
    reply_markup = call_args.kwargs.get("reply_markup")
    assert reply_markup is not None

    buttons_text = [
        btn.text
        for row in reply_markup.inline_keyboard
        for btn in row
    ]

    assert "Д-26-11К" in buttons_text
    assert "ТД-26-9" not in buttons_text
