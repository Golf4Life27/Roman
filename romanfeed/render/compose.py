"""Turn a list of selected assets into one finished MP4 with audio."""
from __future__ import annotations

import logging
from pathlib import Path

from romanfeed.config import ChannelConfig
from romanfeed.render import ffmpeg
from romanfeed.render.captions import prepare_frame
from romanfeed.render.kenburns import Segment
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


def concat_clips(clips: list[Path], out_path: Path) -> Path:
    listfile = out_path.with_suffix(".txt")
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out_path)])
    listfile.unlink(missing_ok=True)
    return out_path


def mux_audio(video: Path, audio: Path, out_path: Path) -> Path:
    ffmpeg.run([
        "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ])
    return out_path


def render_video(assets: list[ImageAsset], cfg: ChannelConfig, *, work_dir: Path, audio_path: Path | None, out_path: Path, seconds_per_image: float | None = None) -> Path:
    clips = render_segments(assets, cfg, work_dir, seconds_per_image=seconds_per_image)
    silent = concat_clips(clips, work_dir / "silent.mp4")
    if audio_path is None:
        silent.replace(out_path)
        return out_path
    return mux_audio(silent, audio_path, out_path)
