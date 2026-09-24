"""Формирование текста расписания для Telegram + наложение изменений."""
from __future__ import annotations

import re
from copy import copy
from datetime import date

from app.database import repository as repo
from app.parser.excel_parser import MIDDAY_CLASS_HOUR, split_lesson_text
from app.services.class_hour import get_class_hour_range
from app.utils import human_date, get_lesson_times

NUMBER_EMOJI = {
    0: "0️⃣", 1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣",
    5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣",
}

# Время классного часа, если его не удалось вытащить из исходного файла (.xls):
# 0 — утренний, 90 — классный час середины дня (14:10-14:45).
CLASS_HOUR_FALLBACK = {
    0: ("08:30", "09:10"),
    MIDDAY_CLASS_HOUR: ("14:10", "14:45"),
}


def _class_hour_range(lesson, group: str) -> tuple[str, str] | None:
    """(начало, конец) классного часа: из исходного файла либо по номеру записи."""
    time_range = get_class_hour_range(lesson.source_file_id, group)
    if time_range:
        return time_range
    return CLASS_HOUR_FALLBACK.get(lesson.lesson_number)


def get_day_schedule(group: str, day: date, faculty: str = "permskaya", course: int | None = None) -> tuple[list[tuple], bool]:
    """Возвращает (список пар, были_ли_изменения).

    Логика ТЗ: основное расписание + последние изменения = актуальное.
    """
    rows = []
    for stored in repo.get_lessons(day, group, faculty):
        lesson = copy(stored)
        parsed = split_lesson_text(stored.subject or "")
        lesson.subject = parsed.subject
        lesson.teacher = stored.teacher or parsed.teacher
        lesson.room = stored.room or parsed.room
        lesson.notes = stored.notes or parsed.notes
        rows.append(lesson)

    base = {l.lesson_number: l for l in rows if l.source_type == "schedule"}
    changes = {l.lesson_number: l for l in rows if l.source_type == "changes"}

    merged = dict(base)

    for number, change in changes.items():
        if number not in merged:
            merged[number] = change
            continue

        lesson = copy(merged[number])

        if change.subject:
            lesson.subject = change.subject
            lesson.source_file_id = change.source_file_id

        if change.teacher:
            lesson.teacher = change.teacher

        if change.room:
            # Кабинет приоритетно берётся из основного расписания.
            # Изменения не затирают его ошибочно распознанным значением.
            lesson.room = lesson.room or change.room

        if change.notes:
            lesson.notes = change.notes

        merged[number] = lesson

    def _item_sort_key(item: tuple[int, any, bool]) -> tuple[str, int]:
        number, lesson, _ = item
        is_class_hour = "классный час" in (lesson.subject or "").lower()
        if is_class_hour:
            time_range = _class_hour_range(lesson, group)
            if time_range and time_range[0]:
                return (time_range[0], number)
        lesson_times = get_lesson_times(day, faculty, course)
        if number in lesson_times:
            return (lesson_times[number][0], number)
        return (f"{number:02d}:00", number)

    items = [(number, merged[number], number in changes) for number in sorted(merged)]
    items.sort(key=_item_sort_key)
    return items, bool(changes)


def format_day_schedule(group: str, day: date, faculty: str = "permskaya", course: int | None = None) -> str:
    items, has_changes = get_day_schedule(group, day, faculty, course)

    header = [f"📅 <b>Расписание на {human_date(day)}</b>", f"👨‍🎓 Группа: <b>{group}</b>"]
    if has_changes:
        header.append("⚠️ <i>С учётом опубликованных изменений</i>")

    if not items:
        header.append("")
        header.append("На этот день расписание не найдено.")
        return "\n".join(header)

    lines = header + [""]
    lesson_times = get_lesson_times(day, faculty, course)
    
    for number, lesson, changed in items:
        emoji = NUMBER_EMOJI.get(number, f"{number}.")
        
        # Добавляем время пары
        time_str = ""
        is_class_hour = "классный час" in (lesson.subject or "").lower()
        if is_class_hour:
            emoji = "🕒"
            time_range = _class_hour_range(lesson, group)
            if time_range:
                time_str = f" <code>{time_range[0]}-{time_range[1]}</code>"
        elif number in lesson_times:
            start, end = lesson_times[number]
            time_str = f" <code>{start}-{end}</code>"
        
        # «проф.ком», «проф.деят» Telegram принимает за адрес сайта и красит
        # синим. Невидимый знак после точки ломает автоопределение ссылки.
        subject = re.sub(r"\.(?=[А-Яа-яA-Za-z])", ".⁠", lesson.subject or "—")
        lines.append(f"{emoji}{time_str} <b>{subject}</b>")
        
        # Преподаватель и кабинет в одной строке
        if lesson.teacher or lesson.room:
            teacher_room_parts = []
            
            if lesson.teacher:
                teacher_room_parts.append(lesson.teacher)
            
            if lesson.room:
                room_low = lesson.room.lower()
                if faculty == "chernyshevskogo" and "тренаж" in room_low:
                    room_emoji = "🏋️‍♂️"
                elif "спорт" in room_low or room_low == "с/з":
                    room_emoji = "🏓"
                else:
                    room_emoji = "🚪"
                # Telegram сам делает из голого номера кабинета телефонную ссылку
                # («410» открывается как набор номера). Невидимый знак внутри
                # номера ломает автоопределение, а цвет текста остаётся обычным.
                room_text = lesson.room
                if room_text[:1].isdigit():
                    room_text = "⁠" + room_text
                teacher_room_parts.append(f"{room_text}{room_emoji}")
            
            # Соединяем преподавателя и кабинет через /
            teacher_room_line = "/".join(teacher_room_parts)
            lines.append(teacher_room_line)
        
        if lesson.notes:
            lines.append(f"    📝 {lesson.notes}")
        lines.append("")
    return "\n".join(lines).strip()


def format_recent_changes(group: str | None = None, limit: int = 5) -> str:
    files = repo.get_recent_files(limit=limit, file_type="changes")
    if not files:
        return "⚠️ Файлы изменений пока не обнаружены."

    lines = ["⚠️ <b>Последние файлы изменений</b>", ""]
    for item in files:
        day = item.schedule_date.strftime("%d.%m.%Y") if item.schedule_date else "дата не указана"
        status = "✅" if item.processed else "⏳"
        lines.append(f"{status} 📅 {day} — {item.title}")
    if group:
        lines += [
            "",
            f"Чтобы увидеть актуальное расписание группы {group}, "
            "нажмите «📍 Сегодня» или «📆 Завтра».",
        ]
    return "\n".join(lines)
