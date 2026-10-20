"""Console notifier — always available, useful when Telegram is not configured."""

from __future__ import annotations

import logging

from bms_assistant.ports.notifiers import Notification


class LogNotifier:
    @property
    def name(self) -> str:
        return "log"

    async def send(self, notification: Notification) -> None:
        logger = logging.getLogger("bms_assistant.notifications")
        logger.info("%s — %s", notification.title, notification.body)
        if notification.url:
            logger.info("Handoff URL: %s", notification.url)
