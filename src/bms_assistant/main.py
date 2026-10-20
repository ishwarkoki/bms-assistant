"""Composition root: wires adapters, services, web UI and Telegram."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI
from telegram.ext import Application

from bms_assistant.adapters.notify.log import LogNotifier
from bms_assistant.adapters.notify.telegram import TelegramNotifier
from bms_assistant.adapters.providers.registry import build_default_registry
from bms_assistant.adapters.storage.sqlite import SQLiteJobRepository
from bms_assistant.bot.runner import build_telegram_application, start_telegram, stop_telegram
from bms_assistant.config import Settings, get_settings
from bms_assistant.ports.notifiers import Notifier
from bms_assistant.services.event_bus import EventBus
from bms_assistant.services.job_manager import JobManager
from bms_assistant.web.app import create_web_app

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Runtime:
    settings: Settings
    repository: SQLiteJobRepository
    event_bus: EventBus
    job_manager: JobManager
    web_app: FastAPI
    telegram_app: Application | None


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()
    repository = SQLiteJobRepository(settings.database_path)
    event_bus = EventBus()
    providers = build_default_registry()

    telegram_app = build_telegram_application(settings)
    notifiers: list[Notifier] = [LogNotifier()]
    if telegram_app is not None:
        notifiers.append(
            TelegramNotifier(
                telegram_app.bot,
                default_chat_id=settings.telegram_chat_id,
                tz=settings.tz,
            )
        )

    job_manager = JobManager(
        repository=repository,
        providers=providers,
        settings=settings,
        event_bus=event_bus,
        notifiers=notifiers,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await repository.init()
        bot_started = False
        if telegram_app is not None:
            await start_telegram(telegram_app, settings, job_manager)
            bot_started = True
        await job_manager.recover()
        logger.info(
            "%s ready on http://%s:%s (timezone %s)",
            settings.app_name,
            settings.host,
            settings.port,
            settings.timezone,
        )
        try:
            yield
        finally:
            await job_manager.shutdown()
            if bot_started and telegram_app is not None:
                await stop_telegram(telegram_app)
            logger.info("Shutdown complete")

    web_app = create_web_app(
        settings=settings,
        job_manager=job_manager,
        repository=repository,
        providers=providers,
        event_bus=event_bus,
        lifespan=lifespan,
    )
    web_app.state.runtime = Runtime(
        settings=settings,
        repository=repository,
        event_bus=event_bus,
        job_manager=job_manager,
        web_app=web_app,
        telegram_app=telegram_app,
    )
    return web_app.state.runtime


def create_app() -> FastAPI:
    """Uvicorn factory: `uvicorn bms_assistant.main:create_app --factory`."""
    return build_runtime().web_app


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    runtime = build_runtime(settings)
    uvicorn.run(
        runtime.web_app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    run()
