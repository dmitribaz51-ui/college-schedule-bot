"""Время классного часа из исходного Excel, независимо от номера записи в БД."""
from datetime import time
from functools import lru_cache
from pathlib import Path
import re

from openpyxl import load_workbook

from app.database.database import get_session
from app.database.models import ScheduleFile
from app.utils import normalize_group

# Время классного часа по сменам (из Excel берётся только начало).
CLASS_HOUR_RANGES: dict[str, tuple[str, str]] = {
    "08:30": ("08:30", "09:10"),  # 1 смена
    "14:10": ("14:10", "14:45"),  # 2 смена
}


def get_class_hour_range(file_id: int | None, group: str) -> tuple[str, str] | None:
    """(начало, конец) классного часа для конкретной группы, None если не найдено."""
    start = get_class_hour_start(file_id, group)
    if start is None:
        return None
    return CLASS_HOUR_RANGES.get(start, (start, start))


def get_class_hour_start(file_id: int | None, group: str) -> str | None:
    if not file_id:
        return None
    with get_session() as session:
        source = session.get(ScheduleFile, file_id)
        local_path = source.local_path if source else None
    if not local_path:
        return None
    path = Path(local_path)
    if not path.is_file():
        return None
    if path.suffix.lower() == ".xls":
        # Старый формат читается xlrd, а не openpyxl — время классного часа
        # для таких файлов берётся из fallback по номеру записи.
        return None
    return _read_start(str(path.resolve()), path.stat().st_mtime_ns, group)


@lru_cache(maxsize=256)
def _read_start(path: str, modified: int, group: str) -> str | None:
    workbook = load_workbook(path, data_only=True)
    try:
        for sheet in workbook:
            column = None
            for row in sheet:
                for cell in row:
                    if normalize_group(str(cell.value or "")) == group:
                        column = cell.column
                if column is None or column > len(row):
                    continue
                if "классный час" not in str(row[column - 1].value or "").lower():
                    continue
                for cell in reversed(row[:column - 1]):
                    value = cell.value
                    if isinstance(value, time):
                        return value.strftime("%H:%M")
                    match = re.search(r"\b(\d{1,2}:\d{2})(?::\d{2})?\b", str(value or ""))
                    if match:
                        return match.group(1).zfill(5)
    finally:
        workbook.close()
    return None
