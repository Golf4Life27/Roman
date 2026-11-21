""""You are here" scale Shorts: a huge field of dots that narrows to one.

Test 21 (config `shorts.scale_*`, from Nov 22): three Shorts inside the day's
8, no extra slots. Each opens on a field of thousands of dots (stars,
galaxies or planets), already zooming, and the camera keeps falling toward
one of them until a ring closes on it and the words YOU ARE HERE come up.

Everything on screen is drawn here: the dots are an illustration, made by
this code from a seeded hash (no footage, no photo, nothing copied). Every
number the Short shows or says sits in SCENES with the official page it was
read at and the day it was read; `numbers_checked` in the tests keeps a
number from going out without one.

Tagged `you-are-here`, posted to YouTube only, never with a hook, opening,
title or end-card treatment, and kept out of every other test's numbers
(romanfeed/publish/shorts_styles.py). When one cannot be made the slot is a
normal Short and the log says why."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw

from romanfeed.render.captions import _font

SCALE_TAG = "you-are-here"
SCALE_PARENT = "you-are-here"   # shorts.parent_slug: kept out of the long videos' rotation
CENTRAL = ZoneInfo("America/Chicago")

SECONDS = 24.0
ZOOM_END = 17.0        # the fall ends here; the ring closes just after
RING_AT = 17.2
RING_FOR = 1.3
PLATE_SECONDS = 2.5
ZOOM = 2.0 ** 12       # how far the camera falls
TARGET_Y = 0.42        # the "you are here" dot, as a share of the height
MIN_CELL = 7.0         # px: a level of dots appears once its cells are this big


@dataclass(frozen=True)
class Source:
    org: str           # "NASA", "ESA"
    url: str
    read: str          # the day it was read, and the page's own date
    quote: str         # the words on the page, short


@dataclass(frozen=True)
class ScaleScene:
    id: str
    every: str         # "EVERY DOT IS A STAR"
    fact: str          # the number, on screen
    turn: str          # the line before the ring
    here: str          # under YOU ARE HERE
    say: str           # the voice
    title: str
    numbers: tuple[str, ...]   # every number shown or said, as written
    sources: tuple[Source, ...]
    palette: tuple[tuple[int, int, int], ...]
    target: tuple[int, int, int]
    max_radius: float  # px: how big a dot gets as the camera nears it


MILKY_WAY = Source(
    "NASA", "https://science.nasa.gov/universe/stories/quick-reads/how-big-is-our-galaxy-the-milky-way/",
    "read Nov 21, 2026 (page updated Nov 13, 2026)",
    "Our galaxy contains 100 to 400 billion stars and is about 100,000 light-years across")
GALAXIES = Source(
    "ESA", "https://www.esa.int/Science_Exploration/Space_Science/How_many_stars_are_there_in_the_Universe",
    "read Nov 21, 2026 (published Aug 15, 2025)",
    "a rough estimate of 10^11 to 10^12 galaxies in the Universe")
EXOPLANETS = Source(
    "NASA", "https://science.nasa.gov/exoplanets/",
    "read Nov 21, 2026 (page updated Oct 19, 2026)",
    "We've confirmed more than 6,000 exoplanets")

SCENES: tuple[ScaleScene, ...] = (
    ScaleScene(
        id="stars", every="EVERY DOT IS A STAR",
        fact="Our galaxy holds 100 to 400 billion of them",
        turn="One of them is ours", here="The Sun",
        say=("Every dot here is a star. Our galaxy holds somewhere between 100 and 400 billion of them. "
             "And somewhere in all of that is the one star we call home. You are here."),
        title="You Are Here: One Star Among 100 to 400 Billion",
        numbers=("100", "400 billion"), sources=(MILKY_WAY,),
        palette=((255, 255, 255), (200, 220, 255), (255, 240, 205), (255, 214, 170), (175, 200, 255)),
        target=(255, 236, 170), max_radius=1.9),
    ScaleScene(
        id="galaxies", every="EVERY DOT IS A GALAXY",
        fact="The universe we can see holds at least 100 billion",
        turn="One of them is ours", here="The Milky Way · about 100,000 light-years across",
        say=("Every dot here is a whole galaxy. The universe we can see holds at least 100 billion of them. "
             "One is the Milky Way, about 100,000 light-years across. You are here."),
        title="You Are Here: One Galaxy Among 100 Billion or More",
        numbers=("100 billion", "100,000 light-years"), sources=(GALAXIES, MILKY_WAY),
        palette=((255, 236, 210), (210, 222, 255), (255, 214, 186), (236, 236, 255), (255, 200, 160)),
        target=(220, 232, 255), max_radius=2.6),
    ScaleScene(
        id="planets", every="EVERY DOT IS A PLANET",
        fact="NASA has confirmed more than 6,000 around other stars",
        turn="Only one of them is home", here="Earth",
        say=("Every dot here is a planet. NASA has confirmed more than 6,000 of them around other stars. "
             "Out of every world we know, only one is home. You are here."),
        title="You Are Here: One Planet Among 6,000+ Found",
        numbers=("6,000",), sources=(EXOPLANETS,),
        palette=((214, 146, 104), (222, 196, 150), (160, 196, 232), (226, 232, 240), (176, 170, 164),
                 (200, 120, 90)),
        target=(90, 170, 255), max_radius=2.4),
)


# --- when -------------------------------------------------------------------

def in_window(s, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(CENTRAL).date().isoformat()
    return bool(s.scale_from and s.scale_total and s.scale_slots) and day >= s.scale_from and (
        not s.scale_until or day <= s.scale_until)


def slot_due(s, n: int, posted: int, now: datetime | None = None) -> bool:
    """Whether Short number `n` (lifetime count) is a scale slot, with
    `posted` scale Shorts already up. Slots 8 and 12 of every 16 are calm
    (n even), carry no end card (n % 4 == 0) and are neither sky-event nor
    fly-into slots, so the Short replaces a calm one and every other test
    keeps its two sides."""
    return posted < s.scale_total and in_window(s, now) and n % 16 in set(s.scale_slots)


def next_scene(posted_ids: list[str]) -> ScaleScene | None:
    """The first scene not posted yet, in SCENES order."""
    done = set(posted_ids)
    for sc in SCENES:
        if asset_id(sc) not in done:
            return sc
    return None


def asset_id(scene: ScaleScene) -> str:
    return f"scale:{scene.id}"


# --- the dots ---------------------------------------------------------------

def _hash(i: np.ndarray, j: np.ndarray, o: int, salt: int) -> np.ndarray:
    """A 64-bit hash per cell (splitmix-style), so a cell's dot is the same in every frame."""
    with np.errstate(over="ignore"):
        h = (i.astype(np.uint64) * np.uint64(0x9E3779B97F4A7C15)) ^ (
            j.astype(np.uint64) * np.uint64(0xC2B2AE3D27D4EB4F)) ^ np.uint64(((o + 64) * 0x165667B1 + salt) & 0xFFFFFFFF)
        h ^= h >> np.uint64(30)
        h *= np.uint64(0xBF58476D1CE4E5B9)
        h ^= h >> np.uint64(27)
        h *= np.uint64(0x94D049BB133111EB)
        h ^= h >> np.uint64(31)
    return h


