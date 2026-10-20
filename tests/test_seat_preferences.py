from __future__ import annotations

import pytest

from bms_assistant.services.seat_preferences import (
    Row,
    Seat,
    SeatLayout,
    SeatPrefs,
    SeatStatus,
    find_best_adjacent_seats,
    find_consecutive_groups,
    score_seat,
)

ROWS = 12
SEATS_PER_ROW = 10


def make_layout(
    *,
    rows: int = ROWS,
    seats_per_row: int = SEATS_PER_ROW,
    sold: set[str] | None = None,
    blocked: set[str] | None = None,
    categories: dict[int, str] | None = None,
    price: float | None = None,
) -> SeatLayout:
    sold = sold or set()
    blocked = blocked or set()
    categories = categories or {}

    layout_rows: list[Row] = []
    for row_number in range(1, rows + 1):
        label = chr(ord("A") + row_number - 1)
        seats: list[Seat] = []
        for number in range(1, seats_per_row + 1):
            seat_id = f"{label}-{number}"
            if seat_id in sold:
                status = SeatStatus.SOLD
            elif seat_id in blocked:
                status = SeatStatus.BLOCKED
            else:
                status = SeatStatus.AVAILABLE
            seats.append(
                Seat(
                    row_label=label,
                    number=number,
                    status=status,
                    category=categories.get(row_number),
                    price=price,
                )
            )
        layout_rows.append(Row(label=label, number=row_number, seats=tuple(seats)))
    return SeatLayout(rows=layout_rows)


def all_rows_seats(numbers: tuple[int, ...]) -> set[str]:
    return {
        f"{chr(ord('A') + row_number - 1)}-{number}"
        for row_number in range(1, ROWS + 1)
        for number in numbers
    }


def test_center_beats_corner_in_the_same_row():
    layout = make_layout()
    prefs = SeatPrefs(count=1)
    assert score_seat(Seat("F", 5), layout, prefs) > score_seat(Seat("F", 1), layout, prefs)


def test_rows_behind_the_middle_beat_equally_distant_front_rows():
    layout = make_layout(rows=12)
    prefs = SeatPrefs(count=1)
    # With 12 rows the middle sits between rows 6 and 7, so rows 9 and 4 are equidistant.
    behind = score_seat(Seat("I", 5), layout, prefs)
    front = score_seat(Seat("D", 5), layout, prefs)
    assert behind > front


def test_avoid_bottom_rows_penalizes_front_rows():
    layout = make_layout()
    relaxed = SeatPrefs(count=1)
    strict = SeatPrefs(count=1, avoid_bottom_rows=3)
    assert score_seat(Seat("B", 5), layout, strict) < score_seat(Seat("B", 5), layout, relaxed)


def test_avoided_rows_are_never_selected():
    sold = {
        f"{chr(ord('A') + row_number - 1)}-{number}"
        for row_number in range(2, ROWS + 1)
        for number in range(1, SEATS_PER_ROW + 1)
    }
    layout = make_layout(sold=sold)  # only row A is available

    assert find_best_adjacent_seats(layout, SeatPrefs(count=2, avoid_bottom_rows=1)) is None

    relaxed = SeatPrefs(count=2, avoid_bottom_rows=0, min_score=0.0)
    allowed = find_best_adjacent_seats(layout, relaxed)
    assert allowed is not None
    assert {seat.row_label for seat in allowed.seats} == {"A"}


def test_sold_and_blocked_seats_break_adjacency():
    layout = make_layout(sold=all_rows_seats((4,)), blocked=all_rows_seats((5,)))
    group = find_best_adjacent_seats(layout, SeatPrefs(count=2))

    assert group is not None
    assert all(seat.is_available for seat in group.seats)
    numbers = [seat.number for seat in group.seats]
    assert numbers[1] == numbers[0] + 1
    assert set(numbers).isdisjoint({4, 5})


def test_category_filter_is_case_insensitive():
    categories = {row: ("Premium" if row <= 6 else "Recliner") for row in range(1, ROWS + 1)}
    layout = make_layout(categories=categories)
    group = find_best_adjacent_seats(layout, SeatPrefs(count=2, category="recliner"))

    assert group is not None
    assert all(seat.category == "Recliner" for seat in group.seats)


