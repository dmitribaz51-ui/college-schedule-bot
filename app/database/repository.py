"""Все запросы к базе собраны здесь (репозиторий)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, update

from .database import get_session
from .models import Group, Lesson, ScheduleFile, User

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


def get_all_users() -> list[User]:
    """Возвращает всех зарегистрированных пользователей для ручной рассылки."""
    with get_session() as s:
        return list(s.scalars(select(User).order_by(User.telegram_id)))


def get_notification_users() -> list[User]:
    """Возвращает пользователей с включёнными уведомлениями."""
    with get_session() as s:
        return list(
            s.scalars(
                select(User)
                .where(User.notifications_enabled.is_(True))
                .order_by(User.telegram_id)
            )
        )


def get_group_users(group_name: str) -> list[User]:
    """Возвращает всех пользователей конкретной группы."""
    with get_session() as s:
        return list(
            s.scalars(
                select(User)
                .where(User.group_name == group_name)
                .order_by(User.telegram_id)
            )
        )


def find_user_by_username(username: str) -> User | None:
    """Ищет пользователя по username (без @)."""
    normalized = username.lstrip("@").lower()
    with get_session() as s:
        return s.scalar(
            select(User).where(func.lower(User.username) == normalized)
        )


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
    review_required: bool | None = None,
    retry_count: int | None = None,
    update_check_time: bool = False,
) -> int:
    """Создаёт или обновляет запись о файле. Дубликаты по url невозможны.

    ``review_required``/``retry_count`` со значением ``None`` сохраняют
    текущее значение: обычное обновление статуса не должно случайно
    снимать «нужна проверка» или сбрасывать счётчик попыток.
    """
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
            if record.file_hash and record.file_hash != file_hash:
                # Содержимое файла изменилось — сбрасываем кэшированный file_id
                # и все пометки предыдущей версии PDF.
                record.telegram_file_id = None
                record.review_required = False
                record.retry_count = 0
                record.alerted_hash = None
            record.file_hash = file_hash
        if local_path:
            record.local_path = local_path
            record.downloaded_at = datetime.now()
        if update_check_time:
            record.last_checked = datetime.now()
        record.processed = processed
        record.error = error
        if review_required is not None:
            record.review_required = review_required
        if retry_count is not None:
            record.retry_count = retry_count
        s.flush()
        return record.id


def mark_file_alerted(url: str, file_hash: str) -> None:
    """Запоминает версию PDF, по которой админам уже отправлена тревога."""
    with get_session() as s:
        record = s.scalar(select(ScheduleFile).where(ScheduleFile.url == url))
        if record is not None:
            record.alerted_hash = file_hash


def clear_file_alert(url: str) -> None:
    with get_session() as s:
        record = s.scalar(select(ScheduleFile).where(ScheduleFile.url == url))
        if record is not None:
            record.alerted_hash = None


def get_processed_urls() -> set[str]:
    with get_session() as s:
        return set(
            s.scalars(select(ScheduleFile.url).where(ScheduleFile.processed.is_(True)))
        )


def get_urls_needing_recheck(minutes: int = 25) -> set[str]:
    """Возвращает URL обработанных файлов, которые не проверялись более N минут."""
    with get_session() as s:
        threshold = datetime.now() - timedelta(minutes=minutes)
        from sqlalchemy import or_
        return set(
            s.scalars(
                select(ScheduleFile.url)
                .where(
                    ScheduleFile.processed.is_(True),
                    or_(
                        ScheduleFile.last_checked.is_(None),
                        ScheduleFile.last_checked < threshold
                    )
                )
            )
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


def _zao_problem_condition():
    """Заочные PDF, которые не удалось применить: ошибка/проверка/не обработан.

    «Дубликат» — не проблема: такой PDF уже разобран под другим URL.
    """
    not_duplicate = or_(
        ScheduleFile.error.is_(None),
        ~ScheduleFile.error.like("Дубликат%"),
    )
    return and_(
        ScheduleFile.faculty == "zaochnoe",
        not_duplicate,
        or_(
            ScheduleFile.processed.is_(False),
            ScheduleFile.review_required.is_(True),
            and_(
                ScheduleFile.error.is_not(None),
                ~ScheduleFile.error.like("Дубликат%"),
            ),
        ),
    )


def get_zao_problem_files() -> list[ScheduleFile]:
    """Все заочные файлы, требующие повторного разбора или проверки."""
    with get_session() as s:
        return list(
            s.scalars(
                select(ScheduleFile)
                .where(_zao_problem_condition())
                .order_by(ScheduleFile.schedule_date.desc(), ScheduleFile.id.desc())
            )
        )


def count_zao_problem_files() -> int:
    with get_session() as s:
        return s.scalar(
            select(func.count(ScheduleFile.id)).where(_zao_problem_condition())
        ) or 0


def reset_zao_problem_files() -> int:
    """Сбрасывает только проблемные заочные PDF перед точечным перечитыванием."""
    with get_session() as s:
        result = s.execute(
            update(ScheduleFile)
            .where(_zao_problem_condition())
            .values(
                processed=False,
                error=None,
                review_required=False,
                retry_count=0,
                alerted_hash=None,
                last_checked=None,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount or 0


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
) -> tuple[int, bool]:
    """Заменяет расписание группы на конкретную дату (без дублей)."""
    with get_session() as s:
        # Для определения changed: сравниваем с ЛЮБЫМ источником (schedule/changes)
        # Раньше запрос фильтровал WHERE source_type == source_type, и файл изменений
        # сравнивался только с предыдущим файлом изменений, а не с базовым расписанием.
        # Результат: идентичный файл изменений считался "новым" → ложные уведомления.
        old_lessons_for_comparison = list(
            s.scalars(
                select(Lesson)
                .where(
                    Lesson.schedule_date == schedule_date,
                    Lesson.group_name == group_name,
                )
                .order_by(Lesson.lesson_number)
            )
        )
        # Как и при показе расписания, changes имеет приоритет над schedule.
        # Первый changes-файл сравнивается с базовым расписанием этой группы.
        existing_changes = [
            item for item in old_lessons_for_comparison
            if item.source_type == "changes"
        ]
        comparison_lessons = existing_changes or old_lessons_for_comparison
        # Базовый файл не меняет отображаемые пары, пока есть changes-файл.
        base_is_hidden = source_type == "schedule" and bool(existing_changes)
        if not comparison_lessons:
            logger.info(
                "Новое расписание для %s на %s (source_type=%s): %d пар",
                group_name, schedule_date, source_type, len(lessons)
            )
            changed = True
        else:
            # Сравниваем как СПИСОК ПАР ПО НОМЕРАМ, а не позиционно: порядок
            # пар во входных данных не должен влиять на результат. Раньше
            # списки сравнивались как есть, и расписание с классным часом
            # посередине ([3, 90, 4, 5]) всегда отличалось от базы, где пары
            # лежат по lesson_number, — отсюда уведомления без изменений.
            new_by_number = {
                item["number"]: (
                    item.get("subject"), item.get("teacher"),
                    item.get("room"), item.get("notes"),
                    item.get("start_time"), item.get("end_time"),
                )
                for item in lessons
            }
            old_by_number = {
                item.lesson_number: (
                    item.subject, item.teacher, item.room, item.notes,
                    item.start_time, item.end_time,
                )
                for item in comparison_lessons
            }
            old_numbers = sorted(old_by_number)
            new_numbers = sorted(new_by_number)
            old_faculty_set = {item.faculty for item in comparison_lessons}
            faculty_changed = old_faculty_set != {faculty}

            if base_is_hidden:
                new_by_number = dict(old_by_number)
                new_numbers = sorted(new_by_number)
                faculty_changed = False
            content_changed = old_by_number != new_by_number
            changed = content_changed or faculty_changed

            if changed:
                if content_changed:
                    logger.info(
                        "Изменения в расписании %s на %s (source_type=%s): содержание пар изменилось",
                        group_name, schedule_date, source_type
                    )
                    # Показываем разницу только для первых 3 пар
                    shown = 0
                    for number in sorted(set(old_numbers) | set(new_numbers)):
                        old = old_by_number.get(number)
                        new = new_by_number.get(number)
                        if old == new or shown >= 3:
                            continue
                        logger.info("  Пара %s: БЫЛО %s", number, old)
                        logger.info("  Пара %s: СТАЛО %s", number, new)
                        shown += 1
                    if len(old_numbers) != len(new_numbers):
                        logger.info(
                            "  Количество пар: БЫЛО %d, СТАЛО %d",
                            len(old_numbers), len(new_numbers),
                        )
                if faculty_changed:
                    logger.info(
                        "Изменения в расписании %s на %s (source_type=%s): faculty изменился (%s -> %s)",
                        group_name, schedule_date, source_type, old_faculty_set, faculty
                    )
            else:
                logger.debug(
                    "Расписание %s на %s (source_type=%s) не изменилось, уведомление НЕ отправляется",
                    group_name, schedule_date, source_type
                )
        # Удаляем ВСЕ старые записи для этой группы/даты/типа независимо от faculty
        # (иначе при смене faculty останутся дубли со старым значением)
        s.execute(
            delete(Lesson).where(
                Lesson.schedule_date == schedule_date,
                Lesson.group_name == group_name,
                Lesson.source_type == source_type,
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
                    start_time=item.get("start_time"),
                    end_time=item.get("end_time"),
                    source_type=source_type,
                    source_file_id=source_file_id,
                    faculty=faculty,
                )
            )
            saved += 1
        return saved, changed


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


def _lesson_groups(s, course: int | None, faculty: str) -> list[str]:
    """Старое поведение: группы из разобранных Excel-файлов."""
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
        s.scalars(
            select(Lesson.group_name)
            .where(Lesson.faculty == faculty)
            .distinct()
            .order_by(Lesson.group_name)
        )
    )


def get_groups(course: int | None = None, faculty: str = "permskaya") -> list[str]:
    """Список известных групп: Excel-группы + реестр заочки.

    Очное отделение получает ровно тот же список, что и раньше.
    Заочные группы добавляются из таблицы groups, даже если у них
    ещё нет разобранных пар (например, сканы 2027 года).
    """
    with get_session() as s:
        full_time = _lesson_groups(s, course, faculty)
        query = select(Group.name).where(
            Group.faculty == faculty,
            Group.study_form == "part_time",
        )
        if course is not None:
            query = query.where(Group.course == course)
        part_time = list(s.scalars(query.order_by(Group.name)))
        # dict.fromkeys — стабильный порядок без дублей
        return list(dict.fromkeys(full_time + part_time))


def upsert_groups(
    groups: list[tuple[str, int | None]],
    *,
    faculty: str = "permskaya",
    study_form: str = "part_time",
) -> int:
    """Добавляет/обновляет группы в реестре. Возвращает число записей."""
    saved = 0
    with get_session() as s:
        for name, course in groups:
            if not name:
                continue
            record = s.scalar(select(Group).where(Group.name == name))
            if record is None:
                record = Group(name=name)
                s.add(record)
            record.course = course
            record.faculty = faculty
            record.study_form = study_form
            saved += 1
        s.flush()
    return saved


def get_group_weeks(
    group_name: str,
    faculty: str = "permskaya",
) -> list[tuple[date, date]]:
    """Доступные недели заочки (понедельник–суббота) по сохранённым парам."""
    with get_session() as s:
        dates = sorted(
            set(
                s.scalars(
                    select(Lesson.schedule_date).where(
                        Lesson.group_name == group_name,
                        Lesson.faculty == faculty,
                    )
                )
            )
        )
    weeks: dict[date, date] = {}
    for day in dates:
        start = day - timedelta(days=day.weekday())
        weeks[start] = start + timedelta(days=5)
    return sorted(weeks.items())


def get_group_week_file(
    group_name: str,
    week_start: date,
    faculty: str = "permskaya",
):
    """PDF-файл, из которого взяты пары группы за указанную неделю."""
    week_end = week_start + timedelta(days=5)
    with get_session() as s:
        lesson = s.scalar(
            select(Lesson)
            .where(
                Lesson.group_name == group_name,
                Lesson.faculty == faculty,
                Lesson.schedule_date >= week_start,
                Lesson.schedule_date <= week_end,
                Lesson.source_file_id.is_not(None),
            )
            .order_by(Lesson.schedule_date, Lesson.lesson_number)
        )
        if lesson and lesson.source_file_id:
            record = s.get(ScheduleFile, lesson.source_file_id)
            if record and record.local_path:
                return record
    return None


def has_schedule_for_date(schedule_date: date) -> bool:
    with get_session() as s:
        count = s.scalar(
            select(func.count(Lesson.id)).where(Lesson.schedule_date == schedule_date)
        )
        return bool(count and count > 0)


def count_lessons() -> int:
    with get_session() as s:
        return s.scalar(select(func.count(Lesson.id))) or 0


def count_group_week_lessons(
    group_name: str,
    week_start: date,
    week_end: date,
    faculty: str | None = None,
) -> int:
    """Сколько пар группы уже сохранено за неделю (для проверки OCR)."""
    with get_session() as s:
        stmt = select(func.count(Lesson.id)).where(
            Lesson.group_name == group_name,
            Lesson.schedule_date >= week_start,
            Lesson.schedule_date <= week_end,
        )
        if faculty:
            stmt = stmt.where(Lesson.faculty == faculty)
        return s.scalar(stmt) or 0


def count_week_lessons(
    groups: list[str],
    week_start: date,
    week_end: date,
) -> int:
    """Есть ли сохранённые пары за неделю (для текста админ-алерта)."""
    with get_session() as s:
        stmt = select(func.count(Lesson.id)).where(
            Lesson.schedule_date >= week_start,
            Lesson.schedule_date <= week_end,
        )
        if groups:
            stmt = stmt.where(Lesson.group_name.in_(groups))
        else:
            stmt = stmt.where(Lesson.group_name.like("% зо"))
        return s.scalar(stmt) or 0


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


def get_schedule_file_for_date(schedule_date: date, faculty: str = "permskaya") -> ScheduleFile | None:
    """Возвращает файл расписания для указанной даты и факультета."""
    with get_session() as s:
        return s.scalar(
            select(ScheduleFile)
            .where(
                ScheduleFile.schedule_date == schedule_date,
                ScheduleFile.faculty == faculty,
                ScheduleFile.file_type == "schedule",
                ScheduleFile.processed.is_(True),
                ScheduleFile.local_path.is_not(None),
            )
            .order_by(ScheduleFile.id.desc())
        )


def get_file_by_id(file_id: int) -> ScheduleFile | None:
    with get_session() as s:
        return s.get(ScheduleFile, file_id)


def set_file_telegram_id(file_id: int, telegram_file_id: str | None) -> None:
    with get_session() as s:
        record = s.get(ScheduleFile, file_id)
        if record:
            record.telegram_file_id = telegram_file_id


def get_schedule_file_for_group(
    schedule_date: date,
    group_name: str,
    faculty: str = "permskaya",
) -> ScheduleFile | None:
    """Находит актуальный файл расписания или изменений для конкретной группы.

    Приоритет:
    1. Исходный файл уроков из изменений группы (source_type == 'changes')
    2. Исходный файл базовых уроков группы (source_type == 'schedule')
    3. Файл изменений дня для факультета
    4. Базовый файл расписания дня для факультета
    """
    with get_session() as s:
        lessons = list(
            s.scalars(
                select(Lesson)
                .where(
                    Lesson.schedule_date == schedule_date,
                    Lesson.group_name == group_name,
                    Lesson.faculty == faculty,
                )
            )
        )
        if lessons:
            changes_file_ids = [
                l.source_file_id for l in lessons
                if l.source_type == "changes" and l.source_file_id
            ]
            if changes_file_ids:
                file_rec = s.get(ScheduleFile, changes_file_ids[0])
                if file_rec and file_rec.local_path:
                    return file_rec

            schedule_file_ids = [
                l.source_file_id for l in lessons
                if l.source_type == "schedule" and l.source_file_id
            ]
            if schedule_file_ids:
                file_rec = s.get(ScheduleFile, schedule_file_ids[0])
                if file_rec and file_rec.local_path:
                    return file_rec

        # Если пар нет или у них нет source_file_id — смотрим общие файлы дня
        # Сначала проверяем файл изменений
        changes_file = s.scalar(
            select(ScheduleFile)
            .where(
                ScheduleFile.schedule_date == schedule_date,
                ScheduleFile.faculty == faculty,
                ScheduleFile.file_type == "changes",
                ScheduleFile.processed.is_(True),
                ScheduleFile.local_path.is_not(None),
            )
            .order_by(ScheduleFile.id.desc())
        )
        if changes_file:
            return changes_file

        # Иначе базовый файл расписания
        return s.scalar(
            select(ScheduleFile)
            .where(
                ScheduleFile.schedule_date == schedule_date,
                ScheduleFile.faculty == faculty,
                ScheduleFile.file_type == "schedule",
                ScheduleFile.processed.is_(True),
                ScheduleFile.local_path.is_not(None),
            )
            .order_by(ScheduleFile.id.desc())
        )

