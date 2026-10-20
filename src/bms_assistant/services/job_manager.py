"""Application service that owns watch jobs and their monitoring tasks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlparse

from bms_assistant.config import Settings
from bms_assistant.domain.errors import JobNotFoundError, JobValidationError
from bms_assistant.domain.models import (
    Availability,
    Job,
    JobEvent,
    JobStats,
    JobStatus,
    new_job_id,
    utcnow,
)
from bms_assistant.ports.notifiers import Notification, Notifier
from bms_assistant.ports.providers import ProviderRegistry
from bms_assistant.ports.repository import JobRepository
from bms_assistant.services.event_bus import EventBus

logger = logging.getLogger(__name__)

_MOVIE_ID = re.compile(r"^[A-Z]{2,4}\d{4,}$")
_VENUE_ID = re.compile(r"^[A-Z0-9]{2,12}$")


class JobManager:
    def __init__(
        self,
        *,
        repository: JobRepository,
        providers: ProviderRegistry,
        settings: Settings,
        event_bus: EventBus,
        notifiers: Iterable[Notifier] = (),
    ) -> None:
        self._repository = repository
        self._providers = providers
        self._settings = settings
        self._event_bus = event_bus
        self._notifiers = list(notifiers)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._action_lock = asyncio.Lock()

    # ------------------------------------------------------------------ queries

    async def list_jobs(self) -> list[Job]:
        return await self._repository.list_jobs()

    async def get_job(self, job_id: str) -> Job:
        return await self._require(job_id)

    async def list_events(self, limit: int = 50) -> list[JobEvent]:
        return await self._repository.list_events(limit)

    async def stats(self) -> JobStats:
        jobs = await self._repository.list_jobs()
        counts = Counter(job.status.value for job in jobs)
        return JobStats(
            total=len(jobs),
            monitoring=counts[JobStatus.MONITORING.value],
            triggered=counts[JobStatus.TRIGGERED.value],
            booked=counts[JobStatus.BOOKED.value],
            scheduled=counts[JobStatus.SCHEDULED.value],
            stopped=counts[JobStatus.STOPPED.value],
            failed=counts[JobStatus.FAILED.value],
        )

    async def resolve_job(self, prefix: str) -> Job:
        prefix = prefix.strip().lower()
        if not prefix:
            raise JobNotFoundError("No job id supplied")
        jobs = await self._repository.list_jobs()
        matches = [job for job in jobs if job.id.lower().startswith(prefix)]
        if not matches:
            raise JobNotFoundError(f"No job found for {prefix}")
        if len(matches) > 1:
            raise JobValidationError(f"'{prefix}' matches more than one job")
        return matches[0]

    # ------------------------------------------------------------------ commands

    async def create_job(
        self,
        *,
        name: str,
        provider: str,
        venue: str,
        quantity: int,
        release_at: datetime,
        show_at: datetime | None = None,
        booking_url: str | None = None,
        interval_seconds: int | None = None,
        chat_id: int | None = None,
        auto_start: bool = False,
        movie_id: str | None = None,
        venue_id: str | None = None,
        city: str | None = None,
        show_date: date | None = None,
        window_start: time | None = None,
        window_end: time | None = None,
        seat_prefs: dict[str, Any] | None = None,
    ) -> Job:
        name = name.strip()
        venue = venue.strip()
        if not name:
            raise JobValidationError("Give this watch a name.")
        if len(name) > 120:
            raise JobValidationError("Name must be 120 characters or fewer.")
        if not venue:
            raise JobValidationError("Venue or platform is required.")
        if len(venue) > 160:
            raise JobValidationError("Venue must be 160 characters or fewer.")
        if provider not in self._providers:
            raise JobValidationError(f"Unknown provider: {provider}")
        if not 1 <= quantity <= 10:
            raise JobValidationError("Ticket count must be between 1 and 10.")
        if release_at is None:
            raise JobValidationError("Release time is required.")

        movie_id = (movie_id or "").strip().upper() or None
        if movie_id and not _MOVIE_ID.match(movie_id):
            raise JobValidationError("BMS movie/event code looks wrong (example: ET00521930).")
        venue_id = (venue_id or "").strip().upper() or None
        if venue_id and not _VENUE_ID.match(venue_id):
            raise JobValidationError("BMS venue code looks wrong (example: ALUC).")
        city = (city or "").strip().lower() or None
        if city and len(city) > 60:
            raise JobValidationError("City must be 60 characters or fewer.")
        if (window_start is None) != (window_end is None):
            raise JobValidationError("Set both ends of the showtime window.")
        if window_start is not None and window_end is not None and window_end < window_start:
            raise JobValidationError("The showtime window end must be after the start.")

        info = self._providers.get(provider).info
        booking_url = (booking_url or "").strip() or None
        if info.requires_booking_url and not booking_url:
            raise JobValidationError(f"{info.label} needs a booking URL.")
        if booking_url is not None:
            parsed = urlparse(booking_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise JobValidationError("Booking URL must start with http:// or https://")

        interval = interval_seconds or self._settings.effective_check_interval
        interval = max(int(interval), self._settings.min_check_interval_seconds)
        seat_prefs = {
            key: value for key, value in (seat_prefs or {}).items() if value not in (None, "", [])
        }
        config: dict[str, Any] = {"check_interval_seconds": interval}
        if seat_prefs:
            config["seats"] = seat_prefs
        now = utcnow()
        job = Job(
            id=new_job_id(),
            name=name,
            provider=provider,
            venue=venue,
            quantity=quantity,
            status=JobStatus.SCHEDULED,
            created_at=now,
            updated_at=now,
            show_at=show_at,
            release_at=release_at,
            booking_url=booking_url,
            config=config,
            chat_id=chat_id,
            movie_id=movie_id,
            venue_id=venue_id,
            city=city,
            show_date=show_date,
            window_start=window_start,
            window_end=window_end,
        )
        await self._repository.add_job(job)
        await self._record_event(
            job.id,
            "created",
            f"Watch created for {job.name} at {job.venue}.",
            {
                "provider": provider,
                "release_at": job.release_at.isoformat() if job.release_at else None,
                "movie_id": movie_id,
                "venue_id": venue_id,
                "show_date": show_date.isoformat() if show_date else None,
            },
        )
        if auto_start:
            return await self.start(job.id)
        return job

    async def start(self, job_id: str) -> Job:
        async with self._action_lock:
            job = await self._require(job_id)
            existing = self._tasks.get(job.id)
            if existing is not None and not existing.done():
                return job

            changed = await self._repository.set_status(
                job.id,
                JobStatus.MONITORING,
                error=None,
                error_count=0,
            )
            if not changed:
                raise JobNotFoundError(f"Job {job_id} no longer exists")
            job = await self._require(job.id)
            await self._record_event(
                job.id,
                "started",
                f"Watching {job.name}. Checking every ~{job.check_interval_seconds}s.",
            )
            self._spawn(job.id)
            return job

    async def stop(self, job_id: str) -> Job:
        async with self._action_lock:
            job = await self._require(job_id)
            task = self._tasks.pop(job.id, None)
            await self._repository.set_status(job.id, JobStatus.STOPPED)
            await self._cancel_task(task)
            job = await self._require(job.id)
            await self._record_event(job.id, "stopped", f"Paused {job.name}.")
            return job

    async def mark_booked(self, job_id: str) -> Job:
        async with self._action_lock:
            job = await self._require(job_id)
            task = self._tasks.pop(job.id, None)
            await self._repository.set_status(job.id, JobStatus.BOOKED)
            await self._cancel_task(task)
            job = await self._require(job.id)
            await self._record_event(job.id, "booked", f"Marked {job.name} as booked.")
            return job

    async def delete(self, job_id: str) -> Job:
        async with self._action_lock:
            job = await self._require(job_id)
            task = self._tasks.pop(job.id, None)
            await self._cancel_task(task)
            await self._repository.delete_job(job.id)
            await self._record_event(job.id, "deleted", f"Deleted watch {job.name}.")
            return job

    async def check_now(self, job_id: str) -> Availability:
        job = await self._require(job_id)
        provider = self._providers.get(job.provider)
        try:
            result = await provider.check(job)
        except Exception as exc:
            await self._repository.record_check(
                job.id,
                utcnow(),
                error=str(exc),
                error_count=job.error_count + 1,
            )
            await self._record_event(job.id, "error", f"Manual check failed: {exc}")
            raise

        await self._repository.record_check(job.id, utcnow(), error=None, error_count=0)
        if result.available:
            message = f"Manual check: tickets look live. {result.booking_url or ''}".strip()
        else:
            message = f"Manual check: not live yet. {result.detail}".strip()
        await self._record_event(job.id, "checked", message)
        return result

    async def recover(self) -> None:
        """Resume watches that were running before the process restarted."""
        jobs = await self._repository.list_jobs()
        for job in jobs:
            if job.status == JobStatus.MONITORING and job.id not in self._tasks:
                self._spawn(job.id)
                logger.info("Resumed watch %s (%s)", job.short_id, job.name)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ worker

    def _spawn(self, job_id: str) -> None:
        task = asyncio.create_task(self._run(job_id), name=f"ticket-watch:{job_id}")
        self._tasks[job_id] = task

    async def _run(self, job_id: str) -> None:
        logger.info("Watcher started for job %s", job_id)
        try:
            while True:
                job = await self._repository.get_job(job_id)
                if job is None or job.status != JobStatus.MONITORING:
                    return

                provider = self._providers.get(job.provider)
                try:
                    result = await provider.check(job)
                except Exception as exc:
                    if await self._handle_error(job, exc):
                        return
                    await self._sleep(job)
                    continue

                await self._repository.record_check(
                    job.id,
                    utcnow(),
                    error=None,
                    error_count=0,
                )

                if result.available:
                    changed = await self._repository.set_status(
                        job.id,
                        JobStatus.TRIGGERED,
                        expected_status=JobStatus.MONITORING,
                        triggered_at=utcnow(),
                        booking_url=result.booking_url or job.booking_url,
                        error=None,
                    )
                    if changed:
                        triggered_job = await self._require(job.id)
                        await self._on_triggered(triggered_job, result)
                    return

                await self._sleep(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled watcher failure for job %s", job_id)
        finally:
            current = self._tasks.get(job_id)
            if current is asyncio.current_task():
                self._tasks.pop(job_id, None)

    async def _handle_error(self, job: Job, exc: Exception) -> bool:
        error_count = job.error_count + 1
        logger.warning("Provider error for %s: %s", job.short_id, exc)
        await self._repository.record_check(
            job.id,
            utcnow(),
            error=str(exc),
            error_count=error_count,
        )
        if error_count < self._settings.max_consecutive_errors:
            return False

        changed = await self._repository.set_status(
            job.id,
            JobStatus.FAILED,
            expected_status=JobStatus.MONITORING,
            error=str(exc),
        )
        if not changed:
            return True

        failed_job = await self._require(job.id)
        message = f"Stopped watching {failed_job.name}: {exc}"
        await self._record_event(failed_job.id, "error", message)
        await self._notify(
            Notification(
                kind="failed",
                title="Watch stopped after repeated errors",
                body=message,
                job=failed_job,
            )
        )
        return True

    async def _on_triggered(self, job: Job, result: Availability) -> None:
        schedule = ""
        if job.show_date:
            schedule += f" on {job.show_date.strftime('%a, %d %b %Y')}"
        if job.has_show_window:
            start = job.window_start.strftime("%I:%M %p").lstrip("0")
            end = job.window_end.strftime("%I:%M %p").lstrip("0")
            schedule += f" between {start} and {end}"
        elif job.show_at:
            show_local = job.show_at.astimezone(self._settings.tz)
            schedule += f" at {show_local.strftime('%I:%M %p').lstrip('0')}"
        seat_note = ""
        if job.seat_prefs.get("category"):
            seat_note = f" Seat category: {job.seat_prefs['category']}."
        message = (
            f"Tickets look live for {job.name} at {job.venue}{schedule}. "
            f"Open the BMS link and pay manually.{seat_note}"
        )
        await self._record_event(
            job.id,
            "triggered",
            message,
            {"booking_url": job.booking_url, "detail": result.detail},
        )
        await self._notify(
            Notification(
                kind="triggered",
                title="Tickets look live",
                body=(
                    f"{message}\n\n"
                    f"Seats needed: {job.quantity}. "
                    "Pay in BMS — never share an OTP, CVV, or UPI PIN. "
                    "The watch stops polling until you mark it booked or restart it."
                ),
                job=job,
                url=job.booking_url,
            )
        )

    async def _sleep(self, job: Job) -> None:
        interval = max(job.check_interval_seconds, self._settings.min_check_interval_seconds)
        await asyncio.sleep(interval * random.uniform(0.9, 1.1))

    async def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task is asyncio.current_task() or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ------------------------------------------------------------------ helpers

    async def _require(self, job_id: str) -> Job:
        job = await self._repository.get_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} not found")
        return job

    async def _record_event(
        self,
        job_id: str | None,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> JobEvent:
        event = JobEvent(
            id=None,
            job_id=job_id,
            kind=kind,
            message=message,
            created_at=utcnow(),
            data=data or {},
        )
        saved = await self._repository.add_event(event)
        self._event_bus.publish(
            {
                "type": f"job.{kind}",
                "job_id": job_id,
                "message": message,
                "created_at": saved.created_at.isoformat(),
            }
        )
        return saved

    async def _notify(self, notification: Notification) -> None:
        if not self._notifiers:
            return
        results = await asyncio.gather(
            *(notifier.send(notification) for notifier in self._notifiers),
            return_exceptions=True,
        )
        for notifier, result in zip(self._notifiers, results, strict=True):
            if isinstance(result, Exception):
                logger.error("Notifier %s failed: %s", notifier.name, result)
                await self._record_event(
                    notification.job.id if notification.job else None,
                    "notification_error",
                    f"{notifier.name} notification failed: {result}",
                )
