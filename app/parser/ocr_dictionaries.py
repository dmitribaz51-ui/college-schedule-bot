"""Справочники и осторожные исправления для OCR заочки.

OCR не должен «угадывать» новое имя или предмет. Поэтому здесь только
известные значения из уже разобранных текстовых PDF и несколько явных
алиасов. Неизвестный текст остаётся как есть.
"""
from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher

# Все заочные группы колледжа на 2026/2027 учебный год.
KNOWN_GROUPS = [
    "Л-26-11 зо",
    "Ф-26-11 зо",
    "ТД-26-11 зо",
    "Л-25-11 зо",
    "Ф-25-11 зо",
    "ТД-25-11 зо",
    "Л-25-9 зо",
    "Ф-25-9 зо",
    "ТД-25-9 зо",
    "Л-24-11 зо",
    "ТД-24-11 зо",
    "Л-24-9 зо",
    "Ф-24-9 зо",
    "ТД-24-9 зо",
    "ЮР-24-9 зо",
    "Л-23-9 зо",
    "К-23-9 зо",
]

KNOWN_TEACHERS = [
    "Бурмина Наталья Анатольевна",
    "Бурунова Наталья Владимировна",
    "Иванова Ирина Владимировна",
    "Кабанова Ольга Николаевна",
    "Канюкова Сусанна Рауфовна",
    "Колосова Алина Васильевна",
    "Кривощекова Ксения Эдуардовна",
    "Перевозчиков Денис Викторович",
    "Савастеева Марина Михайловна",
    "Сазонова Татьяна Владимировна",
    "Старцева Ксения Николаевна",
    "Чекалкина Ольга Ивановна",
    "Шигапова Оксана Руслановна",
    "Ширинкин Илья Дмитриевич",
]

KNOWN_SUBJECTS = [
    "Административное право",
    "Безопасность жизнедеятельности",
    "Бухгалтерский учет логистических операций",
    "Бухгалтерский учет",
    "Введение в сравнительное правоведение",
    "Гражданское право",
    "Дифференцированный зачет",
    "Документационное обеспечение управления",
    "Иностранный язык в профессиональной деятельности",
    "Информационные технологии в профессиональной деятельности",
    "История России",
    "Квалификационный экзамен",
    "Логистика",
    "Математика",
    "Экономика организации",
    "Экономика и основы анализа финансово-хозяйственной деятельности торговой организации",
    "МДК.01.01 Административный процесс",
    "МДК.01.03 Гражданский процесс",
    "МДК.02.01 Судоустройство и правоохранительные органы",
    "МДК.02.01 Технология проведения маркетинговых исследований",
    "МДК.02.01 Финансы, налоги и налогообложение",
    "МДК.02.02 Ценообразование в торговой деятельности",
    "МДК.02.03 Бизнес-планирование и финансовое моделирование предпринимательской единицы",
    "МДК.03.02 Логистика сервисного обслуживания",
    "Менеджмент",
    "Моделирование логистических систем",
    "ОГСЭ.03 Иностранный язык",
    "ОП.05 Основы предпринимательства",
    "ОП.06 Логистика",
    "ОП.06 Финансы, денежное обращение и кредит",
    "ОП.09 Налоги и налогообложение",
    "ОП.11* Основы предпринимательской деятельности",
    "ОП.13* Основы предпринимательской деятельности",
    "ОП.13* Управление человеческими ресурсами",
    "ОП.15* Основы применения искусственного интеллекта в профессиональной деятельности",
    "ОРГ. СОБРАНИЕ",
    "Основы бережливого производства",
    "Основы логистической деятельности",
    "Основы финансовой грамотности",
    "ПМ.01 Правоохранительная деятельность",
    "ПП.01.01 Производственная практика",
    "ПП.02.01 Производственная практика",
    "ПП.03.01 Производственная практика",
    "СГ.02 Иностранный язык в профессиональной деятельности",
    "Семейное право",
    "УП.01.01 Учебная практика",
    "УП.02.01 Учебная практика",
    "Экзамен",
    "Эксплуатация горно-технологического оборудования и охрана труда",
    "Автоматизация горно-технологических процессов",
]

