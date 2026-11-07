"""ШАГ 10-11: загрузка страницы расписания и поиск ссылок на Excel."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.utils import extract_date, normalize_group

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
EXCEL_SUFFIXES = (".xlsx", ".xlsm", ".xls")
CHANGES_WORDS = ("изменен", "изменён", "измен")


@dataclass
class ScheduleLink:
    title: str
    file_type: str          # schedule | changes
    schedule_date: date | None
    url: str
    date_end: date | None = None
    groups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "type": self.file_type,
            "date": self.schedule_date.isoformat() if self.schedule_date else None,
            "date_end": self.date_end.isoformat() if self.date_end else None,
            "groups": self.groups,
            "url": self.url,
        }


def detect_file_type(text: str) -> str:
    low = (text or "").lower()
    return "changes" if any(w in low for w in CHANGES_WORDS) else "schedule"


def parse_links_from_html(html: str, base_url: str) -> list[ScheduleLink]:
    """Отдельная функция — удобно тестировать без сети."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[ScheduleLink] = []
    seen: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href.lower().split("?")[0].endswith(EXCEL_SUFFIXES):
            continue

        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        title = " ".join(tag.get_text(" ", strip=True).split())
        if not title:
            title = href.rsplit("/", 1)[-1]

        # дату ищем в тексте ссылки, затем у родительского блока
        day = extract_date(title)
        if day is None and tag.parent is not None:
            day = extract_date(tag.parent.get_text(" ", strip=True))

        file_type = detect_file_type(title)
        if file_type == "schedule" and tag.parent is not None:
            # иногда слово "Изменения" стоит рядом, а не в самой ссылке
            parent_text = tag.parent.get_text(" ", strip=True)
            if len(parent_text) < 200:
                file_type = detect_file_type(parent_text)

        results.append(
            ScheduleLink(title=title, file_type=file_type, schedule_date=day, url=url)
        )

    return results


PDF_DATE_RANGE_RE = re.compile(
    r"(\d{1,2})\.(\d{2})\s*[-–—]\s*(\d{1,2})\.(\d{2})\.(\d{2,4})"
)
ZAOCHNOE_GROUP_RE = re.compile(
    r"([А-ЯЁA-Z]{1,6}-\d{1,2}-\d{1,2}(?:-\d+)?)\s*зо",
    re.IGNORECASE,
)


def _pdf_date_range(text: str) -> tuple[date | None, date | None]:
    match = PDF_DATE_RANGE_RE.search(text or "")
    if not match:
        return None, None
    day1, month1, day2, month2, year = (int(value) for value in match.groups())
    if year < 100:
        year += 2000
    try:
        start = date(year, month1, day1)
        end_year = year + 1 if (month2, day2) < (month1, day1) else year
        end = date(end_year, month2, day2)
    except ValueError:
        return None, None
    return start, end


def extract_zaochnoe_groups(text: str) -> list[str]:
    """Вытаскивает заочные группы из названия PDF-ссылки."""
    groups: list[str] = []
    for match in ZAOCHNOE_GROUP_RE.finditer(text or ""):
        group = normalize_group(f"{match.group(1)} зо")
        if group and group not in groups:
            groups.append(group)
    return groups


def parse_pdf_links_from_html(html: str, base_url: str) -> list[ScheduleLink]:
    """Ищет недельные PDF заочки («Расписание 09.11-14.11.2026»)."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[ScheduleLink] = []
    seen: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href.lower().split("?")[0].endswith(".pdf"):
            continue
        title = " ".join(tag.get_text(" ", strip=True).split())
        low = title.lower()
        if not low.startswith("расписание") or "график" in low:
            continue
        start, end = _pdf_date_range(title)
        if start is None:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        results.append(
            ScheduleLink(
                title=title,
                file_type="schedule",
                schedule_date=start,
                url=url,
                date_end=end,
                groups=extract_zaochnoe_groups(title),
            )
        )
    return results


async def fetch_pdf_schedule_links(
    page_url: str, timeout: float = 30.0
) -> list[ScheduleLink]:
    """PDF-ссылки заочки. При ошибке сети — пустой список + лог."""
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=timeout, follow_redirects=True
        ) as client:
            response = await client.get(page_url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as exc:
        logger.error("Не удалось загрузить страницу заочки: %s", exc)
        return []
    except Exception:
        logger.exception("Неожиданная ошибка при загрузке страницы заочки")
        return []

    links = parse_pdf_links_from_html(html, page_url)
    logger.info("На странице заочки найдено PDF-расписаний: %s", len(links))
    return links


async def fetch_schedule_links(page_url: str, timeout: float = 30.0) -> list[ScheduleLink]:
    """Возвращает список Excel-ссылок. При ошибке сети — пустой список + лог."""
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=timeout, follow_redirects=True
        ) as client:
            response = await client.get(page_url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as exc:
        logger.error("Не удалось загрузить страницу расписания: %s", exc)
        return []
    except Exception:
        logger.exception("Неожиданная ошибка при загрузке страницы расписания")
        return []

    links = parse_links_from_html(html, page_url)
    logger.info("На странице найдено Excel-ссылок: %s", len(links))
    return links


async def fetch_all_schedule_links(page_urls: list[str]) -> list[tuple[str, ScheduleLink]]:
    """Получает ссылки с каждой страницы и сохраняет принадлежность факультету."""
    result: list[tuple[str, ScheduleLink]] = []
    for faculty, page_url in page_urls:
        result.extend((faculty, link) for link in await fetch_schedule_links(page_url))
    return result
