"""Turn a list of selected assets into one finished MP4 with audio.

Rendering the clips is the expensive part; stitching them is a stream copy and
costs almost nothing. So the long cuts (3h, 8h) reuse the same clips rather
than re-rendering: `repeat_order` lays the same images out again in a fresh
shuffle each pass, so hour six does not replay hour one in the same order."""
from __future__ import annotations

import logging
import random
from pathlib import Path

from PIL import Image

from romanfeed.config import ChannelConfig
from romanfeed.render import ffmpeg
from romanfeed.render.captions import prepare_frame
from romanfeed.render.cards import intro_card, subscribe_card
from romanfeed.render.kenburns import Segment, TimedOverlay
from romanfeed.sources.base import ImageAsset

log = logging.getLogger(__name__)


def render_segments(assets: list[ImageAsset], cfg: ChannelConfig, work_dir: Path, *, seconds_per_image: float | None = None) -> list[Path]:
    v = cfg.video
    dur = seconds_per_image or v.seconds_per_image
    frames_dir = work_dir / "frames"
    clips_dir = work_dir / "clips"
    clips: list[Path] = []
    for i, asset in enumerate(assets):
        png, cap = prepare_frame(asset, frames_dir / f"{i:04d}.png", width=v.width, height=v.height, captions=v.captions)
        seg = Segment(
            index=i, frame_png=png, out_mp4=clips_dir / f"{i:04d}.mp4", duration=dur, fps=v.fps,
            width=v.width, height=v.height, max_zoom=v.max_zoom, fade=v.transition_seconds,
            crf=v.crf, preset=v.preset, caption_png=cap,
        )
        log.info("rendering clip %d/%d: %s", i + 1, len(assets), asset.title[:60])
        clips.append(seg.render())
        png.unlink(missing_ok=True)  # frames are large; don't keep 80 of them
        if cap:
            cap.unlink(missing_ok=True)
    return clips


def render_opener(asset: ImageAsset, cfg: ChannelConfig, work_dir: Path, *, seconds_per_image: float | None = None) -> Path | None:
    """The first clip again, with the intro card and the subscribe prompt.

    A separate file rather than cards baked into clip 0, because the long cuts
    reuse clip 0 later in shuffled passes and the intro belongs only at the
    very start. One extra clip render per run; the timings are clamped to the
    clip so a short test render still works. None when both cards are off."""
    v = cfg.video
    dur = seconds_per_image or v.seconds_per_image
    intro_end = min(v.intro_seconds, dur - v.transition_seconds)
    sub_start, sub_end = v.subscribe_from, min(v.subscribe_until, dur - v.transition_seconds)
    want_intro = v.intro_seconds > 0 and intro_end > 2.0
    want_sub = v.subscribe_until > v.subscribe_from and sub_end - sub_start > 3.0
    if not (want_intro or want_sub):
        return None
    frames_dir = work_dir / "frames"
    png, cap = prepare_frame(asset, frames_dir / "opener.png", width=v.width, height=v.height, captions=v.captions)
    timed: list[TimedOverlay] = []
    cards: list[Path] = []
    if want_intro:
        card = frames_dir / "opener_intro.png"
        intro_card(asset, v.width, v.height, channel_name=cfg.channel.name).save(card, "PNG", compress_level=1)
        timed.append(TimedOverlay(card, start=0.6, end=intro_end, fade=1.2))
        cards.append(card)
    if want_sub:
        card = frames_dir / "opener_subscribe.png"
        subscribe_card(v.width, v.height, channel_name=cfg.channel.name, line=v.subscribe_line).save(card, "PNG", compress_level=1)
        timed.append(TimedOverlay(card, start=sub_start, end=sub_end, fade=1.0))
        cards.append(card)
    seg = Segment(
        index=0, frame_png=png, out_mp4=work_dir / "clips" / "opener.mp4", duration=dur, fps=v.fps,
        width=v.width, height=v.height, max_zoom=v.max_zoom, fade=v.transition_seconds,
        crf=v.crf, preset=v.preset, caption_png=cap, timed=timed,
        caption_from=(intro_end + 0.5) if want_intro and cap else 0.0,
    )
    log.info("rendering opener: intro card %s, subscribe prompt %s", "on" if want_intro else "off", "on" if want_sub else "off")
    out = seg.render()
    for f in [png, cap, *cards]:
        if f:
            f.unlink(missing_ok=True)
    return out


