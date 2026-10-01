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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests
from PIL import Image, ImageFilter, ImageOps, ImageStat

from romanfeed.audio import MusicLibrary, build_soundtrack
from romanfeed.audio.composer import ComposedLibrary
from romanfeed.config import ChannelConfig
from romanfeed.curation.selector import flat_black_fraction, looks_unsuitable
from romanfeed.publish import publish
from romanfeed.publish.metadata import VideoMetadata, _fit_title, lead_name
from romanfeed.render import ffmpeg
from romanfeed import narration
from romanfeed.render.cards import caption_card, short_cta_card, short_overlay
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


def plan(ledger: Ledger, channel: str, count: int, *, per_short: int = 6,
         public: Callable[[str], bool] = is_public) -> list[tuple[Parent, list[str]]]:
    """Which parent each of the next `count` Shorts comes from, and the
    candidate asset ids it chooses its best image from.

    Parents with the fewest Shorts go first (newest breaking ties), one Short
    per parent per lap, so every long video gets its turn instead of the
    newest one hogging the feed. Candidates are spread through the running
    order and never shared between two Shorts of the same run."""
    used = ledger.shorts_asset_ids(channel)
    done = ledger.shorts_per_parent(channel)
    pool = []
    for p in parents(ledger, channel, public=public):
        ids = [a for a in ledger.assets_of_video(p.slug) if a not in used]
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


def pick_best(candidates: list[ImageAsset], cache_dir: Path) -> ImageAsset | None:
    ranked = ranked_candidates(candidates, cache_dir)
    return ranked[0] if ranked else None


def ranked_candidates(candidates: list[ImageAsset], cache_dir: Path) -> list[ImageAsset]:
    """Usable candidates, strongest frame first."""
    scored = []
    for a in candidates:
        if looks_unsuitable(a):
            continue
        try:
            a.download(cache_dir)
            if flat_black_fraction(a) > 0.02:
                continue
            scored.append((image_score(a.local_path), a))
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
                 cta_from: float | None = None) -> Path:
    """Render one Short. With `captions` (a narrated Short) the pointer to the
    long video waits until `cta_from`; without, it is on screen throughout."""
    s = cfg.shorts
    dur = duration or s.seconds
    handle = cfg.channel.handle or cfg.channel.name
    frame, ex, ey = vertical_frame(asset.local_path, work_dir / "short_frame.png", width=s.width, height=s.height, max_pan=900)
    narrated = bool(captions)
    layers: list[tuple[Path, str | None]] = []   # (png, ffmpeg enable expression or None)
    base = work_dir / "short_overlay.png"
    short_overlay(asset, s.width, s.height, full_label=link_label, handle=handle, cta=not narrated).save(base, "PNG")
    layers.append((base, None))
    if narrated:
        for i, (t0, t1, text) in enumerate(captions):
            png = work_dir / f"short_cap_{i:02d}.png"
            caption_card(text, s.width, s.height).save(png, "PNG")
            layers.append((png, f"between(t,{t0:.3f},{t1:.3f})"))
        cta = work_dir / "short_cta.png"
        short_cta_card(s.width, s.height, full_label=link_label, handle=handle).save(cta, "PNG")
        layers.append((cta, f"gte(t,{(cta_from or dur - 4):.3f})"))
    # Alternate pan direction by asset so a run of Shorts does not all drift left.
    flip = int(hashlib.sha1(asset.asset_id.encode()).hexdigest(), 16) % 2
    prog = f"(1-t/{dur:.3f})" if flip else f"(t/{dur:.3f})"
    crop = f"crop={s.width * 2}:{s.height * 2}:x='{ex}*{prog}':y='{ey}*{prog}'"
    parts, prev = [f"[0:v]{crop},scale={s.width}:{s.height}:flags=bicubic[c0]"], "[c0]"
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


def narrate(asset: ImageAsset, *, cfg: ChannelConfig, link_label: str, work: Path, stem: str, key: str):
    """Script + voice for one image: (script, voice wav, voice seconds), or None.

    A script that runs long is retried without its size/distance sentence; a
    Short tops out at 45 s and the voice needs the music either side of it."""
    budget = 45.0 - 0.8 - 3.0
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


