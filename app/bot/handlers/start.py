"""/start, выбор курса и группы."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.menu import courses_keyboard, faculties_keyboard, groups_keyboard, main_menu
from app.database import repository as repo
from app.utils import normalize_group

logger = logging.getLogger(__name__)
router = Router(name="start")


class Registration(StatesGroup):
    faculty = State()
    course = State()
    group = State()


async def ask_course(message: Message, state: FSMContext) -> None:
    await state.set_state(Registration.course)
    await message.answer(
        "Выберите ваш <b>курс</b>:", reply_markup=courses_keyboard()
    )


async def ask_faculty(message: Message, state: FSMContext) -> None:
    await state.set_state(Registration.faculty)
    await message.answer("Выберите <b>факультет</b>:", reply_markup=faculties_keyboard())


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = repo.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    greeting = (
        "👋 <b>Привет!</b>\n"
        "Я бот расписания колледжа ПКПС.\n"
        "Я сам следю за сайтом колледжа и показываю расписание вашей группы."
    )
    if user.group_name:
        greeting += f"\n\nТекущая группа: <b>{user.group_name}</b>"

    await message.answer(greeting)
    await ask_faculty(message, state)


@router.callback_query(F.data == "back_to_courses")
async def back_to_courses(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Registration.course)
    await callback.message.edit_text(
        "Выберите ваш <b>курс</b>:", reply_markup=courses_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_faculties")
async def back_to_faculties(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Registration.faculty)
    await callback.message.edit_text(
        "Выберите <b>факультет</b>:", reply_markup=faculties_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("faculty:"))
async def choose_faculty(callback: CallbackQuery, state: FSMContext) -> None:
    faculty = callback.data.split(":", 1)[1]
    await state.update_data(faculty=faculty)
    repo.set_user_faculty(callback.from_user.id, faculty)
    await ask_course(callback.message, state)
    await callback.answer()


@router.callback_query(F.data.startswith("course:"))
async def choose_course(callback: CallbackQuery, state: FSMContext) -> None:
    course = int(callback.data.split(":")[1])
    await state.update_data(course=course)

    data = await state.get_data()
    groups = repo.get_groups(course, data.get("faculty", "permskaya"))
    if groups:
        await state.set_state(Registration.group)
        await callback.message.edit_text(
            f"Курс: <b>{course}</b>\nТеперь выберите вашу <b>группу</b>:",
            reply_markup=groups_keyboard(groups),
        )
    else:
        await state.set_state(Registration.group)
        await callback.message.edit_text(
            f"Курс: <b>{course}</b>\n\n"
            "Список групп пока не заполнен (бот ещё не разобрал файлы расписания).\n"
            "Введите название группы вручную, например: <code>ТД-26-9</code>"
        )
    await callback.answer()


@router.callback_query(F.data == "group_manual")
async def manual_group(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Registration.group)
    await callback.message.answer(
        "Введите название группы, например: <code>ТД-26-9</code>"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("group:"))
async def choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    group = callback.data.split(":", 1)[1]
    data = await state.get_data()
    repo.set_user_group(callback.from_user.id, data.get("course"), group)
    await state.clear()

    await callback.message.edit_text(f"✅ Группа сохранена: <b>{group}</b>")
    await callback.message.answer(
        "Готово! Пользуйтесь меню ниже 👇", reply_markup=main_menu()
    )
    await callback.answer()


@router.message(Registration.group)
async def enter_group_manually(message: Message, state: FSMContext) -> None:
    group = normalize_group(message.text)
    if not group:
        await message.answer(
            "Не похоже на название группы 🤔\n"
            "Введите в формате <code>ТД-26-9</code>."
        )
        return

    data = await state.get_data()
    repo.set_user_group(message.from_user.id, data.get("course"), group)
    await state.clear()
    await message.answer(
        f"✅ Группа сохранена: <b>{group}</b>", reply_markup=main_menu()
    )


@router.message(Registration.course)
async def waiting_course(message: Message) -> None:
    await message.answer("Пожалуйста, выберите курс кнопкой выше 👆")
