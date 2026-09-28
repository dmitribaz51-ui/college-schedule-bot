"""Проверки /help и неизвестных команд в режиме выбора даты."""
from contextlib import contextmanager
from datetime import datetime, timezone
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import anyio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User as TelegramUser
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.bot.handlers import schedule, start
from app.bot.keyboards.menu import BTN_PICK_DATE
from app.database.models import Base, User


def test_help_and_unknown_commands() -> None:
    """Реальный dispatcher: команды не трактуются как даты, состояние сбрасывается."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    @contextmanager
    def session_scope() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    async def scenario() -> None:
        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher.include_router(start.router)
        dispatcher.include_router(schedule.router)
        bot = Bot(token="123456:TEST_TOKEN")
        transport = AsyncMock(return_value=True)

        async def send(text: str, user_id: int = 1) -> None:
            message = Message(
                message_id=len(transport.call_args_list) + 100,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                text=text,
                from_user=TelegramUser(id=user_id, is_bot=False, first_name="T"),
            )
            await dispatcher.feed_update(bot, Update(update_id=message.message_id, message=message))

        def sent_texts() -> list[str]:
            return [
                call.args[1].text
                for call in transport.call_args_list
                if isinstance(call.args[1], SendMessage)
            ]

        try:
            with (
                patch.object(schedule.repo, "get_session", session_scope),
                patch.object(bot.session, "make_request", transport),
            ):
                with session_scope() as session:
                    session.add(User(telegram_id=1, group_name="ТД-24-9", course=3))

                # Given: пользователь в режиме выбора даты. When: вводит /help.
                await send(BTN_PICK_DATE)
                await send("/help")
                # Then: справка показана, а не «Не понял дату».
                texts = sent_texts()
                assert any("Помощь" in text for text in texts)
                assert not any("Не понял дату" in text for text in texts)

                # When: после /help текст больше не принимается за дату.
                transport.reset_mock()
                await send("абракадабра")
                assert not any("Не понял дату" in text for text in sent_texts())

                # Given: снова режим выбора даты. When: неизвестная команда.
                transport.reset_mock()
                await send(BTN_PICK_DATE)
                await send("/abc")
                # Then: короткий ответ, состояние сброшено.
                texts = sent_texts()
                assert any("Неизвестная команда" in text for text in texts)
                assert not any("Не понял дату" in text for text in texts)

                # When: после сброса текст не трактуется как дата.
                transport.reset_mock()
                await send("абракадабра")
                assert not any("Не понял дату" in text for text in sent_texts())

                # When: короткие команды /date и /changes.
                transport.reset_mock()
                await send("/date")
                assert any("Введите дату" in text for text in sent_texts())
                transport.reset_mock()
                await send("/changes")
                assert any("Файлы изменений" in text for text in sent_texts())
                print("PASS: help, unknown command, state reset, shortcuts")
        finally:
            await bot.session.close()
            start.router.parent_router.sub_routers.remove(start.router)
            start.router._parent_router = None
            schedule.router.parent_router.sub_routers.remove(schedule.router)
            schedule.router._parent_router = None

    try:
        anyio.run(scenario)
    finally:
        engine.dispose()
