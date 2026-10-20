"""Form parsing for the web layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from bms_assistant.services.bms_links import parse_bms_link
from bms_assistant.timeutils import parse_local_datetime, parse_optional_local_datetime

SEAT_ZONES = {"any", "center", "front", "back"}


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
    movie_id: str | None = None
    venue_id: str | None = None
    city: str | None = None
    show_date: date | None = None
    window_start: time | None = None
    window_end: time | None = None
    seat_prefs: dict[str, Any] = field(default_factory=dict)


def _text(data: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(data.get(key, default) or "").strip()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Show date must look like 2026-10-22.") from exc


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Showtime must look like 18:00 or 23:20.") from exc


def parse_new_job_form(
    data: Mapping[str, Any],
    *,
    tz: Any,
    default_interval: int,
    min_interval: int,
) -> NewJobForm:
    bms_raw = _text(data, "bms_url")
    link = parse_bms_link(bms_raw) if bms_raw else None

    name = _text(data, "name") or (link.movie_name if link else "") or ""
    if not name:
        raise ValueError("Give this watch a name, or paste a BMS movie link.")

    provider = _text(data, "provider", "mock")
    venue = _text(data, "venue")
    if not venue and link:
        venue = link.venue_code or f"Any theatre in {link.city_label}"
    if not venue:
        raise ValueError("Theatre or platform is required.")

    movie_id = _text(data, "movie_id") or (link.event_code if link else None)
    venue_id = _text(data, "venue_id") or (link.venue_code if link else None)
    city = _text(data, "city") or (link.city if link else None)

    show_date_raw = _text(data, "show_date")
    if show_date_raw:
        show_date = _parse_date(show_date_raw)
    elif link:
        show_date = link.show_date
    else:
        show_date = None

    release_mode = _text(data, "release_mode", "at")
    if release_mode not in {"at", "now"}:
        raise ValueError("Choose a valid ticket drop mode.")
    if release_mode == "now":
        release_at = datetime.now(tz) + timedelta(seconds=5)
    else:
        release_raw = _text(data, "release_at")
        if not release_raw:
            raise ValueError('Pick a drop time, or choose "Start watching now".')
        release_at = parse_local_datetime(release_raw, tz)

    showtime_mode = _text(data, "showtime_mode", "any")
    if showtime_mode not in {"any", "exact", "window"}:
        raise ValueError("Choose a valid showtime mode.")
    show_at = parse_optional_local_datetime(_text(data, "show_at"), tz)
    window_start: time | None = None
    window_end: time | None = None

    if showtime_mode == "exact":
        show_time_raw = _text(data, "show_time")
        if show_time_raw:
            show_time = _parse_time(show_time_raw)
            if show_date is None:
                raise ValueError("Pick the show date before the exact showtime.")
            show_at = datetime.combine(show_date, show_time, tzinfo=tz)
        elif show_at is None:
            raise ValueError("Enter the exact showtime.")
    elif showtime_mode == "window":
        start_raw = _text(data, "window_start")
        end_raw = _text(data, "window_end")
        if not start_raw or not end_raw:
            raise ValueError("Enter both the earliest and latest showtime.")
        window_start = _parse_time(start_raw)
        window_end = _parse_time(end_raw)
        if window_end < window_start:
            raise ValueError("The latest showtime must be after the earliest one.")

    if show_at is not None and show_date is None:
        show_date = show_at.date()

    seat_category = _text(data, "seat_category")
    if len(seat_category) > 40:
        raise ValueError("Seat category must be 40 characters or fewer.")
    seat_zone = _text(data, "seat_zone", "any").lower()
    if seat_zone not in SEAT_ZONES:
        raise ValueError("Seat preference must be any, center, front, or back.")
    max_price_raw = _text(data, "seat_max_price")
    max_price: int | None = None
    if max_price_raw:
        try:
            max_price = int(max_price_raw)
        except ValueError as exc:
            raise ValueError("Max ticket price must be a whole number.") from exc
        if not 0 <= max_price <= 100_000:
            raise ValueError("Max ticket price must be between 0 and 100000.")
    seats_together = _text(data, "seats_together").lower() in {
        "on",
        "true",
        "1",
        "yes",
    }
    seat_prefs: dict[str, Any] = {}
    if seat_category:
        seat_prefs["category"] = seat_category
    if max_price is not None:
        seat_prefs["max_price"] = max_price
    if seat_zone != "any":
        seat_prefs["zone"] = seat_zone
    if seats_together:
        seat_prefs["need_adjacent"] = True

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

    booking_url = _text(data, "booking_url") or (link.url if link else None) or None
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
        movie_id=movie_id,
        venue_id=venue_id,
        city=city,
        show_date=show_date,
        window_start=window_start,
        window_end=window_end,
        seat_prefs=seat_prefs,
    )
