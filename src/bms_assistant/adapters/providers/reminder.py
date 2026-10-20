"""Safe real-world provider: alert at a drop time, then hand off to a booking URL."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from bms_assistant.domain.models import Availability, Job, utcnow
from bms_assistant.ports.providers import ProviderInfo


class ReminderProvider:
    """A scheduled reminder, not a scraper.

    It performs no remote requests. At the configured release time it surfaces the
    booking URL so the user can complete the purchase manually.
    """

    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self._clock = clock
        self._info = ProviderInfo(
            name="reminder",
            label="Scheduled reminder",
            description=(
                "Alerts you at the drop time with your booking link. "
                "No scraping, no auto-checkout, no stored payment details."
            ),
            requires_booking_url=True,
            dev_only=False,
        )

    @property
    def info(self) -> ProviderInfo:
        return self._info

    async def check(self, job: Job) -> Availability:
        now = self._clock()
        if job.release_at is not None and now < job.release_at:
            seconds = int((job.release_at - now).total_seconds())
            return Availability(False, detail=f"Reminder in {seconds}s")

        if not job.booking_url:
            return Availability(False, detail="No booking URL configured")

        return Availability(
            True,
            booking_url=job.booking_url,
            detail="Drop time reached — hand off to manual checkout",
        )
