"""A scene clip turned into a loop with no visible seam.

Runway is asked for a 10 s clip that starts and ends on the same still, and
it meets that by cheating at both ends: the first second "starts up" from
the still (steam not yet rising, the sky still settling) and the last half
second rushes everything back to it (steam vanishes, a galaxy jumps). Looping
the whole clip shows both, every 10 seconds.

So a loop is cut from the steady middle instead: frames [a, b), chosen where
the end looks most like the start, joined by a 1-second dissolve. The loop
plays f[a+F .. b-1] and dissolves its last F frames into f[a .. a+F-1], which
lead straight back into f[a+F]. Its length is whole 2-second GOPs (6 or 8 s),
so the live stream can stream-copy it end to end.

Frames are counted by decoding, never from the duration: ffmpeg reports a
241-frame clip as 10.0 s, which once cost every loop its last 2 seconds.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from romanfeed.render import ffmpeg

FPS = 24
GOP = 48              # 2 s at 24 fps, as library.TRANSCODE_ARGS
FADE = 24             # the 1-second dissolve at the seam
LENGTHS = (192, 144)  # loop lengths tried, longest first (8 s, 6 s)
SMALL = (96, 54)      # frame size for choosing the cut


def count_frames(path: Path) -> int:
    """Frames in `path` at 24 fps, by decoding it."""
    res = subprocess.run([ffmpeg.ffmpeg_path(), "-hide_banner", "-i", str(path), "-map", "0:v:0",
                          "-vf", f"fps={FPS}", "-f", "null", "-"], capture_output=True, text=True, check=True)
    found = re.findall(r"frame=\s*(\d+)", res.stderr)
    if not found:
        raise RuntimeError(f"could not count the frames of {path}")
    return int(found[-1])


def small_frames(path: Path) -> np.ndarray:
    w, h = SMALL
    raw = subprocess.run([ffmpeg.ffmpeg_path(), "-v", "error", "-i", str(path), "-vf",
                          f"fps={FPS},scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)


def choose_cut(frames: np.ndarray, *, fade: int = FADE, lengths=LENGTHS) -> tuple[int, int]:
    """(a, length): the loop is f[a+fade .. a+fade+length-1], dissolving into
    f[a .. a+fade-1]. Picks the window whose end best matches its start,
    skipping the start-up second and the rush back in the last half second;
    a longer loop wins unless it matches clearly worse."""
    usable = len(frames) - 1                       # the closing frame repeats the first
    first, last = FPS, usable - FPS // 2           # steady middle only
    best: dict[int, tuple[float, int]] = {}
    for length in lengths:
        for a in range(first, last - length - fade + 1):
            b = a + fade + length                  # end, exclusive
            cost = float(np.abs(frames[b - fade:b] - frames[a:a + fade]).mean())
            if length not in best or cost < best[length][0]:
                best[length] = (cost, a)
    if not best:                                    # a short clip: no steady middle to spare
        return 0, max(GOP, (usable - fade) // GOP * GOP)
    top = min(c for c, _ in best.values())
    for length in lengths:                          # longest first
        if length in best and best[length][0] <= top * 1.15:
            return best[length][1], length
    raise AssertionError("unreachable")


def smooth_loop(src: Path, dst: Path, *, fade: int = FADE) -> tuple[Path, int]:
    """`src` as a seamless 1920x1080, 24 fps loop (cached). Returns (path, frames)."""
    a, length = choose_cut(small_frames(src), fade=fade)
    if dst.exists() and dst.stat().st_size > 0:
        return dst, length

    def part(i: int, trim: str, label: str) -> str:
        return (f"[{i}:v]fps={FPS},scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                f"trim={trim},setpts=PTS-STARTPTS[{label}]")

    # Two inputs, not a split: a split stalls behind the later trim.
    graph = ";".join([
        part(0, f"start_frame={a + fade}:end_frame={a + fade + length}", "body"),
        part(1, f"start_frame={a}:end_frame={a + fade}", "start"),
        f"[body][start]xfade=transition=fade:duration={fade / FPS:.6f}:offset={(length - fade) / FPS:.6f},"
        "format=yuv420p[v]",
    ])
    tmp = dst.with_suffix(".part.mp4")
    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(["-i", str(src), "-i", str(src), "-filter_complex", graph, "-map", "[v]", "-an",
                "-frames:v", str(length), "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "14",
                "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(tmp)])
    tmp.replace(dst)
    return dst, length
