"""Persistence port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from bms_assistant.domain.models import Job, JobEvent, JobStatus


class JobRepository(Protocol):
    async def init(self) -> None: ...

    async def add_job(self, job: Job) -> None: ...

    async def update_job(self, job: Job) -> None: ...

    async def get_job(self, job_id: str) -> Job | None: ...

    async def list_jobs(self) -> list[Job]: ...

    async def delete_job(self, job_id: str) -> None: ...

    async def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        expected_status: JobStatus | None = None,
        **fields: object,
    ) -> bool: ...

    async def record_check(
        self,
        job_id: str,
        checked_at: datetime,
        *,
        error: str | None = None,
        error_count: int = 0,
    ) -> None: ...

    async def add_event(self, event: JobEvent) -> JobEvent: ...

    async def list_events(self, limit: int = 50) -> list[JobEvent]: ...
