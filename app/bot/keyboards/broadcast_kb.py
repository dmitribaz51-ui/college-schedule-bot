"""Клавиатуры центра рассылок (этап 2)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.models import Broadcast


def center_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✈️ Новая рассылка", callback_data="broadcast:new")],
            [InlineKeyboardButton(text="🗂 История рассылок", callback_data="broadcast:history")],
            [InlineKeyboardButton(text="📝 Черновики", callback_data="broadcast:drafts")],
            [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin:panel")],
        ]
    )


def audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Всем пользователям", callback_data="broadcast:audience:all")],
            [InlineKeyboardButton(text="🎓 Конкретной группе", callback_data="broadcast:audience:group")],
            [InlineKeyboardButton(text="🔔 Только подписанным", callback_data="broadcast:audience:enabled")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel")],
        ]
    )


def groups_keyboard(rows: list[tuple[str, int]]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{group} — {count}", callback_data=f"broadcast:group:{group}")]
        for group, count in rows
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить сейчас", callback_data="broadcast:send")],
            [InlineKeyboardButton(text="🕒 Отложить", callback_data="broadcast:schedule")],
            [InlineKeyboardButton(text="💾 В черновик", callback_data="broadcast:save_draft")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="broadcast:edit")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="broadcast:cancel")],
        ]
    )


def history_keyboard(items: list[Broadcast]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for item in items:
        day = item.created_at.strftime("%d.%m %H:%M") if item.created_at else "?"
        status = _status_emoji(item.status)
        title = (item.text or "[медиа]").strip().replace("\n", " ")[:28] or "[медиа]"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {title} · {day}",
                    callback_data=f"broadcast:history:{item.id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ В центр рассылок", callback_data="broadcast:center")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def history_detail_keyboard(broadcast_id: int, has_errors: bool) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if has_errors:
        buttons.append(
            [InlineKeyboardButton(text="📋 Ошибки", callback_data=f"broadcast:errors:{broadcast_id}")]
        )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ К истории", callback_data="broadcast:history")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def drafts_keyboard(items: list[Broadcast]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for item in items:
        day = item.created_at.strftime("%d.%m %H:%M") if item.created_at else "?"
        title = (item.text or "[медиа]").strip().replace("\n", " ")[:28] or "[медиа]"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"📝 {title} · {day}",
                    callback_data=f"broadcast:draft:{item.id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ В центр рассылок", callback_data="broadcast:center")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def draft_detail_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить сейчас", callback_data=f"broadcast:draft_send:{broadcast_id}")],
            [InlineKeyboardButton(text="🕒 Отложить", callback_data=f"broadcast:draft_schedule:{broadcast_id}")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"broadcast:draft_edit:{broadcast_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"broadcast:draft_delete:{broadcast_id}")],
            [InlineKeyboardButton(text="⬅️ К черновикам", callback_data="broadcast:drafts")],
        ]
    )


def _status_emoji(status: str) -> str:
    if status == "sent":
        return "✅"
    if status == "scheduled":
        return "🕒"
    if status == "sending":
        return "📤"
    if status == "cancelled":
        return "🚫"
    return "📢"
