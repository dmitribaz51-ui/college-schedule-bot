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
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN faculty VARCHAR(32) DEFAULT 'permskaya'"))
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