def _unit(h: np.ndarray, shift: int) -> np.ndarray:
    return ((h >> np.uint64(shift)) & np.uint64(0xFFFF)).astype(np.float32) / 65536.0


def zoom_at(t: float) -> float:
    """Pixels per world unit at `t` seconds: moving on frame one, easing in at the end."""
    u = min(max(t / ZOOM_END, 0.0), 1.0)
    e = 0.45 * u + 0.55 * (u * u * (3 - 2 * u))
    drift = 0.02 * max(t - ZOOM_END, 0.0)          # a slow push while the ring sits
    return (1.0 + drift) * ZOOM ** e


THIN_FROM = 10.5       # from here the field thins until the one dot stands nearly alone


def min_cell(t: float) -> float:
    """The smallest cell drawn at `t`: fixed while the field is huge, then
    growing 16x, so the finer dots fade and the field empties around the one."""
    u = min(max((t - THIN_FROM) / (ZOOM_END - THIN_FROM), 0.0), 1.0)
    return MIN_CELL * 2.0 ** (4.0 * u * u * (3 - 2 * u))


def dots(scene: ScaleScene, k: float, width: int, height: int, salt: int, floor: float = MIN_CELL):
    """(x, y, radius, brightness, colour index) of every dot on screen at zoom
    `k`, drawing cells of at least `floor` px."""
    cx, cy = width / 2, height * TARGET_Y
    xs, ys, rs, bs, cs = [], [], [], [], []
    for o in range(-16, 40):            # coarse to fine
        cell = 2.0 ** -o
        s = k * cell                        # a cell's size on screen
        if s < floor:
            break
        if s > width * 6:                   # one dot per screens-full: nothing to add
            continue
        x0, x1 = (0 - cx) / k, (width - cx) / k
        y0, y1 = (0 - cy) / k, (height - cy) / k
        i = np.arange(math.floor(x0 / cell), math.floor(x1 / cell) + 1)
        j = np.arange(math.floor(y0 / cell), math.floor(y1 / cell) + 1)
        I, J = np.meshgrid(i, j)
        I, J = I.ravel(), J.ravel()
        h = _hash(I, J, o, salt)
        keep = _unit(h, 48) < 0.78
        wx = (I + _unit(h, 0)) * cell
        wy = (J + _unit(h, 16)) * cell
        # Nothing crowds the one dot that matters.
        keep &= (wx * wx + wy * wy) > (1.6 * cell) ** 2
        if not keep.any():
            continue
        h, wx, wy = h[keep], wx[keep], wy[keep]
        fade = min(1.0, (s - floor) / floor)
        mag = _unit(h, 32)
        xs.append(cx + wx * k)
        ys.append(cy + wy * k)
        rs.append(np.clip(0.7 + 0.014 * s * (0.4 + mag), 0.7, scene.max_radius))
        bs.append(fade * (0.55 + 1.0 * mag ** 1.5))
        cs.append((h % np.uint64(len(scene.palette))).astype(np.int64))
    if not xs:
        z = np.zeros(0, np.float32)
        return z, z, z, z, np.zeros(0, np.int64)
    return (np.concatenate(xs), np.concatenate(ys), np.concatenate(rs), np.concatenate(bs),
            np.concatenate(cs))


