from datetime import date

from app.utils import extract_date, normalize_group, parse_user_date


def test_normalize_group():
    assert normalize_group("тд 26 9") == "ТД-26-9"
    assert normalize_group("ТД–26–9") == "ТД-26-9"
    assert normalize_group("просто текст") is None


def test_extract_date():
    assert extract_date("Расписание на 08.09.2026") == date(2026, 9, 8)
    assert extract_date("Изменения на 9.9.26") == date(2026, 9, 9)


def test_parse_user_date():
    assert parse_user_date("10.09.2026") == date(2026, 9, 10)
    assert parse_user_date("ерунда") is None