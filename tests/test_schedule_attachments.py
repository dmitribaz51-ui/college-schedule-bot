"""Полная доставка расписания при разных лимитах Telegram."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers import schedule


@pytest.mark.parametrize("size", [1024, 1025, 4096, 4097])
def test_attachment_preserves_full_schedule(monkeypatch, tmp_path, size):
    path = tmp_path / "schedule.xls"
    path.write_bytes(b"example")
    text = "<b>" + "Я" * size + "</b>"
    monkeypatch.setattr(schedule, "format_day_schedule", lambda *args: text)
    monkeypatch.setattr(schedule.repo, "get_schedule_file_for_date",
                        lambda *args: SimpleNamespace(local_path=str(path)))
    events = []

    async def answer(value, **kwargs):
        events.append(("text", value))

    async def document(**kwargs):
        events.append(("file", kwargs.get("caption")))

    message = SimpleNamespace(answer=answer, answer_document=document)
    asyncio.run(schedule._send_schedule(message, "ТД-24-9", date(2026, 10, 12), "permskaya", 3))
    if size <= 1024:
        assert events == [("file", text)]
    else:
        assert events[-1] == ("file", None)
        delivered = "".join(schedule._plain_schedule_text(value) for kind, value in events[:-1])
        assert delivered == "Я" * size
        assert all(schedule._telegram_length(schedule._plain_schedule_text(value)) <= 4096
                   for kind, value in events[:-1])


def test_failed_caption_falls_back_without_losing_text(monkeypatch, tmp_path):
    path = tmp_path / "schedule.xls"
    path.write_bytes(b"example")
    text = "<b>Полное расписание</b>"
    monkeypatch.setattr(schedule, "format_day_schedule", lambda *args: text)
    monkeypatch.setattr(schedule.repo, "get_schedule_file_for_date",
                        lambda *args: SimpleNamespace(local_path=str(path)))
    message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock(side_effect=[RuntimeError("error"), None]))
    asyncio.run(schedule._send_schedule(message, "ТД-24-9", date(2026, 10, 12), "permskaya", 3))
    message.answer.assert_awaited_once_with(text)
    assert message.answer_document.await_count == 2


def test_emoji_and_html_length():
    assert schedule._telegram_length(schedule._plain_schedule_text("<b>📅 &amp; Я</b>")) == 6