def short_metadata(cfg: ChannelConfig, asset: ImageAsset, parent: Parent, tracks, script: str | None = None) -> VideoMetadata:
    lead = lead_name(asset.title, limit=60) or asset.title
    tele = telescope_short(asset)
    template = cfg.shorts.title_template
    title_lead = lead
    if "{lead_by}" in template:
        template = template.replace("{lead_by}", "{lead}")
        title_lead = f"{lead} by {tele}" if tele and tele.split(" & ")[0].lower() not in lead.lower() else lead
    title = _fit_title(template, title_lead, {"telescope": tele or "Telescope", "channel": cfg.channel.name})
    title = re.sub(r"\s+by(?=\s*(?:[|·]|$))", "", title)
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
    if handle:
        lines += ["", f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1"]
    lines += ["", "#shorts #space #sleep"]
    tags = list(dict.fromkeys(["shorts", "space", "sleep", "space for sleep", "relaxing space", lead.lower(),
                               *(t.lower() for t in [tele] if t), "telescope", "nebula", "galaxy"]))
    return VideoMetadata(
        title=title[:100], description="\n".join(lines)[:5000], tags=tags[:20],
        category_id=cfg.publish.category_id, privacy=cfg.shorts.privacy, made_for_kids=cfg.publish.made_for_kids,
    )


def _snapshot(ledger: Ledger, cfg: ChannelConfig, today: str) -> None:
    """Note today's public subscriber/view counts for the weekly report (1 quota unit)."""
    from romanfeed.publish.youtube import api_key, public_client

    if not (api_key() and cfg.channel.handle):
        return
    try:
        from romanfeed.publish.stats import channel_counts

        ledger.record_snapshot(today, *channel_counts(public_client(), cfg.channel.handle))
    except Exception as exc:  # a stats hiccup must never cost a Short
        log.warning("channel snapshot failed: %s", exc)


def shorts_today(ledger: Ledger, channel: str, today: str) -> int:
    row = ledger.conn.execute(
        "SELECT COUNT(*) FROM shorts WHERE channel = ? AND youtube_id IS NOT NULL AND created_at LIKE ?",
        (channel, f"{today}%"),
    ).fetchone()
    return int(row[0])


def run_shorts(cfg: ChannelConfig, *, count: int | None = None, dry_run: bool = False,
               data_dir: Path = Path("data"), output_dir: Path = Path("output"),
               public: Callable[[str], bool] = is_public) -> list[ShortResult]:
    """Cut and (if switched on) publish up to `count` Shorts.

    Uploads only when shorts.enabled is true, publish.mode is upload, and this
    is not a dry run. The day's cap is shorts.per_day, counted from the ledger
    so three scheduled runs a day cannot exceed it between them."""
    upload = cfg.shorts.enabled and cfg.publish.mode == "upload" and not dry_run
    mode = "upload" if upload else "dry-run"
    today = datetime.now(timezone.utc).date().isoformat()
    out_dir = output_dir / cfg.channel.slug / "shorts"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = data_dir / "work" / f"shorts-{today}"
    work.mkdir(parents=True, exist_ok=True)
    cache = data_dir / "cache" / "images"
    results: list[ShortResult] = []

    with Ledger(data_dir / "state.db") as ledger:
        _snapshot(ledger, cfg, today)
        room = max(cfg.shorts.per_day - shorts_today(ledger, cfg.channel.slug, today), 0)
        want = room if count is None else min(count, room)
        if want <= 0:
            log.info("shorts: daily cap of %d already reached", cfg.shorts.per_day)
            return results
        picks = plan(ledger, cfg.channel.slug, want, per_short=cfg.shorts.candidates, public=public)
        if not picks:
            log.warning("shorts: no published video with unused images to cut from")
            return results
        meta = backfill_meta(ledger, cfg, sorted({a for _, ids in picks for a in ids}))
        manifest = MusicLibrary(cfg.audio.library)

        key = narration.api_key() if cfg.shorts.narration else None
        if cfg.shorts.narration and not key:
            log.warning("shorts: narration is on but GOOGLE_TTS_API_KEY is not set; making music-only Shorts")
        for parent, ids in picks:
            cands = [asset_from_meta(meta[a]) for a in ids if a in meta]
            ranked = ranked_candidates(cands, cache)
            if not ranked:
                log.warning("shorts: nothing usable among %d candidates from %s", len(cands), parent.slug)
                continue
            label = full_label(parent.link_seconds)
            best, spoken = ranked[0], None
            if key:
                # The strongest frame whose caption makes a script; a pretty
                # image with nothing to say loses to one with a story.
                for cand in ranked:
                    stem = f"{cfg.channel.slug}-short-{cand.asset_id.replace(':', '_')}"
                    spoken = narrate(cand, cfg=cfg, link_label=label, work=work, stem=stem, key=key)
                    if spoken:
                        best = cand
                        break
            stem = f"{cfg.channel.slug}-short-{best.asset_id.replace(':', '_')}"
            dur = min(45.0, max(30.0, spoken[2] + 0.8 + 3.5)) if spoken else cfg.shorts.seconds
            # Composed: one fresh piece per Short, seeded by the image, so the
            # Short's music is as much its own as the long video's.
            library = (ComposedLibrary(work / "composed" / stem, count=1, seconds_each=dur + 4,
                                       seed=best.asset_id) if cfg.audio.source == "composed" else manifest)
            audio, tracks = build_soundtrack(
                library, genre=cfg.audio.genre, duration=dur, out_path=work / f"{stem}.m4a",
                fade=1.5, crossfade=0.0, gain_db=0.0,
                allow_placeholder=cfg.audio.allow_placeholder or mode == "dry-run", seed=best.asset_id,
            )
            if upload and any(not t.publishable for t in tracks):
                raise RuntimeError("refusing to upload a Short: soundtrack contains non-publishable tracks")
            captions, cta_from, script = None, None, None
            if spoken:
                script, wav, secs = spoken
                audio = narration.mix_voice(wav, audio, work / f"{stem}.mix.m4a", voice_start=0.8, duration=dur)
                # Captions cover the facts; the closing pointer sentence is shown
                # as the end card instead of as a caption.
                body = script.rsplit(" The full ", 1)[0]
                body_secs = secs * len(body) / max(len(script), 1)
                captions = narration.caption_chunks(body, start=0.8, duration=body_secs)
                cta_from = 0.8 + body_secs
            path = render_short(best, out_dir / f"{stem}.mp4", cfg=cfg, audio=audio, link_label=label, work_dir=work,
                                duration=dur, captions=captions, cta_from=cta_from)
            md = short_metadata(cfg, best, parent, tracks, script=script)
            vid = publish(path, md, mode=mode)
            if upload:
                ledger.record_short(cfg.channel.slug, best.asset_id, parent.slug, vid)
            log.info("short %s from %s (%s) -> %s", best.asset_id, parent.slug,
                     "narrated" if spoken else "music only", vid or "(dry run)")
            results.append(ShortResult(best.asset_id, parent.slug, path, vid, md.title))
    return results
