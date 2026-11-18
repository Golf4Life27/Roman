"""The journey job: pick one image -> depth -> plan -> render -> music -> cuts.

Separate from the daily render (romanfeed.pipeline) and switched off until
the owner turns on `journey.enabled` in the channel YAML. Until then every
run is a dry run: files and upload sidecars on disk, nothing sent anywhere.

Outputs, under output/<slug>/:
  <slug>-journey-<date>.mp4            the main cut (journey.hours, 1 h by default)
  <slug>-journey-<date>-8h.mp4         each extra length: the same passes, then dark
  <slug>-journey-<date>.short.mp4      a 20-30 s vertical cut (a by-product, not uploaded)
  *.upload.json / *.thumb.jpg          what would be (or was) sent to YouTube"""
from __future__ import annotations

import hashlib
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image

from romanfeed.config import ChannelConfig
from romanfeed.journey.camera import Frame, Leg, plan_journey
from romanfeed.journey.depth import estimate_depth
from romanfeed.journey.layers import find_targets
from romanfeed.journey.metadata import build_journey_metadata, is_headline, journey_subject
from romanfeed.journey.render import Scene, alpha_overlay, chain, have_opencv, render_black, render_legs
from romanfeed.journey.short import render_journey_short
from romanfeed.render import ffmpeg
from romanfeed.sources.base import ImageAsset

log = logging.getLogger(__name__)

BLACK_SECONDS = 60.0


def ledger_key(cfg: ChannelConfig) -> str:
    """Journeys keep their own used-image list, so they never use up the
    daily videos' images and never fly into the same picture twice."""
    return f"{cfg.channel.slug}-journey"


def publish_gate(cfg: ChannelConfig, today: date | None = None) -> tuple[bool, str]:
    """(may this run upload?, why). Uploads need journey.enabled, today in
    journey.dates when dates are listed, and publish.mode upload."""
    today = today or datetime.now(timezone.utc).date()
    j = cfg.journey
    if not j.enabled:
        return False, "journey.enabled is false"
    if j.dates and today.isoformat() not in {str(d) for d in j.dates}:
        return False, f"{today.isoformat()} is not in journey.dates"
    if cfg.publish.mode != "upload":
        return False, "publish.mode is not upload"
    return True, "journey.enabled and today is a journey date"


@dataclass
class JourneyOptions:
    minutes: float | None = None       # main cut length override (previews); None = journey.hours
    image: Path | None = None          # a local image instead of fetching one
    title: str = ""                    # ...and how to credit it
    credit: str = ""
    source_label: str = "Local file"
    dry_run: bool = False
    cuts: bool = True                  # render the extra-length cuts
    short: bool = True                 # render the vertical Short
    seed: str | None = None
    data_dir: Path = Path("data")
    output_dir: Path = Path("output")
    keep_work: bool = False
    workers: int | None = None
    depth: str | None = None           # override journey.depth


@dataclass
class JourneyResult:
    video_path: Path
    asset: ImageAsset
    depth_source: str
    legs: list[Leg]
    title: str
    youtube_id: str | None
    render_seconds: float
    short_path: Path | None = None
    extra_cuts: list[tuple[float, Path, str | None]] = field(default_factory=list)


def pick_image(cfg: ChannelConfig, ledger, *, cache_dir: Path, seed: str) -> ImageAsset:
    """One fresh nebula or galaxy picture from a known telescope, big enough
    to fly into; the largest of a few candidates."""
    from romanfeed.curation import select_assets
    from romanfeed.sources import build_source
    from romanfeed.telescopes import telescope_short

    candidates: list[ImageAsset] = []
    for s in cfg.enabled_sources:
        if (s.options or {}).get("subject") == "earth":
            continue
        got = build_source(s).fetch()
        log.info("%s -> %d candidates", s.type, len(got))
        candidates.extend(got)
    pool = [a for a in candidates
            if journey_subject(a) and telescope_short(a) and not is_headline(a.title)]
    log.info("%d candidates are nebulae or galaxies from a known telescope", len(pool))
    chosen = select_assets(pool, channel=ledger_key(cfg), ledger=ledger, count=4, min_width=cfg.journey.min_width,
                           cache_dir=str(cache_dir), seed=seed, max_flat_black=cfg.video.max_flat_black)
    if not chosen:
        raise RuntimeError(f"no nebula or galaxy image at least {cfg.journey.min_width} px wide")
    return max(chosen, key=lambda a: a.width * a.height)


