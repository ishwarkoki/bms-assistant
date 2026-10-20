"""Domain entities and value objects. No infrastructure imports belong here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_job_id() -> str:
    return uuid4().hex[:10]


class JobStatus(StrEnum):
    SCHEDULED = "scheduled"
    MONITORING = "monitoring"
    TRIGGERED = "triggered"
    BOOKED = "booked"
    STOPPED = "stopped"
    FAILED = "failed"


STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.SCHEDULED: "Scheduled",
    JobStatus.MONITORING: "Watching",
    JobStatus.TRIGGERED: "Live now",
    JobStatus.BOOKED: "Booked",
    JobStatus.STOPPED: "Paused",
    JobStatus.FAILED: "Failed",
}


@dataclass(slots=True)
class Job:
    id: str
    name: str
    provider: str
    venue: str
    quantity: int
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    show_at: datetime | None = None
    release_at: datetime | None = None
    booking_url: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    chat_id: int | None = None
    last_checked_at: datetime | None = None
    triggered_at: datetime | None = None
    error: str | None = None
    check_count: int = 0
    error_count: int = 0

    @property
    def short_id(self) -> str:
        return self.id[:8]

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def is_watching(self) -> bool:
        return self.status == JobStatus.MONITORING

    @property
    def check_interval_seconds(self) -> int:
        value = self.config.get("check_interval_seconds", 5)
        try:
            return max(int(value), 1)
        except (TypeError, ValueError):
            return 5

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "short_id": self.short_id,
            "name": self.name,
            "provider": self.provider,
            "venue": self.venue,
            "quantity": self.quantity,
            "status": self.status.value,
            "status_label": self.status_label,
            "show_at": self.show_at.isoformat() if self.show_at else None,
            "release_at": self.release_at.isoformat() if self.release_at else None,
            "booking_url": self.booking_url,
            "chat_id": self.chat_id,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "error": self.error,
            "check_count": self.check_count,
            "error_count": self.error_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(slots=True)
class Availability:
    available: bool
    detail: str = ""
    booking_url: str | None = None
    observed_at: datetime = field(default_factory=utcnow)


@dataclass(slots=True)
class JobEvent:
    id: int | None
    job_id: str | None
    kind: str
    message: str
    created_at: datetime
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JobStats:
    total: int = 0
    monitoring: int = 0
    triggered: int = 0
    booked: int = 0
    scheduled: int = 0
    stopped: int = 0
    failed: int = 0
