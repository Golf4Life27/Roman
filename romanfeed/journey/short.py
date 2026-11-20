"""A 20-30 second vertical cut of a journey, for Shorts.

Built for the first three seconds: it opens already flying (no fade, no
title card, camera in motion on frame one), lands on a bright region by
second 3, drifts, and ends on a final push into the brightest part of the
picture. Rendered from the same prepared scene as the long video.

The journey job keeps one as a by-product; the Shorts run posts its own in
the fly-into slots (romanfeed/fly_into.py), with cards burned on through
`overlay`."""
from __future__ import annotations

import logging
from pathlib import Path

from romanfeed.journey.camera import Frame, Leg, Pose, short_legs
from romanfeed.journey.layers import Target
from romanfeed.journey.render import Scene, encode_frames

log = logging.getLogger(__name__)

PAYOFF_BY = 3.0
REVEAL_FOR = 5.0


def short_pose(legs: list[Leg], t: float) -> Pose:
    """The camera `t` seconds into the Short (legs run back to back)."""
    for leg in legs:
        if t < leg.start + leg.duration or leg is legs[-1]:
            return leg.pose(min(max(0.0, t - leg.start), leg.duration))
    raise ValueError("no legs")


def plan_short(scene: Scene, targets: list[Target], *, seconds: float = 24.0, width: int = 1080,
               height: int = 1920) -> list[Leg]:
    if not 20 <= seconds <= 30:
        raise ValueError("a journey Short runs 20-30 seconds")
    return short_legs(seconds, Frame(scene.width, scene.height, width, height), targets,
                      payoff_by=PAYOFF_BY, reveal_for=REVEAL_FOR)


def render_journey_short(scene: Scene, targets: list[Target], out: Path, *, seconds: float = 24.0,
                         width: int = 1080, height: int = 1920, fps: int = 30, audio: Path | None = None,
                         crf: int = 20, preset: str = "veryfast", overlay=None) -> Path:
    """Render the vertical cut to `out` (H.264; with `audio` muxed on if given).
    `overlay(seconds, frame) -> frame` burns cards on (render.alpha_overlay)."""
    legs = plan_short(scene, targets, seconds=seconds, width=width, height=height)
    n = int(round(seconds * fps))
    frames = (scene.frame(short_pose(legs, i / fps), width, height) for i in range(n))
    if overlay is not None:
        frames = (overlay(i / fps, fr) for i, fr in enumerate(frames))
    if audio is None:
        return encode_frames(frames, out, width, height, fps, crf=crf, preset=preset)
    silent = out.with_name(out.stem + ".silent.mp4")
    encode_frames(frames, silent, width, height, fps, crf=crf, preset=preset)
    from romanfeed.render import ffmpeg

    ffmpeg.run(["-i", str(silent), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-af", f"afade=t=out:st={max(0.0, seconds - 1.5):.2f}:d=1.5",
                "-t", f"{seconds:.3f}", "-movflags", "+faststart", str(out)])
    silent.unlink(missing_ok=True)
    return out
