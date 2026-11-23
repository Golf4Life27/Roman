"""Vertical Shorts cut from the long videos' best moments.

Subscribers are the binding gate for the Partner Program, and the long sleep
videos convert at 1 subscriber per 142 views (measured). Shorts are the cheap
way to put the channel in front of new people: each one is a single image the
long video already showed, panned slowly in 9:16, naming the object and the
telescope, with a pointer to the full-length sleep video it came from.

The source images are long gone from disk by the time a Short is cut, so the
ledger is the memory: video_assets has each video's running order and
asset_meta has enough of each image to download and credit it again. Videos
rendered before those tables existed are backfilled once from the sources.

"Best moment" is measured, not guessed: up to `shorts.candidates` images from
the parent video are downloaded and scored for colour, detail and exposure,
and the highest scorer is cut. Every image is used for at most one Short.

The upload switch is `shorts.enabled` in the channel config. Off, a run
renders the Shorts and writes their metadata sidecars but sends nothing."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Callable

import requests
from PIL import Image, ImageFilter, ImageOps, ImageStat

from romanfeed.audio import MusicLibrary, build_soundtrack
from romanfeed.audio.composer import ComposedLibrary
from romanfeed.config import ChannelConfig, ShortsSettings
from romanfeed.curation.selector import flat_black_fraction, is_earth, looks_unsuitable
from romanfeed.publish import publish
from romanfeed import explainer as explainer_mod
from romanfeed import fly_into as fly_mod
from romanfeed import scale as scale_mod
from romanfeed import sky_events as sky_mod
from romanfeed import why as why_mod
from romanfeed.publish.metadata import VideoMetadata, _fit_title, lead_name
from romanfeed.render import ffmpeg
from romanfeed import crosspost, narration
from romanfeed.render.cards import caption_card, short_cta_card, short_end_card, short_overlay
from romanfeed.render.ffmpeg import probe_duration
from romanfeed.sources import build_source
from romanfeed.sources.base import USER_AGENT, ImageAsset
from romanfeed.state import META_FIELDS, Ledger
from romanfeed.telescopes import telescope_line, telescope_short

log = logging.getLogger(__name__)

_CUT_SUFFIX = re.compile(r"-(\d+(?:\.\d+)?)h$")


@dataclass
class Parent:
    """One rendered set: the primary video plus its extra-length cuts."""

    slug: str                  # primary slug; the ledger keys its assets on this
    link_id: str               # the YouTube video a Short points at
    link_seconds: float        # that video's running time
    rendered_at: str


@dataclass
class ShortResult:
    asset_id: str
    parent_slug: str
    path: Path
    youtube_id: str | None
    title: str


def is_public(video_id: str, *, timeout: int = 20) -> bool:
    """True if YouTube serves the video to anyone (public or unlisted).

    oEmbed answers 200 for a watchable video and 401/403/404 for private or
    deleted ones. It costs no API quota and needs no token, which matters: a
    Short must never point at a video that is still private."""
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            headers={"User-Agent": USER_AGENT}, timeout=timeout,
        )
    except requests.RequestException as exc:
        log.warning("oEmbed check failed for %s: %s", video_id, exc)
        return False
    return r.status_code == 200


def recent_upload_titles(channel_id: str, *, timeout: int = 20) -> list[str]:
    """Titles of the channel's latest public uploads (its RSS feed: free, no key).

    The second guard against posting the same image twice: the ledger can lose
    a record when parallel jobs race, the channel itself cannot."""
    import html as _html
    import re as _re

    if not channel_id:
        return []
    try:
        r = requests.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id},
                         headers={"User-Agent": USER_AGENT}, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as exc:
        log.warning("upload feed unavailable: %s", exc)
        return []
    entries = _re.findall(r"<entry>.*?<title>(.*?)</title>", r.text, _re.S)
    return [_html.unescape(t) for t in entries]


def already_posted(asset: ImageAsset, titles: list[str]) -> bool:
    name = (lead_name(asset.title, limit=60) or asset.title).strip().lower()
    return bool(name) and any(t.lower().startswith(name) for t in titles)


def full_label(seconds: float) -> str:
    """'8-hour', '1-hour', '45-minute' -- for "Full 8-hour sleep video"."""
    if seconds >= 3000:
        return f"{int(round(seconds / 3600))}-hour"
    return f"{max(int(round(seconds / 60)), 1)}-minute"


def parents(ledger: Ledger, channel: str, *, public: Callable[[str], bool] = is_public) -> list[Parent]:
    """Rendered sets with at least one watchable upload, newest first.

    A Short links to the longest watchable cut of its set: the 8-hour sleep
    video banks the most watch hours per viewer who follows the link."""
    groups: dict[str, list] = {}
    for v in ledger.videos(channel):
        base = _CUT_SUFFIX.sub("", v.video_slug)
        groups.setdefault(base, []).append(v)
    out: list[Parent] = []
    for base, vids in groups.items():
        primary = next((v for v in vids if v.video_slug == base), None)
        if primary is None:
            continue
        for v in sorted(vids, key=lambda v: v.duration_s, reverse=True):
            if v.youtube_id and public(v.youtube_id):
                out.append(Parent(base, v.youtube_id, v.duration_s, primary.rendered_at))
                break
    return sorted(out, key=lambda p: p.rendered_at, reverse=True)


SPARE_PICKS = 3   # extra parents tried when one gives no usable image
HOOK_TAG = "hook-style"
PUNCH_TAG = "punch-in-open"

# The punch-in opening: the first frame shows the image this much bigger and
# eases back to the full frame over PUNCH_SECONDS, a fast pull-back where the
# plain Short starts on its slow pan.
PUNCH_ZOOM = 0.4
PUNCH_SECONDS = 1.6


def punch_due(s: ShortsSettings, n: int, now: datetime | None = None) -> bool:
    """Whether Short number `n` (lifetime count) opens with the punch-in.

    Pairs alternate: n // 2 odd gets it. Hook and calm alternate by n % 2,
    so each style lands in both groups equally."""
    if not s.punch_in_until:
        return False
    now = now or datetime.now(timezone.utc)
    if now.astimezone(ZoneInfo("America/Chicago")).date().isoformat() > s.punch_in_until:
        return False
    return (n // 2) % 2 == 1


def punch_filter(width: int, height: int) -> str:
    """ffmpeg filters for the punch-in: a time-based zoom that eases out to
    the full frame, cropped back to the Short's size around its centre."""
    z = f"(1+{PUNCH_ZOOM}*pow(max(0\\,1-t/{PUNCH_SECONDS})\\,2))"
    return (f"scale=w='trunc({width}*{z}/2)*2':h='trunc({height}*{z}/2)*2':eval=frame:flags=bicubic,"
            f"crop={width}:{height}")


ANTIC_TAG = "anticipation-open"

# The anticipation opening (test Nov 21-27): the first frame is a close-up of
# the dark beside the brightest thing in the picture, with its glow at the
# edge. It holds ANTIC_HOLD seconds, then sweeps the subject into the middle
# and pulls back to the full frame by ANTIC_SECONDS, so the payoff lands
# before second 3. Our own image only: nothing is added to it.
ANTIC_ZOOM = 2.2
ANTIC_HOLD = 0.6
ANTIC_SECONDS = 2.4
ANTIC_OFFSET = 0.62   # subject starts this many frame widths from the centre


def _in_window(s_from: str, s_until: str, now: datetime | None) -> bool:
    if not s_until:
        return False
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    return (not s_from or day >= s_from) and day <= s_until


PLAIN_TITLE_TAG = "plain-title"
END_CARD_TAG = "end-card"


def end_card_due(s: ShortsSettings, n: int, now: datetime | None = None) -> bool:
    """Whether Short number `n` ends on the subscribe end card: n % 4 in
    {1, 2}, between end_card_from and end_card_until (Central). That is half
    of every run of four, with hook and calm (n % 2) and both openings
    (n // 2) split evenly between card and no card."""
    return _in_window(s.end_card_from, s.end_card_until, now) and n % 4 in (1, 2)


