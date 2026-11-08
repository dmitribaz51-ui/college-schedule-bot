"""Уведомления студентам заочки о новой/изменённой неделе."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import anyio
import pytest

from app.database import database as db
from app.database import repository as repo
from app.parser.excel_downloader import DownloadResult
from app.parser.excel_parser import GroupSchedule, ParsedLesson
from app.parser.website_parser import ScheduleLink
from app.services import update_service as us
from app.services.notification_service import notify_about_zao_week


@pytest.fixture
def memory_db():
    db.init_db("sqlite:///:memory:")


def _add_zao_user(telegram_id: int = 501, group: str = "ЮР-24-9 зо") -> None:
    repo.get_or_create_user(telegram_id, username=f"user{telegram_id}")
    repo.set_user_group(telegram_id, 3, group)
    repo.set_notifications(telegram_id, True)


def _future_link() -> ScheduleLink:
    return ScheduleLink(
        title="Расписание 15.02-20.02.2027",
        file_type="schedule",
        schedule_date=date(2027, 2, 15),
        date_end=date(2027, 2, 20),
        url="https://example.test/15.02.2027.pdf",
        groups=["ЮР-24-9 зо"],
    )


def _schedule(day: date, subject: str = "Первый предмет") -> GroupSchedule:
    return GroupSchedule(
        group="ЮР-24-9 зо",
        schedule_date=day,
        course=3,
        lessons=[ParsedLesson(number=1, subject=subject)],
    )


def test_zao_week_notification_has_week_button_and_text(memory_db):
    async def scenario():
        _add_zao_user()
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=True)

        with patch(
            "app.services.notification_service.today_perm",
            return_value=date(2027, 2, 1),
        ):
            sent = await notify_about_zao_week(
                bot,
                week_start=date(2027, 2, 15),
                week_end=date(2027, 2, 20),
                groups=["ЮР-24-9 зо"],
                title="Расписание 15.02-20.02.2027",
                is_new=True,
            )

        assert sent == 1
        text = bot.send_message.await_args.args[1]
        assert "Появилось расписание заочки!" in text
        assert "15.02–20.02.2027" in text
        assert "ЮР-24-9 зо" in text
        keyboard = bot.send_message.await_args.kwargs["reply_markup"]
        button = keyboard.inline_keyboard[0][0]
        assert button.text == "Посмотреть неделю⬇️"
        assert button.callback_data == "week:2027-02-15"

    anyio.run(scenario)


def test_zao_week_notification_changed_text(memory_db):
    async def scenario():
        _add_zao_user()
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=True)

        with patch(
            "app.services.notification_service.today_perm",
            return_value=date(2027, 2, 1),
        ):
            sent = await notify_about_zao_week(
                bot,
                week_start=date(2027, 2, 15),
                week_end=date(2027, 2, 20),
                groups=["ЮР-24-9 зо"],
                title="Расписание 15.02-20.02.2027",
                is_new=False,
            )

        assert sent == 1
        assert "Изменения в расписании заочки!" in bot.send_message.await_args.args[1]

    anyio.run(scenario)


def test_zao_week_notification_skips_past_week(memory_db):
    async def scenario():
        _add_zao_user()
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=True)

        with patch(
            "app.services.notification_service.today_perm",
            return_value=date(2026, 11, 8),
        ):
            sent = await notify_about_zao_week(
                bot,
                week_start=date(2026, 10, 19),
                week_end=date(2026, 10, 24),
                groups=["ЮР-24-9 зо"],
                title="Расписание 19.10-24.10.2026",
            )

        assert sent == 0
        bot.send_message.assert_not_called()

    anyio.run(scenario)


def test_zao_week_notification_respects_disabled_settings(memory_db):
    async def scenario():
        _add_zao_user()
        repo.set_notifications(501, False)
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=True)

        with patch(
            "app.services.notification_service.today_perm",
            return_value=date(2027, 2, 1),
        ):
            sent = await notify_about_zao_week(
                bot,
                week_start=date(2027, 2, 15),
                week_end=date(2027, 2, 20),
                groups=["ЮР-24-9 зо"],
                title="Расписание 15.02-20.02.2027",
            )

        assert sent == 0
        bot.send_message.assert_not_called()

    anyio.run(scenario)


@pytest.mark.anyio
async def test_pdf_success_notifies_new_then_changed_week(
    memory_db, monkeypatch, tmp_path
):
    _add_zao_user()
    link = _future_link()
    state = {"subject": "Первый предмет"}

    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "zao.pdf",
            size=1000,
            sha256="zao-week-hash",
            sheet_names=[],
        )

    def fake_parse(path):
        return [_schedule(link.schedule_date, state["subject"])]

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(us, "parse_pdf_file", fake_parse)

    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=True)

    with patch(
        "app.services.notification_service.today_perm",
        return_value=date(2027, 2, 1),
    ):
        first = us.UpdateReport()
        await us._process_pdf_link(
            link, first, force_reprocess=True, bot=bot, notify_users=True
        )

        state["subject"] = "Второй предмет"
        second = us.UpdateReport()
        await us._process_pdf_link(
            link, second, force_reprocess=True, bot=bot, notify_users=True
        )

        state["subject"] = "Третий предмет"
        third = us.UpdateReport()
        await us._process_pdf_link(
            link, third, force_reprocess=True, bot=bot, notify_users=False
        )

    assert first.errors == [] and second.errors == [] and third.errors == []
    assert bot.send_message.await_count == 2
    first_text = bot.send_message.await_args_list[0].args[1]
    second_text = bot.send_message.await_args_list[1].args[1]
    assert "Появилось расписание заочки!" in first_text
    assert "Изменения в расписании заочки!" in second_text
    assert "week:2027-02-15" in str(
        bot.send_message.await_args_list[0].kwargs["reply_markup"]
    )


@pytest.mark.anyio
async def test_check_for_updates_passes_notify_users_to_pdf(memory_db, monkeypatch):
    link = _future_link()
    captured: dict[str, object] = {}

    async def pdf_links(url):
        return [link]

    async def fake_process(link, report, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(us, "fetch_pdf_schedule_links", pdf_links)
    monkeypatch.setattr(us, "_process_pdf_link", fake_process)

    await us.check_for_updates(zao_only=True, notify_users=False)

    assert captured["notify_users"] is False
