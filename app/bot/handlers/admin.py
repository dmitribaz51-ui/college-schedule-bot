"""Админ-функции. Доступ только для ADMIN_IDS из .env."""
from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from app.bot.keyboards.menu import admin_keyboard
from app.config import get_config
from app.database import repository as repo
from app.services.update_service import check_for_updates

router = Router(name="admin")


class AdminStates(StatesGroup):
    waiting_username = State()

GROUPS_PAGE_SIZE = 25
TEXT_LIMIT = 3500


def _is_admin(telegram_id: int) -> bool:
    return get_config().is_admin(telegram_id)


def _panel_text() -> str:
    stats = repo.users_stats()
    return (
        "🛠 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {stats['total']}\n"
        f"🎓 С выбранной группой: {stats['with_group']}\n"
        f"🔔 С уведомлениями: {stats['notifications_on']}\n"
        f"🗂 Файлов в базе: {repo.count_files()}\n"
        f"📚 Записей расписания: {repo.count_lessons()}"
    )


def _require_message(callback: CallbackQuery) -> Message | None:
    return callback.message if isinstance(callback.message, Message) else None


def _groups_keyboard(
    rows: list[tuple[str, int]], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{group} — {count}", callback_data=f"admin:g:{group}:{page}")]
        for group, count in rows
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:glist:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:glist:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _show_groups_page(message: Message, page: int) -> None:
    rows = repo.users_by_group()
    if not rows:
        await message.answer("Пока никто не выбрал группу.")
        return
    total_pages = (len(rows) + GROUPS_PAGE_SIZE - 1) // GROUPS_PAGE_SIZE
    page = min(max(page, 0), total_pages - 1)
    chunk = rows[page * GROUPS_PAGE_SIZE:(page + 1) * GROUPS_PAGE_SIZE]
    title = "👥 <b>Пользователи по группам</b> — выберите группу:"
    if total_pages > 1:
        title += f"\nСтр. {page + 1}/{total_pages}"
    await message.edit_text(title, reply_markup=_groups_keyboard(chunk, page, total_pages))


def _split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        piece = line if not current else current + "\n" + line
        if len(piece) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = piece
    if current:
        chunks.append(current)
    return chunks


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer(_panel_text(), reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin:panel")
async def admin_panel_back(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    message = _require_message(callback)
    if message is None:
        await callback.answer("Откройте /admin заново", show_alert=True)
        return
    await callback.answer()
    await message.edit_text(_panel_text(), reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin:check")
async def admin_check(callback: CallbackQuery, bot: Bot) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Проверяю сайт…")
    report = await check_for_updates(
        bot=bot, force_reprocess=True, notify_users=False
    )
    await callback.message.answer(f"🔄 <b>Результат проверки</b>\n\n{report.summary()}")


@router.callback_query(F.data == "admin:files")
async def admin_files(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    files = repo.get_recent_files(limit=10)
    if not files:
        await callback.message.answer("Файлов пока нет.")
        await callback.answer()
        return

    lines = ["🗂 <b>Последние файлы</b>", ""]
    for item in files:
        day = item.schedule_date.strftime("%d.%m.%Y") if item.schedule_date else "?"
        status = "✅" if item.processed else "❌"
        lines.append(f"{status} [{item.file_type}] {day} — {item.title}")
        if item.error:
            lines.append(f"    ⚠️ {item.error}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "admin:groups")
async def admin_groups(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    message = _require_message(callback)
    if message is None:
        await callback.answer("Откройте /admin заново", show_alert=True)
        return
    await callback.answer()
    await _show_groups_page(message, 0)


@router.callback_query(F.data.startswith("admin:glist:"))
async def admin_groups_page(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    message = _require_message(callback)
    if message is None:
        await callback.answer("Откройте /admin заново", show_alert=True)
        return
    try:
        page = int((callback.data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректная страница", show_alert=True)
        return
    await callback.answer()
    await _show_groups_page(message, page)


@router.callback_query(F.data.startswith("admin:g:"))
async def admin_group_detail(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    message = _require_message(callback)
    if message is None:
        await callback.answer("Откройте /admin заново", show_alert=True)
        return
    payload = (callback.data or "").removeprefix("admin:g:")
    group, _, page_raw = payload.rpartition(":")
    try:
        page = int(page_raw)
    except ValueError:
        await callback.answer("Некорректная группа", show_alert=True)
        return
    await callback.answer()

    users = repo.get_users_by_groups([group], only_enabled=False)
    lines = [f"<b>• {escape(group)}: {len(users)}</b>", ""]
    for user in sorted(users, key=lambda item: item.telegram_id):
        label = f"@{escape(user.username)}" if user.username else str(user.telegram_id)
        lines.append(f"    {label}")
    chunks = _split_text("\n".join(lines))
    back = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ К группам", callback_data=f"admin:glist:{page}")
    ]])
    await message.edit_text(chunks[0], reply_markup=back, parse_mode="HTML")
    for chunk in chunks[1:]:
        await message.answer(chunk, parse_mode="HTML")


@router.callback_query(F.data == "admin:search")
async def admin_search_user(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminStates.waiting_username)
    await callback.message.answer(
        "🔍 Введите username (с @ или без) или Telegram ID:"
    )


@router.message(AdminStates.waiting_username)
async def process_username_search(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    
    query = (message.text or "").strip()
    if not query:
        await message.answer("Вы не ввели username или ID.")
        return

    if query.lstrip("@").isdigit():
        user = repo.get_user(int(query.lstrip("@")))
    else:
        user = repo.find_user_by_username(query)
    if user is None:
        await message.answer("Пользователь не найден.")
        return
    
    username_display = f"@{escape(user.username)}" if user.username else str(user.telegram_id)
    group_display = escape(user.group_name) if user.group_name else "<i>не выбрана</i>"
    
    await message.answer(
        f"✅ <b>Найден пользователь:</b>\n\n"
        f"Username: {username_display}\n"
        f"Группа: {group_display}",
        parse_mode="HTML"
    )
