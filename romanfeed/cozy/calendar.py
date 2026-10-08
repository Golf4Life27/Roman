"""What a given day is about: season, holiday, sky event, rocket launch.

`themes_for(day)` lists the themes that apply, most specific first, and
always ends with the season and "evergreen". Scene picking walks that list
and takes the first theme it has a scene for, so a missing Thanksgiving scene
falls back to autumn, and autumn to anything cozy. The monthly generator
reads the same list ahead of time to know which scenes to make.

Ordering: a theme's peak day (Halloween night, Christmas Eve/Day, a meteor
shower's peak, an eclipse, a notable launch) beats everything; then sky
events, then holidays, then the season. So Oct 20-22 is Halloween except on
the Orionids' peak night, which goes to the meteor scene if there is one.

Dates are for a US (northern hemisphere) audience. Fixed-date facts here are
astronomy, not guesses: the meteor peaks are the IMO's usual nights (they
move by a day at most), and the eclipses are only ones in published NASA
tables. Launch dates come live from Launch Library 2 (free, no key).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

EVENT, HOLIDAY, SEASON, EVERGREEN = "event", "holiday", "season", "evergreen"
_RANK = {EVENT: 0, HOLIDAY: 1, SEASON: 2, EVERGREEN: 3}


@dataclass(frozen=True)
class Theme:
    key: str           # scene tag: "halloween", "meteors", "winter", ...
    label: str         # for titles: "Halloween", "Geminid Meteor Shower"
    kind: str          # EVENT | HOLIDAY | SEASON | EVERGREEN
    peak: bool = False  # the night itself, not the run-up
    detail: str = ""   # launch name, shower name, ...


# Peak night of each annual shower (month, day, name).
METEOR_SHOWERS = [
    (1, 3, "Quadrantid Meteor Shower"),
    (4, 22, "Lyrid Meteor Shower"),
    (5, 6, "Eta Aquariid Meteor Shower"),
    (8, 12, "Perseid Meteor Shower"),
    (10, 21, "Orionid Meteor Shower"),
    (11, 17, "Leonid Meteor Shower"),
    (12, 14, "Geminid Meteor Shower"),
]

# From NASA's eclipse tables. Add new ones here as they come into range.
ECLIPSES = {
    date(2026, 3, 3): "Total Lunar Eclipse",
    date(2026, 8, 12): "Total Solar Eclipse",
    date(2027, 8, 2): "Total Solar Eclipse",
    date(2028, 7, 22): "Total Solar Eclipse",
    date(2028, 12, 31): "Total Lunar Eclipse",
    date(2029, 6, 26): "Total Lunar Eclipse",
}

SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}

# Launches worth a themed night. Matched against the Launch Library name
# ("Falcon 9 Block 5 | Crew-12"), case-insensitive. Starlink and the like
# fly several times a week and are not an event.
NOTABLE_LAUNCH_WORDS = ["artemis", "starship", "crew", "axiom", "polaris", "roman", "europa",
                        "mars", "moon", "lunar", "jupiter", "saturn", "venus", "telescope", "new glenn"]


def easter(year: int) -> date:
    """Western Easter (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    wd = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * wd) // 451
    month, day = divmod(h + wd - 7 * m + 114, 31)
    return date(year, month, day + 1)


def thanksgiving(year: int) -> date:
    """US Thanksgiving: the fourth Thursday of November."""
    first = date(year, 11, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 21)


def season(day: date) -> str:
    return SEASONS[day.month]


def _holidays(day: date) -> list[Theme]:
    y, out = day.year, []

    def window(key: str, label: str, start: date, end: date, peaks: set[date]) -> None:
        if start <= day <= end:
            out.append(Theme(key, label, HOLIDAY, day in peaks))

    window("new_year", "New Year's Eve", date(y, 12, 28), date(y, 12, 31), {date(y, 12, 31)})
    window("new_year", "New Year", date(y, 1, 1), date(y, 1, 2), {date(y, 1, 1)})
    window("valentines", "Valentine's", date(y, 2, 7), date(y, 2, 14), {date(y, 2, 14)})
    e = easter(y)
    window("easter", "Easter", e - timedelta(days=7), e, {e})
    window("independence_day", "Fourth of July", date(y, 7, 1), date(y, 7, 4), {date(y, 7, 4)})
    window("halloween", "Halloween", date(y, 10, 1), date(y, 10, 31), {date(y, 10, 31)})
    t = thanksgiving(y)
    window("thanksgiving", "Thanksgiving", date(y, 11, 1), t, {t})
    window("christmas", "Christmas", date(y, 12, 1), date(y, 12, 27), {date(y, 12, 24), date(y, 12, 25)})
    return out


