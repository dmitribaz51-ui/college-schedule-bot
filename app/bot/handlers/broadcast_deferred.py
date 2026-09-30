"""Отложенные действия рассылки: черновик и планирование."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.broadcast import BroadcastStates
from app.bot.handlers.broadcast_common import (
    audience_label,
    is_admin,
    parse_schedule_datetime,
)
from app.bot.keyboards.broadcast_kb import center_keyboard
from app.database import broadcast_repo
from app.services.manual_broadcast_content import content_from_state

router = Router(name="broadcast_deferred")


@router.callback_query(F.data == "broadcast:save_draft")
async def save_broadcast_draft(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    content = content_from_state(data)
    audience = data.get("audience")
    if content is None or not isinstance(audience, str):
        await callback.answer("Сначала подготовьте сообщение", show_alert=True)
        return
    group_name = data.get("group_name") if isinstance(data.get("group_name"), str) else None
    draft_id = data.get("broadcast_id")
    if isinstance(draft_id, int):
        broadcast_repo.update_broadcast_content(
            draft_id,
            content_type=content.content_type,
            text=content.text,
            file_id=content.file_id,
        )
    else:
        draft_id = broadcast_repo.create_broadcast(
            audience_type=audience,
            group_name=group_name,
            content_type=content.content_type,
            text=content.text,
            file_id=content.file_id,
            status="draft",
            created_by=callback.from_user.id,
        )
    await state.clear()
    await callback.answer("Черновик сохранён")
    await callback.message.answer(
        f"📝 Черновик №{draft_id} сохранён.", reply_markup=center_keyboard()
    )


@router.callback_query(F.data == "broadcast:schedule")
async def ask_schedule_time(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    if content_from_state(data) is None:
        await callback.answer("Сначала подготовьте сообщение", show_alert=True)
        return
    await state.set_state(BroadcastStates.waiting_schedule)
    await callback.answer()
    await callback.message.answer(
        "🕒 Введите дату и время отправки в формате:\n<b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\nНапример: 05.10.2026 09:00"
    )


@router.message(BroadcastStates.waiting_schedule)
async def save_scheduled_broadcast(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data_first = await state.get_data()
    if data_first.get("draft_scheduling"):
        return
    when = parse_schedule_datetime(message.text or "")
    if when is None:
        await message.answer(
            "Не понял дату. Формат: <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>, время должно быть в будущем."
        )
        return
    data = await state.get_data()
    content = content_from_state(data)
    audience = data.get("audience")
    if content is None or not isinstance(audience, str):
        await message.answer("Сообщение потерялось. Начните рассылку заново.")
        await state.clear()
        return
    group_name = data.get("group_name") if isinstance(data.get("group_name"), str) else None
    draft_id = data.get("broadcast_id")
    if isinstance(draft_id, int):
        broadcast_repo.update_broadcast_content(
            draft_id,
            content_type=content.content_type,
            text=content.text,
            file_id=content.file_id,
        )
        broadcast_repo.mark_broadcast_scheduled(draft_id, when)
        saved_id = draft_id
    else:
        saved_id = broadcast_repo.create_broadcast(
            audience_type=audience,
            group_name=group_name,
            content_type=content.content_type,
            text=content.text,
            file_id=content.file_id,
            status="scheduled",
            created_by=message.from_user.id,
            scheduled_at=when,
        )
    await state.clear()
    label = audience_label(audience, group_name)
    await message.answer(
        f"🕒 Рассылка №{saved_id} запланирована.\n"
        f"Аудитория: <b>{escape(label)}</b>\n"
        f"Время: <b>{when.strftime('%d.%m.%Y %H:%M')}</b>",
        reply_markup=center_keyboard(),
    )
