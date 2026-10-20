"""Parse BookMyShow URLs into watch criteria.

Pure functions only: no HTTP, no browser, no credentials. The URL is the
safest way for a user to identify a movie/venue/show because BMS itself
generates it from the page they are looking at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import SplitResult, unquote, urlsplit

EVENT_CODE_RE = re.compile(r"^[A-Z]{2,4}\d{4,}$")
DATE_CODE_RE = re.compile(r"^\d{8}$")
VENUE_CODE_RE = re.compile(r"^[A-Z0-9]{2,12}$")

# BMS uses /movies/<city>/seat-layout/<event>/<venue>/<session>/<date> when a
# show is selected, and /movies/<city>/<slug>/buytickets/<event>/<date> for the
# showtimes list. Movie detail pages are /movies/<city>/<slug>/<event>.
_SEAT_LAYOUT = re.compile(
    r"^/movies/(?P<city>[^/]+)/seat-layout/"
    r"(?P<event>[A-Za-z]{2,4}\d{4,})/(?P<venue>[A-Za-z0-9]+)/"
    r"(?P<session>\d+)/(?P<date>\d{8})/?$"
)
_BUY_TICKETS = re.compile(
    r"^/movies/(?P<city>[^/]+)/(?P<slug>[^/]+)/buytickets/"
    r"(?P<event>[A-Za-z]{2,4}\d{4,})/(?P<date>\d{8})/?$"
)
_MOVIE = re.compile(r"^/movies/(?P<city>[^/]+)/(?P<slug>[^/]+)/(?P<event>[A-Za-z]{2,4}\d{4,})/?$")

_CITY_LABELS = {
    "hyd": "Hyderabad",
    "blr": "Bengaluru",
    "bom": "Mumbai",
    "del": "Delhi",
    "che": "Chennai",
    "kol": "Kolkata",
    "pnq": "Pune",
    "goi": "Goa",
    "cok": "Kochi",
    "amd": "Ahmedabad",
    "jai": "Jaipur",
    "lko": "Lucknow",
    "chd": "Chandigarh",
    "ind": "Indore",
    "nag": "Nagpur",
    "sur": "Surat",
    "viz": "Visakhapatnam",
    "vij": "Vijayawada",
    "pat": "Patna",
    "bbs": "Bhubaneswar",
    "gua": "Guwahati",
    "coi": "Coimbatore",
    "trv": "Thiruvananthapuram",
}
_SMALL_WORDS = {"a", "an", "and", "at", "for", "in", "of", "on", "the", "to"}


@dataclass(frozen=True, slots=True)
class BmsLink:
    """A validated BMS page URL and the identifiers it contains."""

    url: str
    kind: str
    city: str
    event_code: str
    slug: str | None = None
    venue_code: str | None = None
    session_id: str | None = None
    show_date: date | None = None

    @property
    def movie_name(self) -> str | None:
        return humanize_movie_slug(self.slug) if self.slug else None

    @property
    def city_label(self) -> str:
        return _CITY_LABELS.get(self.city, self.city.replace("-", " ").title())

    @property
    def kind_label(self) -> str:
        return {
            "seat-layout": "seat page",
            "showtimes": "showtimes page",
            "movie": "movie page",
        }.get(self.kind, "BMS page")

    @property
    def short_summary(self) -> str:
        bits = [self.movie_name or self.event_code, self.city_label]
        if self.show_date:
            bits.append(self.show_date.strftime("%a, %d %b %Y"))
        if self.venue_code:
            bits.append(self.venue_code)
        return " · ".join(bits)


def humanize_movie_slug(slug: str) -> str:
    """Turn ``jailer-2`` into ``Jailer 2`` while keeping acronyms readable."""
    cleaned = unquote(slug).replace("-", " ").replace("_", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    words = []
    for index, word in enumerate(cleaned.split(" ")):
        if index and word.lower() in _SMALL_WORDS:
            words.append(word.lower())
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def parse_bms_link(value: str) -> BmsLink:
    """Parse a BMS movie, showtimes, or seat-layout URL.

    Raises ``ValueError`` with a user-facing message when the link is not one
    of the supported BMS page shapes. The normalized URL drops tracking query
    parameters but keeps the page path the user copied.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Paste a BookMyShow link.")

    candidate = raw if "://" in raw else f"https://{raw}"
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("The link must start with http:// or https://.")

    host = parts.netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if host != "bookmyshow.com" and not host.endswith(".bookmyshow.com"):
        raise ValueError("That link is not from BookMyShow.")

    path = parts.path.rstrip("/") or "/"
    match = _SEAT_LAYOUT.match(path)
    if match:
        return _build(match, parts, kind="seat-layout")
    match = _BUY_TICKETS.match(path)
    if match:
        return _build(match, parts, kind="showtimes")
    match = _MOVIE.match(path)
    if match and match.group("slug") not in {"seat-layout", "buytickets"}:
        return _build(match, parts, kind="movie")

    raise ValueError(
        "Copy the URL from a BookMyShow movie page or from the seat-selection page "
        "(it should contain /movies/...)."
    )


def _build(match: re.Match[str], parts: SplitResult, *, kind: str) -> BmsLink:
    groups = match.groupdict()
    event_code = groups["event"].upper()
    if not EVENT_CODE_RE.match(event_code):
        raise ValueError("The movie/event code in that BookMyShow link looks wrong.")

    show_date = None
    if groups.get("date"):
        if not DATE_CODE_RE.match(groups["date"]):
            raise ValueError("The date in that BookMyShow link looks wrong.")
        try:
            show_date = datetime.strptime(groups["date"], "%Y%m%d").date()
        except ValueError as exc:
            raise ValueError("The date in that BookMyShow link is not valid.") from exc

    venue_code = groups.get("venue")
    if venue_code:
        venue_code = venue_code.upper()
        if not VENUE_CODE_RE.match(venue_code):
            raise ValueError("The venue code in that BookMyShow link looks wrong.")

    city = unquote(groups["city"]).strip().lower()
    if not city:
        raise ValueError("The city is missing from that BookMyShow link.")

    normalized = f"https://in.bookmyshow.com{parts.path.rstrip('/')}"
    return BmsLink(
        url=normalized,
        kind=kind,
        city=city,
        event_code=event_code,
        slug=unquote(groups.get("slug") or "").strip() or None,
        venue_code=venue_code,
        session_id=groups.get("session"),
        show_date=show_date,
    )


__all__ = ["BmsLink", "humanize_movie_slug", "parse_bms_link"]
