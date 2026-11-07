"""Разбор текстовых PDF заочки (недельные расписания ПКПС).

PDF, которые колледж делает из Excel, содержат текстовый слой и
структуру таблицы. Сканы сюда не попадают: для них позже будет OCR.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

try:  # pypdf — новый пакет, PyPDF2 — запасной вариант для старых окружений
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - зависит от окружения
    from PyPDF2 import PdfReader

from app.parser.excel_parser import GroupSchedule, ParsedLesson
from app.utils import normalize_group

logger = logging.getLogger(__name__)

GROUP_RE = re.compile(
    r"([А-ЯЁA-Z]{1,6}-\d{1,2}-\d{1,2}(?:-\d+)?)\s*зо",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
PAIR_RE = re.compile(r"\b(IV|V|VI|VII)\s*пара\b", re.IGNORECASE)
TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[:.]\s*(\d{2})\s*[-–—]\s*(\d{1,2})\s*[:.]\s*(\d{2})"
)
TIME_ONE_RE = re.compile(r"(\d{1,2})\s*[:.]\s*(\d{2})")
COURSE_RE = re.compile(r"(\d)\s*курс", re.IGNORECASE)

PAIR_NUMBERS = {"IV": 4, "V": 5, "VI": 6, "VII": 7}


@dataclass
class PdfCell:
    text: str
    x: float | None = None


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def _parse_cmap(text: str) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            mapping[int(src, 16)] = "".join(
                chr(int(dst[i : i + 4], 16)) for i in range(0, len(dst), 4)
            )
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for raw_line in block.strip().splitlines():
            match = re.match(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[.*\]|<[0-9A-Fa-f]+>)",
                raw_line.strip(),
            )
            if not match:
                continue
            lo, hi = int(match.group(1), 16), int(match.group(2), 16)
            dst = match.group(3)
            if dst.startswith("["):
                for offset, item in enumerate(re.findall(r"<([0-9A-Fa-f]+)>", dst)):
                    mapping[lo + offset] = "".join(
                        chr(int(item[i : i + 4], 16)) for i in range(0, len(item), 4)
                    )
            else:
                base = int(dst[1:-1], 16)
                for offset in range(hi - lo + 1):
                    mapping[lo + offset] = chr(base + offset)
    return mapping


def _page_blocks(page) -> dict[int, tuple[str, float | None]]:
    fonts: dict[str, tuple[str | None, dict[int, str]]] = {}
    resources = page.get("/Resources")
    if resources:
        resources = resources.get_object()
        for name, ref in (resources.get("/Font") or {}).items():
            font = ref.get_object()
            cmap: dict[int, str] = {}
            if font.get("/ToUnicode"):
                cmap = _parse_cmap(
                    font["/ToUnicode"].get_object().get_data().decode("latin-1")
                )
            fonts["/" + name.strip("/")] = (font.get("/Subtype"), cmap)

    contents = page.get_contents()
    # ContentStream — это словарь без ключей, поэтому bool(contents) == False.
    # Проверяем именно None, иначе pypdf 7 отдаёт пустой текст.
    content = contents.get_data().decode("latin-1") if contents is not None else ""
    blocks: dict[int, tuple[str, float | None]] = {}

    for match in re.finditer(r"/P\s*<<([^>]*)>>\s*BDC(.*?)EMC", content, re.S):
        mcid_match = re.search(r"/MCID\s+(\d+)", match.group(1))
        if not mcid_match:
            continue
        mcid = int(mcid_match.group(1))
        body = match.group(2)
        xs = [float(v) for v in re.findall(r"1 0 0 1 ([\d.]+) [\d.]+ Tm", body)]
        current_font: str | None = None
        parts: list[str] = []
        for token in re.finditer(
            r"/(F\d+)\s+([\d.]+)\s+Tf|<([0-9A-Fa-f]+)>|\((?:\\.|[^\\()])*\)",
            body,
        ):
            if token.group(1):
                current_font = "/" + token.group(1)
            elif token.group(3):
                raw = token.group(3)
                subtype, cmap = fonts.get(current_font or "", (None, {}))
                if subtype == "/Type0":
                    parts.append(
                        "".join(
                            cmap.get(int(raw[i : i + 4], 16), "")
                            for i in range(0, len(raw), 4)
                        )
                    )
                else:
                    parts.append(bytes.fromhex(raw).decode("cp1252", "replace"))
            else:
                parts.append(token.group(0)[1:-1])
        blocks[mcid] = ("".join(parts), xs[0] if xs else None)
    return blocks


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _mcid_of(value) -> int | None:
    try:
        return int(value)
    except Exception:
        if hasattr(value, "get") and "/MCID" in value:
            return int(value["/MCID"])
    return None


def _table_rows(page, reader) -> list[list[PdfCell]] | None:
    blocks = _page_blocks(page)
    root = reader.trailer["/Root"]["/StructTreeRoot"].get_object()
    table = None

    def find(node) -> None:
        nonlocal table
        node = node.get_object()
        if isinstance(node, list):
            for item in node:
                find(item)
            return
        if hasattr(node, "get") and node.get("/S") == "/Table":
            table = node
            return
        if hasattr(node, "get") and "/K" in node:
            find(node["/K"])

    find(root["/K"])
    if table is None:
        return None

    rows: list[list[PdfCell]] = []
    for tr in table.get("/K", []):
        tr = tr.get_object()
        cells: list[PdfCell] = []
        for td in _as_list(tr.get("/K")):
            td = td.get_object()
            if not hasattr(td, "get") or td.get("/S") != "/TD":
                continue
            parts: list[str] = []
            xs: list[float] = []
            for paragraph in _as_list(td.get("/K")):
                paragraph = paragraph.get_object()
                if not hasattr(paragraph, "get") or paragraph.get("/S") != "/P":
                    continue
                for item in _as_list(paragraph.get("/K")):
                    mcid = _mcid_of(item)
                    if mcid is None or mcid not in blocks:
                        continue
                    text, x = blocks[mcid]
                    if text:
                        parts.append(text)
                    if x is not None:
                        xs.append(x)
            cells.append(PdfCell(_clean(" ".join(parts)), min(xs) if xs else None))
        rows.append(cells)
    return rows


def _group_names(text: str) -> list[str]:
    found: list[str] = []
    for match in GROUP_RE.finditer(text or ""):
        group = normalize_group(f"{match.group(1)} зо")
        if group and group not in found:
            found.append(group)
    return found


def _parse_time_range(text: str) -> tuple[str, str] | None:
    match = TIME_RANGE_RE.search(text or "")
    if not match:
        return None
    return (
        f"{int(match.group(1)):02d}:{match.group(2)}",
        f"{int(match.group(3)):02d}:{match.group(4)}",
    )


def _parse_one_time(text: str) -> str | None:
    match = TIME_ONE_RE.search(text or "")
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _split_teacher_room(text: str) -> tuple[str | None, str | None]:
    text = _clean(text)
    if not text:
        return None, None
    if "/" in text:
        left, right = text.split("/", 1)
        return left.strip() or None, right.strip() or None
    room_match = re.search(r"(?:каб\.?|ауд\.?|кабинет)\s*([\w/\- ]+)", text, re.I)
    if room_match:
        room = room_match.group(1).strip(" .,")
        teacher = text[: room_match.start()].strip(" .,")
        return teacher or None, room or None
    return text, None


def _course_from_group(group: str, day: date) -> int | None:
    match = re.search(r"-(\d{2})-", group)
    if not match:
        return None
    admission = 2000 + int(match.group(1))
    academic_year = day.year if day.month >= 9 else day.year - 1
    return academic_year - admission + 1


def _parse_rows(rows: list[list[PdfCell]]) -> list[GroupSchedule]:
    header_index = None
    for index, row in enumerate(rows):
        texts = [cell.text for cell in row]
        has_date = any(text == "Дата" for text in texts)
        has_pair = any(text == "Пара" for text in texts)
        has_groups = any(_group_names(text) for text in texts)
        if has_date and (has_pair or has_groups):
            header_index = index
            break
    if header_index is None:
        return []

    columns: list[tuple[list[str], float]] = []
    for cell in rows[header_index]:
        names = _group_names(cell.text)
        if names and cell.x is not None:
            columns.append((names, cell.x))
    if not columns:
        return []
    columns.sort(key=lambda item: item[1])

    course_map: dict[str, int] = {}
    for row in rows[:header_index]:
        for cell in row:
            match = COURSE_RE.search(cell.text)
            if match:
                for group in _group_names(cell.text):
                    course_map[group] = int(match.group(1))

    current_date: date | None = None
    current_pair: int | None = None
    pair_start: str | None = None
    pair_end: str | None = None
    subjects: dict[int, str] = {}
    teachers: dict[int, str] = {}
    lessons: dict[tuple[date, int, str], ParsedLesson] = {}

    def column_for(x: float | None) -> int | None:
        if x is None:
            return None
        return min(range(len(columns)), key=lambda i: abs(columns[i][1] - x))

    def flush_pair() -> None:
        nonlocal current_pair, pair_start, pair_end, subjects, teachers
        if current_date is not None and current_pair is not None:
            for col, subject in subjects.items():
                teacher_raw = teachers.get(col)
                teacher, room = _split_teacher_room(teacher_raw or "")
                for group in columns[col][0]:
                    lesson = ParsedLesson(
                        number=current_pair,
                        subject=subject,
                        teacher=teacher,
                        room=room,
                        start_time=pair_start,
                        end_time=pair_end,
                    )
                    lessons.setdefault((current_date, current_pair, group), lesson)
        subjects, teachers = {}, {}
        pair_start = pair_end = None

    for row in rows[header_index + 1 :]:
        texts = [cell.text for cell in row if cell.text]
        joined = " ".join(texts)
        if not joined:
            continue

        date_match = DATE_RE.search(joined)
        if date_match:
            # Дата часто стоит в одной строке с первой парой дня.
            flush_pair()
            current_pair = None
            try:
                current_date = date(
                    int(date_match.group(3)),
                    int(date_match.group(2)),
                    int(date_match.group(1)),
                )
            except ValueError:
                current_date = None

        if "СТУДЕНТЫ" in joined.upper() or joined.strip() == "!":
            continue

        if any("ОРГ. СОБРАНИЕ" in text.upper() for text in texts):
            flush_pair()
            row_time = None
            for cell in row:
                if "ОРГ. СОБРАНИЕ" not in cell.text.upper():
                    row_time = _parse_one_time(cell.text)
                    if row_time:
                        break
            for cell in row:
                if "ОРГ. СОБРАНИЕ" not in cell.text.upper():
                    continue
                time_value = _parse_one_time(cell.text) or row_time
                room_match = re.search(r"каб\.?\s*([\w/\-]+)", cell.text, re.I)
                room = room_match.group(1) if room_match else None
                col = column_for(cell.x)
                if col is None or current_date is None:
                    continue
                for group in columns[col][0]:
                    lessons[(current_date, 0, group)] = ParsedLesson(
                        number=0,
                        subject="ОРГ. СОБРАНИЕ",
                        room=room,
                        start_time=time_value,
                    )
            continue

        pair_match = PAIR_RE.search(joined)
        row_time = _parse_time_range(joined)
        if pair_match:
            flush_pair()
            current_pair = PAIR_NUMBERS[pair_match.group(1).upper()]
        if row_time:
            start, end = row_time
            if pair_start is None:
                pair_start = start
            pair_end = max(pair_end or end, end)
        if current_pair is None:
            continue
        phase = "subject" if pair_match else "teacher"

        for cell in row:
            if not cell.text:
                continue
            if DATE_RE.search(cell.text) or PAIR_RE.search(cell.text):
                continue
            if _parse_time_range(cell.text):
                continue
            if TIME_ONE_RE.fullmatch(cell.text.strip()) and len(cell.text.strip()) <= 5:
                continue
            if "СТУДЕНТЫ" in cell.text.upper() or cell.text.strip() == "!":
                continue
            col = column_for(cell.x)
            if col is None:
                continue
            if phase == "subject":
                subjects.setdefault(col, cell.text)
            else:
                teachers.setdefault(col, cell.text)

    flush_pair()

    grouped: dict[tuple[str, date], GroupSchedule] = {}
    for (day, number, group), lesson in sorted(lessons.items()):
        key = (group, day)
        if key not in grouped:
            grouped[key] = GroupSchedule(
                group=group,
                schedule_date=day,
                course=course_map.get(group) or _course_from_group(group, day),
            )
        grouped[key].lessons.append(lesson)

    result: list[GroupSchedule] = []
    for item in grouped.values():
        item.lessons = [lesson for lesson in item.lessons if lesson.subject]
        item.lessons.sort(key=lambda lesson: (lesson.number == 0, lesson.number))
        if item.lessons:
            result.append(item)
    return result


def parse_pdf_file(path: str | Path) -> list[GroupSchedule]:
    """Возвращает расписание по группам из текстового PDF.

    Для сканов (без текстового слоя) вернёт пустой список.
    """
    reader = PdfReader(str(path))
    result: list[GroupSchedule] = []
    for page in reader.pages:
        if not (page.extract_text() or "").strip():
            continue
        rows = _table_rows(page, reader)
        if not rows:
            continue
        result.extend(_parse_rows(rows))
    logger.info("PDF %s: распознано групп/дней: %s", Path(path).name, len(result))
    return result
