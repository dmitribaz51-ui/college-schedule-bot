"""Проверки центра ручных рассылок (этапы 1-2, без реальной отправки)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import anyio
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    Update,
    User as TelegramUser,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.bot.handlers import broadcast
from app.database import broadcast_repo
from app.database.models import Base, User
from app.services.manual_broadcast_service import DetailedBroadcastResult


def test_manual_broadcast_preview_and_audiences() -> None:
    """Реальный Dispatcher: предпросмотр, отмена, отправка строго своей аудитории."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    @contextmanager
    def session_scope() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    async def scenario() -> None:
        dispatcher = Dispatcher()
        dispatcher.include_router(broadcast.router)
        bot = Bot(token="123456:TEST_TOKEN")
        transport = AsyncMock(return_value=True)
        panel = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=1, type="private"),
            text="Broadcast panel",
            from_user=TelegramUser(id=1, is_bot=False, first_name="Admin"),
        )

        async def click(data: str) -> None:
            callback = CallbackQuery(
                id=f"query-{abs(hash(data)) % 10_000}",
                from_user=TelegramUser(id=1, is_bot=False, first_name="Admin"),
                chat_instance="test-chat",
                data=data,
                message=panel,
            )
            await dispatcher.feed_update(
                bot,
                Update(update_id=abs(hash(data)) % 10_000, callback_query=callback),
            )

        async def type_text(text: str) -> None:
            message = Message(
                message_id=2,
                date=datetime.now(timezone.utc),
                chat=Chat(id=1, type="private"),
                text=text,
                from_user=TelegramUser(id=1, is_bot=False, first_name="Admin"),
            )
            await dispatcher.feed_update(bot, Update(update_id=20, message=message))

        try:
            with (
                patch.object(broadcast.repo, "get_session", session_scope),
                patch.object(broadcast_repo, "get_session", session_scope),
                patch.object(broadcast, "is_admin", return_value=True),
                patch.object(bot.session, "make_request", transport),
            ):
                with session_scope() as session:
                    session.add_all(
                        [
                            User(
                                telegram_id=10,
                                username="one",
                                group_name="A",
                                notifications_enabled=True,
                            ),
                            User(
                                telegram_id=20,
                                username="two",
                                group_name="A",
                                notifications_enabled=False,
                            ),
                            User(
                                telegram_id=30,
                                username="three",
                                group_name="B",
                                notifications_enabled=True,
                            ),
                        ]
                    )

                await click("admin:broadcast")
                await click("broadcast:new")
                await click("broadcast:audience:enabled")
                await type_text("<b>Тестовая рассылка</b>")

                edits = [
                    call.args[1]
                    for call in transport.call_args_list
                    if isinstance(call.args[1], EditMessageText)
                ]
                messages = [
                    call.args[1]
                    for call in transport.call_args_list
                    if isinstance(call.args[1], SendMessage)
                ]
                preview = messages[-1].text
                assert "Только подписанные на уведомления" in preview
                assert "Получателей: <b>2</b>" in preview
                assert "&lt;b&gt;Тестовая рассылка&lt;/b&gt;" in preview
                assert any("Центр рассылок" in item.text for item in edits)

                transport.reset_mock()
                await click("broadcast:cancel")
                assert any(
                    "Центр рассылок" in call.args[1].text
                    for call in transport.call_args_list
                    if isinstance(call.args[1], EditMessageText)
                )
                assert any(
                    isinstance(call.args[1], AnswerCallbackQuery)
                    for call in transport.call_args_list
                )

                transport.reset_mock()
                await click("broadcast:new")
                await click("broadcast:group:A")
                await type_text("Сообщение только для группы A")
                captured_users: list = []

                async def mock_send(bot_obj, users, content):
                    captured_users.extend(users)
                    return DetailedBroadcastResult(total=len(users), sent=len(users))

                with patch.object(
                    broadcast, "send_manual_broadcast_detailed", side_effect=mock_send
                ):
                    await click("broadcast:send")

                assert len(captured_users) == 2
                assert {u.telegram_id for u in captured_users} == {10, 20}
                assert 30 not in {u.telegram_id for u in captured_users}
        finally:
            await bot.session.close()
            broadcast.router.parent_router.sub_routers.remove(broadcast.router)
            broadcast.router._parent_router = None

    try:
        anyio.run(scenario)
    finally:
        engine.dispose()
