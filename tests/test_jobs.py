from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bms_assistant.config import Settings
from bms_assistant.domain.errors import JobValidationError
from bms_assistant.domain.models import JobStatus, utcnow
from bms_assistant.ports.notifiers import Notification


class RecordingNotifier:
    def __init__(self) -> None:
        self.items: list[Notification] = []

    @property
    def name(self) -> str:
        return "recording"

    async def send(self, notification: Notification) -> None:
        self.items.append(notification)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "test.db",
        telegram_enabled=False,
        timezone="UTC",
        default_check_interval_seconds=2,
        min_check_interval_seconds=2,
    )


async def test_create_and_trigger_mock_job(tmp_path, settings):
    from bms_assistant.adapters.providers.registry import build_default_registry
    from bms_assistant.adapters.storage.sqlite import SQLiteJobRepository
    from bms_assistant.services.event_bus import EventBus
    from bms_assistant.services.job_manager import JobManager

    repository = SQLiteJobRepository(tmp_path / "test.db")
    await repository.init()
    notifier = RecordingNotifier()
    manager = JobManager(
        repository=repository,
        providers=build_default_registry(),
        settings=settings,
        event_bus=EventBus(),
        notifiers=[notifier],
    )

    job = await manager.create_job(
        name="Test drop",
        provider="mock",
        venue="Test venue",
        quantity=2,
        release_at=utcnow() - timedelta(seconds=1),
        auto_start=True,
    )

    for _ in range(50):
        current = await manager.get_job(job.id)
        if current.status == JobStatus.TRIGGERED:
            break
        import asyncio

        await asyncio.sleep(0.05)

    current = await manager.get_job(job.id)
    assert current.status == JobStatus.TRIGGERED
    assert current.booking_url == f"https://example.com/ticket-watch/{job.id}"
    assert notifier.items
    assert notifier.items[-1].kind == "triggered"
    assert notifier.items[-1].url == current.booking_url
    await manager.shutdown()


async def test_reminder_requires_booking_url(tmp_path, settings):
    from bms_assistant.adapters.providers.registry import build_default_registry
    from bms_assistant.adapters.storage.sqlite import SQLiteJobRepository
    from bms_assistant.services.event_bus import EventBus
    from bms_assistant.services.job_manager import JobManager

    repository = SQLiteJobRepository(tmp_path / "test.db")
    await repository.init()
    manager = JobManager(
        repository=repository,
        providers=build_default_registry(),
        settings=settings,
        event_bus=EventBus(),
    )
    with pytest.raises(JobValidationError):
        await manager.create_job(
            name="Reminder",
            provider="reminder",
            venue="PVR",
            quantity=2,
            release_at=utcnow() + timedelta(minutes=5),
        )


async def test_stop_job(tmp_path, settings):
    from bms_assistant.adapters.providers.registry import build_default_registry
    from bms_assistant.adapters.storage.sqlite import SQLiteJobRepository
    from bms_assistant.services.event_bus import EventBus
    from bms_assistant.services.job_manager import JobManager

    repository = SQLiteJobRepository(tmp_path / "test.db")
    await repository.init()
    manager = JobManager(
        repository=repository,
        providers=build_default_registry(),
        settings=settings,
        event_bus=EventBus(),
    )
    job = await manager.create_job(
        name="Future drop",
        provider="mock",
        venue="Test venue",
        quantity=1,
        release_at=utcnow() + timedelta(minutes=5),
        auto_start=True,
    )
    stopped = await manager.stop(job.id)
    assert stopped.status == JobStatus.STOPPED
    await manager.shutdown()


def test_parse_local_datetime():
    from zoneinfo import ZoneInfo

    from bms_assistant.timeutils import parse_local_datetime

    parsed = parse_local_datetime("2030-01-02 19:30", ZoneInfo("Asia/Kolkata"))
    assert parsed.tzinfo is not None
    assert parsed.astimezone(ZoneInfo("Asia/Kolkata")).hour == 19
    assert parsed == datetime(2030, 1, 2, 14, 0, tzinfo=UTC)
