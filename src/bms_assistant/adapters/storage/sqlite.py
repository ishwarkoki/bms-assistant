"""SQLite adapter for the job repository port."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import aiosqlite

from bms_assistant.domain.models import Job, JobEvent, JobStatus

# Columns added after the first MVP schema. init() applies these to an existing
# local database so users do not have to delete their watches.
_MIGRATIONS = {
    "movie_id": "TEXT",
    "venue_id": "TEXT",
    "city": "TEXT",
    "show_date": "TEXT",
    "window_start": "TEXT",
    "window_end": "TEXT",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    venue TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    show_at TEXT,
    release_at TEXT,
    booking_url TEXT,
    movie_id TEXT,
    venue_id TEXT,
    city TEXT,
    show_date TEXT,
    window_start TEXT,
    window_end TEXT,
    config TEXT NOT NULL DEFAULT '{}',
    chat_id INTEGER,
    last_checked_at TEXT,
    triggered_at TEXT,
    error TEXT,
    check_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);
"""

STATUS_ORDER = """
CASE status
    WHEN 'triggered' THEN 0
    WHEN 'monitoring' THEN 1
    WHEN 'scheduled' THEN 2
    WHEN 'failed' THEN 3
    WHEN 'stopped' THEN 4
    ELSE 5
END
"""


def _dt_to_text(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _date_to_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _time_to_text(value: time | None) -> str | None:
    return value.isoformat(timespec="minutes") if value is not None else None


def _text_to_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _text_to_time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


def _text_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        id=row["id"],
        name=row["name"],
        provider=row["provider"],
        venue=row["venue"],
        quantity=row["quantity"],
        status=JobStatus(row["status"]),
        created_at=_text_to_dt(row["created_at"]) or datetime.now(UTC),
        updated_at=_text_to_dt(row["updated_at"]) or datetime.now(UTC),
        show_at=_text_to_dt(row["show_at"]),
        release_at=_text_to_dt(row["release_at"]),
        booking_url=row["booking_url"],
        config=json.loads(row["config"] or "{}"),
        chat_id=row["chat_id"],
        last_checked_at=_text_to_dt(row["last_checked_at"]),
        triggered_at=_text_to_dt(row["triggered_at"]),
        error=row["error"],
        check_count=row["check_count"],
        error_count=row["error_count"],
        movie_id=row["movie_id"],
        venue_id=row["venue_id"],
        city=row["city"],
        show_date=_text_to_date(row["show_date"]),
        window_start=_text_to_time(row["window_start"]),
        window_end=_text_to_time(row["window_end"]),
    )


def _row_to_event(row: aiosqlite.Row) -> JobEvent:
    return JobEvent(
        id=row["id"],
        job_id=row["job_id"],
        kind=row["kind"],
        message=row["message"],
        created_at=_text_to_dt(row["created_at"]) or datetime.now(UTC),
        data=json.loads(row["data"] or "{}"),
    )


