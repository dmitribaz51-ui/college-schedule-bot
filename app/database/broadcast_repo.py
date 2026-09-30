"""Запросы для ручных рассылок: история, черновики, отложенные, детали ошибок."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select

from .database import get_session
from .models import Broadcast, BroadcastDelivery


def create_broadcast(
    *,
    audience_type: str,
    group_name: str | None = None,
    content_type: str = "text",
    text: str | None = None,
    file_id: str | None = None,
    status: str = "draft",
    created_by: int | None = None,
    scheduled_at: datetime | None = None,
) -> int:
    """Создаёт запись рассылки и возвращает её id."""
    with get_session() as s:
        record = Broadcast(
            audience_type=audience_type,
            group_name=group_name,
            content_type=content_type,
            text=text,
            file_id=file_id,
            status=status,
            created_by=created_by,
            scheduled_at=scheduled_at,
        )
        s.add(record)
        s.flush()
        return record.id


def get_broadcast(broadcast_id: int) -> Broadcast | None:
    with get_session() as s:
        return s.scalar(select(Broadcast).where(Broadcast.id == broadcast_id))


def list_broadcasts(limit: int = 10, include_drafts: bool = False) -> list[Broadcast]:
    """История рассылок, свежие сначала. Черновики по умолчанию скрыты."""
    with get_session() as s:
        stmt = select(Broadcast).order_by(Broadcast.id.desc()).limit(limit)
        if not include_drafts:
            stmt = stmt.where(Broadcast.status != "draft")
        return list(s.scalars(stmt))


def list_drafts(limit: int = 10) -> list[Broadcast]:
    with get_session() as s:
        return list(
            s.scalars(
                select(Broadcast)
                .where(Broadcast.status == "draft")
                .order_by(Broadcast.id.desc())
                .limit(limit)
            )
        )


def list_due_scheduled(now: datetime) -> list[Broadcast]:
    """Отложенные рассылки, время которых наступило."""
    with get_session() as s:
        return list(
            s.scalars(
                select(Broadcast)
                .where(Broadcast.status == "scheduled")
                .where(Broadcast.scheduled_at.is_not(None))
                .where(Broadcast.scheduled_at <= now)
                .order_by(Broadcast.scheduled_at)
            )
        )


def update_broadcast_content(
    broadcast_id: int,
    *,
    content_type: str,
    text: str | None,
    file_id: str | None,
) -> None:
    with get_session() as s:
        record = s.scalar(select(Broadcast).where(Broadcast.id == broadcast_id))
        if record is None:
            return
        record.content_type = content_type
        record.text = text
        record.file_id = file_id


def mark_broadcast_sending(broadcast_id: int) -> None:
    with get_session() as s:
        record = s.scalar(select(Broadcast).where(Broadcast.id == broadcast_id))
        if record:
            record.status = "sending"


def mark_broadcast_scheduled(broadcast_id: int, scheduled_at: datetime) -> None:
    with get_session() as s:
        record = s.scalar(select(Broadcast).where(Broadcast.id == broadcast_id))
        if record:
            record.status = "scheduled"
            record.scheduled_at = scheduled_at


def mark_broadcast_sent(
    broadcast_id: int, *, total: int, sent: int, failed: int
) -> None:
    with get_session() as s:
        record = s.scalar(select(Broadcast).where(Broadcast.id == broadcast_id))
        if record:
            record.status = "sent"
            record.total_recipients = total
            record.sent_count = sent
            record.failed_count = failed
            record.sent_at = datetime.now()


def mark_broadcast_cancelled(broadcast_id: int) -> None:
    with get_session() as s:
        record = s.scalar(select(Broadcast).where(Broadcast.id == broadcast_id))
        if record:
            record.status = "cancelled"


def delete_broadcast(broadcast_id: int) -> None:
    with get_session() as s:
        s.execute(
            delete(BroadcastDelivery).where(
                BroadcastDelivery.broadcast_id == broadcast_id
            )
        )
        s.execute(delete(Broadcast).where(Broadcast.id == broadcast_id))


def save_deliveries(
    broadcast_id: int,
    rows: list[tuple[int, str | None, str, str | None]],
) -> None:
    """Сохраняет детализацию: (telegram_id, username, status, error)."""
    with get_session() as s:
        for telegram_id, username, status, error in rows:
            s.add(
                BroadcastDelivery(
                    broadcast_id=broadcast_id,
                    telegram_id=telegram_id,
                    username=username,
                    status=status,
                    error=error,
                )
            )


def get_deliveries(broadcast_id: int, only_failed: bool = False) -> list[BroadcastDelivery]:
    with get_session() as s:
        stmt = select(BroadcastDelivery).where(
            BroadcastDelivery.broadcast_id == broadcast_id
        )
        if only_failed:
            stmt = stmt.where(BroadcastDelivery.status == "failed")
        return list(s.scalars(stmt.order_by(BroadcastDelivery.telegram_id)))


def count_broadcasts() -> int:
    with get_session() as s:
        return s.scalar(select(func.count(Broadcast.id))) or 0
