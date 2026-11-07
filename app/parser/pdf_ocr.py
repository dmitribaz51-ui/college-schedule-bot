"""OCR сканов заочки.

Модуль рассчитан на недельные PDF, которые колледж выкладывает картинкой:
  * изображение страницы поворачивается так, как его показывает PDF;
  * по проекциям находятся линии таблицы;
  * каждая ячейка распознаётся отдельно (Tesseract, русский язык);
  * дата, пара, группа, предмет, преподаватель и кабинет собираются
    в те же GroupSchedule/ParsedLesson, что и текстовый PDF.

Если Tesseract недоступен или ячейки не сошлись, возвращается пустой
список: лучше показать PDF, чем выдуманное расписание.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from app.parser.excel_parser import GroupSchedule, ParsedLesson
from app.parser.ocr_dictionaries import (
    KNOWN_TEACHERS,
    correct_room,
    correct_subject,
    correct_teacher,
    detect_exam_label,
    extract_groups,
    find_known_subject,
    parse_date,
    parse_pair_number,
    parse_single_time,
    parse_time_ranges,
    split_teacher_room,
)
from app.utils import course_from_group, get_lesson_times

logger = logging.getLogger(__name__)

KNOWN_TEACHER_SET = set(KNOWN_TEACHERS)

try:  # pypdf — основной вариант, PyPDF2 — запасной
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    from PyPDF2 import PdfReader


class OcrUnavailable(RuntimeError):
    """Tesseract не установлен или не запускается."""


@dataclass
class OcrLine:
    text: str
    confidence: float = 0.0
    top: int = 0


@dataclass
class OcrCell:
    x0: int
    y0: int
    x1: int
    y1: int
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines if line.text).strip()

    @property
    def confidence(self) -> float:
        values = [line.confidence for line in self.lines if line.confidence > 0]
        return sum(values) / len(values) if values else 0.0


@dataclass
class _PairRow:
    number: int | None
    start: str | None
    end: str | None
    y0: int
    y1: int
    day: date | None = None
    is_org: bool = False


@dataclass
class _Day:
    date: date
    y0: int
    y1: int


@dataclass
class _GroupColumn:
    x0: int
    x1: int
    groups: list[str]


_tesseract_ready: bool | None = None
_tesseract_error: str | None = None


def _configure_tesseract() -> bool:
    """Настраивает pytesseract один раз. Не падает, если бинаря нет."""
    global _tesseract_ready, _tesseract_error
    if _tesseract_ready is not None:
        return _tesseract_ready
    try:
        import pytesseract

        cmd = os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        pytesseract.get_tesseract_version()
        _tesseract_ready = True
        _tesseract_error = None
        logger.info("OCR: Tesseract доступен (%s)", cmd or "tesseract")
    except Exception as exc:  # ImportError, TesseractNotFoundError и т.п.
        _tesseract_ready = False
        _tesseract_error = str(exc)
        logger.warning("OCR недоступен: %s", exc)
    return _tesseract_ready


def is_ocr_available() -> bool:
    return _configure_tesseract()


def ocr_unavailable_reason() -> str | None:
    _configure_tesseract()
    return _tesseract_error


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split()).strip(" |_")


def _line_text_from_data(data: dict) -> list[OcrLine]:
    groups: dict[tuple[int, int, int], dict] = {}
    for i, raw in enumerate(data.get("text", [])):
        text = _clean(str(raw or ""))
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError, IndexError):
            conf = 0.0
        key = (
            int(data["block_num"][i]),
            int(data["par_num"][i]),
            int(data["line_num"][i]),
        )
        item = groups.setdefault(key, {"words": [], "confs": [], "top": 10**9})
        item["words"].append(text)
        if conf >= 0:
            item["confs"].append(conf)
        item["top"] = min(item["top"], int(data["top"][i]))

    result: list[OcrLine] = []
    for item in sorted(groups.values(), key=lambda value: value["top"]):
        text = _clean(" ".join(item["words"]))
        if not text:
            continue
        conf = sum(item["confs"]) / len(item["confs"]) if item["confs"] else 0.0
        result.append(OcrLine(text=text, confidence=conf, top=item["top"]))
    return result


def _ocr_lines(image: Image.Image) -> list[OcrLine]:
    import pytesseract
    from pytesseract import Output

    last_error: Exception | None = None
    for psm in (6, 11, 7):
        try:
            data = pytesseract.image_to_data(
                image,
                lang="rus",
                config=f"--psm {psm} -c preserve_interword_spaces=1",
                output_type=Output.DICT,
            )
            lines = _line_text_from_data(data)
            if lines:
                return lines
        except Exception as exc:  # пустая ячейка/редкий сбой Tesseract
            last_error = exc
    if last_error:
        logger.debug("OCR ячейки не удался: %s", last_error)
    return []


def _prepare_cell(cell: Image.Image) -> Image.Image:
    # Только выравнивание контраста. Увеличение мелких ячеек, наоборот,
    # теряет верхнюю строку и путает римские цифры.
    return ImageOps.autocontrast(cell.convert("L"))


def _ocr_cell(
    image: Image.Image,
    box: tuple[int, int, int, int],
    rotations: tuple[int, ...] = (0,),
) -> OcrCell:
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, x1), min(image.height, y1)
    result = OcrCell(x0=x0, y0=y0, x1=x1, y1=y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return result

    crop = image.crop((x0, y0, x1, y1))
    best_lines: list[OcrLine] = []
    best_score = -1.0
    for angle in rotations:
        candidate = crop.rotate(angle, expand=True) if angle else crop
        lines = _ocr_lines(_prepare_cell(candidate))
        text = " ".join(line.text for line in lines)
        score = len(re.sub(r"[^А-ЯЁа-яёA-Za-z0-9]", "", text))
        if parse_date(text):
            score += 200
        if extract_groups(text):
            score += 120
        if parse_time_ranges(text):
            score += 40
        if parse_pair_number(text):
            score += 20
        if lines:
            score += sum(line.confidence for line in lines) / len(lines) / 20
        if score > best_score:
            best_score, best_lines = score, lines
    result.lines = best_lines
    return result


def _has_ink(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = box
    if x1 - x0 < 8 or y1 - y0 < 8:
        return False
    crop = np.asarray(image.crop((x0, y0, x1, y1)).convert("L"))
    return float((crop < 170).mean()) > 0.008


def _group_positions(indices: np.ndarray, gap: int = 7) -> list[int]:
    if len(indices) == 0:
        return []
    groups: list[list[int]] = []
    for raw in indices:
        value = int(raw)
        if not groups or value - groups[-1][-1] > gap:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [int(round(sum(group) / len(group))) for group in groups]


def _spread_max(values: np.ndarray) -> np.ndarray:
    """Сглаживает проекцию, чтобы наклонная линия не потерялась."""
    result = values.copy()
    if len(values) > 1:
        result[1:] = np.maximum(result[1:], values[:-1])
        result[:-1] = np.maximum(result[:-1], values[1:])
        result[1:-1] = np.maximum(result[1:-1], (values[:-2] + values[2:]) // 2)
    return result


def _horizontal_lines(
    dark: np.ndarray,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
    ratio: float = 0.58,
) -> list[int]:
    x0, x1 = max(0, x0), min(dark.shape[1], x1)
    y0, y1 = max(0, y0), min(dark.shape[0], y1)
    if x1 - x0 < 10 or y1 - y0 < 10:
        return [y0, y1]
    counts = _spread_max(dark[y0:y1, x0:x1].sum(axis=1))
    threshold = max(4, int((x1 - x0) * ratio))
    line_rows = _group_positions(np.where(counts >= threshold)[0])
    lines = sorted({y0, y1, *(y0 + row for row in line_rows)})
    # Слишком близкие линии — одна и та же граница.
    cleaned: list[int] = []
    for value in lines:
        if not cleaned or value - cleaned[-1] > 8:
            cleaned.append(value)
        else:
            cleaned[-1] = value
    return cleaned


def _vertical_lines(
    dark: np.ndarray,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
    ratio: float = 0.45,
) -> list[int]:
    x0, x1 = max(0, x0), min(dark.shape[1], x1)
    y0, y1 = max(0, y0), min(dark.shape[0], y1)
    if x1 - x0 < 10 or y1 - y0 < 10:
        return [x0, x1]
    counts = _spread_max(dark[y0:y1, x0:x1].sum(axis=0))
    threshold = max(4, int((y1 - y0) * ratio))
    line_cols = _group_positions(np.where(counts >= threshold)[0])
    lines = sorted({x0, x1, *(x0 + col for col in line_cols)})
    cleaned: list[int] = []
    for value in lines:
        if not cleaned or value - cleaned[-1] > 8:
            cleaned.append(value)
        else:
            cleaned[-1] = value
    return cleaned


def _table_bbox(dark: np.ndarray) -> tuple[int, int, int, int]:
    height, width = dark.shape
    rows = _spread_max(dark.sum(axis=1))
    cols = _spread_max(dark.sum(axis=0))
    row_lines = _group_positions(np.where(rows >= width * 0.50)[0])
    col_lines = _group_positions(np.where(cols >= height * 0.40)[0])

    # Верх/низ таблицы определяем по горизонтальным линиям независимо
    # от вертикальных: на некоторых сканах внутренние линии бледные.
    if len(row_lines) >= 2:
        top, bottom = row_lines[0], row_lines[-1]
    else:
        top, bottom = int(height * 0.06), int(height * 0.94)
    if len(col_lines) >= 2:
        left, right = col_lines[0], col_lines[-1]
    else:
        left, right = int(width * 0.02), int(width * 0.98)
    # Небольшой запас, чтобы не срезать внешнюю рамку.
    return (
        max(0, left - 4),
        max(0, top - 4),
        min(width, right + 4),
        min(height, bottom + 4),
    )


def _detect_grid(
    image: Image.Image,
) -> tuple[np.ndarray, int, int, int, int, list[int]]:
    gray = np.asarray(image.convert("L"))
    threshold = min(180, max(100, int(gray.mean() * 0.65)))
    dark = gray < threshold
    left, top, right, bottom = _table_bbox(dark)
    x_lines = _vertical_lines(dark, left, right, top, bottom, ratio=0.45)
    if len(x_lines) < 3:
        x_lines = [left, right]
    return dark, left, top, right, bottom, x_lines


def _header_bottom(
    dark: np.ndarray,
    x_lines: list[int],
    top: int,
    bottom: int,
) -> int:
    if len(x_lines) < 2:
        return top + max(30, int((bottom - top) * 0.08))
    # Граница шапки проходит через всю таблицу. Проекция только по колонке
    # «Дата» иногда ловит линию внутри шапки и обрезает вторую группу.
    table_lines = _horizontal_lines(
        dark, x_lines[0], x_lines[-1], top, bottom, ratio=0.55
    )
    if len(table_lines) >= 2:
        candidate = table_lines[1]
        if top + 20 < candidate < top + (bottom - top) * 0.25:
            return candidate
    date_lines = _horizontal_lines(
        dark, x_lines[0], x_lines[1], top, bottom, ratio=0.62
    )
    if len(date_lines) >= 2:
        candidate = date_lines[1]
        if top + 20 < candidate < top + (bottom - top) * 0.25:
            return candidate
    return min(bottom, top + max(40, int((bottom - top) * 0.08)))


def _header_cells(
    image: Image.Image,
    x_lines: list[int],
    top: int,
    header_bottom: int,
) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    for index in range(len(x_lines) - 1):
        x0, x1 = x_lines[index], x_lines[index + 1]
        if x1 - x0 < 12:
            result.append(("", []))
            continue
        cell = _ocr_cell(
            image,
            (x0 + 2, top + 2, x1 - 2, header_bottom - 2),
            rotations=(0,),
        )
        text = cell.text
        result.append((text, extract_groups(text)))
    return result


def _choose_columns(
    image: Image.Image,
    dark: np.ndarray,
    x_lines: list[int],
    top: int,
    bottom: int,
    default_groups: list[str] | None,
) -> tuple[tuple[int, int], list[_GroupColumn], int]:
    header_bottom = _header_bottom(dark, x_lines, top, bottom)
    headers = _header_cells(image, x_lines, top, header_bottom)

    first_group_index: int | None = None
    for index, (_text, groups) in enumerate(headers):
        if groups:
            first_group_index = index
            break

    if first_group_index is None:
        # Ищем «Пару», а групповые колонки — самые широкие справа от неё.
        pair_index = 1
        for index, (text, _groups) in enumerate(headers):
            if index > 0 and re.search(r"пар", text, re.I):
                pair_index = index
                break
        table_width = max(1, x_lines[-1] - x_lines[0])
        first_group_index = pair_index + 1
        for index in range(pair_index + 1, len(x_lines) - 1):
            if x_lines[index + 1] - x_lines[index] >= table_width * 0.12:
                first_group_index = index
                break

    # Если заголовок не прочитался, но групп из ссылки ровно столько же,
    # сколько широких колонок, используем их по порядку.
    group_columns: list[_GroupColumn] = []
    for index in range(first_group_index, len(x_lines) - 1):
        x0, x1 = x_lines[index], x_lines[index + 1]
        if x1 - x0 < 20:
            continue
        text, groups = headers[index] if index < len(headers) else ("", [])
        if not groups and default_groups:
            remaining = [
                group for group in default_groups
                if not any(group in column.groups for column in group_columns)
            ]
            if len(remaining) == 1:
                groups = remaining
        if groups:
            group_columns.append(_GroupColumn(x0=x0, x1=x1, groups=groups))

    if not group_columns and default_groups:
        # Крайний случай: одна широкая колонка на все группы.
        group_columns.append(
            _GroupColumn(
                x0=x_lines[first_group_index],
                x1=x_lines[-1],
                groups=list(default_groups),
            )
        )

    # Заголовок мог потерять одну группу («Л-25- зо»). Если из ссылки
    # известен полный список, добавляем пропущенную группу в колонку
    # с тем же префиксом (Л, Ф, ТД, ЮР...).
    if default_groups and group_columns:
        assigned = {
            group for column in group_columns for group in column.groups
        }
        for group in default_groups:
            if group in assigned:
                continue
            prefix = group.split("-", 1)[0]
            candidates = [
                column for column in group_columns
                if any(item.split("-", 1)[0] == prefix for item in column.groups)
            ]
            if len(candidates) == 1:
                candidates[0].groups.append(group)
                assigned.add(group)

    pair_x = (x_lines[1], x_lines[first_group_index])
    return pair_x, group_columns, header_bottom


def _date_cells(
    image: Image.Image,
    dark: np.ndarray,
    date_x: tuple[int, int],
    top: int,
    bottom: int,
) -> list[_Day]:
    y_lines = _horizontal_lines(
        dark, date_x[0], date_x[1], top, bottom, ratio=0.62
    )
    days: list[_Day] = []
    for y0, y1 in zip(y_lines, y_lines[1:]):
        if y1 - y0 < 25:
            continue
        cell = _ocr_cell(
            image,
            (date_x[0] + 2, y0 + 2, date_x[1] - 2, y1 - 2),
            rotations=(0, -90, 90),
        )
        parsed = parse_date(cell.text)
        if parsed:
            days.append(_Day(date=parsed, y0=y0, y1=y1))
    return days


def _pair_rows(
    image: Image.Image,
    dark: np.ndarray,
    pair_x: tuple[int, int],
    top: int,
    bottom: int,
    days: list[_Day],
) -> tuple[list[_PairRow], dict[date, str | None]]:
    y_lines = _horizontal_lines(
        dark, pair_x[0], pair_x[1], top, bottom, ratio=0.60
    )
    rows: list[_PairRow] = []
    org_starts: dict[date, str | None] = {}
    for y0, y1 in zip(y_lines, y_lines[1:]):
        if y1 - y0 < 20:
            continue
        day = next(
            (d for d in days if min(y1, d.y1) - max(y0, d.y0) > 8),
            None,
        )
        if day is None:
            continue
        cell = _ocr_cell(
            image,
            (pair_x[0] + 2, y0 + 2, pair_x[1] - 2, y1 - 2),
            rotations=(0,),
        )
        text = cell.text
        if not text or "!" == text.strip():
            continue
        if "СТУДЕНТ" in text.upper():
            continue
        number = parse_pair_number(text)
        ranges = parse_time_ranges(text)
        if ranges:
            start = min(item[0] for item in ranges)
            end = max(item[1] for item in ranges)
            rows.append(_PairRow(number, start, end, y0, y1, day=day.date))
        elif number is None:
            # Верхняя строка «14:10» перед парами — это ОРГ. СОБРАНИЕ.
            single = parse_single_time(text)
            if single:
                org_starts.setdefault(day.date, single)
                rows.append(
                    _PairRow(None, single, None, y0, y1, day=day.date, is_org=True)
                )
    # Нумеруем пары заново для каждого дня: OCR путает IV/V/VI/VII,
    # а физические строки таблицы всегда идут подряд.
    for day in days:
        day_rows = sorted(
            (row for row in rows if row.day == day.date and not row.is_org),
            key=lambda row: row.y0,
        )
        if not day_rows:
            continue
        expected = 4
        for row in day_rows:
            row.number = expected
            expected += 1
    return rows, org_starts


def _overlapping_pair_rows(
    rows: list[_PairRow], y0: int, y1: int, day: date
) -> list[_PairRow]:
    """Пары, с которыми ячейка реально пересекается, а не касается краем.

    Границы ячеек и строк таблицы после OCR расходятся на несколько пикселей.
    Старое условие «пересечение > 4 px» цепляло соседнюю пару, и одна ячейка
    получала время двух пар (например, 13:50–16:00 вместо 13:50–14:50).
    Берём пересечение не меньше 20% высоты строки: настоящая объединённая
    ячейка даёт большой процент по каждой паре, а случайный край — нет.
    """
    selected: list[_PairRow] = []
    for row in rows:
        if row.day != day:
            continue
        overlap = min(y1, row.y1) - max(y0, row.y0)
        threshold = max(4, int((row.y1 - row.y0) * 0.2))
        if overlap >= threshold:
            selected.append(row)
    return selected


def _cell_lesson(
    lines: list[OcrLine],
) -> tuple[str | None, str | None, str | None, bool]:
    """Возвращает subject, teacher, room, is_org."""
    raw_lines = [_clean(line.text) for line in lines if _clean(line.text)]
    joined = " ".join(raw_lines)
    if "СОБРАН" in joined.upper():
        room_match = re.search(
            r"(?:каб\.?|ауд\.?|кабинет)\s*№?\s*([^\s,;]+)", joined, re.I
        )
        room = correct_room(room_match.group(1)) if room_match else None
        return "ОРГ. СОБРАНИЕ", None, room, True

    lines_text = [
        line for line in raw_lines
        if line not in {"!", "|"} and "СТУДЕНТ" not in line.upper()
    ]
    if not lines_text:
        return None, None, None, False

    teacher: str | None = None
    room: str | None = None
    teacher_index: int | None = None

    for index in range(len(lines_text) - 1, -1, -1):
        line = lines_text[index]
        if "/" in line or re.search(r"(?:каб\.?|ауд\.?|кабинет)", line, re.I):
            raw_teacher, raw_room = split_teacher_room(line)
            teacher = correct_teacher(raw_teacher)
            room = correct_room(raw_room)
            teacher_index = index
            break
        candidate = correct_teacher(line)
        if candidate in KNOWN_TEACHER_SET:
            teacher = candidate
            teacher_index = index
            break

    subject_lines = [
        line for index, line in enumerate(lines_text)
        if index != teacher_index
    ]
    if not subject_lines:
        return None, teacher, room, False

    joined_subject = " ".join(subject_lines)
    label = detect_exam_label(joined_subject)
    known_subject = find_known_subject(joined_subject)
    if known_subject:
        subject = (
            f"{label}: {known_subject}"
            if label and label.lower() not in known_subject.lower()
            else known_subject
        )
    elif label:
        rest = re.sub(
            re.escape(label), " ", joined_subject, count=1, flags=re.I
        ).strip(" .,:—-")
        rest = correct_subject(rest)
        subject = f"{label}: {rest}" if rest else label
    else:
        subject = correct_subject(joined_subject)
    return subject, teacher, room, False


def _parse_page(
    image: Image.Image,
    default_groups: list[str] | None,
) -> list[GroupSchedule]:
    dark, left, top, right, bottom, x_lines = _detect_grid(image)
    if len(x_lines) < 3:
        return []

    pair_x, group_columns, _header_bottom = _choose_columns(
        image, dark, x_lines, top, bottom, default_groups
    )
    if not group_columns:
        logger.info("OCR: не найдены колонки групп")
        return []

    date_x = (x_lines[0], x_lines[1])
    days = _date_cells(image, dark, date_x, top, bottom)
    if not days:
        logger.info("OCR: не найдены даты")
        return []

    pair_rows, org_starts = _pair_rows(
        image, dark, pair_x, top, bottom, days
    )
    if not pair_rows:
        logger.info("OCR: не найдены пары")
        return []

    lessons: dict[tuple[str, date, int], ParsedLesson] = {}
    kept_cells = 0
    low_confidence = 0

    for column in group_columns:
        y_lines = _horizontal_lines(
            dark, column.x0, column.x1, top, bottom, ratio=0.58
        )
        for y0, y1 in zip(y_lines, y_lines[1:]):
            if y1 - y0 < 18:
                continue
            day = next(
                (d for d in days if min(y1, d.y1) - max(y0, d.y0) > 8),
                None,
            )
            if day is None:
                continue
            overlapping = _overlapping_pair_rows(
                pair_rows, y0, y1, day.date
            )
            if not overlapping:
                continue
            box = (column.x0 + 3, y0 + 3, column.x1 - 3, y1 - 3)
            if not _has_ink(image, box):
                continue
            cell = _ocr_cell(image, box, rotations=(0,))
            if not cell.text:
                continue
            subject, teacher, room, is_org = _cell_lesson(cell.lines)
            if not subject:
                continue
            if len(re.findall(r"[А-ЯЁа-яё]", subject)) < 3:
                continue
            if cell.confidence and cell.confidence < 30 and len(subject) < 12:
                continue
            if (
                not is_org
                and find_known_subject(subject) is None
                and detect_exam_label(subject) is None
            ):
                words = re.findall(r"[А-ЯЁа-яёA-Za-z]+", subject)
                short_ratio = (
                    sum(len(word) <= 2 for word in words) / len(words)
                    if words else 0
                )
                if cell.confidence < 40 or (
                    len(words) >= 3 and short_ratio > 0.6
                ):
                    continue
            if cell.confidence and cell.confidence < 28:
                low_confidence += 1

            numbers = [row.number for row in overlapping if row.number is not None]
            if is_org:
                number = 0
                start = parse_single_time(cell.text) or org_starts.get(day.date)
                end = None
            else:
                if not numbers:
                    continue
                numbers = sorted(set(numbers))
                number = numbers[0]
                start = next(
                    (row.start for row in overlapping if row.start),
                    None,
                )
                end = next(
                    (row.end for row in reversed(overlapping) if row.end),
                    None,
                )
                if not start:
                    start = parse_single_time(cell.text)
                # Время берём из официальных звонков, а OCR — только чтобы
                # понять, одна это пара или объединённый блок из нескольких.
                # Иначе OCR иногда склеивает соседнюю строку (13:50–16:00).
                bells = get_lesson_times(day.date, faculty="permskaya")
                expected_start = bells.get(number)
                expected_end = bells.get(numbers[-1])
                if expected_start:
                    start = expected_start[0]
                if expected_end:
                    end = expected_end[1]
                if start and end and start > end:
                    # OCR перепутал начало и конец; не показываем «19:20-15:30».
                    start, end = end, start
            kept_cells += 1
            for group in column.groups:
                key = (group, day.date, number)
                lesson = ParsedLesson(
                    number=number,
                    subject=subject,
                    teacher=teacher,
                    room=room,
                    start_time=start,
                    end_time=end,
                )
                lessons.setdefault(key, lesson)

    if not lessons:
        logger.info("OCR: ячейки распознаны, но пары не собраны")
        return []

    grouped: dict[tuple[str, date], GroupSchedule] = {}
    for (group, day, _number), lesson in lessons.items():
        key = (group, day)
        if key not in grouped:
            grouped[key] = GroupSchedule(
                group=group,
                schedule_date=day,
                course=course_from_group(group, day),
            )
        grouped[key].lessons.append(lesson)

    result: list[GroupSchedule] = []
    for item in grouped.values():
        item.lessons.sort(key=lambda lesson: (lesson.number == 0, lesson.number))
        item.lessons = [lesson for lesson in item.lessons if lesson.subject]
        if item.lessons:
            result.append(item)
    logger.info(
        "OCR: групп/дней %s, ячеек %s, низкая уверенность %s",
        len(result), kept_cells, low_confidence,
    )
    return result


def _page_image(page) -> Image.Image | None:
    try:
        images = list(page.images)
    except Exception:
        images = []
    best = None
    best_area = 0
    for item in images:
        try:
            image = item.image
        except Exception:
            continue
        area = image.width * image.height
        if area > best_area:
            best, best_area = image, area
    if best is None:
        return None
    if best.mode not in ("L", "RGB"):
        best = best.convert("RGB")
    return best


def _rotated_for_pdf(page, image: Image.Image) -> Image.Image:
    try:
        rotate = int(page.get("/Rotate", 0) or 0)
    except Exception:
        rotate = 0
    # /Rotate в PDF задаётся по часовой стрелке, PIL rotate — против.
    angle = (-rotate) % 360
    return image.rotate(angle, expand=True) if angle else image


def _parse_page_with_orientation(
    page,
    image: Image.Image,
    default_groups: list[str] | None,
) -> list[GroupSchedule]:
    displayed = _rotated_for_pdf(page, image)
    schedules = _parse_page(displayed, default_groups)
    if sum(len(item.lessons) for item in schedules) >= 2:
        return schedules

    # Если /Rotate нет или он неверный — пробуем остальные повороты.
    best = schedules
    for angle in (90, 180, 270):
        candidate = displayed.rotate(angle, expand=True)
        parsed = _parse_page(candidate, default_groups)
        if sum(len(item.lessons) for item in parsed) > sum(
            len(item.lessons) for item in best
        ):
            best = parsed
    return best


def _merge_schedules(
    target: list[GroupSchedule],
    incoming: list[GroupSchedule],
) -> None:
    by_key = {(item.group, item.schedule_date): item for item in target}
    for item in incoming:
        key = (item.group, item.schedule_date)
        if key not in by_key:
            target.append(item)
            by_key[key] = item
            continue
        existing = by_key[key]
        have = {(lesson.number, lesson.subject) for lesson in existing.lessons}
        for lesson in item.lessons:
            if (lesson.number, lesson.subject) not in have:
                existing.lessons.append(lesson)
                have.add((lesson.number, lesson.subject))


def parse_scanned_pdf(
    path: str | Path,
    default_groups: list[str] | None = None,
    *,
    default_date: date | None = None,
) -> list[GroupSchedule]:
    """Разбирает скан недельного PDF. Пустой список — не смогли."""
    if not _configure_tesseract():
        raise OcrUnavailable(ocr_unavailable_reason() or "Tesseract недоступен")

    reader = PdfReader(str(path))
    result: list[GroupSchedule] = []
    for page in reader.pages:
        image = _page_image(page)
        if image is None:
            continue
        parsed = _parse_page_with_orientation(
            page, image, default_groups
        )
        _merge_schedules(result, parsed)

    if not result and default_date is not None:
        logger.warning(
            "OCR %s: не удалось найти даты (ожидалась неделя с %s)",
            Path(path).name,
            default_date,
        )
    return result


# Совместимость с возможным старым импортом.
def parse_pdf_with_ocr(
    path: str | Path,
    default_groups: list[str] | None = None,
    *,
    default_date: date | None = None,
) -> list[GroupSchedule]:
    return parse_scanned_pdf(
        path, default_groups=default_groups, default_date=default_date
    )
