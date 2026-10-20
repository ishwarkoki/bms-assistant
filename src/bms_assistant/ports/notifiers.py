"""Notification port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bms_assistant.domain.models import Job


@dataclass(slots=True)
class Notification:
    kind: str
    title: str
    body: str
    job: Job | None = None
    url: str | None = None


class Notifier(Protocol):
    @property
    def name(self) -> str: ...

    async def send(self, notification: Notification) -> None: ...
