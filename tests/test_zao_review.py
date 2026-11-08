"""Проблемные PDF заочки: статус, сброс и кнопка админки."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.bot.handlers.admin import _file_line
from app.bot.keyboards.menu import admin_keyboard
from app.database import database as db
from app.database import repository as repo


@pytest.fixture
def memory_db():
    db.init_db("sqlite:///:memory:")


def _add_file(
    *,
    url: str,
    faculty: str,
    processed: bool,
    error: str | None = None,
    review_required: bool = False,
    retry_count: int = 0,
    alerted_hash: str | None = None,
) -> None:
    repo.upsert_file(
        title=f"Файл {url}",
        url=url,
        file_type="schedule",
        faculty=faculty,
        schedule_date=date(2026, 11, 9),
        processed=processed,
        error=error,
        review_required=review_required,
        retry_count=retry_count,
        update_check_time=False,
    )
    if alerted_hash is not None:
        repo.mark_file_alerted(url, alerted_hash)


def test_admin_keyboard_has_zao_retry_button():
    callbacks = {
        button.callback_data
        for row in admin_keyboard().inline_keyboard
        for button in row
    }
    assert "admin:retry_zao" in callbacks
    assert "admin:zao_problems" in callbacks


def test_reset_zao_problem_files_only_touches_problem_zao(memory_db):
    _add_file(
        url="https://example.test/zao-broken.pdf",
        faculty="zaochnoe",
        processed=False,
        error="Нужна проверка: OCR вернул 1 групп из 3",
        review_required=True,
        retry_count=2,
        alerted_hash="broken-hash",
    )
    _add_file(
        url="https://example.test/zao-ok.pdf",
        faculty="zaochnoe",
        processed=True,
    )
    _add_file(
        url="https://example.test/full-time-broken.xlsx",
        faculty="permskaya",
        processed=False,
        error="Ошибка разбора Excel",
    )
    _add_file(
        url="https://example.test/zao-duplicate.pdf",
        faculty="zaochnoe",
        processed=True,
        error="Дубликат уже обработанного PDF",
    )

    assert repo.count_zao_problem_files() == 1
    assert repo.reset_zao_problem_files() == 1

    broken = repo.get_file_by_url("https://example.test/zao-broken.pdf")
    assert broken is not None
    assert broken.processed is False
    assert broken.error is None
    assert broken.review_required is False
    assert broken.retry_count == 0
    assert broken.alerted_hash is None
    assert broken.last_checked is None

    ok = repo.get_file_by_url("https://example.test/zao-ok.pdf")
    assert ok is not None and ok.processed is True
    full_time = repo.get_file_by_url("https://example.test/full-time-broken.xlsx")
    assert full_time is not None
    assert full_time.error == "Ошибка разбора Excel"
    duplicate = repo.get_file_by_url("https://example.test/zao-duplicate.pdf")
    assert duplicate is not None
    assert duplicate.processed is True
    assert duplicate.error == "Дубликат уже обработанного PDF"


def test_file_line_shows_review_reason_and_source_link():
    item = SimpleNamespace(
        schedule_date=date(2026, 11, 9),
        review_required=True,
        processed=False,
        file_type="schedule",
        title="Расписание <09.11>",
        url="https://example.test/09.11.pdf?x=1&y=2",
        error="Нужна проверка: OCR вернул 1 групп из 3",
    )
    text = "\n".join(_file_line(item))
    assert "⚠️" in text
    assert "Нужна проверка" in text
    assert "Расписание &lt;09.11&gt;" in text
    assert 'href="https://example.test/09.11.pdf?x=1&amp;y=2"' in text


def test_problem_list_includes_review_and_excludes_duplicates(memory_db):
    _add_file(
        url="https://example.test/zao-review.pdf",
        faculty="zaochnoe",
        processed=False,
        error="Нужна проверка: дата вне недели",
        review_required=True,
    )
    _add_file(
        url="https://example.test/zao-duplicate.pdf",
        faculty="zaochnoe",
        processed=True,
        error="Дубликат уже обработанного PDF",
    )

    files = repo.get_zao_problem_files()
    assert [item.url for item in files] == ["https://example.test/zao-review.pdf"]