KNOWN_ROOMS = [
    "201а",
    "202",
    "202а",
    "205",
    "209",
    "302",
    "302а",
    "303",
    "303а",
    "305",
    "305а",
    "308",
    "313",
    "314",
    "efront",
]

# Явные алиасы — только там, где ошибка уже встречалась.
GROUP_ALIASES = {
    "Л 24 9 30": "Л-24-9 зо",
    "Ф 24 9 30": "Ф-24-9 зо",
    "ТД 24 9 30": "ТД-24-9 зо",
    "ЮР 24 9 30": "ЮР-24-9 зо",
}
TEACHER_ALIASES = {
    "Чекилкана": "Чекалкина Ольга Ивановна",
    "Чекапкина": "Чекалкина Ольга Ивановна",
    "Чекалкина": "Чекалкина Ольга Ивановна",
    "Кабанова": "Кабанова Ольга Николаевна",
    "Канюкова": "Канюкова Сусанна Рауфовна",
    "Ширинкин": "Ширинкин Илья Дмитриевич",
    "Колосова": "Колосова Алина Васильевна",
    "Старцева": "Старцева Ксения Николаевна",
    "Стариева": "Старцева Ксения Николаевна",
    "Бурмина": "Бурмина Наталья Анатольевна",
    "Бармина": "Бурмина Наталья Анатольевна",
    "Бурунова": "Бурунова Наталья Владимировна",
    "Иванова": "Иванова Ирина Владимировна",
    "Сазонова": "Сазонова Татьяна Владимировна",
    "Шигапова": "Шигапова Оксана Руслановна",
}
ROOM_ALIASES = {
    "30 5": "305",
    "305a": "305а",
    "305а": "305а",
    "314a": "314а",
    "3 14": "314",
    "2 09": "209",
}

EXAM_LABELS = (
    "Экзамен",
    "Дифференцированный зачет",
    "Квалификационный экзамен",
    "Зачет",
)

GROUP_TOKEN_RE = re.compile(
    r"([А-ЯЁA-Z]{1,6})\s*[-–—\s]\s*(\d{2})\s*[-–—\s]\s*(\d{1,2})"
    r"(?:\s*[-–—\s]\s*(\d+))?",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(\d{1,2})\s*[.,]\s*(\d{1,2})\s*[.,]\s*(\d{2,4})")
TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[:.]\s*(\d{2})\s*[-–—]\s*(\d{1,2})\s*[:.]\s*(\d{2})"
)
TIME_ONE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[:.]\s*(\d{2})(?!\d)")
PAIR_TOKEN_RE = re.compile(
    r"\b([IVXІУГТП1]{1,4})\s*(?:пара|п\.)", re.IGNORECASE
)
COURSE_RE = re.compile(r"(\d)\s*курс", re.IGNORECASE)

_ROMAN_REPLACEMENTS = str.maketrans(
    {
        "Г": "I",
        "г": "I",
        "У": "V",
        "у": "V",
        "Т": "I",
        "т": "I",
        "П": "I",
        "п": "I",
        "І": "I",
        "і": "I",
        "Ї": "II",
        "ї": "II",
        "1": "I",
    }
)
_ROMAN_VALUES = {"IV": 4, "V": 5, "VI": 6, "VII": 7}


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split()).strip(" |_")


def _norm(text: str) -> str:
    """Нормализация только для сравнения, не для показа."""
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9]+", "", text)
    return text


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _group_key(group: str) -> str:
    return _norm(group.replace("зо", ""))


