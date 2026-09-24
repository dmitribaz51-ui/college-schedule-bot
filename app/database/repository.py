"""Все запросы к базе собраны здесь (репозиторий)."""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import delete, func, select

from .database import get_session
from .models import Lesson, ScheduleFile, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- пользователи
def get_user(telegram_id: int) -> User | None:
    with get_session() as s:
        return s.scalar(select(User).where(User.telegram_id == telegram_id))


def get_or_create_user(
    telegram_id: int, username: str | None = None, full_name: str | None = None
) -> User:
    with get_session() as s:
        user = s.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(
                telegram_id=telegram_id, username=username, full_name=full_name
            )
            s.add(user)
        else:
            # username может отсутствовать — это нормально
            user.username = username
            if full_name:
                user.full_name = full_name
        s.flush()
        return user


def set_user_group(telegram_id: int, course: int | None, group_name: str) -> None:
    with get_session() as s:
        user = s.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(telegram_id=telegram_id)
            s.add(user)
        user.course = course
        user.group_name = group_name


def set_user_faculty(telegram_id: int, faculty: str) -> None:
    with get_session() as s:
        user = s.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(telegram_id=telegram_id)
            s.add(user)
        user.faculty = faculty


def set_notifications(telegram_id: int, enabled: bool) -> None:
    with get_session() as s:
        user = s.scalar(select(User).where(User.telegram_id == telegram_id))
        if user:
            user.notifications_enabled = enabled


def get_users_by_groups(groups: list[str], only_enabled: bool = True) -> list[User]:
    if not groups:
        return []
    with get_session() as s:
        stmt = select(User).where(User.group_name.in_(groups))
        if only_enabled:
            stmt = stmt.where(User.notifications_enabled.is_(True))
        return list(s.scalars(stmt))


def users_stats() -> dict:
    with get_session() as s:
        return {
            "total": s.scalar(select(func.count(User.id))) or 0,
            "with_group": s.scalar(
                select(func.count(User.id)).where(User.group_name.is_not(None))
            ) or 0,
            "notifications_on": s.scalar(
                select(func.count(User.id)).where(User.notifications_enabled.is_(True))
            ) or 0,
        }


def users_by_group() -> list[tuple[str, int]]:
    with get_session() as s:
        rows = s.execute(
            select(User.group_name, func.count(User.id))
            .where(User.group_name.is_not(None))
            .group_by(User.group_name)
            .order_by(func.count(User.id).desc())
        ).all()
        return [(r[0], r[1]) for r in rows]


# ------------------------------------------------------------------- файлы
def get_file_by_url(url: str) -> ScheduleFile | None:
    with get_session() as s:
        return s.scalar(select(ScheduleFile).where(ScheduleFile.url == url))


def file_hash_exists(file_hash: str, exclude_url: str | None = None) -> bool:
    with get_session() as s:
        stmt = select(func.count(ScheduleFile.id)).where(
            ScheduleFile.file_hash == file_hash, ScheduleFile.processed.is_(True)
        )
        if exclude_url:
            stmt = stmt.where(ScheduleFile.url != exclude_url)
        return (s.scalar(stmt) or 0) > 0


def upsert_file(
    *,
    title: str,
    url: str,
    file_type: str,
    schedule_date: date | None = None,
    file_hash: str | None = None,
    local_path: str | None = None,
    processed: bool = False,
    error: str | None = None,
    faculty: str = "permskaya",
) -> int:
    """Создаёт или обновляет запись о файле. Дубликаты по url невозможны."""
    with get_session() as s:
        record = s.scalar(select(ScheduleFile).where(ScheduleFile.url == url))
        if record is None:
            record = ScheduleFile(url=url, title=title, file_type=file_type, faculty=faculty)
            s.add(record)
        record.title = title
        record.file_type = file_type
        record.faculty = faculty
        record.schedule_date = schedule_date
        if file_hash:
            record.file_hash = file_hash
        if local_path:
            record.local_path = local_path
            record.downloaded_at = datetime.now()
        record.processed = processed
        record.error = error
        s.flush()
        return record.id


def get_processed_urls() -> set[str]:
    with get_session() as s:
        return set(
            s.scalars(select(ScheduleFile.url).where(ScheduleFile.processed.is_(True)))
        )


def get_recent_files(limit: int = 10, file_type: str | None = None) -> list[ScheduleFile]:
    with get_session() as s:
        stmt = select(ScheduleFile).order_by(ScheduleFile.id.desc()).limit(limit)
        if file_type:
            stmt = select(ScheduleFile).where(
                ScheduleFile.file_type == file_type
            ).order_by(ScheduleFile.id.desc()).limit(limit)
        return list(s.scalars(stmt))


def count_files() -> int:
    with get_session() as s:
        return s.scalar(select(func.count(ScheduleFile.id))) or 0


# ------------------------------------------------------------------- уроки
def replace_group_lessons(
    *,
    schedule_date: date,
    group_name: str,
    source_type: str,
    course: int | None,
    lessons: list[dict],
    source_file_id: int | None = None,
    faculty: str = "permskaya",
) -> int:
    """Заменяет расписание группы на конкретную дату (без дублей)."""
    with get_session() as s:
        s.execute(
            delete(Lesson).where(
                Lesson.schedule_date == schedule_date,
                Lesson.group_name == group_name,
                Lesson.source_type == source_type,
                Lesson.faculty == faculty,
            )
        )
        saved = 0
        for item in lessons:
            s.add(
                Lesson(
                    schedule_date=schedule_date,
                    group_name=group_name,
                    course=course,
                    lesson_number=item["number"],
                    subject=item.get("subject"),
                    teacher=item.get("teacher"),
                    room=item.get("room"),
                    notes=item.get("notes"),
                    source_type=source_type,
                    source_file_id=source_file_id,
                    faculty=faculty,
                )
            )
            saved += 1
        return saved


def get_lessons(schedule_date: date, group_name: str, faculty: str = "permskaya") -> list[Lesson]:
    with get_session() as s:
        return list(
            s.scalars(
                select(Lesson)
                .where(
                    Lesson.schedule_date == schedule_date,
                    Lesson.group_name == group_name,
                    Lesson.faculty == faculty,
                )
                .order_by(Lesson.lesson_number)
            )
        )


def get_groups(course: int | None = None, faculty: str = "permskaya") -> list[str]:
    """Список известных групп (из разобранных файлов)."""
    with get_session() as s:
        if course is not None:
            # Для 4 курса фильтруем по году в названии группы: -23-
            # 2026 год, 4 курс = поступление в 2023 году
            year_suffix = str(2026 - course + 1)[-2:]  # 4 курс -> "23"
            
            rows = s.scalars(
                select(Lesson.group_name)
                .where(Lesson.group_name.like(f"%-{year_suffix}-%"))
                .where(Lesson.faculty == faculty)
                .distinct()
                .order_by(Lesson.group_name)
            )
            groups = list(rows)
            if groups:
                return groups
        return list(
            s.scalars(select(Lesson.group_name).where(Lesson.faculty == faculty).distinct().order_by(Lesson.group_name))
        )


def count_lessons() -> int:
    with get_session() as s:
        return s.scalar(select(func.count(Lesson.id))) or 0


def available_dates(group_name: str, limit: int = 7, faculty: str = "permskaya") -> list[date]:
    with get_session() as s:
        return list(
            s.scalars(
                select(Lesson.schedule_date)
                .where(Lesson.group_name == group_name)
                .where(Lesson.faculty == faculty)
                .distinct()
                .order_by(Lesson.schedule_date.desc())
                .limit(limit)
            )
        )
