"""Telegram application construction and lifecycle helpers."""

from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder

from bms_assistant.bot.handlers import register_handlers
from bms_assistant.config import Settings
from bms_assistant.services.job_manager import JobManager

logger = logging.getLogger(__name__)


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("newjob", "Create a ticket watch"),
            BotCommand("jobs", "List watches"),
            BotCommand("job", "Show one watch"),
            BotCommand("run", "Start watching"),
            BotCommand("stop", "Pause a watch"),
            BotCommand("booked", "Mark as booked"),
            BotCommand("delete", "Delete a watch"),
            BotCommand("testalert", "Test notifications"),
            BotCommand("status", "Show stats"),
            BotCommand("cancel", "Cancel the wizard"),
            BotCommand("help", "Show help"),
        ]
    )


def build_telegram_application(settings: Settings) -> Application | None:
    if not settings.telegram_ready or not settings.telegram_bot_token:
        logger.info("Telegram is disabled (no bot token configured)")
        return None

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)
        .post_init(_post_init)
        .build()
    )
    return application


async def start_telegram(
    application: Application, settings: Settings, job_manager: JobManager
) -> None:
    register_handlers(application, settings, job_manager)
    await application.initialize()
    await application.start()
    if application.updater is None:
        raise RuntimeError("Telegram updater is unavailable")
    await application.updater.start_polling(allowed_updates=None)
    logger.info("Telegram polling started")


async def stop_telegram(application: Application) -> None:
    if application.updater is not None:
        await application.updater.stop()
    await application.stop()
    await application.shutdown()
    logger.info("Telegram polling stopped")
