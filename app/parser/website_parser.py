"""ШАГ 10-11: загрузка страницы расписания и поиск ссылок на Excel."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.utils import extract_date

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

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "type": self.file_type,
            "date": self.schedule_date.isoformat() if self.schedule_date else None,
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