def local_asset(opts: JourneyOptions) -> ImageAsset:
    path = Path(opts.image).resolve()
    with Image.open(path) as im:
        w, h = im.size
    return ImageAsset(asset_id=f"local:{path.stem}", title=opts.title or path.stem, url=path.as_uri(),
                      source=opts.source_label, credit=opts.credit, width=w, height=h,
                      keywords=["nebula"] if "nebula" in (opts.title or "").lower() else [], local_path=path)


def opener_overlay(cfg: ChannelConfig, asset: ImageAsset, leg: Leg):
    """The daily videos' intro card and subscribe prompt, on the first pass."""
    from romanfeed.render.cards import intro_card, subscribe_card

    v = cfg.video
    parts = []
    intro_end = min(v.intro_seconds, leg.duration - 2.0)
    if v.intro_seconds > 0 and intro_end > 2.0:
        parts.append(alpha_overlay(intro_card(asset, v.width, v.height, channel_name=cfg.channel.name), 0.6, intro_end, 1.2))
    sub_end = min(v.subscribe_until, leg.duration - 2.0)
    if v.subscribe_until > v.subscribe_from and sub_end - v.subscribe_from > 3.0:
        parts.append(alpha_overlay(subscribe_card(v.width, v.height, channel_name=cfg.channel.name, line=v.subscribe_line),
                                   v.subscribe_from, sub_end, 1.0))
    return chain(*parts) if parts else None


def concat_with_audio(clips: list[Path], audio: Path | None, out: Path, *, faststart: bool = True) -> Path:
    """Stitch clips (stream copy) and lay the soundtrack on in one pass, so
    no full-size silent intermediate ever sits on the disk."""
    listfile = out.with_suffix(".txt")
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    args = ["-f", "concat", "-safe", "0", "-i", str(listfile)]
    if audio is not None:
        args += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 "-shortest"]
    else:
        args += ["-c", "copy"]
    if faststart:
        args += ["-movflags", "+faststart"]
    ffmpeg.run(args + [str(out)])
    listfile.unlink(missing_ok=True)
    return out


def journey_soundtrack(cfg: ChannelConfig, *, seconds: float, work: Path, seed: str, calm_at: float,
                       piece=None):
    """(path, tracks, journey piece) for `seconds` of soundtrack.

    The journey piece (lively, settled by calm_at) is composed to `seconds`
    unless one is passed in (the longer cuts reuse the main cut's); anything
    past its end is calm space-style music, crossfaded on after it."""
    from romanfeed.audio.composer import ComposedLibrary, compose_piece
    from romanfeed.audio.mix import build_soundtrack
    from romanfeed.render.ffmpeg import probe_duration

    a = cfg.audio
    if piece is None:
        piece = compose_piece(work / "journey-piece.m4a", seconds=seconds, seed=f"{seed}-journey", style="journey",
                              artist=cfg.channel.name, calm_at=calm_at)
    piece_len = probe_duration(str(piece.path))
    out = work / f"soundtrack-{int(seconds)}s.m4a"
    fade, xf, gain = a.fade_seconds, max(1.0, a.crossfade_seconds), a.gain_db
    tail = f"afade=t=in:st=0:d={fade},afade=t=out:st={max(0.0, seconds - fade):.3f}:d={fade},volume={gain}dB"
    if seconds <= piece_len + 0.5:
        ffmpeg.run(["-i", str(piece.path), "-af", f"atrim=0:{seconds:.3f},{tail}", "-c:a", "aac", "-b:a", "192k", str(out)])
        return out, [piece], piece
    lib = ComposedLibrary(work / "bed", count=a.composed_pieces, seconds_each=a.composed_seconds, seed=f"{seed}-bed",
                          style="space", artist=cfg.channel.name)
    bed, bed_tracks = build_soundtrack(lib, genre=a.genre, duration=seconds - piece_len + xf, out_path=work / "bed.m4a",
                                       fade=fade, crossfade=a.crossfade_seconds, gain_db=0.0, seed=f"{seed}-bed")
    ffmpeg.run(["-i", str(piece.path), "-i", str(bed), "-filter_complex",
                f"[0:a][1:a]acrossfade=d={xf}:c1=tri:c2=tri,{tail}[a]",
                "-map", "[a]", "-t", f"{seconds:.3f}", "-c:a", "aac", "-b:a", "192k", str(out)])
    bed.unlink(missing_ok=True)
    return out, [piece] + bed_tracks, piece