_OFF = np.array([(dx, dy) for dy in range(-3, 4) for dx in range(-3, 4)], np.float32)


def splat(width: int, height: int, x, y, r, b, rgb: np.ndarray, base: np.ndarray) -> np.ndarray:
    """Soft round dots added onto `base` (h, w, 3 float32), all at once."""
    out = base.copy()
    if len(x) == 0:
        return out
    px, py = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = (x - px).astype(np.float32), (y - py).astype(np.float32)
    flat = out.reshape(-1, 3)
    idx_all, w_all, c_all = [], [], []
    sig = np.maximum(r * 0.62, 0.45)
    for dx, dy in _OFF:
        qx, qy = px + int(dx), py + int(dy)
        d2 = (dx + 0.5 - fx) ** 2 + (dy + 0.5 - fy) ** 2
        w = b * np.exp(-d2 / (2 * sig * sig))
        ok = (qx >= 0) & (qx < width) & (qy >= 0) & (qy < height) & (w > 0.004)
        idx_all.append((qy * width + qx)[ok])
        w_all.append(w[ok])
        c_all.append(rgb[ok])
    idx = np.concatenate(idx_all)
    w = np.concatenate(w_all)
    col = np.concatenate(c_all)
    for ch in range(3):
        flat[:, ch] += np.bincount(idx, weights=w * col[:, ch], minlength=width * height).astype(np.float32)
    return out


