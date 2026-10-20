from __future__ import annotations

from datetime import time

import pytest

from bms_assistant.bot.handlers import _parse_showtime_choice


def test_showtime_any():
    assert _parse_showtime_choice("any") == ("any", None, None, None)


def test_showtime_window():
    assert _parse_showtime_choice("18:00-23:30") == (
        "window",
        time(18, 0),
        time(23, 30),
        None,
    )


def test_showtime_window_accepts_en_dash():
    assert _parse_showtime_choice("18:00 \u2013 23:30") == (
        "window",
        time(18, 0),
        time(23, 30),
        None,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11:20 PM", time(23, 20)),
        ("7:55 pm", time(19, 55)),
        ("23:20", time(23, 20)),
    ],
)
def test_showtime_exact(text, expected):
    assert _parse_showtime_choice(text) == ("exact", None, None, expected)


def test_showtime_rejects_backwards_window():
    with pytest.raises(ValueError, match="after"):
        _parse_showtime_choice("23:30-18:00")


def test_showtime_rejects_garbage():
    with pytest.raises(ValueError, match="Send"):
        _parse_showtime_choice("evening-ish")
