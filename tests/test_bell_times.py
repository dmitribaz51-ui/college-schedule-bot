"""Регрессионные тесты звонков из исходного Excel (app/services/bell_times.py).

Границы: строгий парсинг ячеек, одиночное 11:10 — не «по часу», неоднозначные
концы 2-й пары, конфликт листов/блоков без смешивания, реальные файлы,
сохранённые правила сообщений 02/03.10.2026. Запуск: runpy, как остальные тесты.
"""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpyxl import Workbook

from app.services import bell_times, schedule_service
from app.services.bell_times import (
    _BellBlock,
    _agreeing_ends,
    _lesson_ranges,
    _parse_bell_cell,
    _read_xls_blocks,
    _read_xlsx_blocks,
    _resolve_block,
    get_lesson_time_range,
)
from app.utils import LESSON_TIMES_CHERN_SATURDAY, LESSON_TIMES_SATURDAY, get_lesson_times

BASE = Path(__file__).resolve().parent.parent


def make_xlsx(path: Path, sheets: dict[str, list[tuple[str, str]]]) -> Path:
    """Синтетический XLSX: {лист: [(группа, ...)], ...} + колонка звонков B.

    sheets: {название листа: ([тексты звонков B12, B14, ...], [группы строки 10])}.
    """
    workbook = Workbook()
    first = True
    for title, (bells, groups) in sheets.items():
        sheet = workbook.active if first else workbook.create_sheet(title)
        first = False
        sheet.title = title
        sheet.cell(row=9, column=2, value="День недели")
        for index, group in enumerate(groups, start=3):
            sheet.cell(row=10, column=index, value=group)
        for index, text in enumerate(bells):
            sheet.cell(row=12 + index * 2, column=2, value=text)
    workbook.save(path)
    workbook.close()
    return path


def ranges_of(path: Path, group: str = "") -> dict:
    return _lesson_ranges(str(path.resolve()), path.stat().st_mtime_ns, group)


def blocks_of(path: Path) -> list:
    if path.suffix.lower() == ".xls":
        return _read_xls_blocks(path)
    return _read_xlsx_blocks(path)


def test_parse_bell_cell_variants() -> None:
    assert _parse_bell_cell("I 08:30") == (1, "08:30", None)
    assert _parse_bell_cell("V\n13:50") == (5, "13:50", None)
    assert _parse_bell_cell(" I \n8.30\n") == (1, "08:30", None)
    assert _parse_bell_cell("Vl \n15.10\n") == (6, "15:10", None)
    assert _parse_bell_cell("III                     11:10") == (3, "11:10", None)
    assert _parse_bell_cell("II 09:40-10:40") == (2, "09:40", "10:40")
    # Не звонки: чистое время (классный час), предметы, группы, мусор.
    assert _parse_bell_cell("08:30:00") is None
    assert _parse_bell_cell("14:10") is None
    assert _parse_bell_cell("14:10:00") is None
    assert _parse_bell_cell("Математика") is None
    assert _parse_bell_cell("ТД-26-9") is None
    assert _parse_bell_cell("с 10.30 ВПР") is None
    assert _parse_bell_cell("") is None
    assert _parse_bell_cell(None) is None
    # Невалидные времена и номера.
    assert _parse_bell_cell("I 25:00") is None
    assert _parse_bell_cell("I 08:75") is None
    assert _parse_bell_cell("IX 08:30") is None
    assert _parse_bell_cell("0 08:30") is None


def test_hourly_block_matches_saturday() -> None:
    with TemporaryDirectory() as directory:
        path = make_xlsx(
            Path(directory) / "hourly.xlsx",
            {"1 курс": (["I 08:30", "II 09:40", "III 11:10", "IV 12:25", "V 13:50", "VI 15:00"],
                        ["ТД-26-9"])},
        )
        assert ranges_of(path) == {
            1: ("08:30", "09:30"), 2: ("09:40", "10:40"), 3: ("11:10", "12:10"),
            4: ("12:25", "13:25"), 5: ("13:50", "14:50"), 6: ("15:00", "16:00"),
        }


def test_chern_saturday_block() -> None:
    with TemporaryDirectory() as directory:
        path = make_xlsx(
            Path(directory) / "chern_sat.xlsx",
            {"1 курс": (["I 08:30", "II 09:40", "III 11:10", "IV 12:30", "V 14:00", "VI 15:10"],
                        ["ГД-26-9"])},
        )
        assert ranges_of(path) == {
            1: ("08:30", "09:30"), 2: ("09:40", "10:40"), 3: ("11:10", "12:10"),
            4: ("12:30", "13:30"), 5: ("14:00", "15:00"), 6: ("15:10", "16:10"),
        }