def plain_title_due(s: ShortsSettings, n: int, now: datetime | None = None) -> bool:
    """Whether Short number `n` goes out with the picture's name alone as its
    title: every other run of four (n // 4 odd), between plain_title_from and
    plain_title_until (Central). Runs of four split hook and calm (n % 2) and
    punch-in and plain openings (n // 2) evenly, so neither test leans."""
    if s.plain_title_all_from and _in_window(s.plain_title_all_from, "9999-12-31", now):
        return True   # test 11 won: every Short from plain_title_all_from
    return _in_window(s.plain_title_from, s.plain_title_until, now) and (n // 4) % 2 == 1


# A catalogue number names one object however the headline around it reads.
_CATALOGUE = re.compile(r"\b(NGC|IC|Messier|M|Abell|MACS\s*J?|Arp|UGC|RCW|Westerlund|Terzan|Sh\s*2)\s*-?\s*(\d+)",
                        re.I)
# Words a headline wraps around the name: who took it and how it is shown.
_FILLER = {"by", "the", "of", "a", "an", "and", "in", "new", "view", "views", "image", "images", "picture",
           "annotated", "wide", "field", "widefield", "close", "up", "closeup", "nasa", "esa", "nasas", "esas",
           "hubble", "hubbles", "webb", "webbs", "spitzer", "spitzers", "wise", "galex", "chandra", "nustar",
           "roman", "telescope", "space", "sees", "spies", "eyes", "captures", "portrays", "reveals", "shows",
           "visible", "infrared", "light", "detail", "details", "location", "from", "station"}


def object_key(asset: ImageAsset) -> str:
    """One spelling per object, so "Horsehead Nebula by Webb" and "Hubble's
    view of the Horsehead Nebula" match: the first catalogue number in the
    title (NGC 6302, MACS J0416), else the title's first part without the
    telescope and framing words, lower case, letters and digits only."""
    title = asset.title or ""
    m = _CATALOGUE.search(title)
    if m:
        kind = m.group(1).lower().replace(" ", "")
        kind = {"m": "messier", "macsj": "macs", "sh2": "sh2"}.get(kind, kind)
        return f"{kind}{int(m.group(2))}"
    first = re.split(r"\s*(?::|\(|\s-\s|\swith\s|\|)", title, maxsplit=1)[0]
    words = [w for w in re.findall(r"[a-z0-9]+", first.lower().replace("'", "").replace("\u2019", ""))
             if w not in _FILLER]
    return "".join(words)


def recent_short_objects(ledger: Ledger, channel: str, days: int, now: datetime | None = None) -> set[str]:
    """The objects of every Short posted in the last `days` days."""
    if days <= 0:
        return set()
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    ids = [r[0] for r in ledger.conn.execute(
        "SELECT asset_id FROM shorts WHERE channel = ? AND created_at >= ?", (channel, since))]
    return {object_key(asset_from_meta(m)) for m in ledger.asset_meta(ids).values()} - {""}


def anticipation_due(s: ShortsSettings, n: int, now: datetime | None = None) -> bool:
    """Whether Short number `n` opens with the anticipation reveal: every other
    pair (n // 2 odd), between anticipation_from and anticipation_until
    (Central), so hook and calm land in both groups equally."""
    return _in_window(s.anticipation_from, s.anticipation_until, now) and (n // 2) % 2 == 1


def subject_point(frame: Path, box: tuple[int, int, int, int]) -> tuple[float, float]:
    """Where the brightest region sits in `box` of `frame`, as (x, y) shares
    of the box: a heavy blur first, so one hot star does not win over a
    nebula's glow."""
    with Image.open(frame) as im:
        small = im.crop(box).convert("L").resize((36, 64), Image.BILINEAR).filter(ImageFilter.GaussianBlur(4))
    px = list(small.getdata())
    i = max(range(len(px)), key=px.__getitem__)
    if px[i] < sum(px) / len(px) + 12:   # nothing stands out: sweep in on the middle
        return 0.5, 0.5
    return ((i % 36) + 0.5) / 36, ((i // 36) + 0.5) / 64


def anticipation_filter(width: int, height: int, fps: int, u: float, v: float) -> str:
    """ffmpeg zoompan for the anticipation reveal around subject (u, v): a
    close-up beside it with its glow at the edge, then the subject eased into
    the middle as the view pulls back to the full frame. The window is
    clipped to the picture, which lands it on the full frame as the zoom
    reaches 1; zoompan keeps the output size fixed throughout."""
    t = f"(in/{fps})"
    p = f"clip(({t}-{ANTIC_HOLD})/{ANTIC_SECONDS - ANTIC_HOLD:.3f}\\,0\\,1)"
    e = f"(1-pow({p}\\,2)*(3-2*{p}))"
    z = f"(1+{ANTIC_ZOOM - 1:.3f}*{e})"
    side = -1 if u >= 0.5 else 1   # start on the side with more room
    cx = f"({u:.4f}*iw{side * ANTIC_OFFSET:+.3f}*iw/{z}*{e})"
    x = f"clip({cx}-iw/{z}/2\\,0\\,iw-iw/{z})"
    y = f"clip({v:.4f}*ih-ih/{z}/2\\,0\\,ih-ih/{z})"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"


def plan(ledger: Ledger, channel: str, count: int, *, per_short: int = 6,
         public: Callable[[str], bool] = is_public,
         keep: Callable[[list[str]], list[str]] | None = None) -> list[tuple[Parent, list[str]]]:
    """Which parent each of the next `count` Shorts comes from, and the
    candidate asset ids it chooses its best image from.

    Parents with the fewest Shorts go first (newest breaking ties), one Short
    per parent per lap, so every long video gets its turn instead of the
    newest one hogging the feed. Candidates are spread through the running
    order and never shared between two Shorts of the same run. `keep` narrows
    each parent's unused ids before planning (the Earth cap uses it), so a
    parent with nothing left to offer never takes a pick."""
    used = ledger.shorts_asset_ids(channel)
    done = ledger.shorts_per_parent(channel)
    pool = []
    for p in parents(ledger, channel, public=public):
        ids = [a for a in ledger.assets_of_video(p.slug) if a not in used]
        if ids and keep:
            ids = keep(ids)
        if ids:
            pool.append((p, ids))
    pool.sort(key=lambda pi: done.get(pi[0].slug, 0))  # stable: newest-first kept within a tie
    picks: list[tuple[Parent, list[str]]] = []
    taken: set[str] = set()
    i = 0
    while len(picks) < count and pool:
        idx = i % len(pool)
        p, ids = pool[idx]
        free = [a for a in ids if a not in taken]
        if not free:
            pool.pop(idx)
            continue
        cands = _spread(free, per_short)
        picks.append((p, cands))
        taken.update(cands)
        i += 1
    return picks


def why_pool(ledger: Ledger, channel: str, *, public: Callable[[str], bool] = is_public,
             skip: set[str] = frozenset()) -> list[tuple[Parent, ImageAsset, "why_mod.Why"]]:
    """Every unused image, in any public long video, whose caption answers a why.

    Nov 7: only about one deep-sky caption in 30 says why in so many words, so
    a why Short looks across the whole library rather than one video's six
    picks. A question already asked by an earlier Short is never asked again
    (the Horsehead Nebula has three images with the same answer)."""
    used = ledger.shorts_asset_ids(channel)
    asked: set[str] = set()
    hits: list[tuple[Parent, ImageAsset, why_mod.Why]] = []
    done = ledger.shorts_per_parent(channel)
    ps = sorted(parents(ledger, channel, public=public), key=lambda p: done.get(p.slug, 0))
    for p in ps:
        ids = ledger.assets_of_video(p.slug)
        meta = ledger.asset_meta(ids)
        for a in ids:
            if a not in meta:
                continue
            asset = asset_from_meta(meta[a])
            found = why_mod.find_why(asset)
            if not found:
                continue
            if a in used:
                asked.add(found.question)
            elif a not in skip and not is_earth(asset):
                hits.append((p, asset, found))
    out, seen = [], set(asked)
    for p, asset, found in hits:
        if found.question not in seen:
            seen.add(found.question)
            out.append((p, asset, found))
    return out


def fly_into_pool(ledger: Ledger, cfg: ChannelConfig, *, public: Callable[[str], bool] = is_public,
                  skip_objects: set[str] = frozenset(), posted: list[str] = ()) -> list[tuple[Parent, ImageAsset]]:
    """Every picture in a public long video that a fly-into Short may use,
    best first (romanfeed/fly_into.py rank_key): a nebula or galaxy from a
    known telescope, not a headline (the journey's own test), of one object
    whose name the title gives (fly_into.object_name), never used by a Short or a journey, not Earth, and no
    object posted as a Short in the last repeat_days."""
    from romanfeed.journey.metadata import is_headline, journey_subject
    from romanfeed.journey.run import ledger_key

    slug = cfg.channel.slug
    used = ledger.shorts_asset_ids(slug) | ledger.used_asset_ids(ledger_key(cfg))
    done = ledger.shorts_per_parent(slug)
    seen: set[str] = set()
    hits: list[tuple[Parent, ImageAsset]] = []
    for p in sorted(parents(ledger, slug, public=public), key=lambda p: done.get(p.slug, 0)):
        ids = [a for a in ledger.assets_of_video(p.slug) if a not in used and a not in seen]
        meta = ledger.asset_meta(ids)
        for a in ids:
            if a not in meta:
                continue
            seen.add(a)
            asset = asset_from_meta(meta[a])
            if (is_earth(asset) or not journey_subject(asset) or not telescope_short(asset)
                    or is_headline(asset.title) or not fly_mod.is_the_object(asset)
                    or not fly_mod.object_name(asset)
                    or object_key(asset) in skip_objects
                    or already_posted(asset, list(posted))):
                continue
            if asset.width and asset.height and not fly_mod.fits(asset.width, asset.height, cfg.shorts.width,
                                                                 cfg.shorts.height):
                continue
            hits.append((p, asset))
    # Stable: among equals the parent with the fewest Shorts leads.
    hits.sort(key=lambda pa: fly_mod.rank_key(pa[1], cfg.shorts.subject_weights), reverse=True)
    return hits


def backfill_meta(ledger: Ledger, cfg: ChannelConfig, asset_ids: list[str]) -> dict[str, dict]:
    """Metadata for ids the ledger has no record of, fetched from the sources.

    Only videos rendered before video_assets existed need this, and only once:
    whatever is found is saved."""
    known = ledger.asset_meta(asset_ids)
    missing = [a for a in asset_ids if a not in known]
    if not missing:
        return known
    log.info("backfilling metadata for %d asset(s) from the sources", len(missing))
    want = set(missing)
    found: list[ImageAsset] = []
    for s in cfg.enabled_sources:
        try:
            got = build_source(s).fetch()
        except Exception as exc:  # one archive down should not stop the Shorts
            log.warning("source %s failed during backfill: %s", s.type, exc)
            continue
        found += [a for a in got if a.asset_id in want]
        want -= {a.asset_id for a in got}
        if not want:
            break
    if found:
        ledger.save_asset_meta(found)
    known.update(ledger.asset_meta([a.asset_id for a in found]))
    return known


def asset_from_meta(meta: dict) -> ImageAsset:
    return ImageAsset(**{k: meta[k] for k in META_FIELDS if k in meta})


def image_score(path: Path) -> float:
    """Higher is a stronger single frame: saturated, detailed, well exposed.

    A Short has under a second to stop a thumb, so this favours colour and
    structure over subtlety. Dark frames (mean value under ~45/255) and blown
    ones are pushed down."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((512, 512))
    h, s, v = im.convert("HSV").split()
    sat = ImageStat.Stat(s).mean[0] / 255
    val = ImageStat.Stat(v).mean[0] / 255
    edges = ImageStat.Stat(im.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0] / 255
    exposure = 1.0 - min(abs(val - 0.38) / 0.38, 1.0)
    return round(0.45 * sat + 0.35 * min(edges * 4, 1.0) + 0.20 * exposure, 4)


# Star clusters, nebulae and galaxies earn the subscribers. Week of Oct 18-24:
# the Hercules globular cluster Short brought 7 of the 20 subscribers that came
# from Shorts, 7.5 per 1,000 views against 2.9 for Shorts overall (measured). So
# when a long video offers one, its Short shows the deep-sky object rather than
# a prettier frame of something else; image quality still decides among equals.
DEEP_SKY = re.compile(r"\b(?:globular|clusters?|nebulae?|galax(?:y|ies)|supernova|remnant)\b", re.I)
SUBJECT_BONUS = 0.15


def subject_bonus(asset: ImageAsset) -> float:
    if is_earth(asset):
        return 0.0
    text = " ".join([asset.title, *asset.keywords])
    return SUBJECT_BONUS if DEEP_SKY.search(text) else 0.0


def pick_best(candidates: list[ImageAsset], cache_dir: Path) -> ImageAsset | None:
    ranked = ranked_candidates(candidates, cache_dir)
    return ranked[0] if ranked else None


def is_hook(s, n: int) -> bool:
    """Whether the n-th Short (counted over every Short posted) is hook style."""
    if s.hook_slots:
        return n % 8 in s.hook_slots
    return s.hook_every > 0 and n % s.hook_every == s.hook_every - 1


def ranked_candidates(candidates: list[ImageAsset], cache_dir: Path,
                      weights: dict[str, float] | None = None) -> list[ImageAsset]:
    """Usable candidates, strongest first: frame quality plus the deep-sky
    bonus, plus any subject weight (the subject test's winner)."""
    scored = []
    for a in candidates:
        # An Earth-from-orbit photo is judged as one (it was failing the
        # deep-space test, so an Earth video never gave a Short and, first
        # in line every run, stalled the Shorts for days).
        if looks_unsuitable(a, "earth" if is_earth(a) else "space"):
            continue
        try:
            a.download(cache_dir)
            if flat_black_fraction(a) > 0.02:
                continue
            extra = (weights or {}).get(why_mod.subject(a), 0.0) if not is_earth(a) else 0.0
            scored.append((image_score(a.local_path) + subject_bonus(a) + extra, a))
        except Exception as exc:  # gone from the archive, corrupt file
            log.warning("skipping %s: %s", a.asset_id, exc)
    if not scored:
        return []
    scored.sort(key=lambda sa: sa[0], reverse=True)
    log.info("best of %d: %s (score %.3f)", len(scored), scored[0][1].asset_id, scored[0][0])
    return [a for _, a in scored]


def _spread(ids: list[str], n: int) -> list[str]:
    """n ids spaced evenly through the running order, not just the first n."""
    if len(ids) <= n:
        return list(ids)
    step = len(ids) / n
    return [ids[int(i * step)] for i in range(n)]


def vertical_frame(src: Path, out: Path, *, width: int, height: int, max_pan: int) -> tuple[Path, int, int]:
    """Cover-fit the image to a 2x vertical canvas with room to pan.

    Returns the frame and the pan excess (x, y) in frame pixels. A wide image
    pans sideways, a tall one vertically; the excess is capped so a panorama
    drifts rather than whips past."""
    W, H = width * 2, height * 2
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        scale = max(W / im.width, H / im.height)
        sw, sh = round(im.width * scale), round(im.height * scale)
        im = im.resize((sw, sh), Image.LANCZOS)
    cap = max_pan * 2
    cw, ch = min(sw, W + cap), min(sh, H + cap)
    left, top = (sw - cw) // 2, (sh - ch) // 2
    im = im.crop((left, top, left + cw, top + ch))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "PNG", compress_level=1)
    return out, cw - W, ch - H


def render_short(asset: ImageAsset, out_path: Path, *, cfg: ChannelConfig, audio: Path, link_label: str, work_dir: Path,
                 duration: float | None = None, captions: list[tuple[float, float, str]] | None = None,
                 cta_from: float | None = None, pointer: tuple[str, str] | None = None,
                 max_pan: int = 900, punch_in: bool = False, anticipation: bool = False,
                 overlay: Image.Image | None = None, end_card: bool = False) -> Path:
    """Render one Short. With `captions` (a narrated Short) the pointer to the
    long video waits until `cta_from`; without, it is on screen throughout.
    `pointer` (call, line under it) replaces the subscribe plate's text: the
    social cut's "Full 8-hour version on YouTube / @SpaceScreens". `overlay`
    replaces the standing name-and-telescope layer (a sky-event Short's photo
    credit), and a caption may be a ready-made card instead of text."""
    s = cfg.shorts
    dur = duration or s.seconds
    handle = cfg.channel.handle or cfg.channel.name
    frame, ex, ey = vertical_frame(asset.local_path, work_dir / "short_frame.png", width=s.width, height=s.height, max_pan=max_pan)
    narrated = bool(captions)
    layers: list[tuple[Path, str | None]] = []   # (png, ffmpeg enable expression or None)
    base = work_dir / "short_overlay.png"

    call, sub = pointer if pointer else (narration.subscribe_call(asset.asset_id, earth=is_earth(asset))[1], None)
    if overlay is not None:
        overlay.save(base, "PNG")
    else:
        short_overlay(asset, s.width, s.height, full_label=link_label, handle=handle, cta=not narrated, call=call,
                      sub=sub).save(base, "PNG")
    layers.append((base, None))
    if narrated:
        for i, (t0, t1, text) in enumerate(captions):
            png = work_dir / f"short_cap_{i:02d}.png"
            # A caption is text, or a ready card (a sky-event Short's event card).
            (caption_card(text, s.width, s.height) if isinstance(text, str) else text).save(png, "PNG")
            layers.append((png, f"between(t,{t0:.3f},{t1:.3f})"))
        cta = work_dir / "short_cta.png"
        short_cta_card(s.width, s.height, full_label=link_label, handle=handle, call=call, sub=sub).save(cta, "PNG")
        layers.append((cta, f"gte(t,{(cta_from or dur - 4):.3f})"))
    if end_card:
        end = work_dir / "short_end_card.png"
        short_end_card(s.width, s.height, channel_name=cfg.channel.name).save(end, "PNG")
        layers.append((end, f"gte(t,{max(dur - s.end_card_seconds, 0):.3f})"))
    # Alternate pan direction by asset so a run of Shorts does not all drift left.
    flip = int(hashlib.sha1(asset.asset_id.encode()).hexdigest(), 16) % 2
    prog = f"(1-t/{dur:.3f})" if flip else f"(t/{dur:.3f})"
    crop = f"crop={s.width * 2}:{s.height * 2}:x='{ex}*{prog}':y='{ey}*{prog}'"
    zoom = f",{punch_filter(s.width, s.height)}" if punch_in else ""
    if anticipation:
        x0, y0 = (ex, ey) if flip else (0, 0)    # where the pan starts
        u, v = subject_point(frame, (x0, y0, x0 + s.width * 2, y0 + s.height * 2))
        zoom = f",{anticipation_filter(s.width, s.height, s.fps, u, v)}"
    parts, prev = [f"[0:v]{crop},scale={s.width}:{s.height}:flags=bicubic{zoom}[c0]"], "[c0]"
    inputs = ["-loop", "1", "-framerate", str(s.fps), "-i", str(frame)]
    for k, (png, enable) in enumerate(layers, start=1):
        inputs += ["-loop", "1", "-framerate", str(s.fps), "-i", str(png)]
        en = f":enable='{enable}'" if enable else ""
        parts.append(f"{prev}[{k}:v]overlay=0:0:format=auto{en}[c{k}]")
        prev = f"[c{k}]"
    parts.append(f"{prev}fade=t=out:st={max(dur - 1.0, 0):.3f}:d=1.0,format=yuv420p[v]")
    audio_idx = len(layers) + 1
    ffmpeg.run([
        *inputs, "-i", str(audio),
        "-filter_complex", ";".join(parts), "-map", "[v]", "-map", f"{audio_idx}:a:0",
        "-t", f"{dur:.3f}", "-r", str(s.fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(out_path),
    ])
    for png, _ in layers:
        png.unlink(missing_ok=True)
    frame.unlink(missing_ok=True)
    return out_path


def narrate(asset: ImageAsset, *, cfg: ChannelConfig, link_label: str, work: Path, stem: str, key: str,
            hook: bool = False, why=None, explain=None):
    """Script + voice for one image: (script, voice wav, voice seconds), or None.

    A script that runs long is retried without its size/distance sentence; a
    Short tops out at 45 s and the voice needs the music either side of it."""
    budget = 45.0 - 0.8 - 3.0
    if explain is not None:
        # An explainer runs up to a minute, in the calm voice; a caption that
        # reads long loses its last sentences until it fits.
        w, body = explain
        budget = explainer_mod.MAX_SECONDS - 0.8 - 3.0
        while body:
            script = explainer_mod.explainer_script(asset, w, body, full_label=link_label)
            wav = narration.synthesize(script, work / f"{stem}.voice.wav", api_key=key, voice=cfg.shorts.voice)
            secs = probe_duration(str(wav))
            if secs <= budget:
                return script, wav, secs
            body = body[:-1]
            if len(" ".join([w.question, *body]).split()) < explainer_mod.WORDS[0]:
                return None
        return None
    if why is not None:
        # Read in the Short's own style (hook or calm), so the why test does
        # not tilt the hook vs calm one.
        script = why_mod.why_script(asset, why, full_label=link_label)
        voice, rate = (cfg.shorts.hook_voice, cfg.shorts.hook_rate) if hook else (cfg.shorts.voice, 1.0)
        wav = narration.synthesize(script, work / f"{stem}.voice.wav", api_key=key, voice=voice, speaking_rate=rate)
        secs = probe_duration(str(wav))
        return (script, wav, secs) if secs <= budget else None
    if hook:
        script = narration.hook_script(asset, full_label=link_label)
        if not script:
            return None
        wav = narration.synthesize(script, work / f"{stem}.voice.wav", api_key=key,
                                   voice=cfg.shorts.hook_voice, speaking_rate=cfg.shorts.hook_rate)
        secs = probe_duration(str(wav))
        return (script, wav, secs) if secs <= budget else None
    for include_size in (True, False):
        script = narration.fact_script(asset, full_label=link_label, include_size=include_size)
        if not script:
            return None
        wav = narration.synthesize(script, work / f"{stem}.voice.wav", api_key=key, voice=cfg.shorts.voice)
        secs = probe_duration(str(wav))
        if secs <= budget:
            return script, wav, secs
        log.info("narration for %s runs %.1fs; trying a shorter script", asset.asset_id, secs)
    return None


# "... pictured from the International Space Station": the place is the
# subject, and the clause cut to fit 60 characters read "...from the International".
_FROM_ORBIT = re.compile(r"[\s,]*(?:as\s+)?(?:pictured|seen|photographed|viewed|captured|imaged)\s+(?:from|by|aboard)\s+"
                         r"(?:the\s+)?(?:international\s+space\s+station|space\s+station|iss|orbit)\b.*$", re.I)


def short_lead(asset: ImageAsset) -> str:
    """The name a Short's title opens on ("Messier 15", "the Cat's Head Nebula")."""
    name = (_FROM_ORBIT.sub("", asset.title).strip(" ,.") or asset.title) if is_earth(asset) else asset.title
    return lead_name(name, limit=60) or name


def short_metadata(cfg: ChannelConfig, asset: ImageAsset, parent: Parent, tracks, script: str | None = None,
                   hook: bool = False, why=None, punch_in: bool = False,
                   anticipation: bool = False, explainer=None, plain_title: bool = False,
                   end_card: bool = False) -> VideoMetadata:
    lead = short_lead(asset)
    tele = telescope_short(asset)
    template = cfg.shorts.title_template
    title_lead = lead
    if "{lead_by}" in template:
        template = template.replace("{lead_by}", "{lead}")
        title_lead = f"{lead} by {tele}" if tele and not any(t.lower() in lead.lower() for t in tele.split(" & ")) else lead
        if is_earth(asset):
            title_lead = f"{lead} from the Space Station"
    title = _fit_title(template, title_lead, {"telescope": tele or "Telescope", "channel": cfg.channel.name})
    title = re.sub(r"\s+by(?=\s*(?:[|·]|$))", "", title)
    asked = explainer if explainer is not None else why
    plain_title = plain_title and asked is None
    if plain_title:
        # The plain-title test: the picture's name alone, no channel tagline.
        title = re.sub(r"\s+by$", "", _fit_title("{lead}", title_lead, {}))
    if asked is not None:
        # The why and explainer groups open their title on the question;
        # everything else keeps the name-first title.
        title = _fit_title(cfg.shorts.title_template.replace("{lead_by}", "{lead}"), asked.question,
                           {"telescope": tele or "Telescope", "channel": cfg.channel.name})
    link = f"https://youtu.be/{parent.link_id}"
    label = full_label(parent.link_seconds)
    line = telescope_line(asset)
    music = ", ".join(dict.fromkeys(t.title or t.id for t in tracks)) or "ambient"
    handle = cfg.channel.handle.strip()
    lines = [
        f"Full {label} sleep video: {link}",
        "",
        f"{lead}." + (f" {line}." if line else ""),
        # A narrated Short's description is its script, minus the closing pointer
        # (the link above already says it).
        script.rsplit(" The full ", 1)[0] if script else asset.short_description,
        "",
        f"Image: {asset.credit or asset.source}",
        f"Music: {music}",
    ]
    if asked is not None:
        lines.insert(lines.index(f"Image: {asset.credit or asset.source}"),
                     f"Why, in {asked.source}'s own words: {asked.url}")
        lines.insert(lines.index(f"Image: {asset.credit or asset.source}"), "")
    if handle:
        lines += ["", f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1"]
    lines += ["", "#shorts #space #sleep"]
    tags = list(dict.fromkeys(["shorts", "space", "sleep", "space for sleep", "relaxing space", lead.lower(),
                               *(t.lower() for t in [tele] if t), "telescope", "nebula", "galaxy"]))
    if hook:
        tags.insert(0, HOOK_TAG)   # how the Monday report tells the two styles apart
    if why is not None and explainer is None:
        tags.insert(0, why_mod.WHY_TAG)
    if explainer is not None:
        tags.insert(0, explainer_mod.EXPLAINER_TAG)   # kept apart from every other test
    if punch_in:
        tags.insert(0, PUNCH_TAG)    # how the report tells the two openings apart
    if anticipation:
        tags.insert(0, ANTIC_TAG)
    if plain_title:
        tags.insert(0, PLAIN_TITLE_TAG)
    if end_card:
        tags.insert(0, END_CARD_TAG)
    return VideoMetadata(
        title=title[:100], description="\n".join(lines)[:5000], tags=tags[:20],
        category_id=cfg.publish.category_id, privacy=cfg.shorts.privacy, made_for_kids=cfg.publish.made_for_kids,
        # The explainer's voice is text to speech reading a full minute, so it
        # carries YouTube's altered or synthetic content disclosure.
        synthetic=explainer is not None,
    )


def _snapshot(ledger: Ledger, cfg: ChannelConfig, today: str) -> None:
    """Note today's public subscriber/view counts for the weekly report (1 quota unit)."""
    from romanfeed.publish.youtube import api_key, public_client

    if not (api_key() and cfg.channel.handle):
        return
    try:
        from romanfeed.publish.stats import channel_counts

        ledger.record_snapshot(today, *channel_counts(public_client(), cfg.channel.handle))
        from romanfeed.publish.stats import title_test_rows

        title_test_rows(public_client(), ledger, date.fromisoformat(today))
    except Exception as exc:  # a stats hiccup must never cost a Short
        log.warning("channel snapshot failed: %s", exc)


# GitHub starts the scheduled runs up to ~6 hours late (Oct 24-30: 23:10 ran
# at 02:18, 01:40 at 07:42), so a calendar-day cap let a late evening run eat
# the next day's allowance and a Short was lost. The cap is now a rolling 24
# hours, with one Short of slack for a single scheduled run (each posts one
# Short, so the slots, not the cap, set the pace).
CAP_SLACK = 1
# A one-Short scheduled run posts two when the cap leaves at least this much
# room beyond the slack, i.e. an earlier slot in the last 24 hours never ran.
CATCH_UP_GAP = 2


def _since(now: datetime | None, hours: float = 24) -> str:
    now = now or datetime.now(timezone.utc)
    return (now.astimezone(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def recent_shorts(ledger: Ledger, channel: str, now: datetime | None = None) -> list[str]:
    """Asset ids of the Shorts uploaded in the 24 hours before `now`."""
    rows = ledger.conn.execute(
        "SELECT asset_id FROM shorts WHERE channel = ? AND youtube_id IS NOT NULL AND created_at > ?",
        (channel, _since(now)),
    ).fetchall()
    return [r[0] for r in rows]


def shorts_today(ledger: Ledger, channel: str, now: datetime | None = None) -> int:
    """Shorts uploaded in the 24 hours before `now`."""
    return len(recent_shorts(ledger, channel, now))


def in_test(s: ShortsSettings, now: datetime | None = None) -> bool:
    """True while the Shorts test runs (through `test_until`, Central time)."""
    if not (s.test_per_day and s.test_until):
        return False
    now = now or datetime.now(timezone.utc)
    central = (now.astimezone(timezone.utc) - timedelta(hours=5)).date()
    return central <= date.fromisoformat(s.test_until)


def daily_target(s: ShortsSettings, now: datetime | None = None, reserved: int = 0) -> int:
    """Shorts a day; `reserved` slots go to other kinds of Short that keep their
    own count (the workshop's kids Shorts), so the channel's day stays the same."""
    return max((s.test_per_day if in_test(s, now) else s.per_day) - reserved, 0)


def _length(voice_secs: float, cap: float = 45.0) -> float:
    """A narrated Short's length: the voice plus music either side, 30-45 s
    (an explainer's cap is just under a minute)."""
    return min(cap, max(30.0, voice_secs + 0.8 + 3.5))


def _social_voice(spoken, *, cfg: ChannelConfig, label: str, work: Path, stem: str, key: str | None):
    """(script, wav, seconds, cut length) for the social cut, or None for a
    music-only Short (or if the new voice runs too long)."""
    if not (spoken and key):
        return None
    text = crosspost.social_script(spoken[0], full_label=label)
    wav = narration.synthesize(text, work / f"{stem}.social.voice.wav", api_key=key, voice=cfg.shorts.voice)
    secs = probe_duration(str(wav))
    if secs > 45.0 - 0.8 - 3.0:
        log.warning("social voice for %s runs %.1fs; the social cut will be music only", stem, secs)
        return None
    return text, wav, secs, _length(secs)


def _render_cut(asset: ImageAsset, out: Path, *, cfg: ChannelConfig, music: Path, label: str, work: Path,
                duration: float, voice=None, pointer: tuple[str, str] | None = None, hook: bool = False,
                punch_in: bool = False, anticipation: bool = False, end_card: bool = False) -> Path:
    """One cut of a Short: voice (script, wav, seconds) over the music with
    captions for the facts and the closing plate under the last line, or
    music only with the plate throughout."""
    audio, captions, cta_from = music, None, None
    # The hook style speaks almost at once, over louder music, and pans further.
    start, gain, pan = (0.25, 0.32, 1500) if hook else (0.8, 0.22, 900)
    if voice:
        script, wav, secs = voice
        audio = narration.mix_voice(wav, music, out.with_suffix(".mix.m4a"), voice_start=start, duration=duration,
                                    music_gain=gain)
        # Captions cover the facts; the closing pointer sentence is shown
        # as the end card instead of as a caption.
        body = script.rsplit(" The full ", 1)[0]
        body_secs = secs * len(body) / max(len(script), 1)
        captions = narration.caption_chunks(body, start=start, duration=body_secs)
        cta_from = start + body_secs
    path = render_short(asset, out, cfg=cfg, audio=audio, link_label=label, work_dir=work,
                        duration=duration, captions=captions, cta_from=cta_from, pointer=pointer,
                        max_pan=pan if voice else 900, punch_in=punch_in, anticipation=anticipation,
                        end_card=end_card)
    if voice:
        Path(audio).unlink(missing_ok=True)
    return path


def _sky_short(cfg: ChannelConfig, ledger: Ledger, parent: Parent | None, *, now: datetime, cache: Path, work: Path,
               out_dir: Path, mode: str, upload: bool, manifest, key: str | None) -> tuple[ShortResult | None, str]:
    """One sky-event Short (romanfeed/sky_events.py), or (None, why not).

    The event, its date and the page confirming it come from the hand-checked
    data file; the picture is an unused NASA photo that fits it. Read in the
    calm voice when there is a voice at all, never in the hook style or with a
    test opening, and posted to YouTube only (no social cut)."""
    s, slug = cfg.shorts, cfg.channel.slug
    posted = ledger.sky_events_since(slug, "")
    recent = [p for p in posted if p[1] > _since(now)]
    if len(recent) >= s.sky_event_per_day:
        return None, f"{len(recent)} sky-event Shorts in the last 24 hours, the cap of {s.sky_event_per_day}"
    try:
        cat = sky_mod.load()
    except Exception as exc:  # a bad edit to the data file must not cost the Short
        return None, f"the sky event file could not be read: {exc}"
    choice, why = sky_mod.pick(cat, posted, now, ledger.shorts_asset_ids(slug), cache)
    if choice is None:
        return None, why
    ev, pic, asset = choice.event, choice.picture, choice.asset
    label = full_label(parent.link_seconds) if parent else "8-hour"
    stem = f"{slug}-short-{asset.asset_id.replace(':', '_')}"
    spoken = None
    if key:
        text = sky_mod.script(ev, asset.asset_id)
        try:
            wav = narration.synthesize(text, work / f"{stem}.voice.wav", api_key=key, voice=s.voice)
            secs = probe_duration(str(wav))
            if secs <= 45.0 - 0.8 - 3.0:
                spoken = (text, wav, secs)
            else:
                log.info("sky-event voice for %s runs %.1fs; music only", ev.id, secs)
        except Exception as exc:  # no voice is better than no Short
            log.warning("sky-event voice failed for %s: %s", ev.id, exc)
    dur = _length(spoken[2]) if spoken else s.seconds
    library = (ComposedLibrary(work / "composed" / stem, count=1, seconds_each=dur + 4, seed=asset.asset_id)
               if cfg.audio.source == "composed" else manifest)
    music, tracks = build_soundtrack(
        library, genre=cfg.audio.genre, duration=dur, out_path=work / f"{stem}.m4a", fade=1.5, crossfade=0.0,
        gain_db=0.0, allow_placeholder=cfg.audio.allow_placeholder or mode == "dry-run", seed=asset.asset_id)
    if upload and any(not t.publishable for t in tracks):
        raise RuntimeError("refusing to upload a Short: soundtrack contains non-publishable tracks")
    audio = music
    cta_from = max(dur - 6.0, 4.0)
    if spoken:
        text, wav, secs = spoken
        audio = narration.mix_voice(wav, music, work / f"{stem}.mix.m4a", voice_start=0.8, duration=dur,
                                    music_gain=0.22)
        body = " ".join(ev.say.split())
        cta_from = 0.8 + secs * len(body) / max(len(text), 1)
    # The event card (what, when, where to look) is up from the first frame
    # until the subscribe plate takes over; the photo credit stays throughout.
    # It sits low when the picture's bright subject is high, so it never
    # covers the Moon.
    card = sky_mod.event_card(ev, s.width, s.height, low=sky_mod.subject_high(asset.local_path))
    path = render_short(asset, out_dir / f"{stem}.mp4", cfg=cfg, audio=audio, link_label=label, work_dir=work,
                        duration=dur, captions=[(0.0, cta_from, card)], cta_from=cta_from,
                        overlay=sky_mod.credit_layer(pic, s.width, s.height))
    if spoken:
        Path(audio).unlink(missing_ok=True)
    md = sky_mod.metadata(cfg, ev, pic, link_id=parent.link_id if parent else None, link_label=label, tracks=tracks)
    vid = publish(path, md, mode=mode)
    if upload:
        ledger.record_short(slug, asset.asset_id, sky_mod.SKY_PARENT, vid)
        ledger.record_sky_event(slug, asset.asset_id, ev.id, vid)
    log.info("short %s: sky event %s (%s) -> %s", asset.asset_id, ev.id, ev.when, vid or "(dry run)")
    return ShortResult(asset.asset_id, sky_mod.SKY_PARENT, path, vid, md.title), ""


def fly_into_recent(ledger: Ledger, channel: str, now: datetime | None = None) -> int:
    """Fly-into Shorts uploaded in the 24 hours before `now`."""
    return ledger.conn.execute(
        "SELECT COUNT(*) FROM shorts WHERE channel = ? AND parent_slug = ? AND youtube_id IS NOT NULL"
        " AND created_at > ?", (channel, fly_mod.FLY_PARENT, _since(now))).fetchone()[0]


def _fly_into_short(cfg: ChannelConfig, ledger: Ledger, *, now: datetime, cache: Path, work: Path, out_dir: Path,
                    models: Path, mode: str, upload: bool, public: Callable[[str], bool], skip_objects: set[str],
                    posted: list[str]) -> tuple[ShortResult | None, str]:
    """One fly-into Short (romanfeed/fly_into.py), or (None, why not).

    The picture is the strongest of a shortlist of unused nebulae and
    galaxies from the library (fly_into_pool's order) that downloads cleanly and is big enough for a vertical flight; the journey
    code estimates its depth, builds the scene and renders 20-30 s with its
    own composed music. Never in the hook style or with a test opening or
    title, and posted to YouTube only (no social cut). The object it shows
    is added to `skip_objects`, the run's own set."""
    import hashlib as _hashlib
    import time

    from romanfeed.audio.composer import compose_piece
    from romanfeed.journey.render import have_opencv

    s, slug, j = cfg.shorts, cfg.channel.slug, cfg.journey
    recent = fly_into_recent(ledger, slug, now)
    if recent >= s.fly_into_per_day:
        return None, f"{recent} fly-into Shorts in the last 24 hours, the cap of {s.fly_into_per_day}"
    if not have_opencv():
        # The numpy fallback is about 25x slower: a Short would take most of the job's time.
        return None, "OpenCV is not installed (pip install -e '.[journey]')"
    pool = fly_into_pool(ledger, cfg, public=public, skip_objects=skip_objects, posted=posted)
    if not pool:
        return None, "no unused nebula or galaxy picture from a known telescope in the library"
    log.info("fly-into: %d unused nebula or galaxy pictures in the library could be flown into; first up: %s",
             len(pool), "; ".join(a.title for _, a in pool[:fly_mod.TRIES]))
    choice = None
    shortlist = pool[:fly_mod.TRIES]
    parent_of = {a.asset_id: p for p, a in shortlist}
    for cand in ranked_candidates([a for _, a in shortlist], cache, s.subject_weights):
        parent = parent_of[cand.asset_id]
        with Image.open(cand.local_path) as im:
            w, h = ImageOps.exif_transpose(im).size
        if fly_mod.fits(w, h, s.width, s.height):
            choice = (parent, cand)
            break
        log.info("fly-into: %s is %dx%d, too small to fly into on a vertical screen", cand.asset_id, w, h)
    if choice is None:
        return None, f"none of the first {min(len(pool), fly_mod.TRIES)} of {len(pool)} pictures downloaded at a usable size"
    parent, asset = choice
    label = full_label(parent.link_seconds)
    stem = f"{slug}-short-fly-{asset.asset_id.replace(':', '_')}"
    seconds = j.short_seconds
    t0 = time.monotonic()
    try:
        from romanfeed.journey.depth import estimate_depth
        from romanfeed.journey.layers import find_targets
        from romanfeed.journey.render import Scene

        with Image.open(asset.local_path) as im:
            image = ImageOps.exif_transpose(im).convert("RGB")
        depth, depth_source = estimate_depth(image, method=j.depth, cache_dir=models)
        scene = Scene.from_image(image, depth, depth_source)
        del image
        seed = int.from_bytes(_hashlib.sha256(asset.asset_id.encode()).digest()[:4], "big")
        targets = find_targets(scene.background, scene.depth, count=10, seed=seed)
        if not targets:
            return None, f"no bright region to fly to in {asset.asset_id}"
    except Exception as exc:  # a picture the depth or scene step cannot handle must not cost the slot
        log.exception("fly-into scene failed for %s", asset.asset_id)
        return None, f"the depth/scene step failed for {asset.asset_id} ({exc})"
    log.info("fly-into: %s (%s), depth from %s, %d regions", asset.asset_id, asset.title, depth_source, len(targets))
    try:
        from romanfeed.journey.short import render_journey_short

        piece = compose_piece(work / f"{stem}.music.m4a", seconds=seconds + 2, seed=f"{asset.asset_id}-fly",
                              style="journey", artist=cfg.channel.name, calm_at=1e9)
        if upload and not piece.publishable:
            raise RuntimeError("refusing to upload a Short: soundtrack contains non-publishable tracks")
        path = render_journey_short(scene, targets, out_dir / f"{stem}.mp4", seconds=seconds, width=s.width,
                                    height=s.height, fps=s.fps, audio=piece.path,
                                    overlay=fly_mod.overlay(cfg, asset, seconds=seconds, link_label=label))
    except Exception as exc:
        log.exception("fly-into render failed for %s", asset.asset_id)
        return None, f"the render failed for {asset.asset_id} ({exc})"
    log.info("fly-into Short rendered in %.0fs", time.monotonic() - t0)
    md = fly_mod.metadata(cfg, asset, link_id=parent.link_id, link_label=label, tracks=[piece],
                          depth_source=depth_source)
    vid = publish(path, md, mode=mode)
    skip_objects.add(object_key(asset))
    if upload:
        ledger.record_short(slug, asset.asset_id, fly_mod.FLY_PARENT, vid)
    log.info("short %s: fly-into %s (link %s) -> %s", asset.asset_id, md.title, parent.slug, vid or "(dry run)")
    return ShortResult(asset.asset_id, fly_mod.FLY_PARENT, path, vid, md.title), ""


def scale_posted(ledger: Ledger, channel: str) -> list[str]:
    """Asset ids of the "you are here" Shorts already uploaded, oldest first."""
    rows = ledger.conn.execute(
        "SELECT asset_id FROM shorts WHERE channel = ? AND parent_slug = ? AND youtube_id IS NOT NULL"
        " ORDER BY created_at", (channel, scale_mod.SCALE_PARENT)).fetchall()
    return [r[0] for r in rows]


def _scale_short(cfg: ChannelConfig, ledger: Ledger, parent: Parent | None, *, work: Path, out_dir: Path,
                 mode: str, upload: bool, manifest, key: str | None,
                 done_ids: list[str]) -> tuple[ShortResult | None, str]:
    """One "you are here" Short (romanfeed/scale.py), or (None, why not).

    The next scene not yet posted, drawn by the scale code, read in the calm
    voice when there is a voice, never in the hook style or with a test
    opening, title or end card, and posted to YouTube only."""
    s, slug = cfg.shorts, cfg.channel.slug
    scene = scale_mod.next_scene(done_ids)
    if scene is None:
        return None, "all the scenes have posted"
    aid = scale_mod.asset_id(scene)
    label = full_label(parent.link_seconds) if parent else "8-hour"
    stem = f"{slug}-short-scale-{scene.id}"
    dur = scale_mod.SECONDS
    spoken = None
    if key:
        try:
            wav = narration.synthesize(scene.say, work / f"{stem}.voice.wav", api_key=key, voice=s.voice)
            secs = probe_duration(str(wav))
            if 0.8 + secs <= scale_mod.RING_AT + 2.5:
                spoken = (wav, secs)
            else:
                log.info("you-are-here voice for %s runs %.1fs; music only", scene.id, secs)
        except Exception as exc:  # no voice is better than no Short
            log.warning("you-are-here voice failed for %s: %s", scene.id, exc)
    library = (ComposedLibrary(work / "composed" / stem, count=1, seconds_each=dur + 4, seed=aid)
               if cfg.audio.source == "composed" else manifest)
    music, tracks = build_soundtrack(
        library, genre=cfg.audio.genre, duration=dur, out_path=work / f"{stem}.m4a", fade=1.5, crossfade=0.0,
        gain_db=0.0, allow_placeholder=cfg.audio.allow_placeholder or mode == "dry-run", seed=aid)
    if upload and any(not t.publishable for t in tracks):
        raise RuntimeError("refusing to upload a Short: soundtrack contains non-publishable tracks")
    audio = music
    if spoken:
        audio = narration.mix_voice(spoken[0], music, work / f"{stem}.mix.m4a", voice_start=0.8, duration=dur,
                                    music_gain=0.22)
    path = scale_mod.render(scene, out_dir / f"{stem}.mp4", cfg=cfg, audio=audio, link_label=label, seconds=dur)
    md = scale_mod.metadata(cfg, scene, link_id=parent.link_id if parent else None, link_label=label,
                            tracks=tracks)
    vid = publish(path, md, mode=mode)
    if upload:
        ledger.record_short(slug, aid, scale_mod.SCALE_PARENT, vid)
    done_ids.append(aid)
    log.info("short %s: you are here, %s (%s) -> %s", aid, scene.id, "voice" if spoken else "music only",
             vid or "(dry run)")
    return ShortResult(aid, scale_mod.SCALE_PARENT, path, vid, md.title), ""


def run_shorts(cfg: ChannelConfig, *, count: int | None = None, dry_run: bool = False,
               data_dir: Path = Path("data"), output_dir: Path = Path("output"),
               public: Callable[[str], bool] = is_public, extra: bool = False,
               style: str = "auto", catch_up: bool = False,
               backup: bool = False) -> list[ShortResult]:
    """Cut and (if switched on) publish up to `count` Shorts.

    Uploads only when shorts.enabled is true, publish.mode is upload, and this
    is not a dry run. The day's cap is shorts.per_day, counted from the ledger
    so the scheduled runs of a day cannot exceed it between them.

    `extra` marks a run from one of the test-only slots: it posts only while
    the Shorts test runs. `style` is auto (every hook_every-th Short in the
    hook style), calm or hook; fly-into makes the first Short a fly-into
    Short whatever the slot or date (a preview), the rest calm.

    `catch_up` marks a scheduled run: when the last 24 hours are short by
    more than the slack (GitHub skipped an earlier slot), a one-Short run
    posts one more to make it up, still inside the day's cap.

    `backup` marks a backup slot: it posts only when an earlier slot was
    skipped (the same shortfall `catch_up` looks for), never on a day on pace."""
    upload = cfg.shorts.enabled and cfg.publish.mode == "upload" and not dry_run
    mode = "upload" if upload else "dry-run"
    today = datetime.now(timezone.utc).date().isoformat()
    out_dir = output_dir / cfg.channel.slug / "shorts"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = data_dir / "work" / f"shorts-{today}"
    work.mkdir(parents=True, exist_ok=True)
    cache = data_dir / "cache" / "images"
    results: list[ShortResult] = []

    if extra and not in_test(cfg.shorts):
        log.info("shorts: a test-only slot, and no Shorts test is running")
        return results
    with Ledger(data_dir / "state.db") as ledger:
        _snapshot(ledger, cfg, today)
        recent = recent_shorts(ledger, cfg.channel.slug)
        from romanfeed.workshop.shorts import reserved as workshop_reserved

        target = daily_target(cfg.shorts, reserved=workshop_reserved(cfg))
        # The slack is for a single scheduled run that drift pushed close to
        # the next day's; a manual batch still stops at the day's number.
        room = max(target + (CAP_SLACK if count == 1 else 0) - len(recent), 0)
        want = room if count is None else min(count, room)
        behind = room >= CAP_SLACK + CATCH_UP_GAP
        if backup and not behind:
            log.info("shorts: a backup slot, and no slot was skipped (%d Shorts in the last 24 hours)", len(recent))
            return results
        if (catch_up or backup) and count == 1 and behind:
            # GitHub drops scheduled runs (Nov 2-4: the 12:10, 14:10 and 16:10
            # UTC slots, morning after morning); the next run makes one up.
            log.info("shorts: %d Shorts in the last 24 hours against %d a day; catching up one",
                     len(recent), target)
            want = 2
        if want <= 0:
            log.info("shorts: %d Shorts in the last 24 hours, the cap for %d a day", len(recent), target)
            return results
        recent_meta = ledger.asset_meta(recent) if cfg.shorts.earth_per_day else {}
        earth_recent = sum(1 for m in recent_meta.values() if is_earth(asset_from_meta(m)))
        # earth_per_day: 0 = no cap, N = at most N in 24 hours, -1 = none at all
        cap = cfg.shorts.earth_per_day
        earth_full = cap < 0 or (cap > 0 and earth_recent >= cap)
        keep = None
        if earth_full:
            # Earth sets have the fewest Shorts once Earth is capped, so they
            # head the running order; left in, they took every pick and the run
            # posted nothing (Nov 1-2: no Short for 18 hours). Ids the ledger
            # has no metadata for stay in; the loop below checks them again.
            log.info("shorts: %d Earth Shorts in the last 24 hours, the cap; deep sky only", earth_recent)

            def keep(ids: list[str]) -> list[str]:
                m = ledger.asset_meta(ids)
                return [a for a in ids if a not in m or not is_earth(asset_from_meta(m[a]))]
        # Spare picks: a video whose candidates are all unusable hands its
        # turn to the next one instead of costing the run its Short.
        picks = plan(ledger, cfg.channel.slug, want + SPARE_PICKS, per_short=cfg.shorts.candidates,
                     public=public, keep=keep)
        if not picks:
            log.warning("shorts: no published video with unused images to cut from")
            return results
        meta = backfill_meta(ledger, cfg, sorted({a for _, ids in picks for a in ids}))
        manifest = MusicLibrary(cfg.audio.library)

        posted = recent_upload_titles(cfg.channel.channel_id)
        posted_total = ledger.conn.execute(
            "SELECT COUNT(*) FROM shorts WHERE channel = ? AND youtube_id IS NOT NULL", (cfg.channel.slug,)
        ).fetchone()[0]
        # The social cut is rendered whenever the channel has cross-posting
        # accounts set up (to watch while crosspost.mode is off), posted per mode.
        social_on = bool(cfg.crosspost.tiktok_account or cfg.crosspost.instagram_account)
        key = narration.api_key() if cfg.shorts.narration else None
        explained = ledger.explainer_days(cfg.channel.slug)
        seen_objects = recent_short_objects(ledger, cfg.channel.slug, cfg.shorts.repeat_days)
        scale_done = scale_posted(ledger, cfg.channel.slug)
        if cfg.shorts.narration and not key:
            log.warning("shorts: narration is on but GOOGLE_TTS_API_KEY is not set; making music-only Shorts")
        for parent, ids in picks:
            if len(results) >= want:
                break
            cands = [asset_from_meta(meta[a]) for a in ids if a in meta]
            # Deep sky earns the subscribers (Oct 22-24: 3.6 per 1,000 Shorts
            # views against 1.6 for Earth), so Earth gets a capped share.
            if cap < 0 or (cap > 0 and earth_recent >= cap):
                cands = [a for a in cands if not is_earth(a)]
                if not cands:
                    log.warning("shorts: only Earth images left in %s, and Earth is at its cap", parent.slug)
                    continue
            # Counted over every Short ever posted: a 24-hour count stays the
            # same at the cap (one in, one aged out), so every run got the same
            # style (Nov 4-5: six calm in a row).
            n = posted_total + len(results)
            # "You are here" slots (test 21) are calm slots no other kind of
            # Short takes; a manual "you-are-here" run makes one (a preview).
            if (style == "you-are-here" and not results) or scale_mod.slot_due(
                    cfg.shorts, n, len(scale_done)):
                try:
                    done, why = _scale_short(cfg, ledger, parent, work=work, out_dir=out_dir, mode=mode,
                                             upload=upload, manifest=manifest, key=key, done_ids=scale_done)
                except Exception as exc:  # a new kind of Short must never cost the slot
                    log.exception("you-are-here Short failed")
                    done, why = None, f"it failed ({exc})"
                if done:
                    results.append(done)
                    continue
                log.info("shorts: you-are-here slot (Short %d) falls back to a normal Short: %s", n, why)
            # A manual "fly-into" run makes one whatever the slot (a preview).
            if (style == "fly-into" and not results) or fly_mod.slot_due(cfg.shorts, n):
                # Fly-into slots take the hook/opening/title combinations the
                # sky-event slots leave (romanfeed/fly_into.py), and none of
                # those treatments.
                try:
                    done, why = _fly_into_short(cfg, ledger, now=datetime.now(timezone.utc), cache=cache, work=work,
                                                out_dir=out_dir, models=data_dir / "cache" / "models", mode=mode,
                                                upload=upload, public=public, skip_objects=seen_objects,
                                                posted=posted)
                except Exception as exc:  # a new kind of Short must never cost the slot
                    log.exception("fly-into Short failed")
                    done, why = None, f"it failed ({exc})"
                if done:
                    results.append(done)
                    continue
                log.info("shorts: fly-into slot (Short %d) falls back to a normal Short: %s", n, why)
            elif sky_mod.slot_due(cfg.shorts, n):
                # Sky-event slots sit where every other test keeps its two
                # sides even (romanfeed/sky_events.py), and take none of them.
                try:
                    done, why = _sky_short(cfg, ledger, parent, now=datetime.now(timezone.utc), cache=cache,
                                           work=work, out_dir=out_dir, mode=mode, upload=upload,
                                           manifest=manifest, key=key)
                except Exception as exc:  # a new kind of Short must never cost the slot
                    log.exception("sky-event Short failed")
                    done, why = None, f"it failed ({exc})"
                if done:
                    results.append(done)
                    continue
                log.info("shorts: sky-event slot (Short %d) falls back to a normal Short: %s", n, why)
            hook = style == "hook" or (style == "auto" and is_hook(cfg.shorts, n))
            ranked = [a for a in ranked_candidates(cands, cache, cfg.shorts.subject_weights)
                      if not already_posted(a, posted)]
            # An object posted in the last repeat_days days drew 33-51 views
            # and no subscribers the second time (Nov 1-15), so it waits.
            fresh = [a for a in ranked if object_key(a) not in seen_objects]
            if ranked and not fresh:
                log.info("shorts: every pick from %s repeats a recent Short's object", parent.slug)
                continue
            ranked = fresh
            want_why = why_mod.why_due(cfg.shorts, n)
            antic = anticipation_due(cfg.shorts, n)
            punch = punch_due(cfg.shorts, n) and not antic
            # One explainer on each explainer date, in a slot with no
            # anticipation or punch-in opening, so the tests don't blur.
            want_explainer = bool(key) and not (antic or punch) and explainer_mod.explainer_due(cfg.shorts, explained)
            if not want_explainer and explainer_mod.reserving(cfg.shorts):
                # Keep the few explainer-length captions for the explainer slots.
                spare = [a for a in ranked if not explainer_mod.find_explainer(a, short_lead(a))]
                ranked = spare or ranked
            if not ranked:
                log.warning("shorts: nothing usable among %d candidates from %s", len(cands), parent.slug)
                continue
            label = full_label(parent.link_seconds)
            best, spoken, why, explain = ranked[0], None, None, None
            if want_explainer:
                taken = {r.asset_id for r in results} | {a for _, ids_ in picks for a in ids_}
                options = [(parent, c, label) for c in ranked] + [
                    (p2, c, full_label(p2.link_seconds))
                    for p2, c, _ in why_pool(ledger, cfg.channel.slug, public=public, skip=taken)]
                for p2, cand, l2 in options:
                    found = explainer_mod.find_explainer(cand, short_lead(cand))
                    if not found or not ranked_candidates([cand], cache):
                        continue
                    stem = f"{cfg.channel.slug}-short-{cand.asset_id.replace(':', '_')}"
                    spoken = narrate(cand, cfg=cfg, link_label=l2, work=work, stem=stem, key=key, explain=found)
                    if spoken:
                        best, explain, parent, label = cand, found, p2, l2
                        hook, want_why = False, False
                        break
                if explain is None:
                    log.info("shorts: no caption in the library makes a full explainer; a standard Short instead")
            if key and want_why:
                # A why Short needs a caption sentence that gives the cause;
                # the strongest frame that has one gets the slot.
                for cand in ranked:
                    found = why_mod.find_why(cand, short_lead(cand))
                    if not found:
                        continue
                    stem = f"{cfg.channel.slug}-short-{cand.asset_id.replace(':', '_')}"
                    spoken = narrate(cand, cfg=cfg, link_label=label, work=work, stem=stem, key=key, hook=hook,
                                     why=found)
                    if spoken:
                        best, why = cand, found
                        break
                if why is None:
                    # Nov 6-7: two why slots in a row found no answer in four
                    # videos' picks; the answers are rare, so look library-wide.
                    taken = {r.asset_id for r in results} | {a for _, ids_ in picks for a in ids_}
                    pool = why_pool(ledger, cfg.channel.slug, public=public, skip=taken)
                    for p2, cand, found in pool:
                        if not ranked_candidates([cand], cache):
                            continue
                        l2 = full_label(p2.link_seconds)
                        stem = f"{cfg.channel.slug}-short-{cand.asset_id.replace(':', '_')}"
                        spoken = narrate(cand, cfg=cfg, link_label=l2, work=work, stem=stem, key=key, hook=hook,
                                         why=found)
                        if spoken:
                            best, why, parent, label = cand, found, p2, l2
                            log.info("shorts: a why from %s (%d unasked in the library)", p2.slug, len(pool))
                            break
                if why is None:
                    log.info("shorts: no caption in the library answers a new why; a standard Short instead")
            if key and why is None and explain is None:
                # The strongest frame whose caption makes a script; a pretty
                # image with nothing to say loses to one with a story.
                for cand in ranked:
                    stem = f"{cfg.channel.slug}-short-{cand.asset_id.replace(':', '_')}"
                    spoken = narrate(cand, cfg=cfg, link_label=label, work=work, stem=stem, key=key, hook=hook)
                    if spoken:
                        best = cand
                        break
            stem = f"{cfg.channel.slug}-short-{best.asset_id.replace(':', '_')}"
            dur = (_length(spoken[2], explainer_mod.MAX_SECONDS) if explain else _length(spoken[2])) if spoken \
                else cfg.shorts.seconds
            # The social cut (romanfeed/crosspost.py) says the same facts and
            # points at YouTube instead; voiced now so the music covers both.
            social = None
            if social_on:
                social = _social_voice(spoken, cfg=cfg, label=label, work=work, stem=stem, key=key)
            longest = max(dur, social[3] if social else 0.0)
            # Composed: one fresh piece per Short, seeded by the image, so the
            # Short's music is as much its own as the long video's.
            library = (ComposedLibrary(work / "composed" / stem, count=1, seconds_each=longest + 4,
                                       seed=best.asset_id) if cfg.audio.source == "composed" else manifest)
            audio, tracks = build_soundtrack(
                library, genre=cfg.audio.genre, duration=dur, out_path=work / f"{stem}.m4a",
                fade=1.5, crossfade=0.0, gain_db=0.0,
                allow_placeholder=cfg.audio.allow_placeholder or mode == "dry-run", seed=best.asset_id,
            )
            if upload and any(not t.publishable for t in tracks):
                raise RuntimeError("refusing to upload a Short: soundtrack contains non-publishable tracks")
            script = spoken[0] if spoken else None
            hooked = hook and bool(spoken)
            ending = end_card_due(cfg.shorts, n) and not explain
            path = _render_cut(best, out_dir / f"{stem}.mp4", cfg=cfg, music=audio, label=label, work=work,
                               duration=dur, voice=spoken, hook=hooked, punch_in=punch, anticipation=antic,
                               end_card=ending)
            md = short_metadata(cfg, best, parent, tracks, script=script, hook=hooked, why=why if spoken else None,
                                punch_in=punch, anticipation=antic, explainer=explain[0] if explain else None,
                                plain_title=plain_title_due(cfg.shorts, n), end_card=ending)
            vid = publish(path, md, mode=mode)
            seen_objects.add(object_key(best))
            if upload:
                ledger.record_short(cfg.channel.slug, best.asset_id, parent.slug, vid)
                if explain:
                    day = explainer_mod._central_day()
                    ledger.record_explainer(cfg.channel.slug, day, best.asset_id, vid)
                    explained.add(day)
            log.info("short %s from %s (%s%s%s, %s) -> %s", best.asset_id, parent.slug,
                     ("hook style" if hooked else "narrated") if spoken else "music only",
                     ", explainer" if explain else ", why" if why and spoken else "",
                     ", punch-in open" if punch else ", anticipation open" if antic else "",
                     why_mod.subject(best), vid or "(dry run)")
            if PLAIN_TITLE_TAG in md.tags:
                log.info("short %s goes out with the plain title: %s", best.asset_id, md.title)
            if is_earth(best):
                earth_recent += 1
            # Test-only slots post to YouTube alone: the TikTok and Instagram
            # queue holds four slots a day, and extra clips would pile up there.
            if social_on and not extra:
                s_dur = social[3] if social else dur
                s_audio = audio if s_dur == dur else build_soundtrack(
                    library, genre=cfg.audio.genre, duration=s_dur, out_path=work / f"{stem}.social.m4a",
                    fade=1.5, crossfade=0.0, gain_db=0.0,
                    allow_placeholder=cfg.audio.allow_placeholder or mode == "dry-run", seed=best.asset_id)[0]
                s_path = _render_cut(best, out_dir / f"{stem}.social.mp4", cfg=cfg, music=s_audio, label=label,
                                     work=work, duration=s_dur, voice=social[:3] if social else None,
                                     pointer=crosspost.end_card(label, cfg.channel.handle))
                # Posts only for real runs, or in test mode (private, TikTok only).
                if upload or crosspost.effective_mode(cfg) == "test":
                    crosspost.crosspost(cfg, s_path, best, script=social[0] if social else None, full_label=label)
            results.append(ShortResult(best.asset_id, parent.slug, path, vid, md.title))
    return results
