"""Черновики ручных рассылок: список, отправка, планирование, удаление."""
from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.broadcast import BroadcastStates
from app.bot.handlers.broadcast_common import (
    audience_label,
    content_preview_text,
    is_admin,
    parse_schedule_datetime,
    resolve_audience,
    show_center,
)
from app.bot.keyboards.broadcast_kb import (
    center_keyboard,
    draft_detail_keyboard,
    drafts_keyboard,
)
from app.database import broadcast_repo
from app.services.manual_broadcast_content import BroadcastContent
from app.services.manual_broadcast_service import send_manual_broadcast_detailed

router = Router(name="broadcast_drafts")


def _detail_text(record) -> str:
    content = BroadcastContent(
        content_type=record.content_type or "text",
        text=record.text,
        file_id=record.file_id,
    )
    when = record.created_at.strftime("%d.%m.%Y %H:%M") if record.created_at else "?"
    label = audience_label(record.audience_type, record.group_name)
    lines = [
        f"📝 <b>Черновик №{record.id}</b>",
        "",
        f"Аудитория: <b>{escape(label)}</b>",
        f"Создан: <b>{escape(when)}</b>",
        "",
        content_preview_text(content),
    ]
    return "\n".join(lines)


@router.callback_query(F.data == "broadcast:drafts")
async def open_drafts(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    items = broadcast_repo.list_drafts(limit=10)
    await callback.answer()
    if not items:
        await callback.message.edit_text(
            "📝 Черновиков нет.",
            reply_markup=center_keyboard(),
        )
        return
    await callback.message.edit_text(
        "📝 <b>Черновики</b> — выберите для продолжения:",
        reply_markup=drafts_keyboard(items),
    )


@router.callback_query(F.data.regexp(r"^broadcast:draft:\d+$"))
async def open_draft(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = (callback.data or "")
    try:
        broadcast_id = int(data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректный черновик", show_alert=True)
        return
    record = broadcast_repo.get_broadcast(broadcast_id)
    if record is None or record.status != "draft":
        await callback.answer("Черновик не найден", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        _detail_text(record), reply_markup=draft_detail_keyboard(record.id)
    )


@router.callback_query(F.data.startswith("broadcast:draft_send:"))
async def send_draft(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        broadcast_id = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректный черновик", show_alert=True)
        return
    record = broadcast_repo.get_broadcast(broadcast_id)
    if record is None:
        await callback.answer("Черновик не найден", show_alert=True)
        return
    content = BroadcastContent(
        content_type=record.content_type or "text",
        text=record.text,
        file_id=record.file_id,
    )
    users = resolve_audience(record.audience_type, record.group_name)
    broadcast_repo.mark_broadcast_sending(broadcast_id)
    await callback.answer("Отправляю черновик…")
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
    await callback.message.answer(
        f"📤 Черновик №{broadcast_id} отправлен: {result.sent}/{result.total}.",
        reply_markup=center_keyboard(),
    )


@router.callback_query(F.data.startswith("broadcast:draft_schedule:"))
async def ask_draft_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        broadcast_id = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректный черновик", show_alert=True)
        return
    await state.update_data(broadcast_id=broadcast_id, draft_scheduling=True)
    await state.set_state(BroadcastStates.waiting_schedule)
    await callback.answer()
    await callback.message.answer(
        f"🕒 Черновик №{broadcast_id}: введите дату и время <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>:"
    )


@router.callback_query(F.data.startswith("broadcast:draft_edit:"))
async def edit_draft(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        broadcast_id = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректный черновик", show_alert=True)
        return
    record = broadcast_repo.get_broadcast(broadcast_id)
    if record is None:
        await callback.answer("Черновик не найден", show_alert=True)
        return
    await state.update_data(
        audience=record.audience_type,
        group_name=record.group_name,
        broadcast_id=record.id,
        content_type=record.content_type,
        text=record.text,
        file_id=record.file_id,
    )
    await state.set_state(BroadcastStates.waiting_text)
    await callback.answer()
    await callback.message.answer("Пришлите новый текст, фото или документ для черновика:")


@router.callback_query(F.data.startswith("broadcast:draft_delete:"))
async def delete_draft(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        broadcast_id = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректный черновик", show_alert=True)
        return
    broadcast_repo.mark_broadcast_cancelled(broadcast_id)
    broadcast_repo.delete_broadcast(broadcast_id)
    await callback.answer("Черновик удалён")
    await show_center(callback.message)


@router.message(BroadcastStates.waiting_schedule)
async def save_draft_schedule(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    if not data.get("draft_scheduling"):
        return
    broadcast_id = data.get("broadcast_id")
    if not isinstance(broadcast_id, int):
        await state.clear()
        return
    when = parse_schedule_datetime(message.text or "")
    if when is None:
        await message.answer("Не понял дату. Формат: <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>, время в будущем.")
        return
    broadcast_repo.mark_broadcast_scheduled(broadcast_id, when)
    await state.clear()
    await message.answer(
        f"🕒 Черновик №{broadcast_id} запланирован на <b>{when.strftime('%d.%m.%Y %H:%M')}</b>.",
        reply_markup=center_keyboard(),
    )