def extract_groups(text: str) -> list[str]:
    """Возвращает канонические группы из строки заголовка."""
    found: list[str] = []
    for match in GROUP_TOKEN_RE.finditer(text or ""):
        prefix, year, number, extra = match.groups()
        base = f"{prefix.upper()}-{year}-{number}"
        if extra:
            base += f"-{extra}"
        tail = (text or "")[match.end() : match.end() + 10].lower()
        has_zo = bool(re.search(r"(?:^|\s)(?:зо|30|з0|3о)(?:\s|$|\.|,)", tail))
        candidate = f"{base} зо" if has_zo else base
        group = correct_group(candidate)
        if group and group not in found:
            found.append(group)
    return found


def correct_group(raw: str) -> str | None:
    """Приводит группу к виду «ТД-24-9 зо», но не выдумывает новую."""
    text = _clean(raw)
    if not text:
        return None
    alias = GROUP_ALIASES.get(text.upper())
    if alias:
        return alias

    match = GROUP_TOKEN_RE.search(text.upper())
    if not match:
        return None
    prefix, year, number, extra = match.groups()
    base = f"{prefix}-{year}-{number}"
    if extra:
        base += f"-{extra}"

    # «30» в конце — почти всегда «зо». Добавляем суффикс и пробуем точно.
    candidate = f"{base} зо"
    for known in KNOWN_GROUPS:
        if _group_key(known) == _group_key(candidate):
            return known
    for known in KNOWN_GROUPS:
        if _group_key(known) == _group_key(base):
            return known

    scored = sorted(
        ((_ratio(candidate, known), known) for known in KNOWN_GROUPS),
        reverse=True,
    )
    if scored and scored[0][0] >= 0.82:
        return scored[0][1]
    # Неизвестную группу OCR не создаёт: иначе «В-95-3» из блика станет
    # расписанием. Новые группы приходят из названий ссылок и реестра.
    return None


def split_teacher_room(text: str) -> tuple[str | None, str | None]:
    """Делит «Фамилия И.О. / 305» или «... каб. 305» на преподавателя и кабинет."""
    text = _clean(text)
    if not text:
        return None, None
    if "/" in text:
        left, right = text.split("/", 1)
        return _clean(left) or None, _clean(right) or None
    match = re.search(r"(?:каб\.?|ауд\.?|кабинет)\s*№?\s*(.+)$", text, re.I)
    if match:
        teacher = _clean(text[: match.start()]).strip(" .,")
        return teacher or None, _clean(match.group(1)) or None
    return text, None


def correct_teacher(raw: str | None) -> str | None:
    text = _clean(raw or "")
    if not text:
        return None
    text = re.sub(r"^(?:преп\.?|преподаватель)\s*", "", text, flags=re.I)
    text = text.strip(" .,|")
    if not re.search(r"[А-ЯЁа-яё]", text):
        return None

    if text in TEACHER_ALIASES:
        return TEACHER_ALIASES[text]
    surname = text.split()[0] if text.split() else text
    if surname in TEACHER_ALIASES:
        return TEACHER_ALIASES[surname]

    best = max(KNOWN_TEACHERS, key=lambda known: _ratio(text, known))
    if _ratio(text, best) >= 0.90:
        return best

    # Фамилия распознана устойчиво, а имя/отчество — нет.
    same_surname = [
        known for known in KNOWN_TEACHERS
        if _norm(known.split()[0]) == _norm(surname)
    ]
    if len(same_surname) == 1:
        return same_surname[0]

    # Не подставляем чужую фамилию: неизвестного преподавателя оставляем.
    return text


