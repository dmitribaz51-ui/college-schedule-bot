
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)

BTN_TODAY = "📍 Сегодня"
BTN_TOMORROW = "📆 Завтра"
BTN_PICK_DATE = "📅 Выбрать дату"
BTN_CHANGES = "⚠️ Последние изменения"
BTN_GROUP = "⚙️ Моя группа"
BTN_NOTIFICATIONS = "🔔 Уведомления"
BTN_INFO = "ℹ️ Информация"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_PICK_DATE), KeyboardButton(text=BTN_CHANGES)],
            [KeyboardButton(text=BTN_GROUP), KeyboardButton(text=BTN_NOTIFICATIONS)],
            [KeyboardButton(text=BTN_INFO)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите пункт меню",
    )


def courses_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="1 курс", callback_data="course:1"),
            InlineKeyboardButton(text="2 курс", callback_data="course:2"),
        ],
        [
            InlineKeyboardButton(text="3 курс", callback_data="course:3"),
            InlineKeyboardButton(text="4 курс", callback_data="course:4"),
        ],
        [InlineKeyboardButton(text="⬅️ Факультет", callback_data="back_to_faculties")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faculties_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Предпринимательство (Пермская)", callback_data="faculty:permskaya")],
        [InlineKeyboardButton(text="Сервиса (Чернышевского)", callback_data="faculty:chernyshevskogo")],
    ])


def groups_keyboard(groups: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for group in groups[:60]:
        row.append(InlineKeyboardButton(text=group, callback_data=f"group:{group}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✍️ Ввести группу вручную",
                                      callback_data="group_manual")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к курсам",
                                      callback_data="back_to_courses")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notifications_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    text = "🔕 Отключить уведомления" if enabled else "🔔 Включить уведомления"
    value = "off" if enabled else "on"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=f"notify:{value}")]]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить сайт сейчас", callback_data="admin:check")],
            [InlineKeyboardButton(text="🗂 Последние файлы", callback_data="admin:files")],
            [InlineKeyboardButton(text="👥 Пользователи по группам", callback_data="admin:groups")],
        ]
    )
