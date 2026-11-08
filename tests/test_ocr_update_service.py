"""Интеграция OCR в обработку PDF заочки."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect

from app.database import database as db
from app.database import repository as repo
from app.parser.excel_downloader import DownloadResult
from app.parser.excel_parser import GroupSchedule, ParsedLesson
from app.parser.pdf_parser import parse_pdf_file
from app.parser.website_parser import ScheduleLink
from app.services import update_service as us

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def memory_db():
    db.init_db("sqlite:///:memory:")


def _scan_link() -> ScheduleLink:
    return ScheduleLink(
        title="Расписание 19.10-24.10.2026 заочная форма обучения",
        file_type="schedule",
        schedule_date=date(2026, 10, 19),
        url="https://example.test/19.10.pdf",
        groups=["Л-24-9 зо"],
    )


def _schedule() -> GroupSchedule:
    return GroupSchedule(
        group="Л-24-9 зо",
        schedule_date=date(2026, 10, 19),
        course=3,
        lessons=[
            ParsedLesson(
                number=4,
                subject="Основы финансовой грамотности",
                teacher="Чекалкина Ольга Ивановна",
                room="305",
                start_time="14:45",
                end_time="16:10",
            )
        ],
    )


@pytest.mark.anyio
async def test_scanned_pdf_goes_through_ocr(memory_db, monkeypatch, tmp_path):
    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "scan.pdf",
            size=1000,
            sha256="scan-hash",
            sheet_names=[],
        )

    def fake_ocr(path, default_groups=None, default_date=None):
        assert default_groups == ["Л-24-9 зо"]
        assert default_date == date(2026, 10, 19)
        return [_schedule()]

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(us, "parse_pdf_file", lambda path: [])
    monkeypatch.setattr(us, "is_ocr_available", lambda: True)
    monkeypatch.setattr(us, "parse_scanned_pdf", fake_ocr)

    report = us.UpdateReport()
    await us._process_pdf_link(_scan_link(), report, force_reprocess=True)

    assert report.errors == []
    assert len(report.new_files) == 1
    assert report.new_files[0].groups == ["Л-24-9 зо"]
    assert report.new_files[0].lessons_saved == 1
    saved = repo.get_file_by_url("https://example.test/19.10.pdf")
    assert saved is not None
    assert saved.processed is True
    assert saved.error is None
    assert repo.get_group_weeks("Л-24-9 зо", "permskaya") == [
        (date(2026, 10, 19), date(2026, 10, 24))
    ]


@pytest.mark.anyio
async def test_missing_tesseract_keeps_pdf_and_reports_error(
    memory_db, monkeypatch, tmp_path
):
    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "scan.pdf",
            size=1000,
            sha256="scan-hash-2",
            sheet_names=[],
        )

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(us, "parse_pdf_file", lambda path: [])
    monkeypatch.setattr(us, "is_ocr_available", lambda: False)
    monkeypatch.setattr(us, "ocr_unavailable_reason", lambda: "нет tesseract")

    report = us.UpdateReport()
    await us._process_pdf_link(_scan_link(), report, force_reprocess=True)

    assert report.new_files == []
    assert len(report.errors) == 1
    assert "OCR недоступен" in report.errors[0]
    saved = repo.get_file_by_url("https://example.test/19.10.pdf")
    assert saved is not None
    assert saved.processed is False
    assert "OCR недоступен" in (saved.error or "")


@pytest.mark.anyio
async def test_text_pdf_does_not_call_ocr(memory_db, monkeypatch, tmp_path):
    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "text.pdf",
            size=1000,
            sha256="text-hash",
            sheet_names=[],
        )

    def fail_ocr(*args, **kwargs):
        raise AssertionError("OCR не должен вызываться для текстового PDF")

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(us, "parse_pdf_file", lambda path: [_schedule()])
    monkeypatch.setattr(us, "is_ocr_available", lambda: True)
    monkeypatch.setattr(us, "parse_scanned_pdf", fail_ocr)

    report = us.UpdateReport()
    await us._process_pdf_link(_scan_link(), report, force_reprocess=True)

    assert report.errors == []
    assert len(report.new_files) == 1


@pytest.mark.anyio
async def test_failed_scan_is_retried_after_error(memory_db, monkeypatch, tmp_path):
    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "scan.pdf",
            size=1000,
            sha256="retry-hash",
            sheet_names=[],
        )

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(us, "parse_pdf_file", lambda path: [])
    monkeypatch.setattr(us, "is_ocr_available", lambda: False)
    monkeypatch.setattr(us, "ocr_unavailable_reason", lambda: "нет tesseract")

    first = us.UpdateReport()
    await us._process_pdf_link(_scan_link(), first, force_reprocess=True)
    saved = repo.get_file_by_url("https://example.test/19.10.pdf")
    assert saved is not None
    assert saved.processed is False
    assert "OCR недоступен" in (saved.error or "")

    # Tesseract появился: тот же URL и тот же хэш должны перечитаться,
    # а не остаться навсегда «обработанными».
    monkeypatch.setattr(us, "is_ocr_available", lambda: True)
    monkeypatch.setattr(us, "parse_scanned_pdf", lambda *a, **k: [_schedule()])
    monkeypatch.setattr(us, "PDF_RETRY_AFTER", timedelta(0))

    second = us.UpdateReport()
    await us._process_pdf_link(_scan_link(), second)

    assert second.errors == []
    assert len(second.new_files) == 1
    saved = repo.get_file_by_url("https://example.test/19.10.pdf")
    assert saved is not None
    assert saved.processed is True
    assert saved.error is None


@pytest.mark.anyio
async def test_old_failed_scan_with_processed_flag_is_retried(
    memory_db, monkeypatch, tmp_path
):
    """Так выглядели сканы 2027 после первого запуска без Tesseract."""
    repo.upsert_file(
        title="Расписание 15.02-20.02.2027",
        url="https://example.test/15.02.pdf",
        file_type="schedule",
        faculty="zaochnoe",
        schedule_date=date(2027, 2, 15),
        file_hash="legacy-hash",
        processed=True,
        error="PDF без текста, OCR недоступен: нет tesseract",
    )

    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "scan.pdf",
            size=1000,
            sha256="legacy-hash",
            sheet_names=[],
        )

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(us, "parse_pdf_file", lambda path: [])
    monkeypatch.setattr(us, "is_ocr_available", lambda: True)
    monkeypatch.setattr(
        us,
        "parse_scanned_pdf",
        lambda *a, **k: [
            _schedule_for("Л-24-9 зо", date(2027, 2, 15))
        ],
    )

    report = us.UpdateReport()
    await us._process_pdf_link(
        ScheduleLink(
            title="Расписание 15.02-20.02.2027",
            file_type="schedule",
            schedule_date=date(2027, 2, 15),
            url="https://example.test/15.02.pdf",
        ),
        report,
    )

    assert report.errors == []
    assert len(report.new_files) == 1
    saved = repo.get_file_by_url("https://example.test/15.02.pdf")
    assert saved is not None
    assert saved.processed is True
    assert saved.error is None


def _schedule_for(
    group: str,
    day: date,
    *,
    lessons: int = 1,
    subject: str = "Предмет",
) -> GroupSchedule:
    return GroupSchedule(
        group=group,
        schedule_date=day,
        course=3,
        lessons=[
            ParsedLesson(number=number, subject=f"{subject} {number}")
            for number in range(1, lessons + 1)
        ],
    )


@pytest.mark.anyio
async def test_ocr_failure_keeps_old_lessons_and_pdf(
    memory_db, monkeypatch, tmp_path
):
    link = _scan_link()
    group = "Л-24-9 зо"
    day = date(2026, 10, 19)
    old_pdf = tmp_path / "old.pdf"
    old_pdf.write_bytes(b"old")
    repo.upsert_file(
        title=link.title,
        url=link.url,
        file_type="schedule",
        faculty="zaochnoe",
        schedule_date=day,
        file_hash="old-hash",
        local_path=str(old_pdf),
        processed=True,
    )
    repo.replace_group_lessons(
        schedule_date=day,
        group_name=group,
        source_type="schedule",
        course=3,
        lessons=[{"number": 4, "subject": "Гражданское право"}],
        faculty="permskaya",
    )

    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "new.pdf",
            size=1000,
            sha256="new-hash",
            sheet_names=[],
        )

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(us, "parse_pdf_file", lambda path: [])
    monkeypatch.setattr(us, "is_ocr_available", lambda: False)
    monkeypatch.setattr(us, "ocr_unavailable_reason", lambda: "нет tesseract")

    report = us.UpdateReport()
    await us._process_pdf_link(link, report, force_reprocess=True)

    lessons = repo.get_lessons(day, group, faculty="permskaya")
    assert [lesson.subject for lesson in lessons] == ["Гражданское право"]
    saved = repo.get_file_by_url(link.url)
    assert saved is not None
    assert saved.processed is False
    assert saved.review_required is False
    assert saved.retry_count == 1
    assert saved.local_path == str(old_pdf)
    assert saved.error and "OCR" in saved.error


@pytest.mark.anyio
async def test_partial_ocr_requires_review_and_keeps_all_old_lessons(
    memory_db, monkeypatch, tmp_path
):
    groups = ["Л-26-11 зо", "Ф-26-11 зо", "ТД-26-11 зо"]
    link = ScheduleLink(
        title="Расписание 09.11-14.11.2026",
        file_type="schedule",
        schedule_date=date(2026, 11, 9),
        date_end=date(2026, 11, 14),
        url="https://example.test/partial.pdf",
        groups=groups,
    )
    repo.upsert_file(
        title=link.title,
        url=link.url,
        file_type="schedule",
        faculty="zaochnoe",
        schedule_date=link.schedule_date,
        file_hash="old-hash",
        local_path=str(tmp_path / "old.pdf"),
        processed=True,
    )
    for group in groups:
        repo.replace_group_lessons(
            schedule_date=link.schedule_date,
            group_name=group,
            source_type="schedule",
            course=1,
            lessons=[{"number": 4, "subject": f"Старое {group}"}],
            faculty="permskaya",
        )

    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "partial.pdf",
            size=1000,
            sha256="partial-hash",
            sheet_names=[],
        )

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(
        us,
        "parse_pdf_file",
        lambda path: [_schedule_for(groups[0], link.schedule_date)],
    )

    report = us.UpdateReport()
    await us._process_pdf_link(link, report, force_reprocess=True)

    assert report.new_files == []
    assert len(report.needs_review) == 1
    assert "1 групп из 3" in report.needs_review[0]
    saved = repo.get_file_by_url(link.url)
    assert saved is not None
    assert saved.processed is False
    assert saved.review_required is True
    assert saved.error and saved.error.startswith("Нужна проверка:")
    assert "1 групп из 3" in saved.error
    for group in groups:
        lessons = repo.get_lessons(link.schedule_date, group, faculty="permskaya")
        assert len(lessons) == 1
        assert lessons[0].subject == f"Старое {group}"


@pytest.mark.anyio
async def test_good_ocr_replaces_lessons_and_clears_review(
    memory_db, monkeypatch, tmp_path
):
    link = _scan_link()
    group = "Л-24-9 зо"
    day = date(2026, 10, 19)
    repo.upsert_file(
        title=link.title,
        url=link.url,
        file_type="schedule",
        faculty="zaochnoe",
        schedule_date=day,
        file_hash="old-hash",
        local_path=str(tmp_path / "old.pdf"),
        processed=True,
        review_required=True,
        retry_count=2,
    )
    repo.mark_file_alerted(link.url, "old-alert")
    repo.replace_group_lessons(
        schedule_date=day,
        group_name=group,
        source_type="schedule",
        course=3,
        lessons=[
            {"number": 1, "subject": "Старый 1"},
            {"number": 2, "subject": "Старый 2"},
        ],
        faculty="permskaya",
    )

    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "new.pdf",
            size=1000,
            sha256="good-hash",
            sheet_names=[],
        )

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(
        us,
        "parse_pdf_file",
        lambda path: [_schedule_for(group, day, lessons=2, subject="Новый")],
    )

    report = us.UpdateReport()
    await us._process_pdf_link(link, report, force_reprocess=True)

    assert report.errors == []
    assert report.needs_review == []
    assert len(report.new_files) == 1
    lessons = repo.get_lessons(day, group, faculty="permskaya")
    assert [lesson.subject for lesson in lessons] == ["Новый 1", "Новый 2"]
    saved = repo.get_file_by_url(link.url)
    assert saved is not None
    assert saved.processed is True
    assert saved.review_required is False
    assert saved.retry_count == 0
    assert saved.alerted_hash is None


@pytest.mark.anyio
async def test_admin_alert_is_sent_once_per_pdf_hash(
    memory_db, monkeypatch, tmp_path
):
    link = _scan_link()
    day = date(2026, 10, 19)
    repo.upsert_file(
        title=link.title,
        url=link.url,
        file_type="schedule",
        faculty="zaochnoe",
        schedule_date=day,
        file_hash="old-hash",
        local_path=str(tmp_path / "old.pdf"),
        processed=True,
    )
    repo.replace_group_lessons(
        schedule_date=day,
        group_name="Л-24-9 зо",
        source_type="schedule",
        course=3,
        lessons=[{"number": 4, "subject": "Гражданское право"}],
        faculty="permskaya",
    )

    async def fake_download(url, dest_dir, file_name):
        return DownloadResult(
            path=tmp_path / "broken.pdf",
            size=1000,
            sha256="broken-hash",
            sheet_names=[],
        )

    monkeypatch.setattr(us, "download_pdf", fake_download)
    monkeypatch.setattr(us, "parse_pdf_file", lambda path: [])
    monkeypatch.setattr(us, "is_ocr_available", lambda: False)
    monkeypatch.setattr(us, "ocr_unavailable_reason", lambda: "нет tesseract")
    monkeypatch.setattr(
        us,
        "get_config",
        lambda: SimpleNamespace(admin_ids=[42], pdf_dir=tmp_path),
    )
    bot = SimpleNamespace(send_message=AsyncMock())

    first = us.UpdateReport()
    await us._process_pdf_link(
        link, first, force_reprocess=True, bot=bot, notify_admins=True
    )
    second = us.UpdateReport()
    await us._process_pdf_link(
        link, second, force_reprocess=True, bot=bot, notify_admins=True
    )

    assert bot.send_message.await_count == 1
    chat_id, text = bot.send_message.await_args.args
    assert chat_id == 42
    assert "Проблема с PDF заочки" in text
    assert "старое расписание" in text
    saved = repo.get_file_by_url(link.url)
    assert saved is not None
    assert saved.alerted_hash == "broken-hash"
    assert saved.retry_count == 2


def test_retry_delays_one_hour_then_three_hours():
    assert us._pdf_retry_delay(0) == timedelta(hours=1)
    assert us._pdf_retry_delay(2) == timedelta(hours=1)
    assert us._pdf_retry_delay(3) == timedelta(hours=3)
    assert us._pdf_retry_delay(99) == timedelta(hours=3)


def test_init_db_migrates_review_columns(tmp_path):
    db_file = tmp_path / "migration.db"
    url = f"sqlite:///{db_file.as_posix()}"
    db.init_db(url)
    repo.upsert_file(
        title="Расписание 19.10-24.10.2026",
        url="https://example.test/migration.pdf",
        file_type="schedule",
        faculty="zaochnoe",
        schedule_date=date(2026, 10, 19),
        processed=False,
        error="OCR не распознал",
    )
    db._engine.dispose()

    with sqlite3.connect(db_file) as connection:
        connection.execute("ALTER TABLE schedule_files DROP COLUMN review_required")
        connection.execute("ALTER TABLE schedule_files DROP COLUMN retry_count")
        connection.execute("ALTER TABLE schedule_files DROP COLUMN alerted_hash")

    db.init_db(url)
    columns = {
        column["name"]
        for column in inspect(db._engine).get_columns("schedule_files")
    }
    assert {"review_required", "retry_count", "alerted_hash"} <= columns
    saved = repo.get_file_by_url("https://example.test/migration.pdf")
    assert saved is not None
    assert saved.review_required is False
    assert saved.retry_count == 0
    assert saved.alerted_hash is None


@pytest.mark.anyio
async def test_zao_only_does_not_fetch_full_time_pages(memory_db, monkeypatch):
    async def empty_pdf_links(url):
        return []

    async def fail_full_time(*args, **kwargs):
        raise AssertionError("Очные страницы не должны запрашиваться")

    monkeypatch.setattr(us, "fetch_pdf_schedule_links", empty_pdf_links)
    monkeypatch.setattr(
        "app.parser.website_parser.fetch_all_schedule_links", fail_full_time
    )

    report = await us.check_for_updates(zao_only=True)

    assert report.links_found == 0


def test_quality_gate_accepts_real_text_schedule(memory_db):
    schedules = parse_pdf_file(FIXTURES / "zaochnoe_09.11.pdf")
    link = ScheduleLink(
        title="Расписание 09.11-14.11.2026",
        file_type="schedule",
        schedule_date=date(2026, 11, 9),
        date_end=date(2026, 11, 14),
        url="https://example.test/09.11.pdf",
        groups=[
            "Л-26-11 зо", "Ф-26-11 зо", "ТД-26-11 зо",
            "Л-25-9 зо", "Ф-25-9 зо", "ТД-25-9 зо",
        ],
    )
    assert us._pdf_result_problem(link, schedules) is None


@pytest.mark.anyio
async def test_only_urls_skips_successful_zao_files(memory_db, monkeypatch):
    first = ScheduleLink(
        title="Расписание 09.11-14.11.2026",
        file_type="schedule",
        schedule_date=date(2026, 11, 9),
        date_end=date(2026, 11, 14),
        url="https://example.test/first.pdf",
    )
    second = ScheduleLink(
        title="Расписание 16.11-21.11.2026",
        file_type="schedule",
        schedule_date=date(2026, 11, 16),
        date_end=date(2026, 11, 21),
        url="https://example.test/second.pdf",
    )

    async def pdf_links(url):
        return [first, second]

    processed: list[str] = []

    async def fake_process(link, report, **kwargs):
        processed.append(link.url)

    monkeypatch.setattr(us, "fetch_pdf_schedule_links", pdf_links)
    monkeypatch.setattr(us, "_process_pdf_link", fake_process)

    report = await us.check_for_updates(
        zao_only=True, only_urls={first.url}
    )

    assert processed == [first.url]
    assert report.links_found == 2
    assert report.skipped == 1
