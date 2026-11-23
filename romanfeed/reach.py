""""How long would it take to reach it?" Shorts (test 22, config `shorts.reach_*`).

From the Nov 23 subscriber study (competitor-study.md): the fastest-growing new
space channel found, Mr. Earth (created Apr 3, 2026, 51.7K subscribers), grew on
one question reused across many targets: "How Long Would It Take to Reach
Mars?" 7.17M views, "...Proxima Centauri?" 2.45M, "...Earth From the Sun?" 2.29M
(vidIQ, read Nov 23). This is our version, drawn here and checked against NASA:

  0 s   the question in big type, a star field, Earth at the bottom and the
        target glowing at the top
  0.4 s a probe leaves Earth; a counter races up beside it
  6 s   the probe arrives; the counter lands on the answer at Voyager 1's speed
  6.4 s "Light takes ..." for scale
  7.5 s the subscribe plate every Short carries

Every distance is quoted from the NASA page it was read at (TARGETS). The
travel time is worked out here from that distance and Voyager 1's speed on
NASA's Voyager 1 page, rounded to two significant figures; light's time from
the same distance. `test_reach.py` recomputes every number shown.

Tagged `how-long-reach`, posted to YouTube only, never with a hook, opening,
title or end-card treatment, and kept out of every other test's numbers. When
one cannot be made the slot is a normal Short and the log says why."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageDraw

from romanfeed.render.captions import _font
from romanfeed.scale import CENTRAL, background, splat

REACH_TAG = "how-long-reach"
REACH_PARENT = "how-long-reach"   # shorts.parent_slug: kept out of the long videos' rotation

SECONDS = 10.0
TRAVEL_FROM, TRAVEL_TO = 0.4, 6.0
LIGHT_AT = 6.4
PLATE_SECONDS = 2.5
EARTH_Y, TARGET_Y = 0.84, 0.30
ROUTE_X = 0.13   # the route runs up the left edge, clear of the counter

KM_PER_LY = 9.4607e12          # IAU light-year, km
LIGHT_KM_S = 299_792.458       # exact by definition
YEAR_S = 365.25 * 86400


@dataclass(frozen=True)
class Source:
    org: str
    url: str
    read: str
    quote: str   # the page's own words for the figure (a fragment where only the figure was copied)


VOYAGER = Source(
    "NASA", "https://science.nasa.gov/mission/voyager/voyager-1/",
    "read Nov 23, 2026 (page updated Nov 18, 2026)",
    "moving at a velocity of 38,026.79 mph (17.0 km/second) relative to the Sun")
VOYAGER_KM_S = 17.0


@dataclass(frozen=True)
class Target:
    id: str
    name: str            # as said: "the Orion Nebula"
    km: float            # distance in km (km figure, or light-years x KM_PER_LY)
    shown: str           # the distance as the page writes it: "1,500 light-years"
    source: Source
    colour: tuple[int, int, int]

    @property
    def title(self) -> str:
        return f"How Long Would It Take to Reach {self.name}? 🚀"


def _ly(n: float) -> float:
    return n * KM_PER_LY


TARGETS: tuple[Target, ...] = (
    Target("moon", "the Moon", 384_400, "384,400 kilometers",
           Source("NASA", "https://science.nasa.gov/moon/facts/", "read Nov 23, 2026 (page updated Feb 12, 2026)",
                  "384,400 kilometers (average distance)"), (226, 226, 220)),
    Target("proxima", "Proxima Centauri", _ly(4.2), "4.2 light-years",
           Source("NASA", "https://science.nasa.gov/asset/hubble/proxima-centauri/",
                  "read Nov 23, 2026 (page updated Aug 17, 2025)", "4.2 light-years"),
           (255, 150, 110)),
    Target("orion", "the Orion Nebula", _ly(1_500), "1,500 light-years",
           Source("NASA", "https://science.nasa.gov/mission/hubble/science/explore-the-night-sky/hubble-messier-catalog/messier-42/",
                  "read Nov 23, 2026 (page updated Aug 3, 2026)", "1,500 light-years"),
           (255, 120, 170)),
    Target("andromeda", "the Andromeda Galaxy", _ly(2.5e6), "2.5 million light-years",
           Source("NASA", "https://science.nasa.gov/mission/hubble/science/explore-the-night-sky/hubble-messier-catalog/messier-31/",
                  "read Nov 23, 2026 (page updated Nov 10, 2026)", "2.5 million light-years"),
           (210, 220, 255)),
    Target("sun", "the Sun", 150e6, "150 million kilometers",
           Source("NASA", "https://science.nasa.gov/dwarf-planets/pluto/facts/",
                  "read Nov 23, 2026 (page updated Apr 22, 2025)",
                  "about 93 million miles or 150 million kilometers"),
           (255, 214, 120)),
    Target("crab", "the Crab Nebula", _ly(6_500), "6,500 light-years",
           Source("NASA", "https://science.nasa.gov/mission/hubble/science/explore-the-night-sky/hubble-messier-catalog/messier-1",
                  "read Nov 23, 2026 (page updated Apr 24, 2025)", "6,500 light-years"),
           (255, 170, 110)),
    Target("center", "the Center of the Milky Way", _ly(26_000), "26,000 light-years",
           Source("NASA", "https://science.nasa.gov/asset/hubble/milky-way-center-in-multiple-wavelengths",
                  "read Nov 23, 2026 (page updated Aug 17, 2025)",
                  "26,000 light-years"),
           (255, 236, 190)),
    Target("pillars", "the Pillars of Creation", _ly(6_500), "6,500 light-years",
           Source("NASA", "https://science.nasa.gov/missions/webb/nasas-webb-takes-star-filled-portrait-of-pillars-of-creation/",
                  "read Nov 23, 2026 (page updated Sep 21, 2026)",
                  "set within the vast Eagle Nebula, which lies 6,500 light-years away"),
           (240, 160, 110)),
    Target("whirlpool", "the Whirlpool Galaxy", _ly(31e6), "31 million light-years",
           Source("NASA", "https://science.nasa.gov/mission/hubble/science/explore-the-night-sky/hubble-messier-catalog/messier-51/",
                  "read Nov 23, 2026 (page updated Aug 26, 2026)",
                  "31 million light-years"),
           (200, 215, 255)),
)


# --- the numbers --------------------------------------------------------------

def _sig2(x: float) -> float:
    """Two significant figures."""
    if x == 0:
        return 0.0
    p = math.floor(math.log10(abs(x))) - 1
    return round(x / 10 ** p) * 10 ** p


_SCALES = ((1e9, "billion"), (1e6, "million"))


def duration(seconds: float, *, exact_years: float | None = None) -> tuple[float, int, str]:
    """(number, decimals, unit) for a travel time, two significant figures,
    e.g. (6.3, 1, "hours"), (74000, 0, "years"), (26, 0, "million years").
    `exact_years` is a light time already in whole light-years: shown as is."""
    if exact_years is not None:
        y = exact_years
        for big, word in _SCALES:
            if y >= big:
                v = y / big
                return v, (0 if v == int(v) else 1), f"{word} years"
        return y, (0 if y == int(y) else 1), "years"
    for unit, size in (("seconds", 1), ("minutes", 60), ("hours", 3600), ("days", 86400)):
        v = seconds / size
        nxt = {"seconds": 60, "minutes": 60, "hours": 24, "days": 365.25}[unit]
        if v < nxt:
            v = _sig2(v)
            return v, (1 if v < 10 and v != int(v) else 0), unit
    y = _sig2(seconds / YEAR_S)
    for big, word in _SCALES:
        if y >= big:
            return y / big, 0, f"{word} years"
    return y, 0, "years"


def fmt(v: float, dec: int, unit: str) -> str:
    return f"{v:,.{dec}f} {unit}"


def probe_time(t: Target) -> tuple[float, int, str]:
    return duration(t.km / VOYAGER_KM_S)


def light_time(t: Target) -> tuple[float, int, str]:
    if "light-year" in t.shown:
        return duration(0, exact_years=t.km / KM_PER_LY)
    return duration(t.km / LIGHT_KM_S)


# --- when ---------------------------------------------------------------------

def in_window(s, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(CENTRAL).date().isoformat()
    return bool(s.reach_from and s.reach_slots) and day >= s.reach_from and (
        not s.reach_until or day <= s.reach_until)


def slot_due(s, n: int, posted: int, now: datetime | None = None) -> bool:
    """Short number `n` (lifetime count) is a reach slot, with `posted` already
    up. It shares the "you are here" slots (8 and 12 of every 16: calm, no end
    card, not sky-event or fly-into), which check first, so every other test
    keeps its two sides."""
    return posted < len(TARGETS) and in_window(s, now) and n % 16 in set(s.reach_slots)


def asset_id(t: Target) -> str:
    return f"reach:{t.id}"


def next_target(posted_ids: list[str]) -> Target | None:
    done = set(posted_ids)
    return next((t for t in TARGETS if asset_id(t) not in done), None)


# --- the picture --------------------------------------------------------------

def _stars(width: int, height: int, seed: int) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    n = int(width * height / 1800)
    x = rng.uniform(0, width, n).astype(np.float32)
    y = rng.uniform(0, height, n).astype(np.float32)
    r = rng.uniform(0.6, 1.6, n).astype(np.float32) * (width / 1080)
    b = (0.25 + 0.75 * rng.random(n) ** 2).astype(np.float32)
    return x, y, r, b


def _ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def progress(t: float) -> float:
    """Share of the trip done at `t` seconds: quick off the line, easing in."""
    u = min(max((t - TRAVEL_FROM) / (TRAVEL_TO - TRAVEL_FROM), 0.0), 1.0)
    return 1 - (1 - u) ** 2.2


def frame(target: Target, t: float, width: int, height: int, *, bg=None, stars=None) -> np.ndarray:
    s = width / 1080
    bg = background(width, height) if bg is None else bg
    x, y, r, b = stars if stars is not None else _stars(width, height, 7)
    drift = (t * 6 * s) % height
    img = splat(width, height, x, (y + drift) % height, r, b * 0.8, np.ones((len(x), 3), np.float32) * 0.85, bg)
    cx = width * ROUTE_X
    ey, ty = height * EARTH_Y, height * TARGET_Y
    tgt = np.asarray(target.colour, np.float32)[None] / 255
    glow = 1.0 + 0.15 * math.sin(t * 3.0)
    img = splat(width, height, np.array([cx]), np.array([ty]), np.array([16 * s * glow], np.float32),
                np.array([2.2], np.float32), tgt, img)
    img = splat(width, height, np.array([cx]), np.array([ey]), np.array([11 * s], np.float32),
                np.array([1.8], np.float32), np.array([[0.35, 0.65, 1.0]], np.float32), img)
    fr = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    im = Image.fromarray(fr).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    p = progress(t)
    py = ey + (ty - ey) * p
    # the dotted route, and the part already flown in gold
    for yy in np.arange(ty, ey, 18 * s):
        d.ellipse((cx - 2 * s, yy - 2 * s, cx + 2 * s, yy + 2 * s), fill=(180, 190, 215, 90))
    if p > 0:
        d.line((cx, ey, cx, py), fill=(255, 205, 90, 200), width=max(2, int(4 * s)))
        d.ellipse((cx - 9 * s, py - 9 * s, cx + 9 * s, py + 9 * s), fill=(255, 225, 140, 255))
    f = _font(int(34 * s))
    d.text((cx + 30 * s, ey - 20 * s), "Earth", font=f, fill=(170, 205, 255, 230))
    d.text((cx + 34 * s, ty - 20 * s), target.name.removeprefix("the "),
           font=f, fill=tuple(target.colour) + (235,))
    im = Image.alpha_composite(im, layer)
    return np.asarray(im.convert("RGB"))


# --- cards ----------------------------------------------------------------------

def _layer(width: int, height: int) -> Image.Image:
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def question_card(target: Target, width: int, height: int) -> Image.Image:
    from romanfeed.render.cards import _centered, _wrap

    s = width / 1080
    layer = _layer(width, height)
    d = ImageDraw.Draw(layer)
    small, big = _font(int(54 * s), bold=True), _font(int(88 * s), bold=True)
    y = int(height * 0.07)
    for ln in ("HOW LONG WOULD IT", "TAKE TO REACH"):
        y = _centered(layer, y, ln, small, (225, 232, 255, 250), blur=8) + int(6 * s)
    y += int(14 * s)
    for ln in _wrap(d, target.name.upper() + "?", big, width * 0.9, 2):
        y = _centered(layer, y, ln, big, (255, 255, 255, 255), blur=10) + int(10 * s)
    return layer


def counter_card(target: Target, t: float, width: int, height: int) -> Image.Image:
    from romanfeed.render.cards import _centered

    s = width / 1080
    v, dec, unit = probe_time(target)
    shown = v * progress(t) if t < TRAVEL_TO else v
    layer = _layer(width, height)
    y = int(height * 0.47)
    gold = (255, 214, 110, 255)
    y = _centered(layer, y, f"{shown:,.{dec}f}", _font(int(120 * s), bold=True), gold, blur=10)
    y = _centered(layer, y + int(4 * s), unit.upper(), _font(int(54 * s), bold=True), gold, blur=8)
    _centered(layer, y + int(16 * s), "at the speed of Voyager 1, 17 km per second", _font(int(32 * s)),
              (220, 226, 240, 235), blur=6)
    return layer


def light_card(target: Target, width: int, height: int) -> Image.Image:
    from romanfeed.render.cards import _centered

    s = width / 1080
    layer = _layer(width, height)
    _centered(layer, int(height * 0.66), "Light takes " + fmt(*light_time(target)), _font(int(50 * s), bold=True),
              (210, 232, 255, 255), blur=8)
    return layer


def source_card(target: Target, width: int, height: int) -> Image.Image:
    from romanfeed.render.cards import _centered

    s = width / 1080
    layer = _layer(width, height)
    _centered(layer, int(height * 0.955), f"Distance {target.shown} and speed: NASA · drawn to illustrate",
              _font(int(24 * s)), (205, 212, 228, 210), blur=6)
    return layer


def overlay(cfg, target: Target, *, seconds: float, link_label: str):
    from romanfeed import narration
    from romanfeed.journey.render import alpha_overlay, chain
    from romanfeed.render.cards import short_cta_card

    s = cfg.shorts
    w, h = s.width, s.height
    handle = cfg.channel.handle or cfg.channel.name
    call = narration.subscribe_call(asset_id(target))[1]
    q = alpha_overlay(question_card(target, w, h), 0.0, seconds - PLATE_SECONDS, 0.0)
    light = alpha_overlay(light_card(target, w, h), LIGHT_AT, seconds - PLATE_SECONDS + 0.4, 0.3)
    plate = alpha_overlay(short_cta_card(w, h, full_label=link_label, handle=handle, call=call),
                          seconds - PLATE_SECONDS, seconds + 1.0, 0.4)
    src = alpha_overlay(source_card(target, w, h), 0.0, seconds + 1.0, 0.0)
    final = counter_card(target, TRAVEL_TO, w, h)
    steps = 30   # the counter re-drawn at most 30 times on the way up, then held

    cache: dict[int, Image.Image] = {}

    def counter(t: float, fr: np.ndarray) -> np.ndarray:
        if t >= seconds - PLATE_SECONDS:
            return fr
        if t >= TRAVEL_TO:
            card = final
        else:
            k = int(progress(t) * steps)
            card = cache.get(k)
            if card is None:
                card = cache[k] = counter_card(target, TRAVEL_FROM + (TRAVEL_TO - TRAVEL_FROM) * k / steps, w, h)
        return alpha_overlay(card, 0.0, seconds, 0.0)(t, fr)

    return chain(src, q, counter, light, plate)


def render(target: Target, out, *, cfg, audio, link_label: str, seconds: float = SECONDS):
    from romanfeed.journey.render import encode_frames
    from romanfeed.render import ffmpeg

    s = cfg.shorts
    w, h, fps = s.width, s.height, s.fps
    bg = background(w, h)
    stars = _stars(w, h, sum(map(ord, target.id)))
    over = overlay(cfg, target, seconds=seconds, link_label=link_label)
    n = int(round(seconds * fps))
    frames = (over(i / fps, frame(target, i / fps, w, h, bg=bg, stars=stars)) for i in range(n))
    silent = out.with_name(out.stem + ".silent.mp4")
    encode_frames(frames, silent, w, h, fps)
    ffmpeg.run(["-i", str(silent), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-af", f"afade=t=out:st={max(0.0, seconds - 1.5):.2f}:d=1.5",
                "-t", f"{seconds:.3f}", "-movflags", "+faststart", str(out)])
    silent.unlink(missing_ok=True)
    return out


# --- words ----------------------------------------------------------------------

def metadata(cfg, target: Target, *, link_id: str | None, link_label: str, tracks):
    from romanfeed.publish.metadata import VideoMetadata

    music = ", ".join(dict.fromkeys(tr.title or tr.id for tr in tracks)) or "ambient"
    probe, light = fmt(*probe_time(target)), fmt(*light_time(target))
    lines = []
    if link_id:
        lines += [f"Full {link_label} sleep video: https://youtu.be/{link_id}", ""]
    lines += [f"{target.name[0].upper() + target.name[1:]} is {target.shown} away. At Voyager 1's speed, "
              f"17 km per second, the trip would take about {probe}. Light takes {light}.", "",
              "Drawn to illustrate; times worked out from NASA's figures and rounded.",
              f"Distance: {target.source.org}, {target.source.url}",
              f"Voyager 1's speed: {VOYAGER.org}, {VOYAGER.url}", "",
              f"Music: {music} (composed for this channel)"]
    handle = cfg.channel.handle.strip()
    if handle:
        lines += ["", f"Subscribe for more space: https://www.youtube.com/{handle}?sub_confirmation=1"]
    lines += ["", "#shorts #space #astronomy"]
    tags = list(dict.fromkeys([REACH_TAG, "shorts", "space", "how long would it take", "voyager 1",
                               target.name.removeprefix("the ").lower(), "space facts", "astronomy",
                               "speed of light"]))
    return VideoMetadata(
        title=target.title, description="\n".join(lines)[:5000], tags=tags[:20],
        category_id=cfg.publish.category_id, privacy=cfg.shorts.privacy, made_for_kids=cfg.publish.made_for_kids)
