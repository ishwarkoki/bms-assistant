from __future__ import annotations

from datetime import timedelta

from bms_assistant.adapters.storage.sqlite import SQLiteJobRepository
from bms_assistant.domain.models import Job, JobEvent, JobStatus, utcnow


def make_job() -> Job:
    now = utcnow()
    return Job(
        id="job123",
        name="Repo test",
        provider="mock",
        venue="Venue",
        quantity=2,
        status=JobStatus.SCHEDULED,
        created_at=now,
        updated_at=now,
        release_at=now + timedelta(minutes=5),
    )


async def test_repository_round_trip(tmp_path):
    repo = SQLiteJobRepository(tmp_path / "repo.db")
    await repo.init()
    job = make_job()
    await repo.add_job(job)

    loaded = await repo.get_job(job.id)
    assert loaded is not None
    assert loaded.name == job.name
    assert loaded.status == JobStatus.SCHEDULED

    assert await repo.set_status(job.id, JobStatus.MONITORING)
    loaded = await repo.get_job(job.id)
    assert loaded is not None
    assert loaded.status == JobStatus.MONITORING

    await repo.record_check(job.id, utcnow(), error=None, error_count=0)
    loaded = await repo.get_job(job.id)
    assert loaded is not None
    assert loaded.check_count == 1

    event = await repo.add_event(
        JobEvent(
            id=None,
            job_id=job.id,
            kind="test",
            message="hello",
            created_at=utcnow(),
        )
    )
    assert event.id is not None
    events = await repo.list_events()
    assert events[0].message == "hello"


async def test_status_guard(tmp_path):
    repo = SQLiteJobRepository(tmp_path / "guard.db")
    await repo.init()
    job = make_job()
    await repo.add_job(job)

    changed = await repo.set_status(
        job.id,
        JobStatus.TRIGGERED,
        expected_status=JobStatus.MONITORING,
    )
    assert changed is False
    assert (await repo.get_job(job.id)).status == JobStatus.SCHEDULED
