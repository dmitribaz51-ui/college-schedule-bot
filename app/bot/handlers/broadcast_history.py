"""История и ошибки ручных рассылок."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.handlers.broadcast_common import (
    audience_label,
    content_preview_text,
    is_admin,
)
from app.bot.keyboards.broadcast_kb import (
    center_keyboard,
    history_detail_keyboard,
    history_keyboard,
)
from app.database import broadcast_repo
from app.services.manual_broadcast_content import BroadcastContent

router = Router(name="broadcast_history")


def _audience_of(record) -> str:
    return audience_label(record.audience_type, record.group_name)


def _detail_text(record) -> str:
    content = BroadcastContent(
        content_type=record.content_type or "text",
        text=record.text,
        file_id=record.file_id,
    )
    when = record.created_at.strftime("%d.%m.%Y %H:%M") if record.created_at else "?"
    lines = [
        f"📢 <b>Рассылка №{record.id}</b>",
        "",
        f"Аудитория: <b>{escape(_audience_of(record))}</b>",
        f"Статус: <b>{escape(record.status)}</b>",
        f"Создана: <b>{escape(when)}</b>",
    ]
    if record.scheduled_at:
        lines.append(f"Запланирована: <b>{record.scheduled_at.strftime('%d.%m.%Y %H:%M')}</b>")
    if record.status == "sent":
        lines += [
            f"Всего: <b>{record.total_recipients}</b>",
            f"Доставлено: <b>{record.sent_count}</b>",
            f"Ошибки: <b>{record.failed_count}</b>",
        ]
    lines += ["", content_preview_text(content)]
    return "\n".join(lines)


@router.callback_query(F.data == "broadcast:history")
async def open_history(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    items = broadcast_repo.list_broadcasts(limit=10)
    await callback.answer()
    if not items:
        await callback.message.edit_text(
            "🗂 История пуста — рассылок пока не было.",
            reply_markup=center_keyboard(),
        )
        return
    await callback.message.edit_text(
        "🗂 <b>Последние рассылки</b> — выберите для деталей:",
        reply_markup=history_keyboard(items),
    )


@router.callback_query(F.data.startswith("broadcast:history:"))
async def open_history_detail(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        broadcast_id = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректная рассылка", show_alert=True)
        return
    record = broadcast_repo.get_broadcast(broadcast_id)
    if record is None:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        _detail_text(record),
        reply_markup=history_detail_keyboard(record.id, record.failed_count > 0),
    )


@router.callback_query(F.data.startswith("broadcast:errors:"))
async def open_errors(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        broadcast_id = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректная рассылка", show_alert=True)
        return
    failures = broadcast_repo.get_deliveries(broadcast_id, only_failed=True)
    await callback.answer()
    if not failures:
        await callback.message.answer(f"📋 По рассылке №{broadcast_id} ошибок нет.")
        return
    lines = [f"📋 <b>Ошибки рассылки №{broadcast_id}</b> (показаны первые 20):", ""]
    for item in failures[:20]:
        name = f"@{escape(item.username)}" if item.username else str(item.telegram_id)
        lines.append(f"• {name} — {escape(item.error or 'unknown')}")
    if len(failures) > 20:
        lines.append(f"…и ещё {len(failures) - 20}")
    await callback.message.answer("\n".join(lines))
