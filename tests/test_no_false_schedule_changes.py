"""Регрессия: ложные уведомления об изменениях расписания.

Классный час стоит в файле между 3-й и 4-й парой, поэтому разбор отдавал
пары в порядке [3, 90, 4, 5], а база хранит их по номеру. Сравнение шло
позиционно, и каждый повторный обход сайта выглядел как изменение — группам
приходили уведомления без причины.
"""
from datetime import date

import pytest

from app.database import database as db
from app.database import repository as repo


LESSONS = [
    {"number": 1, "subject": "Физика", "teacher": "Иванов И.И.", "room": "101", "notes": None},
    {"number": 2, "subject": "Математика", "teacher": "Петров П.П.", "room": "102", "notes": None},
    {"number": 3, "subject": "Литература", "teacher": "Сидоров С.С.", "room": "103", "notes": None},
    # классный час вставлен ПОСЕРЕДИНЕ — как его отдаёт разбор .xls/.xlsx
    {"number": 90, "subject": "Классный час", "teacher": "Классный К.К.",
     "room": "104", "notes": "с 14:10"},
    {"number": 4, "subject": "Обществознание", "teacher": "Кузнецов К.К.",
     "room": "105", "notes": None},
    {"number": 5, "subject": "География", "teacher": "Морозов М.М.", "room": "106", "notes": None},
]

DAY = date(2026, 10, 12)
GROUP = "Д-26-11К"


@pytest.fixture
def memory_db():
    db.init_db("sqlite:///:memory:")


def _save(lessons, faculty="chernyshevskogo"):
    return repo.replace_group_lessons(
        schedule_date=DAY, group_name=GROUP, source_type="schedule", course=1,
        lessons=lessons, source_file_id=1, faculty=faculty,
    )


def test_class_hour_in_middle_is_not_a_change(memory_db):
    """Повторная загрузка того же расписания не должна считаться изменением."""
    _save(LESSONS)
    _, changed = _save(LESSONS)
    assert changed is False


def test_order_of_pairs_does_not_matter(memory_db):
    """Перестановка пар в файле — не изменение расписания."""
    _save(LESSONS)
    _, changed = _save(list(reversed(LESSONS)))
    assert changed is False


def test_real_change_is_detected(memory_db):
    """Смена кабинета, предмета и добавление пары — настоящие изменения."""
    _save(LESSONS)

    edited = [dict(x) for x in LESSONS]
    edited[0]["room"] = "999"
    _, changed = _save(edited)
    assert changed is True

    added = LESSONS + [
        {"number": 6, "subject": "Физкультура", "teacher": "Орлов О.О.",
         "room": "107", "notes": None}
    ]
    _save(LESSONS)
    _, changed = _save(added)
    assert changed is True


def test_removed_class_hour_is_a_change(memory_db):
    """Классный час убрали из файла — это изменение, а не ложное срабатывание."""
    _save(LESSONS)
    without = [x for x in LESSONS if x["number"] != 90]
    _, changed = _save(without)
    assert changed is True


def test_faculty_change_is_detected(memory_db):
    _save(LESSONS, faculty="chernyshevskogo")
    _, changed = _save(LESSONS, faculty="permskaya")
    assert changed is True