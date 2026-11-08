from datetime import date
from unittest.mock import patch

from app.database.models import Lesson
from app.parser.excel_parser import MULTI_PAIR_SEP, split_lesson_text
from app.services import schedule_service

CELL = (
    "Курсовой проект Дизайн-проектирование "
    "Мишарина С.А. каб.106А/ Баталова Ю.П. каб.303/ "
    "Гариева А.А. каб.230А/ Омелина И.Л. Каб.106А"
)

MULTI_LESSON = Lesson(
    subject="Курсовой проект Дизайн-проектирование",
    teacher=MULTI_PAIR_SEP.join(
        ["Мишарина С.А.", "Баталова Ю.П.", "Гариева А.А.", "Омелина И.Л."]
    ),
    room=MULTI_PAIR_SEP.join(["106А", "303", "230А", "106А"]),
    lesson_number=1,
    source_type="changes",
)


def test_multi_teacher_rooms_are_parsed_as_pairs() -> None:
    parsed = split_lesson_text(CELL)

    assert parsed.subject == "Курсовой проект Дизайн-проектирование"
    assert parsed.teacher.split(MULTI_PAIR_SEP) == [
        "Мишарина С.А.",
        "Баталова Ю.П.",
        "Гариева А.А.",
        "Омелина И.Л.",
    ]
    assert parsed.room.split(MULTI_PAIR_SEP) == ["106А", "303", "230А", "106А"]


def test_single_room_with_trailing_slash_is_cleaned() -> None:
    parsed = split_lesson_text("Математика Собко Т.А. каб.210/")

    assert (parsed.subject, parsed.teacher, parsed.room) == (
        "Математика",
        "Собко Т.А.",
        "210",
    )


def test_day_schedule_groups_teachers_by_room() -> None:
    with patch.object(schedule_service.repo, "get_lessons", return_value=[MULTI_LESSON]):
        text = schedule_service.format_day_schedule(
            "Д-24-11К", date(2026, 11, 10), "chernyshevskogo", 3
        )

    assert "Курсовой проект Дизайн-проектирование</b>" in text
    assert "Мишарина С.А., Омелина И.Л. / \u2060106А🚪" in text
    assert "Баталова Ю.П. / \u2060303🚪" in text
    assert "Гариева А.А. / \u2060230А🚪" in text
    assert "106А/" not in text


def test_week_schedule_keeps_all_rooms() -> None:
    def fake_lessons(day, group, faculty):
        return [MULTI_LESSON] if day == date(2026, 11, 10) else []

    with patch.object(schedule_service.repo, "get_lessons", side_effect=fake_lessons):
        text = schedule_service.format_week_schedule(
            "Д-24-11К", date(2026, 11, 9), "chernyshevskogo", 3
        )

    assert "Мишарина С.А., Омелина И.Л. — 🚪\u2060106А" in text
    assert "Баталова Ю.П. — 🚪\u2060303" in text
    assert "Гариева А.А. — 🚪\u2060230А" in text
