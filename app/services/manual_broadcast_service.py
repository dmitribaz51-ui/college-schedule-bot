"""Отправка ручных админских рассылок, отдельно от уведомлений расписания."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from app.database.models import User
from app.services.manual_broadcast_content import (
    BroadcastContent,
    TEXT_FORMAT_PLAIN,
    content_variants,
)
from app.services.notification_service import _safe_send

logger = logging.getLogger(__name__)

_premium_emoji_notice_shown = False


@dataclass(frozen=True, slots=True)
class BroadcastResult:
    """Итог ручной рассылки."""

    total: int
    sent: int

    @property
    def failed(self) -> int:
        return self.total - self.sent


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    telegram_id: int
    username: str | None
    error: str


@dataclass(frozen=True, slots=True)
class DetailedBroadcastResult:
    total: int
    sent: int
    failures: tuple[DeliveryFailure, ...] = field(default_factory=tuple)

    @property
    def failed(self) -> int:
        return self.total - self.sent


def _parse_mode_for(content: BroadcastContent) -> ParseMode | None:
    """HTML для новых рассылок, None (явно) — для старых записей без разметки."""
    return ParseMode.HTML if content.is_html else None


async def _send_content_once(
    bot: Bot,
    chat_id: int,
    content: BroadcastContent,
    parse_mode: ParseMode | None,
) -> None:
    if content.content_type == "photo" and content.file_id:
        caption = content.text or None
        await bot.send_photo(
            chat_id,
            photo=content.file_id,
            caption=caption,
            parse_mode=parse_mode if caption else None,
        )
        return
    if content.content_type == "document" and content.file_id:
        caption = content.text or None
        await bot.send_document(
            chat_id,
            document=content.file_id,
            caption=caption,
            parse_mode=parse_mode if caption else None,
        )
        return
    await bot.send_message(chat_id, content.text or "", parse_mode=parse_mode)


def _is_parse_error(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return "parse" in message or "entity" in message


async def _safe_send_content(
    bot: Bot, chat_id: int, content: BroadcastContent
) -> tuple[bool, str | None]:
    """Отправляет текст/фото/документ, возвращает (успех, текст ошибки)."""
    global _premium_emoji_notice_shown
    variants = content_variants(content)
    index = 0
    retry_after_count = 0
    last_error = "unknown"
    while index < len(variants):
        item = variants[index]
        try:
            await _send_content_once(
                bot, chat_id, item, _parse_mode_for(item)
            )
            return True, None
        except TelegramForbiddenError:
            logger.info("Пользователь %s заблокировал бота", chat_id)
            return False, "blocked: bot blocked"
        except TelegramRetryAfter as exc:
            if retry_after_count >= 2:
                return False, "retry_after"
            retry_after_count += 1
            await asyncio.sleep(exc.retry_after)
            continue
        except TelegramBadRequest as exc:
            last_error = f"api: {exc}"[:300]
            if item.is_html and _is_parse_error(exc):
                if (
                    "custom emoji" in str(exc).lower()
                    and not _premium_emoji_notice_shown
                ):
                    logger.warning(
                        "Telegram отклонил premium-эмодзи: боту нужен username, "
                        "купленный на Fragment. Отправляю без premium-эмодзи, "
                        "жирный/курсив сохраняю."
                    )
                    _premium_emoji_notice_shown = True
                else:
                    logger.debug(
                        "Упрощаю разметку для %s: %s", chat_id, exc
                    )
                index += 1
                continue
            logger.warning("Ошибка Telegram API для %s: %s", chat_id, exc)
            return False, last_error
        except TelegramAPIError as exc:
            logger.warning("Ошибка Telegram API для %s: %s", chat_id, exc)
            return False, f"api: {exc}"[:300]
        except Exception:
            logger.exception("Неожиданная ошибка отправки сообщения %s", chat_id)
            return False, "unexpected"
    return False, last_error


async def send_manual_broadcast(
    bot: Bot, users: list[User], text: str
) -> BroadcastResult:
    """Отправляет ручное сообщение выбранной аудитории и считает результат."""
    sent = 0
    for user in users:
        if await _safe_send(bot, user.telegram_id, text):
            sent += 1
        await asyncio.sleep(0.05)
    return BroadcastResult(total=len(users), sent=sent)


async def send_manual_broadcast_detailed(
    bot: Bot, users: list[User], content: BroadcastContent
) -> DetailedBroadcastResult:
    """Отправляет контент и собирает детализированные ошибки по пользователям."""
    sent = 0
    failures: list[DeliveryFailure] = []
    for user in users:
        ok, error = await _safe_send_content(bot, user.telegram_id, content)
        if ok:
            sent += 1
        else:
            failures.append(
                DeliveryFailure(
                    telegram_id=user.telegram_id,
                    username=user.username,
                    error=error or "unknown",
                )
            )
        await asyncio.sleep(0.05)
    return DetailedBroadcastResult(
        total=len(users), sent=sent, failures=tuple(failures)
    )


async def send_stored_broadcast(bot: Bot, broadcast_id: int) -> DetailedBroadcastResult:
    """Отправляет сохранённую рассылку (повтор/черновик/отложенная) и пишет итог в БД."""
    from app.database import broadcast_repo
    from app.database import repository as repo

    record = broadcast_repo.get_broadcast(broadcast_id)
    if record is None:
        return DetailedBroadcastResult(total=0, sent=0, failures=())
    content = BroadcastContent(
        content_type=record.content_type or "text",
        text=record.text,
        file_id=record.file_id,
        text_format=record.text_format or TEXT_FORMAT_PLAIN,
    )
    if record.audience_type == "all":
        users = repo.get_all_users()
    elif record.audience_type == "enabled":
        users = repo.get_notification_users()
    elif record.group_name:
        users = repo.get_group_users(record.group_name)
    else:
        users = []
    broadcast_repo.mark_broadcast_sending(broadcast_id)
    result = await send_manual_broadcast_detailed(bot, users, content)
    broadcast_repo.mark_broadcast_sent(
        broadcast_id, total=result.total, sent=result.sent, failed=result.failed
    )
    failed_ids = {item.telegram_id for item in result.failures}
    broadcast_repo.save_deliveries(
        broadcast_id,
        [(item.telegram_id, item.username, "failed", item.error) for item in result.failures]
        + [
            (user.telegram_id, user.username, "sent", None)
            for user in users
            if user.telegram_id not in failed_ids
        ],
    )
    return result
