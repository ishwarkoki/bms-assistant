from __future__ import annotations

from datetime import date

import pytest

from bms_assistant.services.bms_links import humanize_movie_slug, parse_bms_link


def test_parse_seat_layout_link():
    link = parse_bms_link(
        "https://in.bookmyshow.com/movies/hyderabad/seat-layout/ET00521930/ALUC/6296/20261022"
    )
    assert link.kind == "seat-layout"
    assert link.city == "hyderabad"
    assert link.event_code == "ET00521930"
    assert link.venue_code == "ALUC"
    assert link.session_id == "6296"
    assert link.show_date == date(2026, 10, 22)
    assert link.url == (
        "https://in.bookmyshow.com/movies/hyderabad/seat-layout/ET00521930/ALUC/6296/20261022"
    )


def test_parse_showtimes_link_and_drop_query():
    link = parse_bms_link(
        "https://in.bookmyshow.com/movies/hyderabad/jailer-2/buytickets/"
        "ET00521930/20261022?etCodes=ET00521930&language=telugu"
    )
    assert link.kind == "showtimes"
    assert link.slug == "jailer-2"
    assert link.movie_name == "Jailer 2"
    assert link.show_date == date(2026, 10, 22)
    assert "?" not in link.url


def test_parse_movie_link_and_city_alias():
    link = parse_bms_link("https://in.bookmyshow.com/movies/hyd/jailer-2/ET00518675")
    assert link.kind == "movie"
    assert link.city == "hyd"
    assert link.city_label == "Hyderabad"
    assert link.venue_code is None
    assert link.show_date is None


def test_accepts_missing_scheme_and_www_host():
    link = parse_bms_link(
        "www.bookmyshow.com/movies/hyderabad/seat-layout/ET00521930/ALUC/6296/20261022"
    )
    assert link.city == "hyderabad"
    assert link.url.startswith("https://in.bookmyshow.com/")


def test_rejects_non_bms_host():
    with pytest.raises(ValueError, match="not from BookMyShow"):
        parse_bms_link("https://example.com/movies/hyderabad/jailer-2/ET00521930")


def test_rejects_unknown_path():
    with pytest.raises(ValueError, match="Copy the URL"):
        parse_bms_link("https://in.bookmyshow.com/explore/home/hyderabad")


def test_rejects_partial_seat_layout_path():
    with pytest.raises(ValueError, match="Copy the URL"):
        parse_bms_link("https://in.bookmyshow.com/movies/hyderabad/seat-layout/ET00521930")


def test_rejects_invalid_date():
    with pytest.raises(ValueError, match="date"):
        parse_bms_link(
            "https://in.bookmyshow.com/movies/hyderabad/seat-layout/ET00521930/ALUC/6296/20261340"
        )


def test_humanize_movie_slug():
    assert humanize_movie_slug("jailer-2") == "Jailer 2"
    assert humanize_movie_slug("spider-man_across-the-spider-verse") == (
        "Spider Man Across the Spider Verse"
    )
    assert humanize_movie_slug("") == ""
