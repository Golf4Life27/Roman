"""The daily job: fetch -> curate -> render -> score -> publish -> record.

Every step writes to disk so a failed run can be inspected, and the ledger
is only updated after a successful render so a crash never burns images."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from romanfeed.audio import MusicLibrary, build_soundtrack
from romanfeed.config import ChannelConfig
from romanfeed.curation import select_assets
from romanfeed.publish import build_metadata, publish
from romanfeed.render import render_video_with_clips
from romanfeed.render.compose import concat_clips, mux_audio, repeat_order
from romanfeed.render.ffmpeg import probe_duration
from romanfeed.sources import build_source
from romanfeed.state import Ledger, VideoRecord

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    images: int | None = None
    seconds_per_image: float | None = None
    dry_run: bool | None = None  # None -> use config
    seed: str | None = None
    data_dir: Path = Path("data")
    output_dir: Path = Path("output")
    keep_work: bool = False
    # Smoke-test the upload chain: forces privacy=private, prefixes the title
    # with [TEST], and permits the in-house placeholder drone as audio.
    private_test: bool = False


@dataclass
class RunResult:
    video_path: Path
    metadata_path: Path
    duration_s: float
    n_images: int
    youtube_id: str | None
    # One entry per extra-length cut: (hours, path, youtube id or None)
    extra_cuts: list[tuple[float, Path, str | None]] = field(default_factory=list)


def run(cfg: ChannelConfig, opts: RunOptions | None = None) -> RunResult:
    opts = opts or RunOptions()
    n_images = opts.images or cfg.video.images_per_video
    spi = opts.seconds_per_image or cfg.video.seconds_per_image
    mode = "dry-run" if (opts.dry_run if opts.dry_run is not None else cfg.publish.mode == "dry-run") else "upload"
    if opts.private_test:
        mode = "upload"
    today = date.today().isoformat()
    slug = f"{cfg.channel.slug}-{today}"
    out_dir = opts.output_dir / cfg.channel.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    work = opts.data_dir / "work" / slug
    work.mkdir(parents=True, exist_ok=True)
    cache = opts.data_dir / "cache" / "images"

    with Ledger(opts.data_dir / "state.db") as ledger:
        # 1. fetch
        candidates = []
        for s in cfg.enabled_sources:
            src = build_source(s)
            got = src.fetch()
            log.info("%s -> %d candidates", src.name, len(got))
            candidates.extend(got)
        if not candidates:
            raise RuntimeError("no candidate images from any source")

        # 2. curate
        min_width = min((s.min_width for s in cfg.enabled_sources), default=1600)
        assets = select_assets(
            candidates, channel=cfg.channel.slug, ledger=ledger, count=n_images,
            min_width=min_width, cache_dir=str(cache), seed=opts.seed or today,
            max_flat_black=cfg.video.max_flat_black,
        )
        if not assets:
            raise RuntimeError("no usable images after curation")

        # 3. soundtrack
        target = spi * len(assets)
        library = MusicLibrary(cfg.audio.library)
        audio_path, tracks = build_soundtrack(
            library, genre=cfg.audio.genre, duration=target, out_path=work / "soundtrack.m4a",
            fade=cfg.audio.fade_seconds, crossfade=cfg.audio.crossfade_seconds, gain_db=cfg.audio.gain_db,
            allow_placeholder=cfg.audio.allow_placeholder or mode == "dry-run" or opts.private_test, seed=opts.seed or today,
        )

        # 4. render. Clips are rendered once and reused by every cut below;
        # stitching is a stream copy, so a longer cut is seconds, not another
        # full render.
        video_path = out_dir / f"{slug}.mp4"
        _, clips = render_video_with_clips(
            assets, cfg, work_dir=work, audio_path=audio_path,
            out_path=video_path, seconds_per_image=spi,
        )
        duration = probe_duration(str(video_path))

        # 5. metadata + publish guard
        meta = build_metadata(cfg, assets, tracks, seconds_per_image=spi)
        if opts.private_test:
            meta.privacy = "private"
            meta.title = ("[TEST] " + meta.title)[:100]
        elif mode == "upload" and any(not t.publishable for t in tracks):
            raise RuntimeError("refusing to upload: soundtrack contains non-publishable tracks")
        youtube_id = publish(video_path, meta, mode=mode)

        # 4b. extra-length cuts of the same asset set (skipped on smoke tests)
        extra_cuts: list[tuple[float, Path, str | None]] = []
        wanted = [] if opts.private_test else list(cfg.video.extra_lengths_hours)
        for hours in wanted:
            base_len = spi * len(assets)
            passes = max(1, math.ceil((hours * 3600) / base_len))
            order = repeat_order(len(assets), passes, seed=opts.seed or today)
            cut_assets = [assets[i] for i in order]
            cut_slug = f"{slug}-{hours:g}h"
            log.info("cut %sh: %d clips (%d passes over %d images)", f"{hours:g}", len(order), passes, len(assets))

            cut_audio, cut_tracks = build_soundtrack(
                library, genre=cfg.audio.genre, duration=spi * len(order),
                out_path=work / f"soundtrack-{hours:g}h.m4a",
                fade=cfg.audio.fade_seconds, crossfade=cfg.audio.crossfade_seconds, gain_db=cfg.audio.gain_db,
                allow_placeholder=cfg.audio.allow_placeholder or mode == "dry-run", seed=f"{opts.seed or today}-{hours:g}h",
            )
            if mode == "upload" and any(not t.publishable for t in cut_tracks):
                raise RuntimeError("refusing to upload: soundtrack contains non-publishable tracks")

            cut_path = out_dir / f"{cut_slug}.mp4"
            mux_audio(
                concat_clips([clips[i] for i in order], work / f"silent-{hours:g}h.mp4"),
                cut_audio, cut_path,
            )
            cut_meta = build_metadata(cfg, cut_assets, cut_tracks, seconds_per_image=spi, chapter_limit=len(assets))
            cut_id = publish(cut_path, cut_meta, mode=mode)
            extra_cuts.append((hours, cut_path, cut_id))
            ledger.record_video(VideoRecord(
                video_slug=cut_slug, channel=cfg.channel.slug, path=str(cut_path),
                duration_s=probe_duration(str(cut_path)),
                rendered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                youtube_id=cut_id, published_at=None, title=cut_meta.title,
            ))
            if cut_id:
                ledger.mark_published(cut_slug, cut_id, cut_meta.title)

        # 6. record
        ledger.mark_assets_used(cfg.channel.slug, [a.asset_id for a in assets], slug)
        ledger.record_video(VideoRecord(
            video_slug=slug, channel=cfg.channel.slug, path=str(video_path), duration_s=duration,
            rendered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            youtube_id=youtube_id, published_at=None, title=meta.title,
        ))
        if youtube_id:
            ledger.mark_published(slug, youtube_id, meta.title)

    if not opts.keep_work:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    return RunResult(video_path, video_path.with_suffix(".upload.json"), duration, len(assets), youtube_id, extra_cuts)
