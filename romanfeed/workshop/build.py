"""`python -m romanfeed.workshop.build OUT`: render everything the workshop
stream plays (rooms, countdown cards, music, thumbnail) and its manifest.

Run by .github/workflows/workshop-scenes.yml, which attaches OUT to the
workshop-scenes release that the server downloads from."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

CARDS = range(0, 36)          # 35 sleeps on Nov 20 down to Christmas Day
MUSIC = "workshop-music.m4a"
SEEDS = [f"santas-workshop-{i}" for i in range(6)]


def _job(out: str, kind: str, arg) -> str:
    from romanfeed.workshop import art, music

    out_dir = Path(out)
    if kind == "scene":
        return art.render(art.SCENES[arg].draw, out_dir / f"scene-{arg}.mp4").name
    if kind == "card":
        return art.render(art.countdown(arg), out_dir / f"countdown-{arg:02d}.mp4", seconds=8).name
    if kind == "music":
        music.compose(out_dir / MUSIC, SEEDS)
        return MUSIC
    return art.thumbnail(out_dir / "thumbnail.jpg").name


def build(out_dir: Path, workers: int = 4) -> dict:
    from romanfeed.workshop import art

    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [("music", 0), ("thumb", 0)] + [("scene", s) for s in art.SCENES] + [("card", n) for n in CARDS]
    with ProcessPoolExecutor(workers) as ex:
        for name in ex.map(_job, [str(out_dir)] * len(jobs), *zip(*jobs)):
            print("rendered", name, flush=True)
    manifest = {
        "scenes": [{"id": s.id, "title": s.title, "short_line": s.short_line, "file": f"scene-{s.id}.mp4"}
                   for s in art.SCENES.values()],
        "cards": {str(n): f"countdown-{n:02d}.mp4" for n in CARDS},
        "music": MUSIC,
        "thumbnail": "thumbnail.jpg",
    }
    (out_dir / "workshop.json").write_text(json.dumps(manifest, indent=1))
    return manifest


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "workshop-out"))
