"""`romanfeed live doctor`: what the server would stream tonight, checked.

A regular night joins the library files with the concat demuxer and `-c
copy`, so every file must share one format: the same video codec, profile,
size, frame rate and pixel format, and the same audio codec, sample rate and
channels. One odd file can leave YouTube at "Preparing stream". This prints
each file's format, flags any that differ from the rest, and shows the last
night's stream.json and the prepared cozy loop.

Read-only, and typed on a console that cannot type shifted characters:
`sudo -u romanfeed /opt/romanfeed/venv/bin/romanfeed live doctor --library-dir /var/lib/romanfeed/live`
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from romanfeed.live.library import ready_dir
from romanfeed.render.ffmpeg import ffmpeg_path


@dataclass
class Probe:
    name: str
    video: str = ""      # "h264 (High) yuv420p 1920x1080 24 fps"
    audio: str = ""      # "aac 44100 Hz stereo"
    duration: str = ""
    error: str = ""

    @property
    def signature(self) -> tuple[str, str]:
        return self.video, self.audio


_VIDEO = re.compile(r"Stream #\S+.*?Video: (\w+)(?: \(([^)]*)\))?.*?, (\w+)(?:\([^)]*\))?, (\d+x\d+)[^,]*,"
                    r".*?([\d.]+) fps")
_AUDIO = re.compile(r"Stream #\S+.*?Audio: (\w+).*?, (\d+) Hz, ([\w.()]+)")
_DUR = re.compile(r"Duration: ([\d:.]+)")


def parse(name: str, text: str) -> Probe:
    """A Probe from `ffmpeg -i` output (ffprobe is not always installed)."""
    p = Probe(name)
    if m := _VIDEO.search(text):
        codec, profile, pix, size, fps = m.groups()
        p.video = f"{codec}{f' ({profile})' if profile else ''} {pix} {size} {float(fps):g} fps"
    if m := _AUDIO.search(text):
        p.audio = f"{m.group(1)} {m.group(2)} Hz {m.group(3)}"
    if m := _DUR.search(text):
        p.duration = m.group(1)
    if not p.video:
        p.error = "no video stream found" if "Invalid data" not in text else "unreadable file"
    return p


def probe(path: Path) -> Probe:
    r = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       timeout=60)
    return parse(path.name, r.stderr)


def odd_ones(probes: list[Probe]) -> tuple[tuple[str, str] | None, list[Probe]]:
    """(the majority format, the files that differ from it or failed)."""
    good = [p for p in probes if not p.error]
    if not good:
        return None, probes
    common = Counter(p.signature for p in good).most_common(1)[0][0]
    return common, [p for p in probes if p.error or p.signature != common]


def run(lib: Path) -> int:
    lines = [f"library: {lib}"]
    state = lib / "stream.json"
    if state.exists():
        lines.append(f"last stream.json: {state.read_text().strip()[:400]}")
    else:
        lines.append("last stream.json: none")
    files = sorted(ready_dir(lib).glob("*.mp4"))
    probes = [probe(f) for f in files]
    common, odd = odd_ones(probes)
    lines.append(f"\nready files: {len(files)}")
    if common:
        lines.append(f"common format: video {common[0]} | audio {common[1] or 'NONE'}")
    for p in probes:
        flag = "ODD " if p in odd else "ok  "
        lines.append(f"{flag}{p.name}  {p.duration}  {p.error or p.video + ' | ' + (p.audio or 'NO AUDIO')}")
    issues = []
    if odd:
        issues.append(f"{len(odd)} file(s) differ from the rest; a copy-joined stream breaks at them")
    if common and not common[0].startswith("h264"):
        issues.append("video is not H.264")
    if common and not common[1].startswith("aac"):
        issues.append("audio is not AAC")
    loops = sorted((lib / "scenes").glob("**/*.mp4")) if (lib / "scenes").exists() else []
    if loops:
        lines.append("\nscene files:")
        for f in loops[-4:]:
            p = probe(f)
            lines.append(f"     {f.relative_to(lib)}  {p.duration}  {p.error or p.video + ' | ' + (p.audio or 'no audio')}")
    concat = lib / "tonight.ffconcat"
    if concat.exists():
        listed = [ln.split("'")[1] for ln in concat.read_text().splitlines() if ln.startswith("file '")]
        missing = [n for n in listed if not Path(n).exists()]
        lines.append(f"\ntonight.ffconcat: {len(listed)} entries, {len(missing)} missing")
        if missing:
            issues.append(f"tonight.ffconcat lists {len(missing)} missing file(s)")
    print("\n".join(lines))
    print("\n" + ("PROBLEMS:\n- " + "\n- ".join(issues) if issues else "no format problems found"))
    return 1 if issues else 0

