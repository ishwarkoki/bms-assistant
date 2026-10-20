"""Timezone-aware parsing and display helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

INPUT_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%d %b %Y %H:%M",
)


def parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Parse a wall-clock time in `tz` and return an aware UTC datetime."""
    text = value.strip()
    if not text:
        raise ValueError("A date and time is required.")
    if text.lower() in {"now", "/now"}:
        parsed = datetime.now(tz)
    else:
        parsed = None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in INPUT_FORMATS:
                try:
                    parsed = datetime.strptime(text, fmt)
                except ValueError:
                    continue
                break
        if parsed is None:
            raise ValueError("Use a date like 2026-10-24 19:30.")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def parse_optional_local_datetime(value: str | None, tz: ZoneInfo) -> datetime | None:
    if value is None or not value.strip():
        return None
    return parse_local_datetime(value, tz)


def format_local(value: datetime | None, tz: ZoneInfo, fmt: str = "%a, %d %b · %I:%M %p") -> str:
    if value is None:
        return "—"
    return value.astimezone(tz).strftime(fmt)


def format_local_input(value: datetime | None, tz: ZoneInfo) -> str:
    if value is None:
        return ""
    return value.astimezone(tz).strftime("%Y-%m-%dT%H:%M")


def format_relative(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return ""
    current = now or datetime.now(UTC)
    delta = value - current
    future = delta.total_seconds() >= 0
    seconds = abs(int(delta.total_seconds()))

    if seconds < 10:
        return "just now" if not future else "in a moment"
    if seconds < 60:
        text = f"{seconds}s"
    elif seconds < 3600:
        text = f"{seconds // 60}m"
    elif seconds < 86400:
        text = f"{seconds // 3600}h"
    else:
        text = f"{seconds // 86400}d"
    return f"in {text}" if future else f"{text} ago"