def background(width: int, height: int) -> np.ndarray:
    """A deep blue-black, a little lighter at the top."""
    g = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]
    bg = np.empty((height, width, 3), np.float32)
    bg[..., 0] = (3 + 5 * g) / 255
    bg[..., 1] = (5 + 7 * g) / 255
    bg[..., 2] = (12 + 14 * g) / 255
    return bg


def salt_of(scene: ScaleScene) -> int:
    return int.from_bytes(hashlib.sha256(scene.id.encode()).digest()[:4], "big")


def field_frame(scene: ScaleScene, t: float, width: int, height: int, bg: np.ndarray | None = None) -> np.ndarray:
    """One frame of the fall, as (h, w, 3) uint8, ring and target included."""
    bg = background(width, height) if bg is None else bg
    k = zoom_at(t)
    x, y, r, b, c = dots(scene, k, width, height, salt_of(scene), min_cell(t))
    pal = np.asarray(scene.palette, np.float32) / 255
    img = splat(width, height, x, y, r, b, pal[c], bg)
    # The one dot: just another dot until the ring finds it, then it glows.
    cx, cy = width / 2, height * TARGET_Y
    glow = min(max((t - RING_AT) / RING_FOR, 0.0), 1.0)
    tr = scene.max_radius * (1.0 + 2.6 * glow)
    tgt = np.asarray(scene.target, np.float32)[None] / 255
    img = splat(width, height, np.array([cx]), np.array([cy]), np.array([tr], np.float32),
                np.array([1.0 + 1.2 * glow], np.float32), tgt, img)
    frame = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    if t >= RING_AT:
        frame = ring(frame, t, cx, cy, width / 1080)
    return frame


def ring(frame: np.ndarray, t: float, cx: float, cy: float, s: float) -> np.ndarray:
    """A gold ring that closes from wide onto the dot, then breathes."""
    u = min((t - RING_AT) / RING_FOR, 1.0)
    e = 1 - (1 - u) ** 3
    rad = (420 - 382 * e) * s
    if u >= 1:
        rad *= 1 + 0.06 * math.sin((t - RING_AT - RING_FOR) * 3.2)
    alpha = int(255 * min(1.0, u * 2.5))
    im = Image.fromarray(frame)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w = max(2, int(6 * s))
    d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), outline=(255, 205, 90, alpha), width=w)
    im = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")
    return np.asarray(im)


# --- cards ------------------------------------------------------------------

def _layer(width: int, height: int) -> Image.Image:
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def intro_card(scene: ScaleScene, width: int, height: int) -> Image.Image:
    from romanfeed.render.cards import _centered, _wrap

    s = width / 1080
    layer = _layer(width, height)
    d = ImageDraw.Draw(layer)
    big, mid = _font(int(74 * s), bold=True), _font(int(46 * s))
    y = int(height * 0.12)
    for ln in _wrap(d, scene.every, big, width * 0.88, 2):
        y = _centered(layer, y, ln, big, (255, 255, 255, 255), blur=8) + int(16 * s)
    y += int(14 * s)
    for ln in _wrap(d, scene.fact, mid, width * 0.84, 3):
        y = _centered(layer, y, ln, mid, (225, 232, 255, 245), blur=8) + int(12 * s)
    return layer


def turn_card(scene: ScaleScene, width: int, height: int) -> Image.Image:
    from romanfeed.render.cards import _centered

    s = width / 1080
    layer = _layer(width, height)
    _centered(layer, int(height * 0.14), scene.turn, _font(int(62 * s), bold=True), (255, 255, 255, 250), blur=8)
    return layer


def here_card(scene: ScaleScene, width: int, height: int) -> Image.Image:
    from romanfeed.render.cards import _centered, _wrap

    s = width / 1080
    layer = _layer(width, height)
    d = ImageDraw.Draw(layer)
    cy = height * TARGET_Y
    big = _font(int(92 * s), bold=True)
    _centered(layer, int(cy - 330 * s), "YOU ARE HERE", big, (255, 214, 110, 255), blur=10)
    sub = _font(int(44 * s))
    y = int(cy + 70 * s)
    for ln in _wrap(d, scene.here, sub, width * 0.84, 2):
        y = _centered(layer, y, ln, sub, (240, 240, 250, 245), blur=8) + int(10 * s)
    return layer