def render_dark_clip(cfg: ChannelConfig, work_dir: Path, *, seconds_per_image: float | None = None) -> Path:
    """One clip of black, encoded through the same Segment path as the image
    clips so the concat demuxer sees identical stream parameters. A sleep cut
    repeats it for hours; black at CRF 20 costs almost nothing on disk."""
    v = cfg.video
    dur = seconds_per_image or v.seconds_per_image
    png = work_dir / "frames" / "dark.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (v.width * 2, v.height * 2), (0, 0, 0)).save(png, "PNG")
    seg = Segment(
        index=0, frame_png=png, out_mp4=work_dir / "clips" / "dark.mp4", duration=dur, fps=v.fps,
        width=v.width, height=v.height, max_zoom=1.0, fade=v.transition_seconds, crf=v.crf, preset=v.preset,
    )
    out = seg.render()
    png.unlink(missing_ok=True)
    return out


def sleep_cut_sequence(order: list[int], clips: list[Path], *, opener: Path | None, dark: Path | None,
                       seconds_per_image: float, dark_after_s: float) -> tuple[list[Path], int]:
    """Clip list for a long cut, and how many of its entries are images.

    With `dark` set, images run until `dark_after_s` (rounded up to a whole
    clip, so the last image finishes its own fade) and black fills the rest of
    the running time; otherwise every entry is an image, as before."""
    seq = [clips[i] for i in order]
    if opener is not None and seq:
        seq[0] = opener
    if dark is None or dark_after_s <= 0:
        return seq, len(seq)
    import math

    n_images = min(len(seq), max(1, math.ceil(dark_after_s / seconds_per_image)))
    return seq[:n_images] + [dark] * (len(seq) - n_images), n_images


def repeat_order(n: int, passes: int, *, seed: str | None = None) -> list[int]:
    """Clip indices for `passes` runs over n clips.

    The first pass keeps the curated order -- that is the one a viewer sees if
    they only watch the start. Later passes are shuffled so a long cut does not
    feel like the same hour stapled together."""
    if n <= 0 or passes <= 0:
        return []
    rng = random.Random(seed)
    out: list[int] = list(range(n))
    for _ in range(passes - 1):
        nxt = list(range(n))
        rng.shuffle(nxt)
        # Don't let the same image straddle a seam.
        if nxt[0] == out[-1] and n > 1:
            nxt[0], nxt[-1] = nxt[-1], nxt[0]
        out.extend(nxt)
    return out


def concat_clips(clips: list[Path], out_path: Path) -> Path:
    listfile = out_path.with_suffix(".txt")
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out_path)])
    listfile.unlink(missing_ok=True)
    return out_path


def mux_audio(video: Path, audio: Path, out_path: Path, *, faststart: bool = True) -> Path:
    """Mux the soundtrack onto the silent cut.

    `faststart` moves the moov atom to the front, which costs a full second
    copy of the output on disk while ffmpeg rewrites it. That is cheap for the
    1h cut and ruinous for the 8h one (~13 GB), so long cuts pass
    faststart=False -- YouTube re-encodes on ingest and never streams the
    uploaded file directly, so the atom position buys nothing there."""
    args = [
        "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
    ]
    if faststart:
        args += ["-movflags", "+faststart"]
    ffmpeg.run(args + [str(out_path)])
    return out_path


def render_video_with_clips(
    assets: list[ImageAsset], cfg: ChannelConfig, *, work_dir: Path,
    audio_path: Path | None, out_path: Path, seconds_per_image: float | None = None,
    opener: bool = False,
) -> tuple[Path, list[Path], Path | None]:
    """Render the video and hand back the individual clips and the opener.

    Callers that want extra-length cuts reuse these clips instead of rendering
    the images a second time. With `opener`, the first clip of the video is
    swapped for its intro/subscribe variant (see render_opener)."""
    clips = render_segments(assets, cfg, work_dir, seconds_per_image=seconds_per_image)
    first = render_opener(assets[0], cfg, work_dir, seconds_per_image=seconds_per_image) if opener and assets else None
    silent = concat_clips(([first] + clips[1:]) if first else clips, work_dir / "silent.mp4")
    if audio_path is None:
        silent.replace(out_path)
        return out_path, clips, first
    out = mux_audio(silent, audio_path, out_path)
    # The silent cut is a full-size copy of the finished video and nothing
    # reads it again; the clips it was stitched from are what the long cuts
    # reuse. Drop it now so peak disk is one video, not two.
    silent.unlink(missing_ok=True)
    return out, clips, first


def render_video(assets: list[ImageAsset], cfg: ChannelConfig, *, work_dir: Path, audio_path: Path | None, out_path: Path, seconds_per_image: float | None = None) -> Path:
    path, _, _ = render_video_with_clips(
        assets, cfg, work_dir=work_dir, audio_path=audio_path,
        out_path=out_path, seconds_per_image=seconds_per_image,
    )
    return path
