"""Общие помощники центра рассылок."""
from __future__ import annotations

from datetime import datetime
from html import escape

from aiogram.types import Message

from app.bot.keyboards.broadcast_kb import center_keyboard
from app.config import get_config
from app.database import repository as repo
from app.services.manual_broadcast_content import BroadcastContent


def is_admin(telegram_id: int) -> bool:
    return get_config().is_admin(telegram_id)


def audience_label(audience: str, group_name: str | None = None) -> str:
    if audience == "all":
        return "Все пользователи"
    if audience == "enabled":
        return "Только подписанные на уведомления"
    if audience == "group" and group_name:
        return f"Группа {group_name}"
    return "Не выбрана"


def resolve_audience(audience: str, group_name: str | None = None) -> list:
    if audience == "all":
        return repo.get_all_users()
    if audience == "enabled":
        return repo.get_notification_users()
    if audience == "group" and group_name:
        return repo.get_group_users(group_name)
    return []


def audience_snapshot(audience: str, group_name: str | None = None) -> tuple[str, int]:
    label = audience_label(audience, group_name)
    return label, len(resolve_audience(audience, group_name))


def parse_schedule_datetime(raw: str) -> datetime | None:
    """Парсит 'ДД.ММ.ГГГГ ЧЧ:ММ'. Возвращает None если формат неверный или дата в прошлом."""
    text = (raw or "").strip()
    try:
        value = datetime.strptime(text, "%d.%m.%Y %H:%M")
    except ValueError:
        return None
    if value <= datetime.now():
        return None
    return value


def content_preview_text(content: BroadcastContent) -> str:
    title = content.preview_title()
    body = escape(content.text or "[без текста]")
    if len(body) > 800:
        body = body[:800] + "…"
    return f"{title}\n{body}"


async def show_center(message: Message) -> None:
    await message.edit_text(
        "📣 <b>Центр рассылок</b>\n\nВыберите действие:",
        reply_markup=center_keyboard(),
    )
