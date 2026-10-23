"""How many days of long videos the image library still covers.

Counts the fresh (never shown) suitable images per subject, then walks the
calendar the way the daily render does: an Earth day takes Earth photos while
at least 1.5 videos' worth remain (pipeline.todays_subject), every other day
takes deep-space images. The walk stops on the first day the pool it needs
runs short, which is the day the channel would start repeating images.

Measured from the live sources and the shared ledger; nothing is rendered or
sent. Some images still fail the size check after download, so the real
figure can be a little lower than this one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from romanfeed.config import WEEKDAYS, ChannelConfig


@dataclass
class Supply:
    space_fresh: int
    earth_fresh: int
    space_suitable: int
    earth_suitable: int


@dataclass
class Runway:
    days: int                 # days before the first video that reuses images
    space_days: int
    earth_days: int
    last_day: date | None     # the last of those days
    earth_total: int = 0      # Earth days with fresh photos, counting past a deep-space repeat
    earth_last: date | None = None


def runway(supply: Supply, *, per_video: int, earth_weekdays: list[str], start: date,
           render_weekdays: list[str] | None = None, horizon_days: int = 730) -> Runway:
    """Walk the render calendar. A deep-space day with too few fresh images
    reuses old ones (the selector recycles only the deep-space pool), so the
    walk goes on to count how long the Earth photos last on their own."""
    render_weekdays = render_weekdays or list(WEEKDAYS)
    space, earth = supply.space_fresh, supply.earth_fresh
    days = space_days = earth_days = earth_total = 0
    last: date | None = None
    earth_last: date | None = None
    repeating = False
    for i in range(horizon_days):
        day = start + timedelta(days=i)
        name = WEEKDAYS[day.weekday()]
        if name not in render_weekdays:
            continue
        if name in earth_weekdays and earth >= per_video * 1.5:
            earth -= per_video
            earth_total += 1
            earth_last = day
            if not repeating:
                earth_days += 1
        elif space >= per_video:
            space -= per_video
            if not repeating:
                space_days += 1
        else:
            repeating = True
            if earth < per_video * 1.5:
                break
            continue
        if not repeating:
            days += 1
            last = day
    return Runway(days=days, space_days=space_days, earth_days=earth_days, last_day=last,
                  earth_total=earth_total, earth_last=earth_last)


def report_lines(cfg: ChannelConfig, supply: Supply, start: date) -> list[str]:
    n = cfg.video.images_per_video
    r = runway(supply, per_video=n, earth_weekdays=cfg.earth.weekdays, start=start)
    days = ", ".join(cfg.earth.weekdays) or "none"
    lines = [
        f"Deep-space images never shown: {supply.space_fresh:,} of {supply.space_suitable:,} "
        f"(about {supply.space_fresh // n} videos at {n} each)",
        f"Space station Earth photos never shown: {supply.earth_fresh:,} of {supply.earth_suitable:,} "
        f"(about {supply.earth_fresh // n} videos)",
        f"Daily long videos with no repeated image, from {start:%b %d}: {r.days} days "
        f"({r.space_days} deep space, {r.earth_days} Earth; Earth days {days})"
        + (f", through {r.last_day:%b %d}" if r.last_day else ""),
        f"Earth days with fresh photos: {r.earth_total}"
        + (f", through {r.earth_last:%b %d}" if r.earth_last else "")
        + " (deep-space days after the fresh ones run out reuse earlier images in new mixes)",
    ]
    return lines


def earth_rejections(candidates, samples: int = 4) -> list[str]:
    """Why Earth-source photos were turned away, with a few titles of each
    kind: the evidence for widening the Earth filter without guessing."""
    import re
    from collections import defaultdict

    from romanfeed.curation import selector as sel

    why: dict[str, list] = defaultdict(list)
    for a in candidates:
        if not sel.is_earth(a) or not sel.earth_unsuitable(a):
            continue
        low = a.title.strip().lower()
        if sel._PHOTO_ID_RE.match(a.title.strip()) or sel._is_filename_title(a.title.strip()) or re.match(r"^iss\d{3}e\d+", low):
            reason = "frame number as title"
        elif low.startswith(("earth observation", "crew earth observations", "earth observations taken")) or \
                re.sub(r"[^a-z ]", "", low).strip() in {"earth observation", "earth observations", "earth science",
                                                      "earth", "crew earth observations"}:
            reason = "generic title"
        elif any(h in low for h in sel.EARTH_BLOCK) or low.startswith(("the international space station", "space station")):
            reason = "hardware or people in title"
        elif not any(h in low for h in sel.EARTH_HINTS):
            reason = "no scene word in title"
        elif sel.earth_rights_unclear(a):
            reason = "rights not clearly NASA"
        else:
            reason = "not shown to be from orbit"
        why[reason].append(a)
    lines = []
    for reason, items in sorted(why.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"Earth turned away, {reason}: {len(items):,}")
        for a in items[:samples]:
            desc = " ".join(a.description.split())[:160]
            lines.append(f"  e.g. {a.title[:70]!r} / {desc!r}")
    return lines
