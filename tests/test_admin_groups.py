"""Проверки раскрывающегося списка пользователей по группам."""
from contextlib import contextmanager
from datetime import datetime, timezone
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import anyio
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TelegramUser
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.bot.handlers import admin
from app.database.models import Base, User


def test_admin_groups() -> None:
    """Реальный callback-router: список групп кнопками, детали по нажатию."""
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
        panel = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=1, type="private"),
            text="Admin panel",
            from_user=TelegramUser(id=1, is_bot=False, first_name="Admin"),
        )

        async def click(user_id: int, data: str) -> None:
            callback = CallbackQuery(
                id="test-query",
                from_user=TelegramUser(id=user_id, is_bot=False, first_name="Admin"),
                chat_instance="test-chat",
                data=data,
                message=panel,
            )
            await dispatcher.feed_update(bot, Update(update_id=abs(hash(data)) % 10_000, callback_query=callback))

        def sent_texts() -> list[str]:
            return [
                call.args[1].text
                for call in transport.call_args_list
                if isinstance(call.args[1], SendMessage)
            ]

        def edited() -> list[EditMessageText]:
            return [
                call.args[1]
                for call in transport.call_args_list
                if isinstance(call.args[1], EditMessageText)
            ]

        def buttons() -> list[tuple[str, str]]:
            found: list[tuple[str, str]] = []
            for method in edited():
                markup = method.reply_markup
                if markup is None:
                    continue
                for row in markup.inline_keyboard:
                    for button in row:
                        found.append((button.text, button.callback_data or ""))
            return found

        try:
            with (
                patch.object(admin.repo, "get_session", session_scope),
                patch.object(admin, "_is_admin", side_effect=lambda uid: uid == 1),
                patch.object(bot.session, "make_request", transport),
            ):
                # Given: пустая БД. When: администратор открывает список.
                await click(1, "admin:groups")
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
                # When: тот же список с пользователями.
                await click(1, "admin:groups")
                # Then: группы кнопками со счётчиками, без раскрытых юзеров.
                assert not sent_texts()
                assert any("выберите группу" in method.text for method in edited())
                group_buttons = [(text, data) for text, data in buttons() if data.startswith("admin:g:")]
                assert ("A<& — 2", "admin:g:A<&:0") in group_buttons
                assert ("B — 1", "admin:g:B:0") in group_buttons
                combined = "\n".join(method.text for method in edited())
                assert "student_one" not in combined and "unregistered" not in combined

                transport.reset_mock()
                # When: администратор раскрывает группу.
                await click(1, "admin:g:A<&:0")
                # Then: юзернеймы/ID видны, есть кнопка назад.
                detail = "\n".join(
                    sent_texts() + [method.text for method in edited()]
                )
                assert "A&lt;&amp;: 2" in detail
                assert "@student_one\n    20" in detail
                assert ("⬅️ К группам", "admin:glist:0") in buttons()

                transport.reset_mock()
                # When: возврат к списку групп.
                await click(1, "admin:glist:0")
                # Then: снова свёрнутый список.
                assert any("выберите группу" in method.text for method in edited())
                assert not sent_texts()

                transport.reset_mock()
                # When: посторонний подделывает callback.
                await click(2, "admin:g:A<&:0")
                # Then: никаких списков, только отказ.
                assert not sent_texts() and not edited()
                answer = transport.call_args.args[1]
                assert isinstance(answer, AnswerCallbackQuery)
                assert answer.show_alert and answer.text == "Нет доступа"

                with session_scope() as session:
                    session.add_all(
                        User(telegram_id=1000 + i, username=f"student_{i:04d}", group_name="Large")
                        for i in range(600)
                    )
                transport.reset_mock()
                # When: группа превышает лимит одного сообщения.
                await click(1, "admin:g:Large:0")
                # Then: все пользователи доставлены, сообщения не переполнены.
                texts = sent_texts() + [method.text for method in edited()]
                assert len(texts) > 1
                assert all(len(text.encode("utf-16-le")) // 2 <= 4096 for text in texts)
                combined = "\n".join(texts)
                assert all(combined.count(f"@student_{i:04d}") == 1 for i in range(600))

                with session_scope() as session:
                    session.add_all(
                        User(telegram_id=2000 + i, group_name=f"G-{i:02d}")
                        for i in range(30)
                    )
                transport.reset_mock()
                # When: групп больше одной страницы.
                await click(1, "admin:groups")
                # Then: есть листание, вторая страница открывается.
                assert any("Стр. 1/" in method.text for method in edited())
                assert any(data == "admin:glist:1" for _, data in buttons())
                transport.reset_mock()
                await click(1, "admin:glist:1")
                assert any("Стр. 2/" in method.text for method in edited())
                print("PASS: collapsed list, detail, back, access, chunks, pages")
        finally:
            await bot.session.close()
            admin.router.parent_router.sub_routers.remove(admin.router)
            admin.router._parent_router = None

    try:
        anyio.run(scenario)
    finally:
        engine.dispose()
