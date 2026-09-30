"""Центр ручных рассылок: создание, предпросмотр, отправка."""
from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.broadcast_common import (
    audience_label,
    audience_snapshot,
    content_preview_text,
    is_admin,
    resolve_audience,
    show_center,
)
from app.bot.keyboards.broadcast_kb import (
    audience_keyboard,
    center_keyboard,
    confirm_keyboard,
    groups_keyboard,
)
from app.database import broadcast_repo, repository as repo
from app.services.manual_broadcast_content import (
    BroadcastContent,
    content_from_message,
    content_from_state,
)
from app.services.manual_broadcast_service import send_manual_broadcast_detailed

router = Router(name="broadcast")


class BroadcastStates(StatesGroup):
    """Состояния создания ручной рассылки."""

    waiting_text = State()
    waiting_schedule = State()


async def _send_preview(
    message: Message, label: str, count: int, content: BroadcastContent
) -> None:
    header = (
        "📋 <b>Предварительный просмотр</b>\n\n"
        f"Аудитория: <b>{escape(label)}</b>\n"
        f"Получателей: <b>{count}</b>\n\n"
        f"{content_preview_text(content)}"
    )
    if content.content_type == "photo" and content.file_id:
        await message.answer(header)
        await message.answer_photo(
            photo=content.file_id,
            caption=content.text or None,
            reply_markup=confirm_keyboard(),
        )
        return
    if content.content_type == "document" and content.file_id:
        await message.answer(header)
        await message.answer_document(
            document=content.file_id,
            caption=content.text or None,
            reply_markup=confirm_keyboard(),
        )
        return
    await message.answer(header, reply_markup=confirm_keyboard())


@router.callback_query(F.data == "admin:broadcast")
async def open_broadcast_center(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await show_center(callback.message)


@router.callback_query(F.data == "broadcast:center")
async def back_to_center(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await show_center(callback.message)


@router.callback_query(F.data == "broadcast:new")
async def choose_broadcast_audience(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(
        "📣 <b>Новая рассылка</b>\n\nКому отправить сообщение?",
        reply_markup=audience_keyboard(),
    )


@router.callback_query(F.data == "broadcast:audience:group")
async def choose_broadcast_group(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "🎓 Выберите группу:", reply_markup=groups_keyboard(repo.users_by_group())
    )


@router.callback_query(F.data.startswith("broadcast:audience:"))
async def choose_broadcast_audience_type(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    audience = (callback.data or "").rsplit(":", 1)[1]
    if audience == "group":
        return
    label, count = audience_snapshot(audience)
    await state.update_data(audience=audience, group_name=None, recipient_label=label)
    await state.set_state(BroadcastStates.waiting_text)
    await callback.answer()
    await callback.message.edit_text(
        f"Получателей: <b>{count}</b>\n\nПришлите текст, фото или документ:",
    )


@router.callback_query(F.data.startswith("broadcast:group:"))
async def choose_broadcast_group_value(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_name = (callback.data or "").removeprefix("broadcast:group:")
    label, count = audience_snapshot("group", group_name)
    await state.update_data(audience="group", group_name=group_name, recipient_label=label)
    await state.set_state(BroadcastStates.waiting_text)
    await callback.answer()
    await callback.message.edit_text(
        f"Аудитория: <b>{escape(label)}</b>\nПолучателей: <b>{count}</b>\n\nПришлите текст, фото или документ:",
    )


@router.message(BroadcastStates.waiting_text)
async def show_broadcast_preview(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    content = content_from_message(message)
    if content is None:
        await message.answer("Не вижу текст, фото или документ. Пришлите сообщение ещё раз:")
        return
    data = await state.get_data()
    audience = data.get("audience", "")
    group_name = data.get("group_name")
    label = audience_label(audience, group_name) if isinstance(audience, str) else "Не выбрана"
    users = resolve_audience(audience, group_name) if isinstance(audience, str) else []
    await state.update_data(
        content_type=content.content_type, text=content.text, file_id=content.file_id
    )
    await _send_preview(message, label, len(users), content)


@router.callback_query(F.data == "broadcast:edit")
async def edit_broadcast_text(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastStates.waiting_text)
    await callback.answer()
    await callback.message.answer("Пришлите новый текст, фото или документ:")


@router.callback_query(F.data == "broadcast:send")
async def send_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
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
    users = resolve_audience(audience, group_name)
    draft_id = data.get("broadcast_id")
    if isinstance(draft_id, int):
        broadcast_id = draft_id
        broadcast_repo.update_broadcast_content(
            broadcast_id,
            content_type=content.content_type,
            text=content.text,
            file_id=content.file_id,
        )
    else:
        broadcast_id = broadcast_repo.create_broadcast(
            audience_type=audience,
            group_name=group_name,
            content_type=content.content_type,
            text=content.text,
            file_id=content.file_id,
            status="sending",
            created_by=callback.from_user.id,
        )
    broadcast_repo.mark_broadcast_sending(broadcast_id)
    await state.clear()
    await callback.answer("Начинаю рассылку…")
    result = await send_manual_broadcast_detailed(bot, users, content)
    broadcast_repo.mark_broadcast_sent(
        broadcast_id, total=result.total, sent=result.sent, failed=result.failed
    )
    broadcast_repo.save_deliveries(
        broadcast_id,
        [
            (item.telegram_id, item.username, "failed", item.error)
            for item in result.failures
        ]
        + [
            (user.telegram_id, user.username, "sent", None)
            for user in users
            if user.telegram_id not in {item.telegram_id for item in result.failures}
        ],
    )
    label = audience_label(audience, group_name)
    await callback.message.answer(
        "📤 <b>Рассылка завершена</b>\n\n"
        f"Номер: <b>№{broadcast_id}</b>\n"
        f"Аудитория: <b>{escape(label)}</b>\n"
        f"Всего получателей: <b>{result.total}</b>\n"
        f"Доставлено: <b>{result.sent}</b>\n"
        f"Ошибки: <b>{result.failed}</b>",
        reply_markup=center_keyboard(),
    )


@router.callback_query(F.data == "broadcast:cancel")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer("Отменено")
    await show_center(callback.message)
