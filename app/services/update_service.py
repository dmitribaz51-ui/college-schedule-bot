"""ШАГ 12/18: проверка сайта, поиск новых файлов, скачивание, разбор, сохранение."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape

from app.config import get_config
from app.database import repository as repo
from app.parser.excel_downloader import (
    DownloadError,
    build_file_name,
    download_excel,
    download_pdf,
)
from app.parser.excel_parser import parse_excel_file
from app.parser.pdf_parser import parse_pdf_file
from app.parser.pdf_ocr import (
    OcrUnavailable,
    is_ocr_available,
    ocr_unavailable_reason,
    parse_scanned_pdf,
)
from app.parser.website_parser import (
    ScheduleLink,
    fetch_pdf_schedule_links,
    fetch_schedule_links,
)
from app.services.notification_service import notify_about_schedule_update
from app.utils import (
    course_from_group, detect_faculty, normalize_group, today_perm,
)

logger = logging.getLogger(__name__)

# Неудачный разбор PDF не должен блокировать файл навсегда. Первые попытки
# повторяем через час, после трёх неудач — через три часа. При рестарте
# init_db сбрасывает last_checked/retry_count у заочных файлов с ошибкой,
# чтобы новая версия парсера сразу получила шанс.
PDF_RETRY_AFTER = timedelta(hours=1)
PDF_RETRY_AFTER_SLOW = timedelta(hours=3)
PDF_RETRY_SLOW_AFTER = 3

# Если в названии PDF перечислены группы, требуем хотя бы 80% из них.
# По каждой уже сохранённой группе новых пар не должно стать меньше половины.
PDF_MIN_GROUP_COVERAGE = 0.8
PDF_MIN_LESSON_RATIO = 0.5


def _pdf_error_is_retryable(error: str | None) -> bool:
    """Ошибка разбора/OCR, которую имеет смысл повторить.

    «Дубликат» — финальный статус: такой файл уже разобран под другим URL.
    """
    return bool(error) and not error.startswith("Дубликат")


def _pdf_retry_delay(retry_count: int | None) -> timedelta:
    """Первая повторная попытка через 1 ч, после трёх неудач — через 3 ч."""
    if (retry_count or 0) >= PDF_RETRY_SLOW_AFTER:
        return PDF_RETRY_AFTER_SLOW
    return PDF_RETRY_AFTER


def _week_end(link: ScheduleLink) -> date | None:
    if link.schedule_date is None:
        return None
    return link.date_end or (link.schedule_date + timedelta(days=6))


def _pdf_result_problem(
    link: ScheduleLink,
    schedules: list,
) -> str | None:
    """Проверяет, можно ли применять результат OCR без риска потерять пары.

    Возвращает причину «нужна проверка» или None, если результат похож на
    полный. Это защита от частичного OCR: полностью пустой результат
    обрабатывается отдельно, до вызова этой функции.
    """
    if not schedules:
        return "OCR не распознал ни одной группы"

    expected = {
        normalized
        for group in (link.groups or [])
        if (normalized := normalize_group(group))
    }
    actual_groups: set[str] = set()
    lessons_by_group: dict[str, int] = {}

    for schedule in schedules:
        if not schedule.lessons:
            return f"по группе {schedule.group} не распознано ни одной пары"
        if schedule.schedule_date is None:
            return f"не удалось определить дату для группы {schedule.group}"
        if link.schedule_date and schedule.schedule_date < link.schedule_date:
            return (
                f"дата {schedule.schedule_date:%d.%m.%Y} раньше начала недели "
                f"{link.schedule_date:%d.%m.%Y}"
            )
        end = _week_end(link)
        if end and schedule.schedule_date > end:
            return (
                f"дата {schedule.schedule_date:%d.%m.%Y} позже конца недели "
                f"{end:%d.%m.%Y}"
            )

        group = normalize_group(schedule.group) or schedule.group
        if expected and group not in expected:
            return f"OCR вернул неизвестную группу {schedule.group}"
        actual_groups.add(group)
        lessons_by_group[group] = lessons_by_group.get(group, 0) + len(
            schedule.lessons
        )

    if expected:
        found = len(actual_groups & expected)
        coverage = found / len(expected)
        if coverage < PDF_MIN_GROUP_COVERAGE:
            return (
                f"OCR вернул {found} групп из {len(expected)} "
                f"(меньше {int(PDF_MIN_GROUP_COVERAGE * 100)}%)"
            )

    start = link.schedule_date
    end = _week_end(link)
    if start and end:
        for group, new_count in lessons_by_group.items():
            old_count = repo.count_group_week_lessons(
                group, start, end, faculty=None
            )
            if old_count and new_count < old_count * PDF_MIN_LESSON_RATIO:
                return (
                    f"по группе {group} было {old_count} пар, "
                    f"распознано {new_count}"
                )

    return None


def _old_schedule_kept(link: ScheduleLink) -> bool:
    """Были ли за эту неделю сохранённые пары до текущего разбора."""
    start = link.schedule_date
    end = _week_end(link)
    if start is None or end is None:
        return False
    return repo.count_week_lessons(link.groups, start, end) > 0


def _admin_alert_text(link: ScheduleLink, error: str, old_kept: bool) -> str:
    day = link.schedule_date.strftime("%d.%m.%Y") if link.schedule_date else "?"
    if old_kept:
        fallback = (
            "Пользователям показано старое расписание: сохранённые пары "
            "не изменены."
        )
    else:
        fallback = (
            "Сохранённых пар за эту неделю нет — новое расписание "
            "не опубликовано."
        )
    return (
        "⚠️ <b>Проблема с PDF заочки</b>\n\n"
        f'📄 <a href="{escape(link.url, quote=True)}">{escape(link.title)}</a>\n'
        f"📅 {day}\n"
        f"❌ {escape(error)}\n\n"
        f"{fallback}\n"
        "Проверьте PDF и при необходимости нажмите "
        "«Перечитать упавшие PDF заочки» в /admin."
    )


async def _notify_admins_about_pdf(
    bot,
    link: ScheduleLink,
    error: str,
    *,
    old_kept: bool,
) -> bool:
    """Отправляет тревогу всем ADMIN_IDS. True — хотя бы одному доставлено."""
    if bot is None:
        return False
    admin_ids = get_config().admin_ids
    if not admin_ids:
        return False
    text = _admin_alert_text(link, error, old_kept)
    sent = False
    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            sent = True
        except Exception:
            logger.exception(
                "Не удалось отправить админ-алерт о PDF %s", link.url
            )
    return sent


async def _record_pdf_failure(
    link: ScheduleLink,
    report: "UpdateReport",
    *,
    downloaded,
    error: str,
    existing,
    attempts: int,
    review_required: bool,
    bot,
    notify_admins: bool,
) -> None:
    """Сохраняет ошибку, не трогая старые пары и старый PDF."""
    # Если по URL уже было успешное расписание, оставляем local_path на
    # старый PDF: админ и студенты должны видеть ту версию, которая
    # действительно разобрана. Новый файл остаётся на диске для повтора.
    local_path = None
    if existing is None:
        local_path = str(downloaded.path)

    repo.upsert_file(
        title=link.title,
        url=link.url,
        file_type=link.file_type,
        faculty="zaochnoe",
        schedule_date=link.schedule_date,
        file_hash=downloaded.sha256,
        local_path=local_path,
        processed=False,
        error=error,
        review_required=review_required,
        retry_count=attempts,
        update_check_time=True,
    )

    if review_required:
        report.needs_review.append(f"{link.title}: {error}")
    else:
        report.errors.append(f"{link.title}: {error}")
    logger.error("PDF заочки не применён (%s): %s", link.title, error)

    already_alerted = bool(
        existing and existing.alerted_hash == downloaded.sha256
    )
    if notify_admins and not already_alerted:
        sent = await _notify_admins_about_pdf(
            bot, link, error, old_kept=_old_schedule_kept(link)
        )
        if sent:
            repo.mark_file_alerted(link.url, downloaded.sha256)


@dataclass
class ProcessedFile:
    title: str
    file_type: str
    schedule_date: date | None
    groups: list[str]
    lessons_saved: int
    changed_groups: list[str] = field(default_factory=list)


@dataclass
class UpdateReport:
    links_found: int = 0
    new_files: list[ProcessedFile] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"🔎 Ссылок на сайте: {self.links_found}",
            f"🆕 Обработано новых файлов: {len(self.new_files)}",
            f"⏭ Пропущено (уже были): {self.skipped}",
        ]
        if self.needs_review:
            lines.append(f"⚠️ Требуют проверки: {len(self.needs_review)}")
        for item in self.new_files:
            day = item.schedule_date.strftime("%d.%m.%Y") if item.schedule_date else "?"
            kind = "изменения" if item.file_type == "changes" else "расписание"
            changed_info = f", изменено групп: {len(item.changed_groups)}" if item.changed_groups else ", без изменений пар"
            lines.append(
                f"• {kind} на {day}: групп {len(item.groups)}, пар {item.lessons_saved}{changed_info}"
            )
        for error in self.errors:
            lines.append(f"❌ {error}")
        for item in self.needs_review:
            lines.append(f"⚠️ {item}")
        return "\n".join(lines)


async def _process_link(
    link: ScheduleLink,
    report: UpdateReport,
    bot=None,
    faculty: str = "permskaya",
    force_reprocess: bool = False,
    notify_users: bool = True,
) -> None:
    config = get_config()

    file_name = build_file_name(link.schedule_date, link.file_type, link.url)
    # файлы каждого факультета хранятся в своей подпапке:
    # data/excel/permskaya и data/excel/chernyshevskogo
    faculty_dir = config.excel_dir / faculty
    try:
        downloaded = await download_excel(link.url, faculty_dir, file_name)
    except DownloadError as exc:
        repo.upsert_file(
            title=link.title, url=link.url, file_type=link.file_type, faculty=faculty,
            schedule_date=link.schedule_date, processed=False, error=str(exc),
            update_check_time=True,
        )
        report.errors.append(f"{link.title}: {exc}")
        logger.error("Файл не скачан (%s): %s", link.title, exc)
        return

    # тот же файл под другой ссылкой — не обрабатываем повторно
    if not force_reprocess and repo.file_hash_exists(downloaded.sha256, exclude_url=link.url):
        repo.upsert_file(
            title=link.title, url=link.url, file_type=link.file_type, faculty=faculty,
            schedule_date=link.schedule_date, file_hash=downloaded.sha256,
            local_path=str(downloaded.path), processed=True,
            error="Дубликат уже обработанного файла",
            update_check_time=True,
        )
        report.skipped += 1
        return

    file_id = repo.upsert_file(
        title=link.title, url=link.url, file_type=link.file_type, faculty=faculty,
        schedule_date=link.schedule_date, file_hash=downloaded.sha256,
        local_path=str(downloaded.path), processed=False,
        update_check_time=True,
    )

    try:
        schedules = parse_excel_file(downloaded.path, default_date=link.schedule_date)
    except Exception as exc:
        repo.upsert_file(
            title=link.title, url=link.url, file_type=link.file_type,
            schedule_date=link.schedule_date, file_hash=downloaded.sha256,
            local_path=str(downloaded.path), processed=False,
            error=f"Ошибка разбора Excel: {exc}",
            update_check_time=True,
        )
        report.errors.append(f"{link.title}: ошибка разбора Excel")
        logger.exception("Ошибка разбора файла %s", downloaded.path)
        return

    saved_groups: list[str] = []
    changed_groups: list[str] = []
    saved_lessons = 0
    had_any_prior_schedule = False
    if link.schedule_date is not None:
        had_any_prior_schedule = repo.has_schedule_for_date(link.schedule_date)

    for schedule in schedules:
        day = schedule.schedule_date or link.schedule_date
        if day is None or not schedule.lessons:
            continue
        # Автоопределение факультета по префиксу группы (приоритет над URL файла)
        from app.utils import detect_faculty
        group_faculty = detect_faculty(schedule.group)
        saved_count, changed = repo.replace_group_lessons(
            schedule_date=day,
            group_name=schedule.group,
            source_type=link.file_type,
            course=schedule.course,
            lessons=[l.to_dict() for l in schedule.lessons],
            source_file_id=file_id, faculty=group_faculty,
        )
        saved_lessons += saved_count
        saved_groups.append(schedule.group)
        if changed:
            changed_groups.append(schedule.group)

    error = None if saved_groups else "Не удалось распознать ни одной группы"
    repo.upsert_file(
        title=link.title, url=link.url, file_type=link.file_type, faculty=faculty,
        schedule_date=link.schedule_date, file_hash=downloaded.sha256,
        local_path=str(downloaded.path), processed=bool(saved_groups), error=error,
        update_check_time=True,
    )
    if not saved_groups:
        report.errors.append(f"{link.title}: группы не распознаны (нужна донастройка парсера)")
        return

    processed = ProcessedFile(
        title=link.title, file_type=link.file_type,
        schedule_date=link.schedule_date, groups=saved_groups,
        lessons_saved=saved_lessons, changed_groups=changed_groups,
    )
    report.new_files.append(processed)

    if bot is not None and notify_users and changed_groups:
        is_new_day = link.file_type != "changes" and not had_any_prior_schedule
        await notify_about_schedule_update(
            bot,
            schedule_date=link.schedule_date,
            groups=changed_groups,
            title=link.title,
            is_new=is_new_day,
        )


async def _process_pdf_link(
    link: ScheduleLink,
    report: UpdateReport,
    force_reprocess: bool = False,
    bot=None,
    notify_admins: bool = True,
) -> None:
    """Недельный PDF заочки: скачать, разобрать, проверить и сохранить пары."""
    config = get_config()
    file_name = build_file_name(link.schedule_date, link.file_type, link.url)
    pdf_dir = config.pdf_dir / "zaochnoe"

    existing = repo.get_file_by_url(link.url)
    # Не долбим OCR/сеть каждые 15 минут, если файл уже падал с ошибкой.
    # После рестарта/обновления кода init_db обнуляет last_checked/retry_count,
    # поэтому новая версия парсера сразу получает повторный шанс.
    if (
        existing
        and _pdf_error_is_retryable(existing.error)
        and existing.last_checked
        and not (existing.error or "").startswith("Ошибка сети")
        and not force_reprocess
        and datetime.now() - existing.last_checked
        < _pdf_retry_delay(existing.retry_count)
    ):
        report.skipped += 1
        return

    try:
        downloaded = await download_pdf(link.url, pdf_dir, file_name)
    except DownloadError as exc:
        repo.upsert_file(
            title=link.title,
            url=link.url,
            file_type=link.file_type,
            faculty="zaochnoe",
            schedule_date=link.schedule_date,
            processed=False,
            error=str(exc),
            update_check_time=True,
        )
        report.errors.append(f"{link.title}: {exc}")
        logger.error("PDF не скачан (%s): %s", link.title, exc)
        return

    same_hash = bool(existing and existing.file_hash == downloaded.sha256)

    # Подозрительный результат уже ждёт человека. Раз в интервал повтора
    # скачиваем PDF, чтобы заметить новую версию, но OCR без явной команды
    # админа не запускаем.
    if existing and same_hash and existing.review_required and not force_reprocess:
        repo.upsert_file(
            title=link.title,
            url=link.url,
            file_type=link.file_type,
            faculty="zaochnoe",
            schedule_date=link.schedule_date,
            file_hash=downloaded.sha256,
            processed=False,
            error=existing.error,
            review_required=True,
            retry_count=existing.retry_count or 0,
            update_check_time=True,
        )
        report.skipped += 1
        return

    if (
        existing
        and same_hash
        and existing.processed
        and not _pdf_error_is_retryable(existing.error)
        and not force_reprocess
    ):
        # Файл не изменился: не парсим и не OCR-им повторно каждые 25 минут.
        repo.upsert_file(
            title=link.title,
            url=link.url,
            file_type=link.file_type,
            faculty="zaochnoe",
            schedule_date=link.schedule_date,
            file_hash=downloaded.sha256,
            processed=existing.processed,
            error=existing.error,
            review_required=False,
            retry_count=0,
            update_check_time=True,
        )
        report.skipped += 1
        return

    if not force_reprocess and repo.file_hash_exists(
        downloaded.sha256, exclude_url=link.url
    ):
        repo.upsert_file(
            title=link.title,
            url=link.url,
            file_type=link.file_type,
            faculty="zaochnoe",
            schedule_date=link.schedule_date,
            file_hash=downloaded.sha256,
            local_path=str(downloaded.path),
            processed=True,
            error="Дубликат уже обработанного PDF",
            review_required=False,
            retry_count=0,
            update_check_time=True,
        )
        repo.clear_file_alert(link.url)
        report.skipped += 1
        return

    # Старый PDF не подменяем в базе, пока новый успешно не разберётся:
    # иначе студент получит ссылку на файл, из которого нет расписания.
    keep_old_path = existing is not None
    previous_attempts = (
        (existing.retry_count or 0) if existing and same_hash else 0
    )
    attempts = previous_attempts + 1

    file_id = repo.upsert_file(
        title=link.title,
        url=link.url,
        file_type=link.file_type,
        faculty="zaochnoe",
        schedule_date=link.schedule_date,
        file_hash=downloaded.sha256,
        local_path=None if keep_old_path else str(downloaded.path),
        processed=False,
        update_check_time=True,
    )

    # Группы из названия ссылки нужны даже для сканов: так все заочные
    # группы появляются в списке до того, как появится текст расписания.
    if link.groups:
        for group in link.groups:
            repo.upsert_groups(
                [(group, course_from_group(group, link.schedule_date))],
                faculty=detect_faculty(group),
            )

    try:
        schedules = parse_pdf_file(downloaded.path)
    except Exception as exc:
        await _record_pdf_failure(
            link,
            report,
            downloaded=downloaded,
            error=f"Ошибка разбора PDF: {exc}",
            existing=existing,
            attempts=attempts,
            review_required=False,
            bot=bot,
            notify_admins=notify_admins,
        )
        logger.exception("Ошибка разбора PDF %s", downloaded.path)
        return

    ocr_error: str | None = None
    if not schedules:
        # Скан без текстового слоя — пробуем OCR. Если Tesseract нет или
        # ячейки не сошлись, оставляем PDF и честно пишем об ошибке.
        if not is_ocr_available():
            reason = ocr_unavailable_reason() or "Tesseract не установлен"
            ocr_error = f"PDF без текста, OCR недоступен: {reason}"
        else:
            try:
                schedules = await asyncio.to_thread(
                    parse_scanned_pdf,
                    downloaded.path,
                    default_groups=link.groups,
                    default_date=link.schedule_date,
                )
            except OcrUnavailable as exc:
                ocr_error = f"OCR недоступен: {exc}"
            except Exception as exc:
                ocr_error = f"Ошибка OCR: {exc}"
                logger.exception("Ошибка OCR PDF %s", downloaded.path)
            if not schedules and not ocr_error:
                ocr_error = "OCR не распознал ни одной группы"

    if not schedules:
        await _record_pdf_failure(
            link,
            report,
            downloaded=downloaded,
            error=ocr_error or "Не удалось распознать PDF",
            existing=existing,
            attempts=attempts,
            review_required=False,
            bot=bot,
            notify_admins=notify_admins,
        )
        return

    quality_problem = _pdf_result_problem(link, schedules)
    if quality_problem:
        # Главное: до этой проверки ни одна пара ещё не заменена.
        await _record_pdf_failure(
            link,
            report,
            downloaded=downloaded,
            error=f"Нужна проверка: {quality_problem}",
            existing=existing,
            attempts=attempts,
            review_required=True,
            bot=bot,
            notify_admins=notify_admins,
        )
        return

    saved_groups: list[str] = []
    changed_groups: list[str] = []
    saved_lessons = 0
    for schedule in schedules:
        group_faculty = detect_faculty(schedule.group)
        saved_count, changed = repo.replace_group_lessons(
            schedule_date=schedule.schedule_date,
            group_name=schedule.group,
            source_type=link.file_type,
            course=schedule.course,
            lessons=[lesson.to_dict() for lesson in schedule.lessons],
            source_file_id=file_id,
            faculty=group_faculty,
        )
        saved_lessons += saved_count
        if schedule.group not in saved_groups:
            saved_groups.append(schedule.group)
        if changed and schedule.group not in changed_groups:
            changed_groups.append(schedule.group)
        repo.upsert_groups(
            [(schedule.group, schedule.course)],
            faculty=group_faculty,
        )

    if not saved_groups:
        await _record_pdf_failure(
            link,
            report,
            downloaded=downloaded,
            error="Не удалось сохранить ни одной группы",
            existing=existing,
            attempts=attempts,
            review_required=False,
            bot=bot,
            notify_admins=notify_admins,
        )
        return

    repo.upsert_file(
        title=link.title,
        url=link.url,
        file_type=link.file_type,
        faculty="zaochnoe",
        schedule_date=link.schedule_date,
        file_hash=downloaded.sha256,
        local_path=str(downloaded.path),
        processed=True,
        error=None,
        review_required=False,
        retry_count=0,
        update_check_time=True,
    )
    repo.clear_file_alert(link.url)

    report.new_files.append(
        ProcessedFile(
            title=link.title,
            file_type=link.file_type,
            schedule_date=link.schedule_date,
            groups=saved_groups,
            lessons_saved=saved_lessons,
            changed_groups=changed_groups,
        )
    )


async def check_for_updates(
    bot=None,
    force_reprocess: bool = False,
    notify_users: bool = True,
    notify_admins: bool = True,
    zao_only: bool = False,
    only_urls: set[str] | None = None,
) -> UpdateReport:
    """Полный цикл проверки. Никогда не выбрасывает исключение наружу.

    ``zao_only``/``only_urls`` нужны кнопке «Перечитать упавшие PDF заочки»:
    она не трогает очные Excel и успешные заочные файлы.
    """
    config = get_config()
    report = UpdateReport()

    from app.parser.website_parser import fetch_all_schedule_links

    links_with_faculty: list[tuple[str, ScheduleLink]] = []
    if not zao_only:
        links_with_faculty = await fetch_all_schedule_links([
            ("permskaya", config.schedule_page_url),
            ("chernyshevskogo", config.chernyshevskogo_page_url),
        ])

    pdf_links = await fetch_pdf_schedule_links(config.zaochnoe_page_url)
    # Старые недели не нужны, но 05.10/12.10 ещё полезны для истории.
    oldest = today_perm() - timedelta(days=60)
    pdf_links = [
        link for link in pdf_links
        if link.schedule_date is not None and link.schedule_date >= oldest
    ]
    links_with_faculty.extend(("zaochnoe", link) for link in pdf_links)

    report.links_found = len(links_with_faculty)
    if not links_with_faculty:
        return report

    known_urls = repo.get_processed_urls()
    # URL файлов, которые давно не проверялись (>25 минут)
    urls_needing_recheck = repo.get_urls_needing_recheck(minutes=25)

    for faculty, link in links_with_faculty:
        if only_urls is not None and link.url not in only_urls:
            report.skipped += 1
            continue
        # Пропускаем только если файл известен И не требует перепроверки И не force_reprocess
        if link.url in known_urls and link.url not in urls_needing_recheck and not force_reprocess:
            report.skipped += 1
            continue
        try:
            if faculty == "zaochnoe":
                await _process_pdf_link(
                    link,
                    report,
                    force_reprocess=force_reprocess,
                    bot=bot,
                    notify_admins=notify_admins,
                )
            else:
                await _process_link(
                    link,
                    report,
                    bot=bot,
                    faculty=faculty,
                    force_reprocess=force_reprocess,
                    notify_users=notify_users,
                )
        except Exception as exc:
            logger.exception("Ошибка обработки ссылки %s", link.url)
            report.errors.append(f"{link.title}: {exc}")

    return report