def test_single_1110_is_not_hourly() -> None:
    """Одиночное 11:10 совпадает с началом 3-й пары двух субботних профилей,
    но блок короче MIN_BLOCK — профиль не подбираем, выдуманного конца нет."""
    assert _agreeing_ends({3: "11:10"}) == {3: "12:10"}  # без защиты было бы так
    with TemporaryDirectory() as directory:
        path = make_xlsx(
            Path(directory) / "single.xlsx",
            {"1 курс": (["III 11:10"], ["ТД-26-9"])},
        )
        assert ranges_of(path) == {}
        path2 = make_xlsx(
            Path(directory) / "two.xlsx",
            {"1 курс": (["I 08:30", "III 11:10"], ["ТД-26-9"])},
        )
        assert ranges_of(path2) == {}


def test_ambiguous_second_lesson_ends() -> None:
    """Черн-будни: уроки 2 расходятся (11:50 vs 11:30) — 2-я пара без конца,
    остальные берутся из файла. Понедельник Пермь: то же для 12:15 vs 12:40."""
    chern_tue = {1: "08:30", 2: "10:05", 3: "12:00", 4: "14:15", 5: "16:00", 6: "17:35"}
    ends = _agreeing_ends(chern_tue)
    assert ends[2] is None
    assert ends[1] == "09:55" and ends[4] == "15:40"
    perm_mon = {1: "09:15", 2: "10:50", 3: "12:45", 4: "14:45", 5: "16:30", 6: "18:05"}
    ends = _agreeing_ends(perm_mon)
    assert ends[2] is None
    assert ends[1] == "10:40" and ends[3] == "14:10"


def test_unknown_starts_return_empty() -> None:
    """Неизвестный start-only профиль — честный пустой результат, не выдумка."""
    with TemporaryDirectory() as directory:
        path = make_xlsx(
            Path(directory) / "unknown.xlsx",
            {"1 курс": (["I 09:05", "II 10:15", "III 11:55", "IV 13:05", "V 14:45", "VI 16:15"],
                        ["ТД-26-9"])},
        )
        assert ranges_of(path) == {}


def test_explicit_ends_used_without_profile() -> None:
    """Явные концы в файле приоритетны и работают без известного профиля."""
    with TemporaryDirectory() as directory:
        path = make_xlsx(
            Path(directory) / "explicit.xlsx",
            {"1 курс": (["I 09:05-09:50", "II 10:00-10:45", "III 11:00-11:45"],
                        ["ТД-26-9"])},
        )
        assert ranges_of(path) == {
            1: ("09:05", "09:50"), 2: ("10:00", "10:45"), 3: ("11:00", "11:45"),
        }


def test_subject_columns_are_not_bell_columns() -> None:
    """Колонка с предметами/временем в тексте не становится колонкой звонков."""
    with TemporaryDirectory() as directory:
        path = Path(directory) / "subjects.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.title = "1 курс"
        sheet.cell(row=10, column=3, value="ТД-26-9")
        sheet.cell(row=12, column=2, value="I 08:30")
        sheet.cell(row=12, column=3, value="Математика")
        sheet.cell(row=13, column=3, value="с 10.30 ВПР")
        sheet.cell(row=14, column=3, value="II вариант")
        workbook.save(path)
        workbook.close()
        # Одна ячейка звонков — ниже MIN_COLUMN_CELLS: блоков нет вообще.
        assert blocks_of(path) == []
        assert ranges_of(path) == {}


def test_conflicting_sheets_attributed_by_group() -> None:
    """Листы с разными звонками не смешиваются: блок выбирается по группе."""
    saturday = ["I 08:30", "II 09:40", "III 11:10", "IV 12:25", "V 13:50", "VI 15:00"]
    weekday = ["I 08:30", "II 10:05", "III 12:00", "IV 14:05", "V 16:00", "VI 17:35"]
    with TemporaryDirectory() as directory:
        path = make_xlsx(
            Path(directory) / "conflict.xlsx",
            {
                "1 курс": (saturday, ["ТД-26-9"]),
                "2 курс": (weekday, ["ТД-25-9"]),
            },
        )
        assert ranges_of(path, "ТД-25-9")[4] == ("14:05", "15:30")
        assert ranges_of(path, "ТД-26-9")[4] == ("12:25", "13:25")
        assert ranges_of(path, "НЕИЗВЕСТНАЯ-ГРУППА") == {}
        assert ranges_of(path) == {}


def test_resolve_block_attribution_logic() -> None:
    """Покрытие ветки атрибуции без файлов: две зоны, группа в одной."""
    blocks = [
        _BellBlock(sheet="лист", zone=(1, 5), starts={1: "08:30", 2: "09:40", 3: "11:10"}, explicit={}),
        _BellBlock(sheet="лист", zone=(6, 10), starts={1: "08:30", 2: "10:05", 3: "12:00"}, explicit={}),
    ]
    with patch.object(bell_times, "_find_group_locations", return_value=[("лист", 8)]):
        resolved = _resolve_block(blocks, Path("dummy.xlsx"), "ГРУППА")
    assert resolved is not None and resolved.starts[2] == "10:05"
    with patch.object(bell_times, "_find_group_locations", return_value=[]):
        assert _resolve_block(blocks, Path("dummy.xlsx"), "ГРУППА") is None
    assert _resolve_block(blocks, Path("dummy.xlsx"), "") is None


