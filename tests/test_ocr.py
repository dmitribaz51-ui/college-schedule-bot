"""Тесты OCR заочки: справочники, ячейки и реальные сканы."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from app.parser.ocr_dictionaries import (
    correct_group,
    correct_room,
    correct_subject,
    correct_teacher,
    detect_exam_label,
    extract_groups,
    find_known_subject,
    parse_date,
    parse_pair_number,
    parse_time_ranges,
)
from app.parser.pdf_ocr import OcrLine, _cell_lesson, parse_scanned_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def test_group_correction_keeps_known_and_rejects_noise():
    assert correct_group("ЮР-24-9 зо") == "ЮР-24-9 зо"
    assert correct_group("юр 24 9 30") == "ЮР-24-9 зо"
    assert correct_group("ЮР-24-9 зо же 3") == "ЮР-24-9 зо"
    # Блик/штамп не должен превращаться в новую группу.
    assert correct_group("В-95-3 зо") is None
    assert extract_groups("Л-24-9 зо Л-25-11 зо") == [
        "Л-24-9 зо",
        "Л-25-11 зо",
    ]


def test_teacher_correction_uses_surname_only_when_unambiguous():
    assert correct_teacher("Чекилкана Ольга Ивановна") == "Чекалкина Ольга Ивановна"
    assert correct_teacher("Канюкова Сусана Рауф кызы") == "Канюкова Сусанна Рауфовна"
    assert correct_teacher("Бармина Ольга Анатольевна") == "Бурмина Наталья Анатольевна"
    # Неизвестную фамилию не подменяем похожей.
    assert correct_teacher("Новикова Мария Петровна") == "Новикова Мария Петровна"


def test_subject_and_room_correction():
    assert correct_subject("у Математика") == "Математика"
    assert correct_subject("Основы финансовой грамотности") == "Основы финансовой грамотности"
    # Для торговых групп: «торгово», а не «горно».
    assert correct_subject(
        "Автоматизация торгово-технологических процессов"
    ) == "Автоматизация торгово-технологических процессов"
    assert correct_subject(
        "Автоматизация горно-технологических процессов"
    ) == "Автоматизация торгово-технологических процессов"
    assert find_known_subject("ерунда Иностранный язык в профессиональной деятельности") == (
        "Иностранный язык в профессиональной деятельности"
    )
    assert correct_room("каб. 30 5") == "305"
    assert correct_room("З14") == "314"
    assert correct_room("каб.305") == "305"


def test_exam_label_and_date_time_helpers():
    assert detect_exam_label("пифференцированный вает енот") == (
        "Дифференцированный зачет"
    )
    assert detect_exam_label("Экзамен Административное право") == "Экзамен"
    assert parse_date("19.10.2026 |") == date(2026, 10, 19)
    assert parse_time_ranges("14:45-15:25 15:30-16:10") == [
        ("14:45", "15:25"),
        ("15:30", "16:10"),
    ]
    assert parse_pair_number("ГУ пара") == 4
    assert parse_pair_number("УГ пара") == 6
    assert parse_pair_number("без номера") is None


def test_cell_lesson_splits_subject_teacher_room():
    lines = [
        OcrLine("Экзамен"),
        OcrLine("Административное право"),
        OcrLine("Ширинкин Илья Дмитриевич / 314"),
    ]
    subject, teacher, room, is_org = _cell_lesson(lines)
    assert subject == "Экзамен: Административное право"
    assert teacher == "Ширинкин Илья Дмитриевич"
    assert room == "314"
    assert is_org is False


def test_cell_lesson_recovers_subject_from_noisy_exam_text():
    lines = [
        OcrLine("И НЕ А ии Уи пифференцированный вает енот Иа Ее"),
        OcrLine("Иностранный язык в профессиональной деятельности"),
        OcrLine("Кабанова Ольга Николаевна / 302"),
    ]
    subject, teacher, room, is_org = _cell_lesson(lines)
    assert subject == (
        "Дифференцированный зачет: Иностранный язык в профессиональной деятельности"
    )
    assert teacher == "Кабанова Ольга Николаевна"
    assert room == "302"
    assert is_org is False


def test_cell_lesson_recognizes_org_meeting_without_org_prefix():
    lines = [OcrLine("о ©. СОБРАНИЕ 1410 каб.305")]
    subject, teacher, room, is_org = _cell_lesson(lines)
    assert subject == "ОРГ. СОБРАНИЕ"
    assert teacher is None
    assert room == "305"
    assert is_org is True


RUN_OCR = os.getenv("RUN_OCR_TESTS") == "1"


@pytest.mark.skipif(
    not RUN_OCR,
    reason="Реальный OCR запускается с RUN_OCR_TESTS=1 и установленным Tesseract",
)
def test_real_scan_15_02_exams_and_org():
    schedules = parse_scanned_pdf(FIXTURES / "zaochnoe_15.02.pdf")
    assert {item.group for item in schedules} == {"ЮР-24-9 зо"}
    assert len(schedules) == 6
    assert {item.schedule_date for item in schedules} == {
        date(2027, 2, day) for day in range(15, 21)
    }

    monday = next(item for item in schedules if item.schedule_date == date(2027, 2, 15))
    org = next(lesson for lesson in monday.lessons if lesson.number == 0)
    assert org.start_time == "14:10"
    assert org.room == "305"

    tuesday = next(item for item in schedules if item.schedule_date == date(2027, 2, 16))
    exam = next(lesson for lesson in tuesday.lessons if lesson.number == 4)
    assert exam.subject.startswith("Экзамен")
    assert "Административное право" in exam.subject
    assert "Ширинкин" in exam.teacher
    assert exam.room == "314"

    thursday = next(item for item in schedules if item.schedule_date == date(2027, 2, 18))
    subjects = " ".join(lesson.subject for lesson in thursday.lessons)
    assert "Иностранный язык" in subjects
    assert "Безопасность жизнедеятельности" in subjects
    assert "Семейное право" in subjects

    saturday = next(item for item in schedules if item.schedule_date == date(2027, 2, 20))
    exam = next(lesson for lesson in saturday.lessons if lesson.number == 4)
    assert exam.start_time == "12:25"
    assert "Введение в сравнительное правоведение" in exam.subject


@pytest.mark.skipif(
    not RUN_OCR,
    reason="Реальный OCR запускается с RUN_OCR_TESTS=1 и установленным Tesseract",
)
def test_real_scan_22_02_faint_table_lines():
    """22.02 — бледные внутренние линии таблицы; верх таблицы нельзя терять."""
    schedules = parse_scanned_pdf(FIXTURES / "zaochnoe_22.02.pdf")
    assert {item.group for item in schedules} == {"ЮР-24-9 зо"}
    assert {item.schedule_date for item in schedules} == {
        date(2027, 2, 22),
        date(2027, 2, 24),
        date(2027, 2, 25),
        date(2027, 2, 26),
        date(2027, 2, 27),
    }
    monday = next(item for item in schedules if item.schedule_date == date(2027, 2, 22))
    assert any(
        "Административный процесс" in (lesson.subject or "")
        for lesson in monday.lessons
    )
    friday = next(item for item in schedules if item.schedule_date == date(2027, 2, 26))
    exam = next(lesson for lesson in friday.lessons if lesson.number == 4)
    assert exam.subject == "Экзамен: Менеджмент"
    assert exam.teacher == "Сазонова Татьяна Владимировна"


@pytest.mark.skipif(
    not RUN_OCR,
    reason="Реальный OCR запускается с RUN_OCR_TESTS=1 и установленным Tesseract",
)
def test_real_scan_19_10_all_groups_and_org():
    schedules = parse_scanned_pdf(FIXTURES / "zaochnoe_19.10.pdf")
    groups = {item.group for item in schedules}
    assert groups == {
        "Л-24-9 зо",
        "Л-25-11 зо",
        "Ф-24-9 зо",
        "Ф-25-11 зо",
        "ТД-24-9 зо",
        "ТД-25-11 зо",
        "ЮР-24-9 зо",
    }
    assert len(schedules) == 42

    monday = next(
        item for item in schedules
        if item.group == "Л-24-9 зо" and item.schedule_date == date(2026, 10, 19)
    )
    lesson = next(item for item in monday.lessons if item.number == 4)
    assert lesson.subject == "Основы финансовой грамотности"
    assert lesson.teacher == "Чекалкина Ольга Ивановна"
    assert lesson.room == "305"

    # ОРГ. СОБРАНИЕ стоит в каждой из четырёх колонок 19.10.
    monday_org = [
        item for item in schedules
        if item.schedule_date == date(2026, 10, 19)
        and any(lesson.number == 0 for lesson in item.lessons)
    ]
    assert len(monday_org) == 7


@pytest.mark.skipif(
    not RUN_OCR,
    reason="Реальный OCR запускается с RUN_OCR_TESTS=1 и установленным Tesseract",
)
def test_real_scan_26_10_uses_link_groups_and_valid_times():
    groups = [
        "Л-25-11 зо",
        "Ф-25-11 зо",
        "ТД-25-11 зо",
        "Л-24-9 зо",
        "Ф-24-9 зо",
        "ТД-24-9 зо",
        "ЮР-24-9 зо",
    ]
    schedules = parse_scanned_pdf(
        FIXTURES / "zaochnoe_26.10.pdf", default_groups=groups
    )
    assert {item.group for item in schedules} == set(groups)
    assert len(schedules) == 35

    tuesday = next(
        item for item in schedules
        if item.group == "Л-24-9 зо" and item.schedule_date == date(2026, 10, 27)
    )
    assert any(
        lesson.subject == "Экономика организации" for lesson in tuesday.lessons
    )
    automation = [
        lesson.subject
        for item in schedules
        for lesson in item.lessons
        if "Автоматизация" in (lesson.subject or "")
    ]
    assert automation
    assert set(automation) == {
        "Автоматизация торгово-технологических процессов"
    }

    saturday = next(
        item for item in schedules
        if item.group == "ТД-25-11 зо"
        and item.schedule_date == date(2026, 10, 31)
    )
    saturday_by_number = {lesson.number: lesson for lesson in saturday.lessons}
    assert (
        saturday_by_number[5].start_time,
        saturday_by_number[5].end_time,
    ) == ("13:50", "14:50")
    assert (
        saturday_by_number[6].start_time,
        saturday_by_number[6].end_time,
    ) == ("15:00", "16:00")

    monday = next(
        item for item in schedules
        if item.group == "ТД-25-11 зо"
        and item.schedule_date == date(2026, 10, 26)
    )
    monday_by_number = {lesson.number: lesson for lesson in monday.lessons}
    assert (
        monday_by_number[5].start_time,
        monday_by_number[5].end_time,
    ) == ("16:30", "17:55")
    assert (
        monday_by_number[6].start_time,
        monday_by_number[6].end_time,
    ) == ("18:05", "19:30")

    for item in schedules:
        for lesson in item.lessons:
            if lesson.start_time and lesson.end_time:
                assert lesson.start_time <= lesson.end_time
