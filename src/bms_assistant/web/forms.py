"""Form parsing for the web layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bms_assistant.timeutils import parse_local_datetime, parse_optional_local_datetime


@dataclass(slots=True)
class NewJobForm:
    name: str
    provider: str
    venue: str
    release_at: datetime
    show_at: datetime | None
    quantity: int
    booking_url: str | None
    interval_seconds: int
    auto_start: bool


def _text(data: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(data.get(key, default) or "").strip()


def parse_new_job_form(
    data: Mapping[str, Any],
    *,
    tz: Any,
    default_interval: int,
    min_interval: int,
) -> NewJobForm:
    name = _text(data, "name")
    provider = _text(data, "provider", "mock")
    venue = _text(data, "venue")
    if not name:
        raise ValueError("Give this watch a name.")
    if not venue:
        raise ValueError("Venue or platform is required.")

    release_raw = _text(data, "release_at")
    if not release_raw:
        raise ValueError("Release time is required.")
    release_at = parse_local_datetime(release_raw, tz)
    show_at = parse_optional_local_datetime(_text(data, "show_at"), tz)

    try:
        quantity = int(_text(data, "quantity", "2"))
    except ValueError as exc:
        raise ValueError("Ticket count must be a whole number.") from exc
    if not 1 <= quantity <= 10:
        raise ValueError("Ticket count must be between 1 and 10.")

    try:
        interval_seconds = int(_text(data, "interval_seconds", str(default_interval)))
    except ValueError as exc:
        raise ValueError("Check interval must be a whole number.") from exc
    interval_seconds = max(interval_seconds, min_interval)

    booking_url = _text(data, "booking_url") or None
    auto_start = _text(data, "auto_start").lower() in {"on", "true", "1", "yes"}

    return NewJobForm(
        name=name,
        provider=provider,
        venue=venue,
        release_at=release_at,
        show_at=show_at,
        quantity=quantity,
        booking_url=booking_url,
        interval_seconds=interval_seconds,
        auto_start=auto_start,
    )
