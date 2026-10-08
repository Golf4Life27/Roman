"""Scene nights: the live stream plays one cozy animated loop all night.

Instead of tonight's playlist of rendered space videos, a scene night loops a
10-second seamless clip (romanfeed.cozy.scenes) under freshly composed music.
Everything is prepared offline so the stream itself stays a stream copy:

  loop   the scene re-encoded once: trimmed to a whole number of 2-second
         GOPs (the clip's first and last frames are the same image, so the
         last one is dropped -- otherwise it would hold for a frame at every
         seam), live-ingest settings, a small channel line burned in.
  music  a composed piece, music_minutes long, seeded by the night.

ffmpeg then loops both inputs with -stream_loop -1 and -c copy until the
same -t cap as every other night. CPU: next to nothing.

Which nights: `live.scenes` in the channel config. "off" never; "always"
every night a scene exists for; "alternate" every other night, so the two
kinds of night can be compared on views -- except a theme's peak night
(Halloween, Christmas Eve, a meteor peak, a launch), which is always a scene.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from romanfeed.config import ChannelConfig
from romanfeed.cozy import scenes as sc
from romanfeed.cozy.calendar import Theme, fetch_launches, themes_for
from romanfeed.live.playlist import MAX_SECONDS
from romanfeed.render import ffmpeg
from romanfeed.render.captions import _font

log = logging.getLogger(__name__)

FPS = 24
GOP = 48             # 2 s at 24 fps, as library.TRANSCODE_ARGS
LOOP_VERSION = 2     # bump when the loop encode or overlay changes, to rebuild cached loops


@dataclass
class SceneNight:
    night: date
    scene: sc.Scene
    theme: Theme
    loop: Path
    music: Path


def wants_scene(mode: str, night: date, theme: Theme) -> bool:
    if mode == "off":
        return False
    if mode == "always" or theme.peak:
        return True
    return night.toordinal() % 2 == 0  # "alternate"


def choose(cfg: ChannelConfig, lib_dir: Path, night: date) -> tuple[sc.Scene, Theme] | None:
    """Tonight's scene and why, or None for a regular night."""
    mode = cfg.live.scenes
    if mode == "off":
        return None
    library = sc.fetch_manifest(cfg.cozy.repo, cfg.cozy.release_tag, lib_dir / "scenes" / sc.MANIFEST)
    if not library.scenes:
        return None
    themes = themes_for(night, launches=fetch_launches(cache=lib_dir / "launches.json"))
    last = _read_state(lib_dir).get("scene")
    picked = sc.pick(library, night, themes, avoid=last)
    if picked is None or not wants_scene(mode, night, picked[1]):
        return None
    return picked


def brand_png(path: Path, *, width: int, height: int, text: str) -> Path:
    """A small, quiet line bottom-left: the channel, and why to subscribe."""
    s = height / 1080
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _font(int(30 * s), bold=True)
    x, y = int(40 * s), height - int(78 * s)
    tw = d.textlength(text, font=font)
    d.rounded_rectangle((x - int(16 * s), y - int(10 * s), x + tw + int(16 * s), y + int(46 * s)),
                        radius=int(14 * s), fill=(8, 10, 22, 120))
    d.text((x, y), text, font=font, fill=(255, 255, 255, 200))
    layer.save(path)
    return path


def loop_command(src: Path, overlay: Path, dst: Path, *, frames: int) -> list[str]:
    return [
        "-i", str(src), "-i", str(overlay),
        "-filter_complex",
        f"[0:v]fps={FPS},scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
        f"trim=end_frame={frames},setpts=PTS-STARTPTS[v];[v][1:v]overlay=0:0,format=yuv420p[out]",
        # -r pins the output: the overlay's still-image input would otherwise
        # make the mp4 muxer pad to 25 fps with duplicated frames (a stutter).
        "-map", "[out]", "-an", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium",
        "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
        "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0",
        "-movflags", "+faststart", str(dst),
    ]


def loop_frames(seconds: float) -> int:
    """Whole GOPs that fit, leaving out the duplicated closing frame."""
    usable = int(round(seconds * FPS)) - 1
    return max(GOP, usable // GOP * GOP)


def prepare_loop(scene_file: Path, out_dir: Path, *, channel_line: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{scene_file.stem}.v{LOOP_VERSION}.mp4"
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    overlay = brand_png(out_dir / "brand.png", width=1920, height=1080, text=channel_line)
    frames = loop_frames(ffmpeg.probe_duration(str(scene_file)))
    tmp = dst.with_suffix(".part.mp4")
    ffmpeg.run(loop_command(scene_file, overlay, tmp, frames=frames))
    tmp.replace(dst)
    return dst


def prepare_music(out_dir: Path, night: date, minutes: int) -> Path:
    from romanfeed.audio.composer import compose_piece

    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"music-{night.isoformat()}.m4a"
    if not (dst.exists() and dst.stat().st_size > 0):
        compose_piece(dst, seconds=minutes * 60, seed=f"live-{night.isoformat()}")
    for old in out_dir.glob("music-*.m4a"):  # one night's music on disk at a time
        if old != dst:
            old.unlink(missing_ok=True)
    return dst


def prepare(cfg: ChannelConfig, lib_dir: Path, night: date) -> SceneNight | None:
    """Everything a scene night needs, on disk. None means a regular night.

    Idempotent and cached: the 15:00 sync does the work and the 21:00 start
    finds it done. Any failure here means a regular night, never no night."""
    try:
        picked = choose(cfg, lib_dir, night)
        if picked is None:
            return None
        scene, theme = picked
        folder = lib_dir / "scenes"
        src = sc.fetch_scene(cfg.cozy.repo, cfg.cozy.release_tag, scene, folder)
        sub = cfg.video.subscribe_line
        line = f"{cfg.channel.name}  ·  {sub}" if sub else cfg.channel.name
        loop = prepare_loop(src, folder / "ready", channel_line=line)
        music = prepare_music(folder / "music", night, cfg.live.music_minutes)
        return SceneNight(night, scene, theme, loop, music)
    except Exception:
        log.exception("scene night not prepared; tonight is a regular night")
        return None


def stream_command(night: SceneNight, duration_s: int, rtmp_url: str) -> list[str]:
    """Both inputs looped forever and paced in real time; -t ends the night."""
    if not 0 < duration_s <= MAX_SECONDS:
        raise ValueError(f"refusing a {duration_s} s stream: the cap is {MAX_SECONDS} s")
    return [
        ffmpeg.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-re", "-stream_loop", "-1", "-i", str(night.loop),
        "-re", "-stream_loop", "-1", "-i", str(night.music),
        "-t", str(duration_s),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c", "copy",
        "-flvflags", "no_duration_filesize",
        "-f", "flv", rtmp_url,
    ]


def _state_path(lib_dir: Path) -> Path:
    return lib_dir / "scene-state.json"


def _read_state(lib_dir: Path) -> dict:
    try:
        return json.loads(_state_path(lib_dir).read_text())
    except (OSError, ValueError):
        return {}


def record_played(lib_dir: Path, night: SceneNight) -> None:
    _state_path(lib_dir).write_text(json.dumps({"night": night.night.isoformat(), "scene": night.scene.id,
                                                "theme": night.theme.key}))