def source_card(scene: ScaleScene, width: int, height: int) -> Image.Image:
    from romanfeed.render.cards import _centered

    s = width / 1080
    layer = _layer(width, height)
    orgs = " and ".join(dict.fromkeys(src.org for src in scene.sources))
    _centered(layer, int(height * 0.80), f"Numbers: {orgs} · dots drawn to illustrate", _font(int(26 * s)),
              (205, 212, 228, 220), blur=6)
    return layer


def overlay(cfg, scene: ScaleScene, *, seconds: float, link_label: str):
    from romanfeed import narration
    from romanfeed.journey.render import alpha_overlay, chain
    from romanfeed.render.cards import short_cta_card

    s = cfg.shorts
    handle = cfg.channel.handle or cfg.channel.name
    call = narration.subscribe_call(asset_id(scene))[1]
    w, h = s.width, s.height
    return chain(
        alpha_overlay(intro_card(scene, w, h), 0.0, 8.2, 0.4),
        alpha_overlay(turn_card(scene, w, h), 8.8, RING_AT - 0.3, 0.5),
        alpha_overlay(here_card(scene, w, h), RING_AT + 0.4, seconds + 1.0, 0.5),
        alpha_overlay(short_cta_card(w, h, full_label=link_label, handle=handle, call=call),
                      seconds - PLATE_SECONDS, seconds + 1.0, 0.4),
        alpha_overlay(source_card(scene, w, h), 0.0, seconds + 1.0, 0.0),
    )


def render(scene: ScaleScene, out, *, cfg, audio, link_label: str, seconds: float = SECONDS):
    """The Short, H.264 with `audio` muxed on."""
    from romanfeed.journey.render import encode_frames
    from romanfeed.render import ffmpeg

    s = cfg.shorts
    w, h, fps = s.width, s.height, s.fps
    bg = background(w, h)
    over = overlay(cfg, scene, seconds=seconds, link_label=link_label)
    n = int(round(seconds * fps))
    frames = (over(i / fps, field_frame(scene, i / fps, w, h, bg)) for i in range(n))
    silent = out.with_name(out.stem + ".silent.mp4")
    encode_frames(frames, silent, w, h, fps)
    ffmpeg.run(["-i", str(silent), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-af", f"afade=t=out:st={max(0.0, seconds - 1.5):.2f}:d=1.5",
                "-t", f"{seconds:.3f}", "-movflags", "+faststart", str(out)])
    silent.unlink(missing_ok=True)
    return out


# --- words ------------------------------------------------------------------

def metadata(cfg, scene: ScaleScene, *, link_id: str | None, link_label: str, tracks):
    from romanfeed.publish.metadata import VideoMetadata

    music = ", ".join(dict.fromkeys(t.title or t.id for t in tracks)) or "ambient"
    lines = []
    if link_id:
        lines += [f"Full {link_label} sleep video: https://youtu.be/{link_id}", ""]
    lines += [scene.say, "",
              "The dots are drawn to illustrate the idea, not to show real positions or sizes.",
              "Numbers from " + "; ".join(f"{src.org}: {src.url}" for src in scene.sources), "",
              f"Music: {music} (composed for this channel)"]
    handle = cfg.channel.handle.strip()
    if handle:
        lines += ["", f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1"]
    lines += ["", "#shorts #space #youarehere"]
    tags = list(dict.fromkeys([SCALE_TAG, "shorts", "space", "you are here", "scale of the universe",
                               f"how many {scene.id}", scene.id, "space facts", "space for sleep"]))
    return VideoMetadata(
        title=scene.title, description="\n".join(lines)[:5000], tags=tags[:20],
        category_id=cfg.publish.category_id, privacy=cfg.shorts.privacy, made_for_kids=cfg.publish.made_for_kids)
