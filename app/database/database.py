"""Инициализация SQLite и выдача сессий."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory: sessionmaker[Session] | None = None


def init_db(database_url: str) -> None:
    """Создаёт движок и таблицы. Вызывать один раз при старте."""
    global _engine, _session_factory
    _engine = create_engine(database_url, echo=False, future=True)
    _session_factory = sessionmaker(
        bind=_engine, expire_on_commit=False, class_=Session
    )
    Base.metadata.create_all(_engine)
    with _engine.begin() as connection:
        for table in ("users", "lessons", "schedule_files"):
            columns = {column["name"] for column in inspect(_engine).get_columns(table)}
            if "faculty" not in columns:
                connection.execute(
                    text(
                        f"ALTER TABLE {table} "
                        "ADD COLUMN faculty VARCHAR(32) DEFAULT 'permskaya'"
                    )
                )
        lesson_columns = {
            column["name"]
            for column in inspect(_engine).get_columns("lessons")
        }
        if "start_time" not in lesson_columns:
            connection.execute(
                text("ALTER TABLE lessons ADD COLUMN start_time VARCHAR(5)")
            )
        if "end_time" not in lesson_columns:
            connection.execute(
                text("ALTER TABLE lessons ADD COLUMN end_time VARCHAR(5)")
            )
        schedule_file_columns = {
            column["name"]
            for column in inspect(_engine).get_columns("schedule_files")
        }
        if "last_checked" not in schedule_file_columns:
            connection.execute(
                text(
                    "ALTER TABLE schedule_files "
                    "ADD COLUMN last_checked DATETIME"
                )
            )
        if "telegram_file_id" not in schedule_file_columns:
            connection.execute(
                text(
                    "ALTER TABLE schedule_files "
                    "ADD COLUMN telegram_file_id VARCHAR(255)"
                )
            )
        if "review_required" not in schedule_file_columns:
            connection.execute(
                text(
                    "ALTER TABLE schedule_files "
                    "ADD COLUMN review_required BOOLEAN DEFAULT 0"
                )
            )
        if "retry_count" not in schedule_file_columns:
            connection.execute(
                text(
                    "ALTER TABLE schedule_files "
                    "ADD COLUMN retry_count INTEGER DEFAULT 0"
                )
            )
        if "alerted_hash" not in schedule_file_columns:
            connection.execute(
                text(
                    "ALTER TABLE schedule_files "
                    "ADD COLUMN alerted_hash VARCHAR(64)"
                )
            )
        # OCR до исправления словаря подставлял «горно» вместо «торгово»
        # в предмете «Автоматизация ... процессов». Правим только заочные
        # группы ТД, чтобы не затронуть очное отделение.
        connection.execute(
            text(
                "UPDATE lessons SET subject = :correct "
                "WHERE subject = :wrong "
                "AND group_name LIKE 'ТД-% зо'"
            ),
            {
                "correct": "Автоматизация торгово-технологических процессов",
                "wrong": "Автоматизация горно-технологических процессов",
            },
        )
        # Заочные PDF, которые упали до включения OCR или после смены парсера,
        # не должны навсегда оставаться «обработанными». Сбрасываем им статус
        # и время проверки: первый же цикл перечитает файл. Повторные неудачи
        # ограничены интервалами 1 ч / 3 ч в update_service.
        connection.execute(
            text(
                "UPDATE schedule_files "
                "SET processed = 0, last_checked = NULL, retry_count = 0 "
                "WHERE faculty = 'zaochnoe' "
                "AND error IS NOT NULL "
                "AND error NOT LIKE 'Дубликат%'"
            )
        )
        # Один раз перечитываем 26.10.2026: до исправления OCR склеивал
        # 5-ю и 6-ю пары (13:50–16:00 вместо 13:50–14:50 / 15:00–16:00).
        # Маркер в app_meta не даёт запускать тяжёлый OCR при каждом старте.
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS app_meta ("
                "key TEXT PRIMARY KEY, value TEXT)"
            )
        )
        applied = connection.execute(
            text("SELECT value FROM app_meta WHERE key = :key"),
            {"key": "ocr_time_fix_2026_11"},
        ).scalar()
        if applied is None:
            connection.execute(
                text(
                    "UPDATE schedule_files "
                    "SET processed = 0, error = NULL, last_checked = NULL "
                    "WHERE faculty = 'zaochnoe' "
                    "AND schedule_date = :day"
                ),
                {"day": "2026-10-26"},
            )
            connection.execute(
                text(
                    "INSERT INTO app_meta (key, value) "
                    "VALUES (:key, :value)"
                ),
                {"key": "ocr_time_fix_2026_11", "value": "1"},
            )
        # Один раз перечитываем все заочные PDF 2027 года. Они появились на
        # сайте 02.11.2026, но могли остаться processed=True без пар: старая
        # версия помечала скан обработанным даже при ошибке OCR. Общий сброс
        # выше лечит только строки с error; этот — гарантирует повторный
        # разбор и для строк без ошибки, но с пустым результатом.
        applied_2027 = connection.execute(
            text("SELECT value FROM app_meta WHERE key = :key"),
            {"key": "zao_2027_recheck_2026_11"},
        ).scalar()
        if applied_2027 is None:
            connection.execute(
                text(
                    "UPDATE schedule_files "
                    "SET processed = 0, error = NULL, last_checked = NULL "
                    "WHERE faculty = 'zaochnoe' "
                    "AND schedule_date >= :day"
                ),
                {"day": "2027-01-01"},
            )
            connection.execute(
                text(
                    "INSERT INTO app_meta (key, value) "
                    "VALUES (:key, :value)"
                ),
                {"key": "zao_2027_recheck_2026_11", "value": "1"},
            )
        broadcast_columns = {
            column["name"]
            for column in inspect(_engine).get_columns("broadcasts")
        }
        if "text_format" not in broadcast_columns:
            connection.execute(
                text(
                    "ALTER TABLE broadcasts "
                    "ADD COLUMN text_format VARCHAR(16) DEFAULT 'plain'"
                )
            )
    logger.info("База данных готова: %s", database_url)


@contextmanager
def get_session() -> Iterator[Session]:
    if _session_factory is None:
        raise RuntimeError("init_db() не вызван — база не инициализирована")
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
