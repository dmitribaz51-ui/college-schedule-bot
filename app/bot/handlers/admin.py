"""Админ-функции. Доступ только для ADMIN_IDS из .env."""
from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.menu import admin_keyboard
from app.config import get_config
from app.database import repository as repo
from app.services.update_service import check_for_updates

router = Router(name="admin")


def _is_admin(telegram_id: int) -> bool:
    return get_config().is_admin(telegram_id)


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    stats = repo.users_stats()
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {stats['total']}\n"
        f"🎓 С выбранной группой: {stats['with_group']}\n"
        f"🔔 С уведомлениями: {stats['notifications_on']}\n"
        f"🗂 Файлов в базе: {repo.count_files()}\n"
        f"📚 Записей расписания: {repo.count_lessons()}",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:check")
async def admin_check(callback: CallbackQuery, bot: Bot) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Проверяю сайт…")
    report = await check_for_updates(bot=bot)
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
    if not isinstance(callback.message, Message):
        await callback.answer("Откройте /admin заново", show_alert=True)
        return
    await callback.answer()
    rows = repo.users_by_group()
    if not rows:
        await callback.message.answer("Пока никто не выбрал группу.")
    else:
        text = "👥 <b>Пользователи по группам</b>"
        for group, count in rows:
            heading = f"\n\n<b>• {escape(group)}: {count}</b>"
            if len(text) + len(heading) > 3500:
                await callback.message.answer(text, parse_mode="HTML")
                text = "👥 <b>Пользователи по группам</b>"
            text += heading
            users = repo.get_users_by_groups([group], only_enabled=False)
            for user in sorted(users, key=lambda item: item.telegram_id):
                label = f"@{escape(user.username)}" if user.username else str(user.telegram_id)
                line = f"\n    {label}"
                if len(text) + len(line) > 3500:
                    await callback.message.answer(text, parse_mode="HTML")
                    text = f"<b>• {escape(group)} (продолжение)</b>"
                text += line
        await callback.message.answer(text, parse_mode="HTML")
