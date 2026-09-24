"""Мелкие вспомогательные функции: время, даты, названия групп, логирование."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

# Пермь = UTC+5. Фиксированное смещение, чтобы не тянуть лишние библиотеки.
PERM_TZ = timezone(timedelta(hours=5))

WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
)

# Время начала и окончания пар (по дням недели)
# Понедельник
LESSON_TIMES_MONDAY = {
    0: ("08:30", "09:10"),  # классный час 1 смена
    1: ("09:15", "10:40"),
    2: ("10:50", "12:15"),
    3: ("12:45", "14:10"),
    4: ("14:45", "16:10"),
    5: ("16:30", "17:55"),
    6: ("18:05", "19:30"),
}

# Вторник-пятница
LESSON_TIMES_WEEKDAY = {
    1: ("08:30", "09:55"),
    2: ("10:05", "11:30"),
    3: ("12:00", "13:25"),
    4: ("14:05", "15:30"),
    5: ("16:00", "17:25"),
    6: ("17:35", "19:00"),
}

# Суббота
LESSON_TIMES_SATURDAY = {
    1: ("08:30", "09:30"),
    2: ("09:40", "10:40"),
    3: ("11:10", "12:10"),
    4: ("12:25", "13:25"),
    5: ("13:50", "14:50"),
    6: ("15:00", "16:00"),
}

LESSON_TIMES_CHERN_WEEKDAY_1 = {1: ("08:30", "09:55"), 2: ("10:05", "11:50"), 3: ("12:00", "13:25"), 4: ("14:15", "15:40"), 5: ("16:00", "17:25"), 6: ("17:35", "19:00")}
LESSON_TIMES_CHERN_WEEKDAY_24 = {1: ("08:30", "09:55"), 2: ("10:05", "11:30"), 3: ("12:00", "13:25"), 4: ("14:15", "15:40"), 5: ("16:00", "17:25"), 6: ("17:35", "19:00")}
LESSON_TIMES_CHERN_MONDAY_1 = {0: ("08:30", "09:10"), 1: ("09:15", "10:40"), 2: ("11:10", "12:35"), 3: ("12:45", "14:10"), 4: ("14:45", "16:10"), 5: ("16:30", "17:55"), 6: ("18:05", "19:30")}
LESSON_TIMES_CHERN_MONDAY_24 = {0: ("08:30", "09:10"), 1: ("09:15", "10:40"), 2: ("10:50", "12:40"), 3: ("12:45", "14:10"), 4: ("14:45", "16:10"), 5: ("16:30", "17:55"), 6: ("18:05", "19:30")}

def get_lesson_times(day: date, faculty: str = "permskaya", course: int | None = None) -> dict[int, tuple[str, str]]:
    """Возвращает таблицу времен пар для конкретной даты."""
    weekday = day.weekday()
    if faculty == "chernyshevskogo":
        if weekday == 0:
            return LESSON_TIMES_CHERN_MONDAY_1 if course == 1 else LESSON_TIMES_CHERN_MONDAY_24
        return LESSON_TIMES_CHERN_WEEKDAY_1 if course == 1 else LESSON_TIMES_CHERN_WEEKDAY_24
    if weekday == 0:  # понедельник
        return LESSON_TIMES_MONDAY
    elif weekday == 5:  # суббота
        return LESSON_TIMES_SATURDAY
    else:  # вторник-пятница
        return LESSON_TIMES_WEEKDAY

# Старая таблица для обратной совместимости (вторник-пятница)
LESSON_TIMES = LESSON_TIMES_WEEKDAY


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    # httpx слишком болтливый
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def today_perm() -> date:
    return datetime.now(PERM_TZ).date()


def tomorrow_perm() -> date:
    return today_perm() + timedelta(days=1)


def human_date(day: date) -> str:
    return f"{day.strftime('%d.%m.%Y')} ({WEEKDAYS[day.weekday()]})"


def normalize_group(raw: str | None) -> str | None:
    """'тд 26 9' / 'ТД–26–9' / 'ТД-24-9к-2' -> 'ТД-26-9' / 'ТД-24-9К-2'. Возвращает None, если это не похоже на группу."""
    if not raw:
        return None
    text = raw.strip().upper().replace("–", "-").replace("—", "-")
    # Заменяем пробелы на дефисы, удаляем дубли дефисов
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    # Форматы: ТД-26-9 / ТД-26-9А / ТД-24-9К-2 / Л-26-9-1
    # Базовый формат: БУКВЫ-ЦИФРЫ-ЦИФРЫ, потом опционально БУКВА или -БУКВА-ЦИФРА
    if not re.fullmatch(
        r"[А-ЯЁA-Z]{1,6}-\d{1,2}-\d{1,2}"
        r"(?:[А-ЯЁA-Z](?:-\d+)?|-\d+)?",
        text,
    ):
        return None
    return text


_DATE_PATTERNS = (
    "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
)


def parse_user_date(text: str) -> date | None:
    """Разбирает дату, введённую пользователем."""
    if not text:
        return None
    value = text.strip().lower()
    if value in ("сегодня", "today"):
        return today_perm()
    if value in ("завтра", "tomorrow"):
        return tomorrow_perm()

    value = value.replace(" ", "")
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    # формат без года: 10.09
    m = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})", value)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        try:
            return date(today_perm().year, month, day)
        except ValueError:
            return None
    return None


def extract_date(text: str) -> date | None:
    """Ищет первую дату вида 08.09.2026 / 8.9.26 внутри произвольного текста."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})(?!\d)", text)
    if m:
        try:
            return date(today_perm().year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None
