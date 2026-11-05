"""Контент ручной рассылки: текст, фото или документ."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace

from aiogram.utils.text_decorations import html_decoration

TEXT_FORMAT_PLAIN = "plain"
TEXT_FORMAT_HTML = "html"

_TAG_RE = re.compile(r"<[^>]*>")
_CUSTOM_EMOJI_TAG_RE = re.compile(r"</?tg-emoji[^>]*>", re.IGNORECASE)


def html_to_plain(value: str) -> str:
    """Убирает HTML-разметку, сохраняя текст и обычные (fallback) эмодзи."""
    return html.unescape(_TAG_RE.sub("", value))


def strip_custom_emoji(value: str) -> str:
    """Убирает теги premium-эмодзи, оставляя обычный эмодзи внутри них."""
    return _CUSTOM_EMOJI_TAG_RE.sub("", value)


def content_variants(content: "BroadcastContent") -> list["BroadcastContent"]:
    """Варианты отправки: HTML → HTML без premium-эмодзи → обычный текст.

    Telegram разрешает custom_emoji только ботам с купленным username на
    Fragment. Если API отклонит такие сущности, сохраняем хотя бы жирный/
    курсив и остальную разметку, а в крайнем случае отправляем чистый текст.
    """
    variants: list[BroadcastContent] = [content]
    if content.is_html:
        text = content.text or ""
        without_custom = strip_custom_emoji(text)
        if without_custom != text:
            variants.append(replace(content, text=without_custom))
        variants.append(
            replace(
                content,
                text=content.plain_text(),
                text_format=TEXT_FORMAT_PLAIN,
            )
        )
    return variants


@dataclass(frozen=True, slots=True)
class BroadcastContent:
    """Контент для отправки. file_id — Telegram file_id для фото/документа.

    text_format:
        plain — обычный текст (старые записи), отправляется без parse_mode;
        html  — текст с разметкой Telegram и premium-эмодзи, отправляется
                с parse_mode=HTML.
    """

    content_type: str = "text"  # text | photo | document
    text: str | None = None
    file_id: str | None = None
    text_format: str = TEXT_FORMAT_PLAIN

    @property
    def is_html(self) -> bool:
        return self.text_format == TEXT_FORMAT_HTML

    def plain_text(self) -> str:
        """Текст без разметки — для превью, кнопок и запасной отправки."""
        text = self.text or ""
        return html_to_plain(text) if self.is_html else text

    def is_valid(self) -> bool:
        if self.content_type == "text":
            return bool(self.plain_text().strip())
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
    text_format = data.get("text_format", TEXT_FORMAT_PLAIN)
    if text_format not in (TEXT_FORMAT_PLAIN, TEXT_FORMAT_HTML):
        text_format = TEXT_FORMAT_PLAIN
    content = BroadcastContent(
        content_type=content_type if isinstance(content_type, str) else "text",
        text=text if isinstance(text, str) else None,
        file_id=file_id if isinstance(file_id, str) else None,
        text_format=text_format if isinstance(text_format, str) else TEXT_FORMAT_PLAIN,
    )
    return content if content.is_valid() else None


def _html_caption(message) -> str | None:
    """Подпись к фото/документу с сохранением разметки и premium-эмодзи."""
    caption = getattr(message, "caption", None) or ""
    if not caption.strip():
        return None
    entities = getattr(message, "caption_entities", None) or []
    formatted = html_decoration.unparse(caption, entities).strip()
    return formatted or None


def content_from_message(message) -> BroadcastContent | None:
    """Строит контент из входящего сообщения админа: текст/фото/документ.

    Использует HTML-представление сообщения, поэтому жирный/курсив, ссылки,
    спойлеры и premium-эмодзи (custom_emoji) не теряются при рассылке.
    """
    photo_id: str | None = None
    if getattr(message, "photo", None):
        photos = message.photo or []
        if photos:
            photo_id = photos[-1].file_id
    doc_id: str | None = None
    if getattr(message, "document", None) and message.document is not None:
        doc_id = message.document.file_id
    if photo_id:
        return BroadcastContent(
            content_type="photo",
            text=_html_caption(message),
            file_id=photo_id,
            text_format=TEXT_FORMAT_HTML,
        )
    if doc_id:
        return BroadcastContent(
            content_type="document",
            text=_html_caption(message),
            file_id=doc_id,
            text_format=TEXT_FORMAT_HTML,
        )
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        return None
    formatted = (getattr(message, "html_text", None) or "").strip()
    if not formatted:
        entities = getattr(message, "entities", None) or []
        formatted = html_decoration.unparse(text, entities).strip()
    return BroadcastContent(
        content_type="text",
        text=formatted,
        text_format=TEXT_FORMAT_HTML,
    )
