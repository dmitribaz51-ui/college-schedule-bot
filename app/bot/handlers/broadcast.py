"""Первый этап центра ручных рассылок для администраторов."""
from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import get_config
from app.database import repository as repo
from app.services.manual_broadcast_service import send_manual_broadcast

router = Router(name="broadcast")


class BroadcastStates(StatesGroup):
    """Состояния создания ручной рассылки."""

    waiting_text = State()


def _is_admin(telegram_id: int) -> bool:
    return get_config().is_admin(telegram_id)


def _center_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✈️ Новая рассылка", callback_data="broadcast:new")],
            [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin:panel")],
        ]
    )


def _audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Всем пользователям", callback_data="broadcast:audience:all")],
            [InlineKeyboardButton(text="🎓 Конкретной группе", callback_data="broadcast:audience:group")],
            [InlineKeyboardButton(text="🔔 Только подписанным", callback_data="broadcast:audience:enabled")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel")],
        ]
    )


def _groups_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{group} — {count}", callback_data=f"broadcast:group:{group}")]
        for group, count in repo.users_by_group()
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast:send")],
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="broadcast:edit")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="broadcast:cancel")],
        ]
    )


def _audience_data(audience: str, group_name: str | None = None) -> tuple[str, list]:
    if audience == "all":
        return "Все пользователи", repo.get_all_users()
    if audience == "enabled":
        return "Только подписанные на уведомления", repo.get_notification_users()
    if group_name is not None:
        return f"Группа {group_name}", repo.get_group_users(group_name)
    return "Не выбрана", []


async def _show_center(message: Message) -> None:
    await message.edit_text(
        "📣 <b>Центр рассылок</b>\n\nВыберите действие:",
        reply_markup=_center_keyboard(),
    )


@router.callback_query(F.data == "admin:broadcast")
async def open_broadcast_center(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _show_center(callback.message)


@router.callback_query(F.data == "broadcast:new")
async def choose_broadcast_audience(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "📣 <b>Новая рассылка</b>\n\nКому отправить сообщение?",
        reply_markup=_audience_keyboard(),
    )


@router.callback_query(F.data == "broadcast:audience:group")
async def choose_broadcast_group(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text("🎓 Выберите группу:", reply_markup=_groups_keyboard())


@router.callback_query(F.data.startswith("broadcast:audience:"))
async def choose_broadcast_audience_type(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    audience = (callback.data or "").rsplit(":", 1)[1]
    label, users = _audience_data(audience)
    await state.update_data(audience=audience, recipient_label=label)
    await state.set_state(BroadcastStates.waiting_text)
    await callback.answer()
    await callback.message.edit_text(
        f"Получателей: <b>{len(users)}</b>\n\nВведите текст обычным сообщением:",
    )


@router.callback_query(F.data.startswith("broadcast:group:"))
async def choose_broadcast_group_value(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_name = (callback.data or "").removeprefix("broadcast:group:")
    label, users = _audience_data("group", group_name)
    await state.update_data(audience="group", group_name=group_name, recipient_label=label)
    await state.set_state(BroadcastStates.waiting_text)
    await callback.answer()
    await callback.message.edit_text(
        f"Аудитория: <b>{escape(label)}</b>\nПолучателей: <b>{len(users)}</b>\n\nВведите текст обычным сообщением:",
    )


@router.message(BroadcastStates.waiting_text)
async def show_broadcast_preview(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст рассылки не может быть пустым. Введите сообщение ещё раз:")
        return
    data = await state.get_data()
    label, users = _audience_data(data.get("audience", ""), data.get("group_name"))
    await state.update_data(text=text, recipient_count=len(users))
    await message.answer(
        "📋 <b>Предварительный просмотр</b>\n\n"
        f"Аудитория: <b>{escape(label)}</b>\n"
        f"Получателей: <b>{len(users)}</b>\n\n"
        "<b>Сообщение:</b>\n"
        f"{escape(text)}",
        reply_markup=_confirm_keyboard(),
    )


@router.callback_query(F.data == "broadcast:edit")
async def edit_broadcast_text(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastStates.waiting_text)
    await callback.answer()
    await callback.message.answer("Введите новый текст обычным сообщением:")


@router.callback_query(F.data == "broadcast:send")
async def send_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("text")
    if not isinstance(text, str):
        await callback.answer("Сначала подготовьте текст", show_alert=True)
        return
    _, users = _audience_data(data.get("audience", ""), data.get("group_name"))
    await state.clear()
    await callback.answer("Начинаю рассылку…")
    result = await send_manual_broadcast(bot, users, text)
    await callback.message.edit_text(
        "📤 <b>Рассылка завершена</b>\n\n"
        f"Всего получателей: <b>{result.total}</b>\n"
        f"Доставлено: <b>{result.sent}</b>\n"
        f"Ошибки: <b>{result.failed}</b>",
        reply_markup=_center_keyboard(),
    )


@router.callback_query(F.data == "broadcast:cancel")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer("Отменено")
    await _show_center(callback.message)
