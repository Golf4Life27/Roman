"""How many frames of a scene clip make one clean loop.

Runway's scene clips are 241 frames at 24 fps: 240 frames of motion plus a
closing frame that repeats the first, which a loop must drop (or it holds for
a frame at every seam). One loop is therefore 240 frames, 5 whole 2-second
GOPs, so the live stream can stream-copy it end to end.

Counted by decoding, never from the duration: ffmpeg reports a 241-frame
clip as 10.0 s, which rounded the loop down to 192 frames. Every loop then
lost its last 2 seconds and visibly jumped back at each seam (measured at
15-45x an ordinary frame-to-frame change; with all 240 frames the seam is
no bigger than the scene's own motion).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from romanfeed.render import ffmpeg

FPS = 24
GOP = 48     # 2 s at 24 fps, as library.TRANSCODE_ARGS


def count_frames(path: Path) -> int:
    """Frames in `path` at 24 fps, by decoding it."""
    res = subprocess.run([ffmpeg.ffmpeg_path(), "-hide_banner", "-i", str(path), "-map", "0:v:0",
                          "-vf", f"fps={FPS}", "-f", "null", "-"], capture_output=True, text=True, check=True)
    found = re.findall(r"frame=\s*(\d+)", res.stderr)
    if not found:
        raise RuntimeError(f"could not count the frames of {path}")
    return int(found[-1])


def loop_length(frames: int) -> int:
    """Whole GOPs of a clip's `frames`, leaving out the duplicated closing frame."""
    return max(GOP, (frames - 1) // GOP * GOP)


def loop_frames(path: Path) -> int:
    return loop_length(count_frames(path))