def _sky_events(day: date) -> list[Theme]:
    out = []
    for month, d, name in METEOR_SHOWERS:
        try:
            peak = date(day.year, month, d)
        except ValueError:
            continue
        if abs((day - peak).days) <= 1:
            out.append(Theme("meteors", name, EVENT, day == peak, name))
    for when, name in ECLIPSES.items():
        if abs((day - when).days) <= 1:
            out.append(Theme("eclipse", name, EVENT, day == when, name))
    return out


@dataclass(frozen=True)
class Launch:
    name: str
    net: datetime   # UTC
    status: str     # "Go", "TBC", "TBD", ...

    @property
    def mission(self) -> str:
        return self.name.split(" | ", 1)[-1].strip()


def notable(launch: Launch, words: list[str] | None = None) -> bool:
    if launch.status not in {"Go", "TBC"}:
        return False  # "TBD" dates move by weeks; a themed night on a guess is worse than none
    name = launch.name.lower()
    return any(w in name for w in (words or NOTABLE_LAUNCH_WORDS))


def _launch_events(day: date, launches: list[Launch] | None, words: list[str] | None) -> list[Theme]:
    out = []
    for launch in launches or []:
        d = launch.net.date()
        if notable(launch, words) and d - timedelta(days=1) <= day <= d:
            out.append(Theme("launch", f"{launch.mission} Launch", EVENT, day == d, launch.name))
    return out


def themes_for(day: date, *, launches: list[Launch] | None = None,
               launch_words: list[str] | None = None) -> list[Theme]:
    """Every theme for `day`, most specific first; never empty."""
    found = _launch_events(day, launches, launch_words) + _sky_events(day) + _holidays(day)
    found.sort(key=lambda t: (not t.peak, _RANK[t.kind]))
    s = season(day)
    found.append(Theme(s, s.capitalize(), SEASON))
    found.append(Theme("evergreen", "Cozy", EVERGREEN))
    seen, out = set(), []
    for t in found:  # one entry per key: a key's first (best) reason wins
        if t.key not in seen:
            seen.add(t.key)
            out.append(t)
    return out


def upcoming(start: date, days: int, *, launches: list[Launch] | None = None,
             launch_words: list[str] | None = None) -> dict[str, list[date]]:
    """theme key -> the dates in [start, start+days) it is the night's first
    choice or a fallback for. The generator uses it to see what is coming."""
    out: dict[str, list[date]] = {}
    for i in range(days):
        d = start + timedelta(days=i)
        for t in themes_for(d, launches=launches, launch_words=launch_words):
            out.setdefault(t.key, []).append(d)
    return out


LL2 = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/?limit=100&mode=list&hide_recent_previous=true"


def fetch_launches(*, cache: Path | None = None, max_age_s: int = 6 * 3600, timeout: int = 20) -> list[Launch]:
    """Upcoming launches from Launch Library 2, or [] if it cannot be reached.

    The free tier allows 15 calls an hour; a cache file keeps a busy day (a
    sync, a plan, a start) to one call. A failure never stops a stream: the
    night just has no launch theme."""
    raw = None
    if cache and cache.exists() and time.time() - cache.stat().st_mtime < max_age_s:
        try:
            raw = json.loads(cache.read_text())
        except ValueError:
            raw = None
    if raw is None:
        try:
            req = urllib.request.Request(LL2, headers={"User-Agent": "romanfeed (Space Screens)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = json.load(r)
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(raw))
        except Exception as exc:  # network, HTTP 429, bad JSON: no launch themes tonight
            log.warning("launch schedule unavailable: %s", exc)
            return []
    return parse_launches(raw)


def parse_launches(raw: dict) -> list[Launch]:
    out = []
    for r in raw.get("results", []):
        try:
            net = datetime.fromisoformat(r["net"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        out.append(Launch(r.get("name", ""), net, (r.get("status") or {}).get("abbrev", "")))
    return out
