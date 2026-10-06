"""ШАГ 12/18: проверка сайта, поиск новых файлов, скачивание, разбор, сохранение."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from app.config import get_config
from app.database import repository as repo
from app.parser.excel_downloader import DownloadError, build_file_name, download_excel
from app.parser.excel_parser import parse_excel_file
from app.parser.website_parser import ScheduleLink, fetch_schedule_links
from app.services.notification_service import notify_about_schedule_update

logger = logging.getLogger(__name__)


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

    def summary(self) -> str:
        lines = [
            f"🔎 Ссылок на сайте: {self.links_found}",
            f"🆕 Обработано новых файлов: {len(self.new_files)}",
            f"⏭ Пропущено (уже были): {self.skipped}",
        ]
        for item in self.new_files:
            day = item.schedule_date.strftime("%d.%m.%Y") if item.schedule_date else "?"
            kind = "изменения" if item.file_type == "changes" else "расписание"
            changed_info = f", изменено групп: {len(item.changed_groups)}" if item.changed_groups else ", без изменений пар"
            lines.append(
                f"• {kind} на {day}: групп {len(item.groups)}, пар {item.lessons_saved}{changed_info}"
            )
        for error in self.errors:
            lines.append(f"❌ {error}")
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


async def check_for_updates(
    bot=None, force_reprocess: bool = False, notify_users: bool = True
) -> UpdateReport:
    """Полный цикл проверки. Никогда не выбрасывает исключение наружу."""
    config = get_config()
    report = UpdateReport()

    from app.parser.website_parser import fetch_all_schedule_links
    links_with_faculty = await fetch_all_schedule_links([
        ("permskaya", config.schedule_page_url),
        ("chernyshevskogo", config.chernyshevskogo_page_url),
    ])
    links = [link for _, link in links_with_faculty]
    report.links_found = len(links)
    if not links:
        return report

    known_urls = repo.get_processed_urls()
    # URL файлов, которые давно не проверялись (>25 минут)
    urls_needing_recheck = repo.get_urls_needing_recheck(minutes=25)
    
    for faculty, link in links_with_faculty:
        # Пропускаем только если файл известен И не требует перепроверки И не force_reprocess
        if link.url in known_urls and link.url not in urls_needing_recheck and not force_reprocess:
            report.skipped += 1
            continue
        try:
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