def correct_subject(raw: str | None) -> str | None:
    text = _clean(raw or "")
    if not text:
        return None
    low = text.lower()
    if low.startswith("орг") and "собран" in low:
        return "ОРГ. СОБРАНИЕ"

    # Код предмета (МДК.01.01, ОП.05, ПП.02.01...) должен совпасть точно.
    code_match = re.match(r"^(МДК|ОП|ПП|УП|СГ|ОГСЭ)\.?\s*[\d.*]+", text, re.I)
    if code_match:
        code = _norm(code_match.group(0))
        same_code = [
            known for known in KNOWN_SUBJECTS
            if _norm(known).startswith(code)
        ]
        if len(same_code) == 1:
            return same_code[0]
        if same_code:
            best = max(same_code, key=lambda known: _ratio(text, known))
            if _ratio(text, best) >= 0.78:
                return best
        return text

    best = max(KNOWN_SUBJECTS, key=lambda known: _ratio(text, known))
    if _ratio(text, best) >= 0.88:
        return best
    # «Экзамен» и «Дифференцированный зачет» могут быть с уточнением.
    for label in EXAM_LABELS:
        if low.startswith(label.lower()) and len(text) > len(label):
            return text
    return text


def correct_room(raw: str | None) -> str | None:
    text = _clean(raw or "")
    if not text:
        return None
    text = re.sub(r"^(?:каб\.?|ауд\.?|кабинет|№)\s*", "", text, flags=re.I)
    text = text.strip(" .,|")
    alias = ROOM_ALIASES.get(text.lower())
    if alias:
        return alias

    # Только в номере кабинета: З/О — частая ошибка OCR.
    if re.search(r"\d", text):
        text = text.translate(str.maketrans({"З": "3", "з": "3", "О": "0", "о": "0"}))
        text = text.replace(" ", "")
    for known in KNOWN_ROOMS:
        if text.lower() == known.lower():
            return known
    if re.fullmatch(r"\d{2,3}[а-яa-z]?", text, re.I):
        return text.lower()
    if re.fullmatch(r"[A-Za-zА-Яа-я0-9/_-]{1,20}", text):
        return text
    return None


def parse_date(text: str) -> date | None:
    match = DATE_RE.search(text or "")
    if not match:
        return None
    day, month, year = (int(value) for value in match.groups())
    if year < 100:
        year += 2000
    if not (2020 <= year <= 2035):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_time_ranges(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for match in TIME_RANGE_RE.finditer(text or ""):
        start = f"{int(match.group(1)):02d}:{match.group(2)}"
        end = f"{int(match.group(3)):02d}:{match.group(4)}"
        result.append((start, end))
    return result


def parse_single_time(text: str) -> str | None:
    match = TIME_ONE_RE.search(text or "")
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def parse_pair_number(text: str) -> int | None:
    """Понимает IV/V/VI/VII и типичные ошибки OCR вроде «ГУ пара»."""
    match = PAIR_TOKEN_RE.search(text or "")
    if not match:
        return None
    token = match.group(1).upper().translate(_ROMAN_REPLACEMENTS)
    if token in _ROMAN_VALUES:
        return _ROMAN_VALUES[token]
    best = max(_ROMAN_VALUES, key=lambda roman: SequenceMatcher(None, token, roman).ratio())
    if SequenceMatcher(None, token, best).ratio() >= 0.5:
        return _ROMAN_VALUES[best]
    return None


def looks_like_exam_label(text: str) -> bool:
    low = (text or "").lower()
    return any(low.startswith(label.lower()) for label in EXAM_LABELS)


def detect_exam_label(text: str) -> str | None:
    """Ищет форму аттестации даже в зашумлённой строке OCR."""
    low = (text or "").lower()
    for label in sorted(EXAM_LABELS, key=len, reverse=True):
        if label.lower() in low:
            return label
    if "ференцирован" in low:
        return "Дифференцированный зачет"
    if "валификацион" in low:
        return "Квалификационный экзамен"
    if "кзамен" in low:
        return "Экзамен"
    return None


def find_known_subject(text: str) -> str | None:
    """Находит самый длинный известный предмет внутри строки OCR."""
    normalized = _norm(text)
    if not normalized:
        return None
    candidates = [
        known for known in KNOWN_SUBJECTS
        if known not in EXAM_LABELS
        and known != "ОРГ. СОБРАНИЕ"
        and _norm(known)
        and _norm(known) in normalized
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda known: len(_norm(known)))
