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
    monkeypatch.setattr(schedule.repo, "get_schedule_file_for_group",
                        lambda *args: SimpleNamespace(id=1, local_path=str(path), telegram_file_id=None))
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
    monkeypatch.setattr(schedule.repo, "get_schedule_file_for_group",
                        lambda *args: SimpleNamespace(id=1, local_path=str(path), telegram_file_id=None))
    message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock(side_effect=[RuntimeError("error"), None]))
    asyncio.run(schedule._send_schedule(message, "ТД-24-9", date(2026, 10, 12), "permskaya", 3))
    message.answer.assert_awaited_once_with(text)
    assert message.answer_document.await_count == 2


def test_emoji_and_html_length():
    assert schedule._telegram_length(schedule._plain_schedule_text("<b>📅 &amp; Я</b>")) == 6


def test_telegram_file_id_caching_and_reuse(monkeypatch, tmp_path):
    path = tmp_path / "schedule.xls"
    path.write_bytes(b"example")
    text = "<b>Расписание</b>"
    monkeypatch.setattr(schedule, "format_day_schedule", lambda *args: text)

    file_record = SimpleNamespace(id=42, local_path=str(path), telegram_file_id=None)
    monkeypatch.setattr(schedule.repo, "get_schedule_file_for_group", lambda *args: file_record)

    saved_cache = {}
    def fake_set_file_telegram_id(f_id, tg_id):
        saved_cache[f_id] = tg_id
        file_record.telegram_file_id = tg_id

    monkeypatch.setattr(schedule.repo, "set_file_telegram_id", fake_set_file_telegram_id)

    # 1. Первая отправка: файла в кэше нет, отправляется FSInputFile, сохраняется file_id
    sent_doc_obj = SimpleNamespace(document=SimpleNamespace(file_id="BQADBAAD_cached_id_123"))
    doc_mock = AsyncMock(return_value=sent_doc_obj)
    message1 = SimpleNamespace(answer=AsyncMock(), answer_document=doc_mock)

    asyncio.run(schedule._send_schedule(message1, "ТД-24-9", date(2026, 10, 12), "permskaya", 3))

    assert doc_mock.call_count == 1
    sent_arg = doc_mock.call_args.kwargs["document"]
    # Первый раз отправлялся FSInputFile
    assert not isinstance(sent_arg, str)
    assert saved_cache[42] == "BQADBAAD_cached_id_123"
    assert file_record.telegram_file_id == "BQADBAAD_cached_id_123"

    # 2. Вторая отправка: файл уже в кэше, отправляется сразу string id
    doc_mock2 = AsyncMock(return_value=sent_doc_obj)
    message2 = SimpleNamespace(answer=AsyncMock(), answer_document=doc_mock2)
    asyncio.run(schedule._send_schedule(message2, "ТД-24-9", date(2026, 10, 12), "permskaya", 3))

    assert doc_mock2.call_count == 1
    sent_arg2 = doc_mock2.call_args.kwargs["document"]
    assert sent_arg2 == "BQADBAAD_cached_id_123"


def test_telegram_file_id_fallback_on_error(monkeypatch, tmp_path):
    path = tmp_path / "schedule.xls"
    path.write_bytes(b"example")
    text = "<b>Расписание</b>"
    monkeypatch.setattr(schedule, "format_day_schedule", lambda *args: text)

    # В кэше есть старый невалидный file_id
    file_record = SimpleNamespace(id=42, local_path=str(path), telegram_file_id="invalid_file_id")
    monkeypatch.setattr(schedule.repo, "get_schedule_file_for_group", lambda *args: file_record)

    saved_cache = {}
    def fake_set_file_telegram_id(f_id, tg_id):
        saved_cache[f_id] = tg_id
        file_record.telegram_file_id = tg_id

    monkeypatch.setattr(schedule.repo, "set_file_telegram_id", fake_set_file_telegram_id)

    new_doc_obj = SimpleNamespace(document=SimpleNamespace(file_id="new_valid_file_id"))
    # Первый вызов по file_id выбрасывает RuntimeError("Wrong file_id"), второй через FSInputFile успешен
    doc_mock = AsyncMock(side_effect=[RuntimeError("Wrong file_id"), new_doc_obj])
    message = SimpleNamespace(answer=AsyncMock(), answer_document=doc_mock)

    asyncio.run(schedule._send_schedule(message, "ТД-24-9", date(2026, 10, 12), "permskaya", 3))

    assert doc_mock.call_count == 2
    # Первый был по строке, второй по FSInputFile
    assert doc_mock.call_args_list[0].kwargs["document"] == "invalid_file_id"
    assert not isinstance(doc_mock.call_args_list[1].kwargs["document"], str)
    assert file_record.telegram_file_id == "new_valid_file_id"

