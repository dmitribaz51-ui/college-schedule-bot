"""Кнопка уведомления открывает дату через существующий обработчик расписания."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.handlers import schedule
from app.services import notification_service


def test_notification_button_opens_schedule() -> None:
    async def scenario() -> None:
        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher.include_router(schedule.router)
        bot = Bot(token="123456:TEST_TOKEN")
        transport = AsyncMock(return_value=True)
        send_schedule = AsyncMock()
        day = date(2026, 10, 28)
        user = User(id=101, is_bot=False, first_name="T")
        state = dispatcher.fsm.get_context(bot=bot, chat_id=user.id, user_id=user.id)

        try:
            with (
                patch.object(notification_service, "today_perm", return_value=date(2026, 10, 25)),
                patch.object(notification_service.repo, "get_users_by_groups", return_value=[
                    SimpleNamespace(telegram_id=user.id, group_name="ТД-24-9")
                ]),
                patch.object(bot.session, "make_request", transport),
                patch.object(schedule, "_user_group", return_value=("ТД-24-9", "permskaya", 3)) as get_group,
                patch.object(schedule, "_send_schedule", send_schedule),
            ):
                await notification_service.notify_about_schedule_update(
                    bot, schedule_date=day, groups=["ТД-24-9"], title="Изменения на 28.10.2026"
                )
                notification = transport.call_args.args[1]
                button = notification.reply_markup.inline_keyboard[0][0]
                message = Message(
                    message_id=10,
                    date=datetime.now(timezone.utc),
                    chat=Chat(id=user.id, type="private"),
                    from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
                    text=notification.text,
                    reply_markup=notification.reply_markup,
                )
                await state.set_state(schedule.ScheduleStates.waiting_date)
                await dispatcher.feed_update(bot, Update(
                    update_id=11,
                    callback_query=CallbackQuery(
                        id="notification-click", from_user=user, chat_instance="test",
                        message=message, data=button.callback_data,
                    ),
                ))
                get_group.assert_called_once_with(user.id)
                send_schedule.assert_awaited_once()
                args = send_schedule.call_args.args
                assert args[0].chat.id == user.id
                assert args[1:] == ("ТД-24-9", day, "permskaya", 3)
                assert await state.get_state() is None
        finally:
            await bot.session.close()
            dispatcher.sub_routers.remove(schedule.router)
            schedule.router._parent_router = None

    anyio.run(scenario)


def test_notification_retry_keeps_button() -> None:
    async def scenario() -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = [
            TelegramRetryAfter(method=SendMessage(chat_id=101, text="test"), message="Retry", retry_after=1),
            True,
        ]
        with (
            patch.object(notification_service, "today_perm", return_value=date(2026, 10, 25)),
            patch.object(notification_service.repo, "get_users_by_groups", return_value=[
                SimpleNamespace(telegram_id=101, group_name="ТД-24-9")
            ]),
            patch.object(notification_service.asyncio, "sleep", new_callable=AsyncMock),
        ):
            sent = await notification_service.notify_about_schedule_update(
                bot, schedule_date=date(2026, 10, 28), groups=["ТД-24-9"], title="Изменения"
            )
        assert sent == 1
        assert bot.send_message.await_count == 2
        first, retry = bot.send_message.call_args_list
        assert first.kwargs["reply_markup"] == retry.kwargs["reply_markup"]
        assert retry.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "pickdate:2026-10-28"

    anyio.run(scenario)
