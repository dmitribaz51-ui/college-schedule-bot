"""Проверки второго этапа рассылок: контент, планирование, история, черновики."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import anyio
from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    Update,
    User as TelegramUser,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.bot.handlers import broadcast, broadcast_drafts, broadcast_history
from app.bot.handlers.broadcast_common import parse_schedule_datetime
from app.database import broadcast_repo
from app.database.models import Base, User
from app.services.manual_broadcast_content import BroadcastContent, content_from_state


def test_parse_schedule_datetime() -> None:
    future = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y %H:%M")
    assert parse_schedule_datetime(future) is not None
    assert parse_schedule_datetime("99.99.9999 99:99") is None
    assert parse_schedule_datetime("01.01.2020 10:00") is None
    assert parse_schedule_datetime("не дата") is None
    print("PASS: schedule parsing")


def test_content_from_state() -> None:
    assert content_from_state({}) is None
    assert content_from_state({"content_type": "text", "text": "  "}) is None
    content = content_from_state({"content_type": "text", "text": "hello"})
    assert isinstance(content, BroadcastContent) and content.is_valid()
    photo = content_from_state(
        {"content_type": "photo", "text": "cap", "file_id": "file123"}
    )
    assert photo is not None and photo.content_type == "photo"
    assert content_from_state({"content_type": "photo", "file_id": None}) is None
    print("PASS: content validation")


def test_broadcast_history_and_drafts() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    @contextmanager
    def session_scope() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    try:
        with patch.object(broadcast_repo, "get_session", session_scope):
            draft_id = broadcast_repo.create_broadcast(
                audience_type="group",
                group_name="A",
                content_type="text",
                text="draft text",
                status="draft",
                created_by=1,
            )
            sent_id = broadcast_repo.create_broadcast(
                audience_type="all",
                content_type="text",
                text="sent text",
                status="sent",
                created_by=1,
            )
            broadcast_repo.mark_broadcast_sent(sent_id, total=3, sent=2, failed=1)
            broadcast_repo.save_deliveries(
                sent_id, [(10, "one", "failed", "blocked: bot blocked")]
            )

            history = broadcast_repo.list_broadcasts(limit=10)
            assert [item.id for item in history] == [sent_id]
            drafts = broadcast_repo.list_drafts(limit=10)
            assert [item.id for item in drafts] == [draft_id]
            failures = broadcast_repo.get_deliveries(sent_id, only_failed=True)
            assert len(failures) == 1 and failures[0].telegram_id == 10

            when = datetime.now() + timedelta(hours=2)
            broadcast_repo.mark_broadcast_scheduled(draft_id, when)
            due_future = broadcast_repo.list_due_scheduled(datetime.now())
            assert due_future == []
            due_later = broadcast_repo.list_due_scheduled(when + timedelta(minutes=1))
            assert [item.id for item in due_later] == [draft_id]
            print("PASS: history, drafts, errors, schedule")
    finally:
        engine.dispose()


def test_broadcast_history_and_drafts_dispatcher() -> None:
    """Dispatcher: история и черновики открываются, детали показывают контент."""
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
        dispatcher.include_router(broadcast_history.router)
        dispatcher.include_router(broadcast_drafts.router)
        bot = Bot(token="123456:TEST_TOKEN")
        transport = AsyncMock(return_value=True)
        panel = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=1, type="private"),
            text="panel",
            from_user=TelegramUser(id=1, is_bot=False, first_name="Admin"),
        )

        async def click(data: str) -> None:
            callback = CallbackQuery(
                id=f"q-{abs(hash(data)) % 10_000}",
                from_user=TelegramUser(id=1, is_bot=False, first_name="Admin"),
                chat_instance="test-chat",
                data=data,
                message=panel,
            )
            await dispatcher.feed_update(
                bot, Update(update_id=abs(hash(data)) % 10_000, callback_query=callback)
            )

        try:
            with (
                patch.object(broadcast.repo, "get_session", session_scope),
                patch.object(broadcast_repo, "get_session", session_scope),
                patch.object(broadcast, "is_admin", return_value=True),
                patch.object(broadcast_history, "is_admin", return_value=True),
                patch.object(broadcast_drafts, "is_admin", return_value=True),
                patch.object(bot.session, "make_request", transport),
            ):
                with session_scope() as session:
                    session.add(
                        User(telegram_id=10, username="one", group_name="A")
                    )
                sent_id = broadcast_repo.create_broadcast(
                    audience_type="all",
                    content_type="text",
                    text="sent hello",
                    status="sent",
                    created_by=1,
                )
                broadcast_repo.mark_broadcast_sent(sent_id, total=1, sent=1, failed=0)
                draft_id = broadcast_repo.create_broadcast(
                    audience_type="group",
                    group_name="A",
                    content_type="text",
                    text="draft hello",
                    status="draft",
                    created_by=1,
                )

                await click("admin:broadcast")
                await click("broadcast:history")
                await click(f"broadcast:history:{sent_id}")
                await click("broadcast:drafts")
                await click(f"broadcast:draft:{draft_id}")

                texts = [
                    call.args[1].text
                    for call in transport.call_args_list
                    if isinstance(call.args[1], EditMessageText)
                ]
                assert any("Последние рассылки" in text for text in texts)
                assert any(f"Рассылка №{sent_id}" in text for text in texts)
                assert any("Черновики" in text for text in texts)
                assert any(f"Черновик №{draft_id}" in text for text in texts)
                print("PASS: history/drafts dispatcher")
        finally:
            await bot.session.close()
            for r in (broadcast.router, broadcast_history.router, broadcast_drafts.router):
                try:
                    r.parent_router.sub_routers.remove(r)
                except ValueError:
                    pass
                r._parent_router = None

    try:
        anyio.run(scenario)
    finally:
        engine.dispose()
