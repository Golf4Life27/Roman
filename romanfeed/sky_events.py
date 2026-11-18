"""Sky-event Shorts: something in this week's sky a viewer can go and see.

Test Nov 18-25 (config `shorts.sky_event_*`): 2 of the day's 8 Shorts point at
a sky event in the next 7 days (Central time): a full Moon, a Moon phase, a
rocket launch. Each opens on the event and its date in big type from the
first frame, over a real NASA photo that fits it, and its description links
the official page that confirms the date.

Nothing here is computed or remembered. Every event, date and time comes from
data/sky_events.yaml, where each entry was read by hand at an official source
(NASA, USNO, IMO, AMS, or the launch provider's own page) and records that
page and the day it was read. A launch entry goes stale `max_age_days` after
it was read, because launches slip. With no usable event, the slot is a
normal Short and the log says why.

Sky-event Shorts are tagged `sky-event` and never take a hook, punch-in,
anticipation, plain-title or explainer treatment, so the weekly report keeps
them out of those tests and compares them against calm-style Shorts on their
own (romanfeed/publish/shorts_styles.py). They post to YouTube only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from PIL import Image, ImageDraw

from romanfeed.render.captions import _font
from romanfeed.sources.base import ImageAsset

log = logging.getLogger(__name__)

SKY_TAG = "sky-event"
SKY_PARENT = "sky-events"   # shorts.parent_slug: keeps them out of the long videos' rotation
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "sky_events.yaml"
CENTRAL = ZoneInfo("America/Chicago")
AHEAD = timedelta(days=7)
LAUNCH_LEAD = timedelta(hours=1)   # a launch Short stops an hour before liftoff
REPEAT_GAP = timedelta(hours=20)   # the same event at most once in this long
TITLE_SUFFIX = "This Week in the Sky"
NASA_FILE = "https://images-assets.nasa.gov/image/{id}/{id}~large.jpg"
NASA_PAGE = "https://images.nasa.gov/details/{id}"


@dataclass
class Source:
    name: str
    url: str
    read: str = ""


@dataclass
class Picture:
    id: str
    title: str
    credit: str
    note: str = ""

    def asset(self) -> ImageAsset:
        return ImageAsset(
            asset_id=f"nasa:{self.id}", title=self.title, url=NASA_FILE.format(id=self.id),
            source="NASA Image Library", credit=self.credit,
            description=self.title + (f" ({self.note})." if self.note else "."),
            licence="public-domain", keywords=["sky event"])


@dataclass
class SkyEvent:
    id: str
    kind: str                 # launch, moon, meteors, planet
    title: str
    kicker: str
    when: str                 # on-screen date/time line, Central time
    look: str
    say: str
    start: datetime
    verified_on: date
    sources: list[Source]
    images: list[str]
    end: datetime | None = None
    max_age_days: int = 0     # 0 = does not go stale (fixed sky events)

    @property
    def day_label(self) -> str:
        """The date part of `when` ("Tue, Nov 24"), for the title."""
        return self.when.split(" · ")[0]

    def usable(self, now: datetime) -> str | None:
        """None if a Short can point at this event at `now`, else why not."""
        if self.max_age_days and now.astimezone(CENTRAL).date() > self.verified_on + timedelta(days=self.max_age_days):
            return f"{self.id}: read {self.verified_on}, older than {self.max_age_days} days; read the page again"
        if self.start > now + AHEAD:
            return f"{self.id}: more than 7 days away"
        if self.kind == "launch":
            if now > self.start - LAUNCH_LEAD:
                return f"{self.id}: launch time passed"
        elif now > (self.end or self.start):
            return f"{self.id}: over"
        return None


@dataclass
class Catalogue:
    events: list[SkyEvent]
    pictures: dict[str, list[Picture]] = field(default_factory=dict)


def _when(v) -> datetime:
    t = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    if t.tzinfo is None:
        raise ValueError(f"sky event time {v!r} has no UTC offset")
    return t


def load(path: Path | None = None) -> Catalogue:
    raw = yaml.safe_load(Path(path or DATA_FILE).read_text()) or {}
    pictures = {k: [Picture(**p) for p in v] for k, v in (raw.get("images") or {}).items()}
    events = []
    for e in raw.get("events") or []:
        e = dict(e)
        srcs = [Source(**s) for s in e.pop("sources")]
        if not srcs or not all(s.url.startswith("https://") for s in srcs):
            raise ValueError(f"sky event {e.get('id')} needs an https source page")
        events.append(SkyEvent(
            start=_when(e.pop("start")), end=_when(e.pop("end")) if e.get("end") else None,
            verified_on=date.fromisoformat(str(e.pop("verified_on"))), sources=srcs,
            **{k: v for k, v in e.items() if k != "end"}))
    return Catalogue(events, pictures)


def _central_day(now: datetime) -> str:
    return now.astimezone(CENTRAL).date().isoformat()


def in_window(s, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    day = _central_day(now)
    return bool(s.sky_event_until and s.sky_event_per_day) and (
        not s.sky_event_from or day >= s.sky_event_from) and day <= s.sky_event_until


def slot_due(s, n: int, now: datetime | None = None) -> bool:
    """Whether Short number `n` (lifetime count) is a sky-event slot.

    The slots are positions in every run of 16 Shorts. The default [0, 6, 11,
    13] holds one Short of each (n % 2, n // 2 % 2) pair and one of each
    (n // 2 % 2, n // 4 % 2) pair, so hook/calm, the opening tests and the
    title test each lose the same number from both sides and still compare
    like with like. That is 4 in 16: 2 a day at 8 a day."""
    return in_window(s, now) and n % 16 in set(s.sky_event_slots or [])


@dataclass
class Choice:
    event: SkyEvent
    picture: Picture
    asset: ImageAsset


def choose(cat: Catalogue, posted: list[tuple[str, str]], now: datetime, used_assets: set[str],
           ) -> tuple[list[SkyEvent], str]:
    """Usable events, best first, or ([], why there is none).

    `posted` is (event_id, created_at) of sky-event Shorts so far. An event
    shown in the last 20 hours waits; the rest go fewest Shorts first, then
    soonest. An event with no unused picture is skipped."""
    reasons, ok = [], []
    counts: dict[str, int] = {}
    last: dict[str, datetime] = {}
    for ev_id, at in posted:
        counts[ev_id] = counts.get(ev_id, 0) + 1
        t = datetime.fromisoformat(at)
        last[ev_id] = max(last.get(ev_id, t), t)
    for ev in cat.events:
        why = ev.usable(now)
        if why:
            reasons.append(why)
            continue
        if ev.id in last and now - last[ev.id] < REPEAT_GAP:
            reasons.append(f"{ev.id}: shown in the last 20 hours")
            continue
        if not any(f"nasa:{p.id}" not in used_assets for k in ev.images for p in cat.pictures.get(k, [])):
            reasons.append(f"{ev.id}: every picture for it is used")
            continue
        ok.append(ev)
    ok.sort(key=lambda e: (counts.get(e.id, 0), e.start))
    return ok, "; ".join(reasons) or "no events in the file"


def pick(cat: Catalogue, posted: list[tuple[str, str]], now: datetime, used_assets: set[str],
         cache_dir: Path) -> tuple[Choice | None, str]:
    """The event and picture for a sky-event Short, downloaded, or (None, why)."""
    events, why = choose(cat, posted, now, used_assets)
    if not events:
        return None, f"no verified sky event in the next 7 days can be shown ({why})"
    failed = []
    for ev in events:
        for k in ev.images:
            for p in cat.pictures.get(k, []):
                a = p.asset()
                if a.asset_id in used_assets:
                    continue
                try:
                    a.download(cache_dir)
                except Exception as exc:  # gone from the archive, network
                    log.warning("sky event picture %s failed: %s", p.id, exc)
                    failed.append(p.id)
                    continue
                return Choice(ev, p, a), ""
    return None, f"no sky-event picture could be downloaded ({', '.join(failed)})"


def subject_high(path: Path | None) -> bool:
    """True when the picture's brightest region (a Moon, a rocket's flame)
    sits in its upper half, where the event card would cover it."""
    if not path or not Path(path).exists():
        return False
    from PIL import ImageFilter, ImageOps

    with Image.open(path) as im:
        g = ImageOps.exif_transpose(im).convert("L").resize((36, 64), Image.BILINEAR)
    px = list(g.filter(ImageFilter.GaussianBlur(3)).getdata())
    i = max(range(len(px)), key=px.__getitem__)
    return px[i] > sum(px) / len(px) + 12 and (i // 36) < 64 * 0.45


def _band(width: int, height: int, top: int, bottom: int, alpha: int) -> Image.Image:
    """A soft dark band from `top` to `bottom`, strongest in its middle third."""
    band = max(bottom - top, 1)
    col = Image.new("L", (1, band))
    col.putdata([int(alpha * min(1.0, 3 * min(i, band - i) / band)) for i in range(band)])
    mask = Image.new("L", (width, height), 0)
    mask.paste(col.resize((width, band)), (0, top))
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    shade.putalpha(mask)
    return shade


def event_card(ev: SkyEvent, width: int, height: int, *, low: bool = False) -> Image.Image:
    """What, when and where to look, in big type: the first thing a viewer
    reads. In the upper part of the frame, or from just under the middle when
    `low`; always above the Shorts player's bottom fifth and clear of its
    right-hand strip."""
    from romanfeed.render.cards import _centered, _wrap

    s = width / 1080
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y0 = int(height * (0.42 if low else 0.08))
    d = ImageDraw.Draw(layer)
    max_w = width * 0.84
    y = y0
    kick = _font(int(42 * s), bold=True)
    y = _centered(layer, y, ev.kicker.upper(), kick, (255, 206, 120, 255), blur=8) + int(24 * s)
    big = _font(int(78 * s), bold=True)
    for ln in _wrap(d, ev.title, big, max_w, 3):
        y = _centered(layer, y, ln, big, (255, 255, 255, 255), blur=10) + int(16 * s)
    when = _font(int(48 * s), bold=True)
    y += int(12 * s)
    for ln in _wrap(d, ev.when, when, max_w, 2):
        y = _centered(layer, y, ln, when, (190, 225, 255, 255), blur=8, shadow_alpha=230) + int(12 * s)
    look = _font(int(36 * s))
    y += int(20 * s)
    for ln in _wrap(d, " ".join(ev.look.split()), look, max_w, 3):
        y = _centered(layer, y, ln, look, (235, 238, 245, 245), blur=8, shadow_alpha=230) + int(10 * s)
    # A soft dark band behind the text, so it reads on a bright frame; it
    # stops short of the player's bottom fifth.
    floor = int(height * 0.8)
    if y > floor:
        log.warning("sky event card for %s runs into the player's bottom fifth", ev.id)
    out = _band(width, height, max(y0 - int(height * 0.08), 0), min(y + int(height * 0.06), floor), 200)
    out.alpha_composite(layer)
    return out


def credit_layer(pic: Picture, width: int, height: int) -> Image.Image:
    """The photo credit, small, just above the Shorts player's bottom fifth,
    on screen throughout."""
    from romanfeed.render.cards import _centered, _wrap

    s = width / 1080
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    small = _font(int(26 * s))
    credit = f"Photo: {pic.credit}" + (f", {pic.note}" if pic.note else "")
    lines = _wrap(d, credit, small, width * 0.80, 2)
    y = int(height * 0.79) - len(lines) * int(34 * s)
    for ln in lines:
        y = _centered(layer, y, ln, small, (225, 230, 240, 230), blur=6) + int(8 * s)
    return layer


def script(ev: SkyEvent, asset_id: str) -> str:
    """What the calm voice reads: the entry's own sentence and the usual subscribe call."""
    from romanfeed.narration import subscribe_call

    return f"{' '.join(ev.say.split())} {subscribe_call(asset_id)[0]}"


def metadata(cfg, ev: SkyEvent, pic: Picture, *, link_id: str | None, link_label: str, tracks):
    """Title, description (with the page that confirms the date) and tags."""
    from romanfeed.publish.metadata import VideoMetadata

    title = f"{ev.title}, {ev.day_label} | {TITLE_SUFFIX}"
    if len(title) > 100:
        title = f"{ev.title}, {ev.day_label}"[:100]
    music = ", ".join(dict.fromkeys(t.title or t.id for t in tracks)) or "ambient"
    lines = [f"{ev.title}: {ev.when} (US Central time).", " ".join(ev.look.split()), ""]
    for src in ev.sources:
        lines.append(f"Date confirmed: {src.name}: {src.url} (read {ev.verified_on.isoformat()})")
    if ev.kind == "launch":
        lines.append(f"Launch times often change: this is the schedule as listed on {ev.verified_on:%b} "
                     f"{ev.verified_on.day}. Check the page above before you watch.")
    lines += ["", f"Image: {pic.credit}, NASA Image and Video Library: {NASA_PAGE.format(id=pic.id)}"]
    if pic.note:
        lines.append(f"The photo shows {pic.note}.")
    lines.append(f"Music: {music}")
    if link_id:
        lines += ["", f"Full {link_label} sleep video: https://youtu.be/{link_id}"]
    handle = cfg.channel.handle.strip()
    if handle:
        lines += ["", f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1"]
    lines += ["", "#shorts #space #skywatching"]
    kind_tags = {"launch": ["rocket launch", "spacex", "launch schedule"], "moon": ["moon", "full moon", "supermoon"],
                 "meteors": ["meteor shower"], "planet": ["planets"]}.get(ev.kind, [])
    tags = list(dict.fromkeys([SKY_TAG, "shorts", "space", "skywatching", "night sky", "this week in the sky",
                               *kind_tags, "space for sleep"]))
    return VideoMetadata(
        title=title, description="\n".join(lines)[:5000], tags=tags[:20],
        category_id=cfg.publish.category_id, privacy=cfg.shorts.privacy, made_for_kids=cfg.publish.made_for_kids)
