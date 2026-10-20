"""Pure seat-scoring logic for the opt-in auto-cart flow.

This module is deliberately infrastructure-free: no Playwright, no network, no I/O.
The seating math can be unit tested in CI while the browser pieces stay optional.

Scoring philosophy: the ideal seat sits near the middle of the auditorium,
horizontally and vertically, with two asymmetries:

- Rows closer to the screen are penalized harder than rows behind the middle, so
  when two seats are equidistant from the center the one farther back wins.
- Rows listed in ``SeatPrefs.avoid_bottom_rows`` (the first rows next to the
  screen) get a heavy score penalty, and the group finder skips them entirely.

Everything here is a reimplementation of well-known seat-picking heuristics; no
external code was copied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

DEFAULT_MIN_SCORE = 0.4
"""Groups scoring below this are not held automatically; ask the user instead."""

FRONT_ROW_WEIGHT = 1.35
"""Rows closer to the screen count as farther from the sweet spot than rows behind it."""

AVOIDED_ROW_MULTIPLIER = 0.25
"""Score multiplier for rows inside ``SeatPrefs.avoid_bottom_rows``."""

_MAX_RADIAL_DISTANCE = math.sqrt(2.0)


class SeatStatus(StrEnum):
    AVAILABLE = "available"
    SOLD = "sold"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Seat:
    """A single seat. Rows are lettered, numbers run left to right."""

    row_label: str
    number: int
    status: SeatStatus = SeatStatus.AVAILABLE
    category: str | None = None
    price: float | None = None

    @property
    def id(self) -> str:
        return f"{self.row_label}-{self.number}"

    @property
    def is_available(self) -> bool:
        return self.status is SeatStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class Row:
    """One row of seats. ``number`` is 1-based counting from the screen."""

    label: str
    number: int
    seats: tuple[Seat, ...] = ()


@dataclass(slots=True)
class SeatLayout:
    """All rows of an auditorium, ordered from the screen outwards."""

    rows: list[Row] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.rows)

    @property
    def max_seats_per_row(self) -> int:
        return max(
            (max((seat.number for seat in row.seats), default=0) for row in self.rows),
            default=0,
        )

    @property
    def categories(self) -> list[str]:
        found = {seat.category for row in self.rows for seat in row.seats if seat.category}
        return sorted(found)

    def row_for_label(self, label: str) -> Row | None:
        return next((row for row in self.rows if row.label == label), None)


@dataclass(slots=True)
class SeatPrefs:
    """What the user wants; mirrors the ``seat_prefs`` job configuration."""

    count: int = 2
    category: str | None = None
    avoid_bottom_rows: int = 0
    prefer_center: bool = True
    need_adjacent: bool = True
    min_score: float = DEFAULT_MIN_SCORE


@dataclass(frozen=True, slots=True)
class SeatGroup:
    """A candidate set of seats for one hold."""

    seats: tuple[Seat, ...]
    avg_score: float
    total_price: float | None = None

    @property
    def ids(self) -> list[str]:
        return [seat.id for seat in self.seats]


def score_seat(seat: Seat, layout: SeatLayout, prefs: SeatPrefs) -> float:
    """Score a single seat from 0 (unusable or worst) to 1 (ideal)."""
    if not seat.is_available:
        return 0.0
    if prefs.category and (seat.category or "").lower() != prefs.category.lower():
        return 0.0
    row = layout.row_for_label(seat.row_label)
    if row is None:
        return 0.0
    return _score_position(row.number, seat.number, layout, prefs)


def find_consecutive_groups(
    layout: SeatLayout,
    count: int,
    *,
    category: str | None = None,
    exclude_rows_below: int = 0,
) -> list[tuple[Seat, ...]]:
    """Every run of ``count`` consecutive available seats, searched row by row."""
    if count < 1:
        raise ValueError("count must be at least 1")

    groups: list[tuple[Seat, ...]] = []
    wanted_category = category.lower() if category else None
    for row in layout.rows:
        if row.number <= exclude_rows_below:
            continue
        run: list[Seat] = []
        for seat in sorted(row.seats, key=lambda item: item.number):
            usable = seat.is_available and (
                wanted_category is None or (seat.category or "").lower() == wanted_category
            )
            if usable and run and seat.number == run[-1].number + 1:
                run.append(seat)
            elif usable:
                run = [seat]
            else:
                run = []
            if len(run) >= count:
                groups.append(tuple(run[-count:]))
    return groups


def find_best_adjacent_seats(
    layout: SeatLayout,
    prefs: SeatPrefs,
    *,
    min_score: float | None = None,
) -> SeatGroup | None:
    """Pick the best holdable group, or ``None`` when nothing meets the threshold.

    When ``prefs.need_adjacent`` is false the best individual seats are combined
    even if they are not neighbors. Rows inside ``prefs.avoid_bottom_rows`` are
    never selected, whatever the score.
    """
    if prefs.count < 1:
        raise ValueError("count must be at least 1")

    threshold = prefs.min_score if min_score is None else min_score

    if prefs.need_adjacent:
        candidates = find_consecutive_groups(
            layout,
            prefs.count,
            category=prefs.category,
            exclude_rows_below=prefs.avoid_bottom_rows,
        )
    else:
        wanted_category = prefs.category.lower() if prefs.category else None
        seats = [
            seat
            for row in layout.rows
            if row.number > prefs.avoid_bottom_rows
            for seat in row.seats
            if seat.is_available
            and (wanted_category is None or (seat.category or "").lower() == wanted_category)
        ]
        if len(seats) < prefs.count:
            return None
        seats.sort(key=lambda seat: score_seat(seat, layout, prefs), reverse=True)
        candidates = [tuple(seats[: prefs.count])]

    best: SeatGroup | None = None
    for group in candidates:
        scores = [score_seat(seat, layout, prefs) for seat in group]
        avg_score = round(sum(scores) / len(scores), 4)
        if avg_score < threshold:
            continue
        prices = [seat.price for seat in group]
        total_price = float(sum(prices)) if all(price is not None for price in prices) else None
        candidate = SeatGroup(seats=group, avg_score=avg_score, total_price=total_price)
        if best is None or candidate.avg_score > best.avg_score:
            best = candidate
    return best


def _score_position(
    row_number: int,
    seat_number: int,
    layout: SeatLayout,
    prefs: SeatPrefs,
) -> float:
    """Radial distance from the middle, with a bias against front rows."""
    row_center = (layout.total_rows + 1) / 2
    column_center = (layout.max_seats_per_row + 1) / 2

    vertical = (row_number - row_center) / row_center
    if vertical < 0:
        vertical *= FRONT_ROW_WEIGHT

    horizontal = (seat_number - column_center) / column_center if prefs.prefer_center else 0.0

    distance = min(math.hypot(vertical, horizontal), _MAX_RADIAL_DISTANCE)
    score = 1.0 - distance / _MAX_RADIAL_DISTANCE
    if row_number <= prefs.avoid_bottom_rows:
        score *= AVOIDED_ROW_MULTIPLIER
    return round(score, 4)
