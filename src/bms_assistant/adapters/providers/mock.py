"""Development provider that simulates a ticket drop at a configured time."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from bms_assistant.domain.models import Availability, Job, utcnow
from bms_assistant.ports.providers import ProviderInfo


class MockProvider:
    """Time-based fake provider for exercising the full watch → alert flow."""

    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self._clock = clock
        self._info = ProviderInfo(
            name="mock",
            label="Mock drop (dev)",
            description=(
                "Fires at the release time and generates a demo checkout link. "
                "Use it to test alerts end to end."
            ),
            requires_booking_url=False,
            dev_only=True,
        )

    @property
    def info(self) -> ProviderInfo:
        return self._info

    async def check(self, job: Job) -> Availability:
        now = self._clock()
        if job.release_at is not None and now < job.release_at:
            seconds = int((job.release_at - now).total_seconds())
            return Availability(False, detail=f"Mock drop in {seconds}s")

        url = job.booking_url or f"https://example.com/ticket-watch/{job.id}"
        return Availability(True, booking_url=url, detail="Mock tickets are live")