def long_cut_sequence(legs: list[Leg], clips: list[Path], black: Path | None, *, total: float, dark_after: float,
                      black_seconds: float = BLACK_SECONDS) -> tuple[list[Path], float, float | None]:
    """(clips, running time, when it goes dark or None) for an extra-length cut.

    With dark_after > 0: the passes in order until dark_after (rounded up to
    the end of a pass, so the reveal it is in finishes), then black to the
    end. Otherwise the calm passes loop until the running time is filled."""
    seq: list[Path] = []
    t = 0.0
    if dark_after > 0 and black is not None:
        for leg, clip in zip(legs, clips):
            if t >= dark_after:
                break
            seq.append(clip)
            t += leg.duration
        dark_at = t
        while t < total:
            seq.append(black)
            t += black_seconds
        return seq, t, dark_at
    for leg, clip in zip(legs, clips):
        seq.append(clip)
        t += leg.duration
    calm = [(lg, c) for lg, c in zip(legs, clips) if lg.energy < 0.2] or list(zip(legs[1:], clips[1:])) or list(zip(legs, clips))
    i = 0
    while t < total:
        lg, c = calm[i % len(calm)]
        seq.append(c)
        t += lg.duration
        i += 1
    return seq, t, None


def run_journey(cfg: ChannelConfig, opts: JourneyOptions | None = None) -> JourneyResult:
    from romanfeed.pipeline import build_thumbnail, log_disk_free
    from romanfeed.publish import publish
    from romanfeed.render.ffmpeg import probe_duration
    from romanfeed.state import Ledger, VideoRecord

    opts = opts or JourneyOptions()
    j, v = cfg.journey, cfg.video
    allowed, why = publish_gate(cfg)
    mode = "upload" if allowed and not opts.dry_run else "dry-run"
    log.info("journey: %s (%s)", mode, why if not opts.dry_run else "--dry-run")
    if not have_opencv():
        log.warning("OpenCV is not installed: rendering on the slow numpy path (pip install -e '.[journey]')")
    today = date.today().isoformat()
    seed = opts.seed or today
    slug = f"{cfg.channel.slug}-journey-{today}"
    out_dir = opts.output_dir / cfg.channel.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    work = opts.data_dir / "work" / slug
    work.mkdir(parents=True, exist_ok=True)
    t_start = time.monotonic()

    with Ledger(opts.data_dir / "state.db") as ledger:
        asset = local_asset(opts) if opts.image else pick_image(cfg, ledger, cache_dir=opts.data_dir / "cache" / "images", seed=seed)
        log.info("journey image: %s (%dx%d)", asset.title, asset.width, asset.height)
        with Image.open(asset.local_path) as im:
            image = im.convert("RGB")
        depth, depth_source = estimate_depth(image, method=opts.depth or j.depth, cache_dir=opts.data_dir / "cache" / "models")
        scene = Scene.from_image(image, depth, depth_source)
        del image
        log.info("depth from %s; scene %dx%d", depth_source, scene.width, scene.height)
        seed_int = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big")
        targets = find_targets(scene.background, scene.depth, count=10, seed=seed_int)

        seconds = (opts.minutes * 60) if opts.minutes else j.hours * 3600
        calm_at = j.calm_minutes * 60
        frame = Frame(scene.width, scene.height, v.width, v.height)
        legs = plan_journey(seconds, frame, targets, seed=seed_int, calm_at=calm_at)
        log.info("%d passes over %.0f s; reveals at %s", len(legs), seconds,
                 ", ".join(f"{lg.reveal_at / 60:.1f}m" for lg in legs))

        overlay = opener_overlay(cfg, asset, legs[0])
        log_disk_free(out_dir, "before journey render")
        clips = render_legs(scene, legs, work / "clips", w=v.width, h=v.height, fps=v.fps, crf=v.crf, preset=v.preset,
                            workers=opts.workers or (j.workers or None), overlays={0: overlay} if overlay else None)
        render_s = time.monotonic() - t_start

        audio, tracks, piece = journey_soundtrack(cfg, seconds=sum(lg.duration for lg in legs), work=work, seed=seed,
                                                  calm_at=calm_at)
        video_path = concat_with_audio(clips, audio, out_dir / f"{slug}.mp4")
        total = probe_duration(str(video_path))

        meta = build_journey_metadata(cfg, asset, tracks, legs, total_seconds=total, depth_source=depth_source)
        plate = tuple(v.thumbnail_plate[:3]) if len(v.thumbnail_plate) >= 3 else None
        thumb = build_thumbnail([asset], total, out_dir / f"{slug}.thumb.jpg", subject="FLY-IN JOURNEY", plate_rgb=plate)
        youtube_id = publish(video_path, meta, mode=mode, thumbnail=thumb)
        log_disk_free(out_dir, "after journey main cut")

        extra: list[tuple[float, Path, str | None]] = []
        if opts.cuts and j.extra_lengths_hours:
            dark_after = j.sleep_dark_after_minutes * 60
            black = render_black(work / "clips" / "black.mp4", BLACK_SECONDS, w=v.width, h=v.height, fps=v.fps,
                                 crf=v.crf, preset=v.preset) if dark_after > 0 else None
            for hours in j.extra_lengths_hours:
                seq, cut_total, dark_at = long_cut_sequence(legs, clips, black, total=hours * 3600, dark_after=dark_after)
                cut_audio, cut_tracks, _ = journey_soundtrack(cfg, seconds=cut_total, work=work, seed=seed,
                                                              calm_at=calm_at, piece=piece)
                cut_path = concat_with_audio(seq, cut_audio, out_dir / f"{slug}-{hours:g}h.mp4", faststart=False)
                cut_audio.unlink(missing_ok=True)
                cut_meta = build_journey_metadata(cfg, asset, cut_tracks, legs, total_seconds=cut_total,
                                                  depth_source=depth_source, sleep=True, dark_after=dark_at)
                cut_thumb = build_thumbnail([asset], cut_total, out_dir / f"{slug}-{hours:g}h.thumb.jpg",
                                            subject="DEEP SLEEP · DARK SCREEN" if dark_at else "FLY-IN JOURNEY", plate_rgb=plate)
                cut_id = publish(cut_path, cut_meta, mode=mode, thumbnail=cut_thumb)
                extra.append((hours, cut_path, cut_id))
                if cut_id:
                    ledger.record_video(VideoRecord(
                        video_slug=f"{slug}-{hours:g}h", channel=cfg.channel.slug, path=str(cut_path),
                        duration_s=cut_total, rendered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        youtube_id=cut_id, published_at=None, title=cut_meta.title))
                    ledger.mark_published(f"{slug}-{hours:g}h", cut_id, cut_meta.title)

        short_path = None
        if opts.short:
            try:
                from romanfeed.audio.composer import compose_piece

                s_audio = compose_piece(work / "short-music.m4a", seconds=j.short_seconds + 2, seed=f"{seed}-short",
                                        style="journey", artist=cfg.channel.name, calm_at=1e9).path
                short_path = render_journey_short(scene, targets, out_dir / f"{slug}.short.mp4",
                                                  seconds=j.short_seconds, audio=s_audio)
            except Exception as exc:  # noqa: BLE001 - the Short is a by-product; never fail the run for it
                log.warning("journey Short failed: %s", exc)

        if youtube_id:
            ledger.mark_assets_used(ledger_key(cfg), [asset.asset_id], slug)
            ledger.record_video_assets(slug, [asset])
            ledger.record_video(VideoRecord(
                video_slug=slug, channel=cfg.channel.slug, path=str(video_path), duration_s=total,
                rendered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                youtube_id=youtube_id, published_at=None, title=meta.title))
            ledger.mark_published(slug, youtube_id, meta.title)

    if not opts.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return JourneyResult(video_path, asset, depth_source, legs, meta.title, youtube_id, render_s, short_path, extra)


__all__ = ["JourneyOptions", "JourneyResult", "long_cut_sequence", "publish_gate", "run_journey"]
