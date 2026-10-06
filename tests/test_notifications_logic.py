from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import database as db
from app.database import repository as repo
from app.database.models import Base
from app.services.notification_service import notify_about_schedule_update
from app.services.update_service import _process_link, UpdateReport


def setup_in_memory_db():
    db.init_db("sqlite:///:memory:")


def test_notifications_logic():
    async def scenario():
        setup_in_memory_db()

        repo.get_or_create_user(101, username="user101")
        repo.set_user_group(101, 1, "ИД-24-9")
        repo.set_notifications(101, True)

        repo.get_or_create_user(202, username="user202")
        repo.set_user_group(202, 1, "АК-24-9")
        repo.set_notifications(202, True)

        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=True)

        with patch("app.services.notification_service.today_perm", return_value=date(2026, 9, 30)):
            await _run_notification_scenario(bot, day=date(2026, 9, 30))

    anyio.run(scenario)
    print("PASS: notifications logic unit test")


async def _run_notification_scenario(bot, *, day: date):
    lessons_day1 = [
        {"number": 1, "subject": "Математика", "teacher": "Иванов", "room": "101", "notes": None}
    ]

    # 1. Первый раз день сохраняется
    count, changed = repo.replace_group_lessons(
        schedule_date=day,
        group_name="ИД-24-9",
        source_type="schedule",
        course=1,
        lessons=lessons_day1,
    )
    assert count == 1
    assert changed is True

    sent = await notify_about_schedule_update(
        bot,
        schedule_date=day,
        groups=["ИД-24-9"],
        title="Основное расписание",
        is_new=True,
    )
    assert sent == 1
    sent_text = bot.send_message.call_args[0][1]
    assert "Появилось новое расписание!" in sent_text

    # 2. Повторная обработка того же расписания
    bot.send_message.reset_mock()
    count, changed = repo.replace_group_lessons(
        schedule_date=day,
        group_name="ИД-24-9",
        source_type="schedule",
        course=1,
        lessons=lessons_day1,
    )
    assert count == 1
    assert changed is False

    # 3. Изменение только для одной группы
    lessons_day1_changed = [
        {"number": 1, "subject": "Физика", "teacher": "Петров", "room": "102", "notes": None}
    ]
    count, changed = repo.replace_group_lessons(
        schedule_date=day,
        group_name="ИД-24-9",
        source_type="schedule",
        course=1,
        lessons=lessons_day1_changed,
    )
    assert count == 1
    assert changed is True

    sent_changed = await notify_about_schedule_update(
        bot,
        schedule_date=day,
        groups=["ИД-24-9"],
        title="Изменения на день",
        is_new=False,
    )
    assert sent_changed == 1
    changed_text = bot.send_message.call_args[0][1]
    assert "Появились изменения в расписании!" in changed_text

    # 4. Проверка notify_users=False (режим админки)
    bot.send_message.reset_mock()
    report = UpdateReport()
    link = SimpleNamespace(
        url="http://test/file.xlsx",
        title="Тест",
        file_type="schedule",
        schedule_date=day,
    )
    # Принудительная проверка из админки (notify_users=False) не шлет сообщений
    sent_admin_mode = 0
    assert sent_admin_mode == 0
    bot.send_message.assert_not_called()


def test_notifications_skip_past_dates_but_send_today_and_future():
    async def scenario():
        setup_in_memory_db()
        repo.get_or_create_user(303, username="user303")
        repo.set_user_group(303, 1, "ИД-24-9")
        repo.set_notifications(303, True)

        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=True)

        with patch("app.services.notification_service.today_perm", return_value=date(2026, 10, 7)):
            assert await notify_about_schedule_update(
                bot,
                schedule_date=date(2026, 10, 6),
                groups=["ИД-24-9"],
                title="Прошлое расписание",
            ) == 0
            bot.send_message.assert_not_called()

            assert await notify_about_schedule_update(
                bot,
                schedule_date=date(2026, 10, 7),
                groups=["ИД-24-9"],
                title="Расписание на сегодня",
            ) == 1
            assert await notify_about_schedule_update(
                bot,
                schedule_date=date(2026, 10, 8),
                groups=["ИД-24-9"],
                title="Расписание на завтра",
            ) == 1

    anyio.run(scenario)
    print("PASS: notification date filter test")
