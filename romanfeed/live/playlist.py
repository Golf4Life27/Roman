"""Tonight's running order.

A date-seeded shuffle, so a night's order is reproducible (plan and start
agree, and a rerun the same evening plays the same thing) but differs night
to night. The opening video is what the archived VOD's first frames and
auto-generated preview show, so it should not be the same two nights
running. Repeats only happen when the library is shorter than the target.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from romanfeed.config import LIVE_MAX_HOURS
from romanfeed.live.library import ReadyFile

MAX_SECONDS = int(LIVE_MAX_HOURS * 3600)  # 41400


def target_seconds(hours: float) -> int:
    """The night's length, clamped to the hard ceiling whatever was asked."""
    return int(min(max(hours, 0.0), LIVE_MAX_HOURS) * 3600)


def build_playlist(files: list[ReadyFile], hours: float, *, seed: str, avoid_first: str | None = None) -> list[ReadyFile]:
    """Files in play order, totalling at least the target (or the hard cap).

    Overshoot is less than one file: the last file starts before the target
    and ffmpeg's -t cuts it off. A short library is cycled, reshuffling each
    pass and never putting the same file back to back.
    """
    files = [f for f in files if f.duration_s > 0]
    if not files:
        raise ValueError("the live library is empty; run `romanfeed live sync` first")
    target = target_seconds(hours)
    rng = random.Random(seed)

    def shuffled() -> list[ReadyFile]:
        order = list(files)
        rng.shuffle(order)
        return order

    first = shuffled()
    if avoid_first and len(first) > 1 and first[0].name == avoid_first:
        first.append(first.pop(0))
    out: list[ReadyFile] = []
    total = 0.0
    queue = first
    while total < target:
        if not queue:
            queue = shuffled()
            if len(queue) > 1 and queue[0].name == out[-1].name:
                queue.append(queue.pop(0))
        f = queue.pop(0)
        out.append(f)
        total += f.duration_s
    return out


def stream_seconds(playlist: list[ReadyFile], hours: float) -> int:
    """What -t gets: the target, or less if the playlist itself is shorter."""
    return min(int(sum(f.duration_s for f in playlist)), target_seconds(hours))


def write_concat(playlist: list[ReadyFile], path: Path) -> Path:
    """An ffconcat list for `-f concat -safe 0`, absolute paths, quotes escaped."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    for f in playlist:
        p = str(f.path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{p}'")
    path.write_text("\n".join(lines) + "\n")
    return path


def read_last_first(state_path: Path) -> str | None:
    try:
        return json.loads(state_path.read_text()).get("last_first")
    except (OSError, ValueError):
        return None


def write_last_first(state_path: Path, name: str, night: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_first": name, "night": night}, indent=2))
