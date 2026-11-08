
from __future__ import annotations

from datetime import date

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)

BTN_TODAY = "📍 Сегодня"
BTN_TOMORROW = "📆 Завтра"
BTN_PICK_DATE = "📅 Выбрать дату"
BTN_BELLS = "Звонки⏰"
BTN_GROUP = "⚙️ Моя группа"
BTN_NOTIFICATIONS = "🔔 Уведомления"
BTN_INFO = "ℹ️ Информация"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_PICK_DATE), KeyboardButton(text=BTN_BELLS)],
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
            [InlineKeyboardButton(text="🔁 Перечитать упавшие PDF заочки", callback_data="admin:retry_zao")],
            [InlineKeyboardButton(text="⚠️ Проблемные PDF заочки", callback_data="admin:zao_problems")],
            [InlineKeyboardButton(text="🗂 Последние файлы", callback_data="admin:files")],
            [InlineKeyboardButton(text="👥 Пользователи по группам", callback_data="admin:groups")],
            [InlineKeyboardButton(text="🔍 Поиск по username", callback_data="admin:search")],
            [InlineKeyboardButton(text="📣 Центр рассылок", callback_data="admin:broadcast")],
        ]
    )


WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def bells_keyboard(faculty: str = "permskaya", course: int | None = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора дня для звонков с учётом факультета."""
    if faculty == "chernyshevskogo":
        # Если курс известен (1 или 2-4), показываем сразу дни для этого курса
        if course == 1:
            rows = [
                [InlineKeyboardButton(text="🗓 Понедельник", callback_data="bells:chern_mon_1")],
                [InlineKeyboardButton(text="🗓 Вторник – Пятница", callback_data="bells:chern_week_1")],
                [InlineKeyboardButton(text="❌ Закрыть", callback_data="bells:close")],
            ]
        elif course in (2, 3, 4):
            rows = [
                [InlineKeyboardButton(text="🗓 Понедельник", callback_data="bells:chern_mon_24")],
                [InlineKeyboardButton(text="🗓 Вторник – Пятница", callback_data="bells:chern_week_24")],
                [InlineKeyboardButton(text="❌ Закрыть", callback_data="bells:close")],
            ]
        else:
            # Курс неизвестен: предлагаем выбрать курс
            rows = [
                [
                    InlineKeyboardButton(text="1 курс (Пн)", callback_data="bells:chern_mon_1"),
                    InlineKeyboardButton(text="1 курс (Вт-Пт)", callback_data="bells:chern_week_1"),
                ],
                [
                    InlineKeyboardButton(text="2-4 курс (Пн)", callback_data="bells:chern_mon_24"),
                    InlineKeyboardButton(text="2-4 курс (Вт-Пт)", callback_data="bells:chern_week_24"),
                ],
                [InlineKeyboardButton(text="❌ Закрыть", callback_data="bells:close")],
            ]
    else:
        # Пермская
        rows = [
            [InlineKeyboardButton(text="🗓 Понедельник", callback_data="bells:perm_mon")],
            [InlineKeyboardButton(text="🗓 Вторник – Пятница", callback_data="bells:perm_week")],
            [InlineKeyboardButton(text="🗓 Суббота", callback_data="bells:perm_sat")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="bells:close")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pick_date_keyboard(dates: list[date]) -> InlineKeyboardMarkup | None:
    """Один ряд до 3 кнопок свежих дат. None — если дат нет."""
    if not dates:
        return None
    buttons = [
        InlineKeyboardButton(
            text=f"{d.strftime('%d.%m')} {WEEKDAY_SHORT[d.weekday()]}",
            callback_data=f"pickdate:{d.isoformat()}",
        )
        for d in dates[:3]
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def _week_button_text(start: date, end: date) -> str:
    if start.year == end.year:
        return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m.%Y')}"
    return f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"


def week_picker_keyboard(
    weeks: list[tuple[date, date]], index: int = 0
) -> InlineKeyboardMarkup:
    """Заочка: две доступные недели и стрелки листания."""
    if not weeks:
        return InlineKeyboardMarkup(inline_keyboard=[])
    index = max(0, min(index, len(weeks) - 1))
    rows: list[list[InlineKeyboardButton]] = []
    for start, end in weeks[index : index + 2]:
        rows.append([
            InlineKeyboardButton(
                text=_week_button_text(start, end),
                callback_data=f"week:{start.isoformat()}",
            )
        ])
    arrows: list[InlineKeyboardButton] = []
    if index > 0:
        arrows.append(
            InlineKeyboardButton(text="◀️", callback_data=f"weekpage:{index - 1}")
        )
    if index + 2 < len(weeks):
        arrows.append(
            InlineKeyboardButton(text="▶️", callback_data=f"weekpage:{index + 1}")
        )
    if arrows:
        rows.append(arrows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bells_back_keyboard() -> InlineKeyboardMarkup:
    """Кнопка возврата к выбору дня."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к выбору дня", callback_data="bells:back")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="bells:close")],
        ]
    )



