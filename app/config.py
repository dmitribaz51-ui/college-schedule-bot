"""Конфигурация проекта. Все секреты берутся только из .env"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_PAGE_URL = (
    "https://pkps-perm.ru/students/raspisanie/"
    "fakultet_predprinimatelstva_ul_permskaya_226/"
)
CHERNYSHEVSKOGO_PAGE_URL = (
    "https://pkps-perm.ru/students/raspisanie/"
    "fakultet_dizayna_i_servisa_ul_chernyshevskogo_11/"
)


def _parse_admin_ids(raw: str | None) -> list[int]:
    """'123, 456; 789' -> [123, 456, 789]"""
    ids: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.append(int(part))
    return ids


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: list[int]
    schedule_page_url: str
    chernyshevskogo_page_url: str
    check_interval_minutes: int
    data_dir: Path
    excel_dir: Path
    database_url: str
    log_level: str

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids


@lru_cache
def get_config() -> Config:
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не найден. Скопируйте .env.example в .env и вставьте токен."
        )

    data_dir = BASE_DIR / "data"
    excel_dir = data_dir / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)

    try:
        interval = int(os.getenv("CHECK_INTERVAL_MINUTES", "15"))
    except ValueError:
        interval = 15
    interval = max(5, min(interval, 120))

    return Config(
        bot_token=token,
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS")),
        schedule_page_url=(os.getenv("SCHEDULE_PAGE_URL") or DEFAULT_PAGE_URL).strip(),
        chernyshevskogo_page_url=(os.getenv("CHERNYSHEVSKOGO_PAGE_URL") or CHERNYSHEVSKOGO_PAGE_URL).strip(),
        check_interval_minutes=interval,
        data_dir=data_dir,
        excel_dir=excel_dir,
        database_url=os.getenv("DATABASE_URL") or f"sqlite:///{data_dir / 'bot.db'}",
        log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
    )