class SQLiteJobRepository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(SCHEMA)
            await self._ensure_columns(db)
            await db.commit()

    async def _ensure_columns(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(jobs)")
        existing = {row["name"] for row in await cursor.fetchall()}
        for column, column_type in _MIGRATIONS.items():
            if column not in existing:
                await db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {column_type}")

    async def add_job(self, job: Job) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO jobs (
                    id, name, provider, venue, quantity, status, created_at, updated_at,
                    show_at, release_at, booking_url, movie_id, venue_id, city, show_date,
                    window_start, window_end, config, chat_id, last_checked_at,
                    triggered_at, error, check_count, error_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.name,
                    job.provider,
                    job.venue,
                    job.quantity,
                    job.status.value,
                    _dt_to_text(job.created_at),
                    _dt_to_text(job.updated_at),
                    _dt_to_text(job.show_at),
                    _dt_to_text(job.release_at),
                    job.booking_url,
                    job.movie_id,
                    job.venue_id,
                    job.city,
                    _date_to_text(job.show_date),
                    _time_to_text(job.window_start),
                    _time_to_text(job.window_end),
                    _json(job.config),
                    job.chat_id,
                    _dt_to_text(job.last_checked_at),
                    _dt_to_text(job.triggered_at),
                    job.error,
                    job.check_count,
                    job.error_count,
                ),
            )
            await db.commit()

    async def update_job(self, job: Job) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE jobs SET
                    name = ?, provider = ?, venue = ?, quantity = ?, status = ?,
                    created_at = ?, updated_at = ?, show_at = ?, release_at = ?,
                    booking_url = ?, movie_id = ?, venue_id = ?, city = ?, show_date = ?,
                    window_start = ?, window_end = ?, config = ?, chat_id = ?,
                    last_checked_at = ?, triggered_at = ?, error = ?, check_count = ?,
                    error_count = ?
                WHERE id = ?
                """,
                (
                    job.name,
                    job.provider,
                    job.venue,
                    job.quantity,
                    job.status.value,
                    _dt_to_text(job.created_at),
                    _dt_to_text(job.updated_at),
                    _dt_to_text(job.show_at),
                    _dt_to_text(job.release_at),
                    job.booking_url,
                    job.movie_id,
                    job.venue_id,
                    job.city,
                    _date_to_text(job.show_date),
                    _time_to_text(job.window_start),
                    _time_to_text(job.window_end),
                    _json(job.config),
                    job.chat_id,
                    _dt_to_text(job.last_checked_at),
                    _dt_to_text(job.triggered_at),
                    job.error,
                    job.check_count,
                    job.error_count,
                    job.id,
                ),
            )
            await db.commit()

    async def get_job(self, job_id: str) -> Job | None:
        async with self._connect() as db:
            cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cursor.fetchone()
            return _row_to_job(row) if row else None

    async def list_jobs(self) -> list[Job]:
        async with self._connect() as db:
            cursor = await db.execute(
                f"SELECT * FROM jobs ORDER BY {STATUS_ORDER}, release_at IS NULL, release_at, created_at DESC"
            )
            rows = await cursor.fetchall()
            return [_row_to_job(row) for row in rows]

    async def delete_job(self, job_id: str) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            await db.commit()

    async def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        expected_status: JobStatus | None = None,
        **fields: object,
    ) -> bool:
        allowed = {
            "booking_url",
            "triggered_at",
            "error",
            "updated_at",
            "release_at",
            "show_at",
            "chat_id",
            "error_count",
        }
        now = datetime.now(UTC)
        assignments = ["status = ?", "updated_at = ?"]
        params: list[object] = [status.value, _dt_to_text(now)]
        for key, value in fields.items():
            if key not in allowed:
                continue
            if isinstance(value, datetime):
                value = _dt_to_text(value)
            assignments.append(f"{key} = ?")
            params.append(value)

        where = "id = ?"
        params.append(job_id)
        if expected_status is not None:
            where += " AND status = ?"
            params.append(expected_status.value)

        async with self._connect() as db:
            cursor = await db.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE {where}",
                params,
            )
            await db.commit()
            return cursor.rowcount > 0

    async def record_check(
        self,
        job_id: str,
        checked_at: datetime,
        *,
        error: str | None = None,
        error_count: int = 0,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE jobs SET
                    last_checked_at = ?,
                    updated_at = ?,
                    check_count = check_count + 1,
                    error = ?,
                    error_count = ?
                WHERE id = ?
                """,
                (
                    _dt_to_text(checked_at),
                    _dt_to_text(datetime.now(UTC)),
                    error,
                    error_count,
                    job_id,
                ),
            )
            await db.commit()

    async def add_event(self, event: JobEvent) -> JobEvent:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO events (job_id, kind, message, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    event.job_id,
                    event.kind,
                    event.message,
                    _json(event.data),
                    _dt_to_text(event.created_at),
                ),
            )
            await db.commit()
            return JobEvent(
                id=cursor.lastrowid,
                job_id=event.job_id,
                kind=event.kind,
                message=event.message,
                created_at=event.created_at,
                data=event.data,
            )

    async def list_events(self, limit: int = 50) -> list[JobEvent]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM events ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            )
            rows = await cursor.fetchall()
            return [_row_to_event(row) for row in rows]
