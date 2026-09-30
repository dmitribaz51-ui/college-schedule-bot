"""Отправка ручных админских рассылок, отдельно от уведомлений расписания."""
from __future__ import annotations

from dataclasses import dataclass

import asyncio
from aiogram import Bot

from app.database.models import User
from app.services.notification_service import _safe_send


@dataclass(frozen=True, slots=True)
class BroadcastResult:
    """Итог ручной рассылки."""

    total: int
    sent: int

    @property
    def failed(self) -> int:
        return self.total - self.sent


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
