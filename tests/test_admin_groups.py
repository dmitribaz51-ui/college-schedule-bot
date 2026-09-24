"""Изолированные проверки админского списка, без Telegram и рабочей БД."""
from contextlib import contextmanager
from datetime import datetime, timezone
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import anyio
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TelegramUser
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.bot.handlers import admin
from app.database.models import Base, User


def test_admin_groups() -> None:
    """Проверяет реальный callback-router и запросы на SQLite в памяти."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    @contextmanager
    def session_scope() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    async def scenario() -> None:
        dispatcher = Dispatcher()
        dispatcher.include_router(admin.router)
        bot = Bot(token="123456:TEST_TOKEN")
        transport = AsyncMock(return_value=True)

        async def click(user_id: int) -> None:
            callback = CallbackQuery(
                id="test-query",
                from_user=TelegramUser(id=user_id, is_bot=False, first_name="Admin"),
                chat_instance="test-chat",
                data="admin:groups",
                message=Message(
                    message_id=1,
                    date=datetime.now(timezone.utc),
                    chat=Chat(id=user_id, type="private"),
                    text="Admin panel",
                ),
            )
            await dispatcher.feed_update(bot, Update(update_id=1, callback_query=callback))

        def sent_texts() -> list[str]:
            return [
                call.args[1].text
                for call in transport.call_args_list
                if isinstance(call.args[1], SendMessage)
            ]

        try:
            with (
                patch.object(admin.repo, "get_session", session_scope),
                patch.object(admin, "_is_admin", side_effect=lambda uid: uid == 1),
                patch.object(bot.session, "make_request", transport),
            ):
                # Given: пустая БД. When: администратор открывает список.
                await click(1)
                # Then: понятный пустой результат.
                assert sent_texts() == ["Пока никто не выбрал группу."]

                with session_scope() as session:
                    session.add_all([
                        User(telegram_id=10, username="student_one", group_name="A<&"),
                        User(telegram_id=20, group_name="A<&", notifications_enabled=False),
                        User(telegram_id=30, username="other", group_name="B"),
                        User(telegram_id=40, username="unregistered"),
                    ])
                transport.reset_mock()
                # When: тот же callback с зарегистрированными пользователями.
                await click(1)
                # Then: username/ID, точные группы, отключенные уведомления включены.
                output = "\n".join(sent_texts())
                assert "A&lt;&amp;: 2" in output
                assert "@student_one\n    20" in output
                assert "B: 1" in output and "@other" in output
                assert "unregistered" not in output
                print("GROUP LIST:", output)

                transport.reset_mock()
                # When: посторонний подделывает callback.
                await click(2)
                # Then: никаких списков, только отказ.
                assert not sent_texts()
                answer = transport.call_args.args[1]
                assert isinstance(answer, AnswerCallbackQuery)
                assert answer.show_alert and answer.text == "Нет доступа"

                with session_scope() as session:
                    session.add_all(
                        User(telegram_id=1000 + i, username=f"student_{i:04d}", group_name="Large")
                        for i in range(600)
                    )
                transport.reset_mock()
                # When: список превышает лимит одного сообщения.
                await click(1)
                # Then: все пользователи доставлены, сообщения не переполнены.
                texts = sent_texts()
                assert len(texts) > 1
                assert all(len(text.encode("utf-16-le")) // 2 <= 4096 for text in texts)
                combined = "\n".join(texts)
                assert all(combined.count(f"@student_{i:04d}") == 1 for i in range(600))
                print(f"PASS: empty, identities, disabled notifications, access, {len(texts)} chunks")
        finally:
            await bot.session.close()
            admin.router.parent_router.sub_routers.remove(admin.router)
            admin.router._parent_router = None

    try:
        anyio.run(scenario)
    finally:
        engine.dispose()
