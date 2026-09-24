from datetime import date
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory

from unittest.mock import patch
from openpyxl import Workbook

from app.database.models import Lesson
from app.parser.excel_parser import split_lesson_text
from app.services import schedule_service
from app.services.class_hour import _read_start, get_class_hour_range


def test_compact_initials_and_gym() -> None:
    parsed = split_lesson_text(
        "Физическая культура Двинянинова ИН / верхний спортивный зал"
    )
    assert (parsed.subject, parsed.teacher, parsed.room) == (
        "Физическая культура", "Двинянинова ИН", "верхний спортивный зал"
    )


def test_existing_subject_is_split_for_display() -> None:
    lesson = Lesson(subject="Технология проведения маркетинговых исследований Агаджанян ДС",
                    room="201а", lesson_number=4, source_type="changes")
    with patch.object(schedule_service.repo, "get_lessons", return_value=[lesson]):
        text = schedule_service.format_day_schedule("ТД-24-9", date(2026, 9, 14))
    assert "исследований</b>\nАгаджанян ДС/201а🚪" in text
    assert lesson.subject.endswith("Агаджанян ДС")


def test_existing_gym_is_decorated() -> None:
    lesson = Lesson(subject="Физическая культура Двинянинова ИН / верхний спортивный зал",
                    lesson_number=3, source_type="changes")
    with patch.object(schedule_service.repo, "get_lessons", return_value=[lesson]):
        text = schedule_service.format_day_schedule("ТД-24-9", date(2026, 9, 11))
    assert "Физическая культура</b>\nДвинянинова ИН/верхний спортивный зал🏓" in text


def test_class_hour_uses_group_column() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "schedule.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.append(["Время", "ТД-24-9", "Ф-24-9"])
        sheet.append([time(8, 30), None, "Классный час"])
        sheet.append([time(14, 10), "Классный час", None])
        workbook.save(path)
        workbook.close()
        assert _read_start(str(path), 0, "ТД-24-9") == "14:10"
        assert _read_start(str(path), 0, "Ф-24-9") == "08:30"


def test_class_hour_is_not_first_pair() -> None:
    lesson = Lesson(subject="Классный час Бурунова НВ", room="202а",
                    lesson_number=1, source_type="changes")
    with patch.object(schedule_service.repo, "get_lessons", return_value=[lesson]), \
            patch.object(schedule_service, "get_class_hour_range",
                         return_value=("14:10", "14:45")):
        text = schedule_service.format_day_schedule("ТД-24-9", date(2026, 9, 14))
    assert "🕒 <code>14:10-14:45</code> <b>Классный час</b>\nБурунова НВ/202а🚪" in text
    assert "09:15" not in text


def test_class_hour_shift_times() -> None:
    assert get_class_hour_range(None, "ТД-24-9") is None
    assert get_class_hour_range(0, "ТД-24-9") is None


def test_time_cell_is_not_lesson_number() -> None:
    from app.parser.excel_parser import parse_lesson_number
    assert parse_lesson_number("14:10:00") is None
    assert parse_lesson_number("08:30:00") is None
    assert parse_lesson_number("I 09:15") == 1
    assert parse_lesson_number("3") == 3
