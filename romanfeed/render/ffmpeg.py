"""Locate and run ffmpeg. Uses the system binary if present, else the static
binary bundled with imageio-ffmpeg so the pipeline runs on any CI runner."""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg  # noqa: WPS433 - optional fallback

    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str], *, quiet: bool = True) -> None:
    cmd = [ffmpeg_path(), "-hide_banner", "-y"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    log.debug("ffmpeg %s", " ".join(args))
    subprocess.run(cmd, check=True)


def probe_duration(path: str) -> float:
    """Duration in seconds via ffmpeg (ffprobe isn't always bundled)."""
    cmd = [ffmpeg_path(), "-hide_banner", "-i", str(path), "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    import re

    m = None
    for m in re.finditer(r"time=(\d+):(\d+):(\d+\.?\d*)", res.stderr):
        pass
    if not m:
        raise RuntimeError(f"could not probe duration of {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)
