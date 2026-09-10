"""Assemble a soundtrack of the target length.

Concatenates tracks (shuffled, looped as needed) with a long fade in/out and
a gentle gain reduction; sleep content should sit well below speech level.
With `crossfade` set, consecutive tracks overlap instead of butting together,
so an hour of audio has no gaps at the joins -- important once cuts run long
enough to cycle the library several times.
If no publishable track exists and placeholders are allowed (dev / dry-run),
synthesises a slow ambient drone with ffmpeg so the pipeline still runs
end-to-end."""
from __future__ import annotations

import logging
import random
from pathlib import Path

from romanfeed.audio.library import MusicLibrary, Track
from romanfeed.render import ffmpeg

log = logging.getLogger(__name__)


def synth_placeholder(out_path: Path, duration: float) -> Track:
    """A soft, detuned two-oscillator drone with slow tremolo. Test-only."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    graph = (
        "sine=frequency=55:sample_rate=44100[a];"
        "sine=frequency=82.5:sample_rate=44100[b];"
        "sine=frequency=110.3:sample_rate=44100[c];"
        "[a][b][c]amix=inputs=3:normalize=1,"
        "lowpass=f=400,tremolo=f=0.1:d=0.35,volume=0.25"
    )
    ffmpeg.run(["-f", "lavfi", "-i", graph, "-t", f"{duration:.2f}", "-c:a", "aac", "-b:a", "128k", str(out_path)])
    return Track(id="placeholder-drone", path=out_path, genre="ambient", licence="placeholder", title="Placeholder drone")


def build_soundtrack(
    library: MusicLibrary,
    *,
    genre: str,
    duration: float,
    out_path: Path,
    fade: float = 6.0,
    gain_db: float = -6.0,
    allow_placeholder: bool = False,
    seed: str | None = None,
    crossfade: float = 0.0,
) -> tuple[Path, list[Track]]:
    tracks = library.for_genre(genre)
    if not tracks:
        if not allow_placeholder:
            raise RuntimeError(
                f"no publishable '{genre}' tracks in {library.manifest_path}. "
                "Add licensed music to the manifest, or set audio.allow_placeholder for dry runs."
            )
        log.warning("no licensed tracks; using synthesised placeholder audio (NOT publishable)")
        t = synth_placeholder(out_path.with_name("placeholder.m4a"), duration)
        tracks = [t]

    # Keep the overlap shorter than the shortest track, or acrossfade would
    # swallow a whole file and silently drop it from the running order.
    shortest = min(ffmpeg.probe_duration(str(t.path)) for t in tracks)
    xf = max(0.0, min(crossfade, shortest / 3.0))

    rng = random.Random(seed)
    order: list[Track] = []
    remaining = duration
    pool = tracks[:]
    while remaining > 0:
        if not pool:
            pool = tracks[:]
        rng.shuffle(pool)
        t = pool.pop()
        order.append(t)
        # Each track after the first gives up `xf` seconds to the overlap.
        remaining -= max(ffmpeg.probe_duration(str(t.path)) - (xf if len(order) > 1 else 0.0), 1.0)

    fade_out = max(duration - fade, 0)
    tail = f"afade=t=in:st=0:d={fade},afade=t=out:st={fade_out:.2f}:d={fade},volume={gain_db}dB"

    if xf > 0 and len(order) > 1:
        args: list[str] = []
        for t in order:
            args += ["-i", str(t.path)]
        chain, prev = [], "[0:a]"
        for i in range(1, len(order)):
            label = f"[x{i}]"
            chain.append(f"{prev}[{i}:a]acrossfade=d={xf:.2f}:c1=tri:c2=tri{label}")
            prev = label
        ffmpeg.run(args + [
            "-filter_complex", ";".join(chain) + f";{prev}{tail}[out]",
            "-map", "[out]", "-t", f"{duration:.2f}",
            "-c:a", "aac", "-b:a", "192k", str(out_path),
        ])
        return out_path, order

    listfile = out_path.with_suffix(".txt")
    listfile.write_text("".join(f"file '{t.path.resolve()}'\n" for t in order))
    ffmpeg.run([
        "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-t", f"{duration:.2f}", "-af", tail,
        "-c:a", "aac", "-b:a", "192k", str(out_path),
    ])
    listfile.unlink(missing_ok=True)
    return out_path, order