def test_missing_and_broken_sources_are_safe() -> None:
    assert get_lesson_time_range(None, "ТД-26-9", 1) is None
    assert get_lesson_time_range(0, "ТД-26-9", 1) is None
    with TemporaryDirectory() as directory:
        missing = Path(directory) / "нет-такого-файла.xlsx"
        assert _lesson_ranges(str(missing), 0, "ТД-26-9") == {}
        broken = Path(directory) / "broken.xlsx"
        broken.write_bytes(b"not an excel file at all")
        assert _lesson_ranges(str(broken), broken.stat().st_mtime_ns, "ТД-26-9") == {}


def test_real_permskaya_tuesday_hourly() -> None:
    path = BASE / "data/excel/2026-09-08_schedule.xlsx"
    if not path.is_file():
        print("  (пропущено: нет локального файла)")
        return
    assert ranges_of(path) == {
        1: ("08:30", "09:30"), 2: ("09:40", "10:40"), 3: ("11:10", "12:10"),
        4: ("12:25", "13:25"), 5: ("13:50", "14:50"), 6: ("15:00", "16:00"),
    }


def test_real_permskaya_monday() -> None:
    """Понедельник 28.09: 2-я пара неоднозначна (12:15 vs 12:40) — её нет,
    остальные начала из файла с концами профиля."""
    path = BASE / "data/excel/permskaya/2026-09-28_schedule.xlsx"
    if not path.is_file():
        print("  (пропущено: нет локального файла)")
        return
    assert ranges_of(path) == {
        1: ("09:15", "10:40"), 3: ("12:45", "14:10"), 4: ("14:45", "16:10"),
        5: ("16:30", "17:55"), 6: ("18:05", "19:30"),
    }


def test_real_chern_saturday() -> None:
    """Суббота Чернышевского: свой профиль 60-минутных пар, не Пермь-суббота."""
    path = BASE / "data/excel/chernyshevskogo/2026-09-26_schedule.xls"
    if not path.is_file():
        print("  (пропущено: нет локального файла)")
        return
    blocks = blocks_of(path)
    zones = sorted(block.zone for block in blocks)
    assert (1, 3) in zones and (10, 12) in zones  # два сегмента курсов
    assert ranges_of(path) == {
        1: ("08:30", "09:30"), 2: ("09:40", "10:40"), 3: ("11:10", "12:10"),
        4: ("12:30", "13:30"), 5: ("14:00", "15:00"), 6: ("15:10", "16:10"),
    }


def test_real_chern_weekday() -> None:
    """Будни Чернышевского: 2-я пара на fallback по курсу, остальные из файла."""
    path = BASE / "data/excel/chernyshevskogo/2026-09-22_schedule.xls"
    if not path.is_file():
        print("  (пропущено: нет локального файла)")
        return
    assert ranges_of(path) == {
        1: ("08:30", "09:55"), 3: ("12:00", "13:25"), 4: ("14:15", "15:40"),
        5: ("16:00", "17:25"), 6: ("17:35", "19:00"),
    }


def test_oct2_override_preserved() -> None:
    assert get_lesson_times(date(2026, 10, 2)) == LESSON_TIMES_SATURDAY
    assert get_lesson_times(date(2026, 10, 2), "chernyshevskogo") == LESSON_TIMES_CHERN_SATURDAY
    # Суббота 03.10.2026: на Чернышевского учатся — fallback по часу из расписания,
    # время/кабинет берутся из файла (bell_times), здесь — из таблицы.
    assert get_lesson_times(date(2026, 10, 3), "chernyshevskogo") == LESSON_TIMES_CHERN_SATURDAY
    assert get_lesson_times(date(2026, 10, 3)) == LESSON_TIMES_SATURDAY


def test_oct2_note_preserved() -> None:
    with patch.object(schedule_service.repo, "get_lessons", return_value=[]):
        text = schedule_service.format_day_schedule("ТД-24-9", date(2026, 10, 2))
    assert "по часу" in text


def test_oct3_no_holiday_anymore() -> None:
    """03.10.2026 (суббота) — выходной для всех факультетов."""
    with patch.object(schedule_service.repo, "get_lessons", return_value=[]):
        text = schedule_service.format_day_schedule("ТД-24-9", date(2026, 10, 3))
    assert "Сегодня выходной" in text
    with patch.object(schedule_service.repo, "get_lessons", return_value=[]):
        chern = schedule_service.format_day_schedule("ГД-25-9", date(2026, 10, 3), "chernyshevskogo")
    assert "Сегодня выходной" in chern
