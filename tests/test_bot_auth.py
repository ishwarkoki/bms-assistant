from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from telegram.ext import ApplicationHandlerStop

from bms_assistant.bot.handlers import _require_authorized, register_handlers
from bms_assistant.config import Settings


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data: dict[str, Any] = {}
        self.handlers: list[tuple[int, Any]] = []
        self.error_handlers: list[Any] = []

    def add_handler(self, handler: Any, group: int = 0) -> None:
        self.handlers.append((group, handler))

    def add_error_handler(self, handler: Any) -> None:
        self.error_handlers.append(handler)


def _context(allowed_chat_id: int | None):
    application = SimpleNamespace(bot_data={"allowed_chat_id": allowed_chat_id})
    return SimpleNamespace(application=application)


def _message(text: str | None = None):
    async def reply_text(*args: Any, **kwargs: Any) -> None:
        return None

    return SimpleNamespace(text=text, reply_text=reply_text)


def _update(chat_id: int, text: str | None = None, callback: Any = None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=_message(text),
        callback_query=callback,
    )


async def test_telegram_guard_allows_configured_chat():
    await _require_authorized(_update(42), _context(42))


async def test_telegram_guard_blocks_strangers():
    with pytest.raises(ApplicationHandlerStop):
        await _require_authorized(_update(7, "/jobs"), _context(42))


async def test_telegram_guard_allows_start_for_chat_id_setup():
    await _require_authorized(_update(7, "/start"), _context(42))
    await _require_authorized(_update(7, "/help"), _context(42))


async def test_telegram_guard_blocks_callback_buttons():
    answered: list[str] = []

    async def answer(text: str, show_alert: bool = False) -> None:
        answered.append(text)

    with pytest.raises(ApplicationHandlerStop):
        await _require_authorized(_update(7, callback=SimpleNamespace(answer=answer)), _context(42))
    assert answered == ["This bot is private."]


async def test_telegram_guard_is_open_without_configured_chat():
    await _require_authorized(_update(7, "/jobs"), _context(None))


@pytest.mark.filterwarnings("ignore:If 'per_message=False'")
def test_register_handlers_wires_the_guard():
    settings = Settings(
        telegram_enabled=False,
        telegram_chat_id=42,
        _env_file=None,
    )
    application = FakeApplication()
    register_handlers(application, settings, SimpleNamespace())

    assert application.bot_data["allowed_chat_id"] == 42
    assert any(group == -1 for group, _ in application.handlers)
