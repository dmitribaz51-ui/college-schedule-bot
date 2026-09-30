"""Контент ручной рассылки: текст, фото или документ."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BroadcastContent:
    """Контент для отправки. file_id — Telegram file_id для фото/документа."""

    content_type: str = "text"  # text | photo | document
    text: str | None = None
    file_id: str | None = None

    def is_valid(self) -> bool:
        if self.content_type == "text":
            return bool((self.text or "").strip())
        if self.content_type in ("photo", "document"):
            return bool(self.file_id)
        return False

    def preview_title(self) -> str:
        if self.content_type == "photo":
            return "📷 Фото"
        if self.content_type == "document":
            return "📄 Документ"
        return "📝 Текст"


def content_from_state(data: dict) -> BroadcastContent | None:
    """Собирает контент из FSM-данных. Возвращает None если данных недостаточно."""
    content_type = data.get("content_type", "text")
    text = data.get("text")
    file_id = data.get("file_id")
    content = BroadcastContent(
        content_type=content_type if isinstance(content_type, str) else "text",
        text=text if isinstance(text, str) else None,
        file_id=file_id if isinstance(file_id, str) else None,
    )
    return content if content.is_valid() else None


def content_from_message(message) -> BroadcastContent | None:
    """Строит контент из входящего сообщения админа: текст/фото/документ."""
    photo_id: str | None = None
    if getattr(message, "photo", None):
        photos = message.photo or []
        if photos:
            photo_id = photos[-1].file_id
    doc_id: str | None = None
    if getattr(message, "document", None) and message.document is not None:
        doc_id = message.document.file_id
    if photo_id:
        caption = (getattr(message, "caption", None) or "").strip()
        return BroadcastContent(content_type="photo", text=caption or None, file_id=photo_id)
    if doc_id:
        caption = (getattr(message, "caption", None) or "").strip()
        return BroadcastContent(content_type="document", text=caption or None, file_id=doc_id)
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        return None
    return BroadcastContent(content_type="text", text=text)