def test_min_score_threshold_blocks_a_bad_hold():
    sold = {
        f"{chr(ord('A') + row_number - 1)}-{number}"
        for row_number in range(2, 11)
        for number in range(1, SEATS_PER_ROW + 1)
    }
    layout = make_layout(rows=10, sold=sold)  # only the worst-row seats are left

    assert find_best_adjacent_seats(layout, SeatPrefs(count=2)) is None
    assert find_best_adjacent_seats(layout, SeatPrefs(count=2), min_score=0.1) is not None


def test_need_adjacent_false_allows_scattered_seats():
    layout = make_layout(sold=all_rows_seats((2, 4, 6, 8, 10)))  # no two neighbours available

    assert find_best_adjacent_seats(layout, SeatPrefs(count=2)) is None

    group = find_best_adjacent_seats(layout, SeatPrefs(count=2, need_adjacent=False))
    assert group is not None
    assert len(group.seats) == 2
    assert all(seat.number % 2 == 1 for seat in group.seats)


def test_total_price_is_summed_only_when_known():
    priced = make_layout(price=250.0)
    group = find_best_adjacent_seats(priced, SeatPrefs(count=2))
    assert group is not None
    assert group.total_price == 500.0

    unpriced = make_layout()
    group = find_best_adjacent_seats(unpriced, SeatPrefs(count=2))
    assert group is not None
    assert group.total_price is None


def test_score_seat_rejects_unavailable_unknown_and_mismatched():
    layout = make_layout()
    assert score_seat(Seat("F", 5, status=SeatStatus.SOLD), layout, SeatPrefs(count=1)) == 0.0
    assert score_seat(Seat("Z", 1), layout, SeatPrefs(count=1)) == 0.0

    premium = SeatPrefs(count=1, category="Recliner")
    assert score_seat(Seat("F", 5, category="Premium"), layout, premium) == 0.0


def test_prefer_center_false_treats_every_column_alike():
    layout = make_layout()
    centered = SeatPrefs(count=1)
    anywhere = SeatPrefs(count=1, prefer_center=False)

    assert score_seat(Seat("G", 5), layout, centered) > score_seat(Seat("G", 1), layout, centered)
    assert score_seat(Seat("G", 1), layout, anywhere) == score_seat(Seat("G", 9), layout, anywhere)


def test_empty_and_insufficient_layouts():
    assert find_best_adjacent_seats(SeatLayout(), SeatPrefs(count=1)) is None

    only_one = {
        f"{chr(ord('A') + row_number - 1)}-{number}"
        for row_number in range(1, ROWS + 1)
        for number in range(1, SEATS_PER_ROW + 1)
        if not (row_number == 7 and number == 5)
    }
    layout = make_layout(sold=only_one)
    assert find_best_adjacent_seats(layout, SeatPrefs(count=2)) is None

    single = find_best_adjacent_seats(layout, SeatPrefs(count=1))
    assert single is not None
    assert single.ids == ["G-5"]


def test_find_consecutive_groups_slides_windows_and_applies_filters():
    plain = make_layout(rows=1, seats_per_row=4)
    groups = find_consecutive_groups(plain, 2)
    assert [group[0].number for group in groups] == [1, 2, 3]

    categories = {row: ("Premium" if row <= 3 else "Recliner") for row in range(1, 7)}
    layout = make_layout(rows=6, categories=categories)
    filtered = find_consecutive_groups(layout, 2, category="Recliner", exclude_rows_below=3)

    assert filtered
    assert all(seat.category == "Recliner" for group in filtered for seat in group)
    assert all(group[0].row_label in {"D", "E", "F"} for group in filtered)


def test_invalid_count_is_rejected():
    with pytest.raises(ValueError):
        find_best_adjacent_seats(make_layout(), SeatPrefs(count=0))
    with pytest.raises(ValueError):
        find_consecutive_groups(make_layout(), 0)
