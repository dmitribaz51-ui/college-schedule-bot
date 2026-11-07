"""Интеграция OCR в обработку PDF заочки."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.database import database as db
from app.database import repository as repo
from app.parser.excel_downloader import DownloadResult
from app.parser.excel_parser import GroupSchedule, ParsedLesson
from app.parser.website_parser import ScheduleLink
from app.services import update_service as us


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
    assert saved.processed is True
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
