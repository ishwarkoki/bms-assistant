from __future__ import annotations

from datetime import timedelta

from bms_assistant.adapters.providers.mock import MockProvider
from bms_assistant.adapters.providers.reminder import ReminderProvider
from bms_assistant.domain.models import Job, JobStatus, utcnow


def make_job(release_at) -> Job:
    now = utcnow()
    return Job(
        id="abc123",
        name="Test",
        provider="mock",
        venue="Venue",
        quantity=2,
        status=JobStatus.MONITORING,
        created_at=now,
        updated_at=now,
        release_at=release_at,
    )


async def test_mock_provider_waits_for_release():
    provider = MockProvider()
    result = await provider.check(make_job(utcnow() + timedelta(minutes=5)))
    assert result.available is False
    assert result.booking_url is None


async def test_mock_provider_fires_after_release():
    provider = MockProvider()
    result = await provider.check(make_job(utcnow() - timedelta(seconds=1)))
    assert result.available is True
    assert result.booking_url == "https://example.com/ticket-watch/abc123"


async def test_reminder_provider_requires_url():
    provider = ReminderProvider()
    job = make_job(utcnow() - timedelta(seconds=1))
    result = await provider.check(job)
    assert result.available is False

    job.booking_url = "https://in.bookmyshow.com/example"
    result = await provider.check(job)
    assert result.available is True
    assert result.booking_url == job.booking_url
