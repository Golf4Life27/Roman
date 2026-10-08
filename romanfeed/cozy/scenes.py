"""The cozy scene library and tonight's pick from it.

Scenes live in one GitHub Release (tag `cozy-scenes` by default) on the
public repo: each loop is an asset (`<id>.mp4`, ~6 MB for 10 s of 1080p),
and `scenes.json` lists them with their theme tags. The release is the single
source of truth: the monthly generator adds to it, the weekly upload and the
live server read from it, and anyone can download from it without a token.

scenes.json:
  {"scenes": [{"id": "halloween-observatory", "file": "halloween-observatory.mp4",
               "title": "Halloween Observatory", "themes": ["halloween", "autumn"],
               "created": "2026-10-08", "source": "...", "prompt": "...", "motion": "..."}],
   "spend": {"2026-10": 2000}}

`spend` is Runway API credits used per calendar month (the generator's cap).
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from romanfeed.cozy.calendar import Theme, themes_for

log = logging.getLogger(__name__)

MANIFEST = "scenes.json"


@dataclass
class Scene:
    id: str
    file: str
    title: str
    themes: list[str]
    created: str = ""
    source: str = ""
    prompt: str = ""
    motion: str = ""


@dataclass
class Library:
    scenes: list[Scene] = field(default_factory=list)
    spend: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict) -> "Library":
        fields = Scene.__dataclass_fields__
        return cls([Scene(**{k: v for k, v in s.items() if k in fields}) for s in raw.get("scenes", [])],
                   {k: int(v) for k, v in (raw.get("spend") or {}).items()})

    def to_json(self) -> dict:
        return {"scenes": [asdict(s) for s in self.scenes], "spend": self.spend}

    def tagged(self, key: str) -> list[Scene]:
        return [s for s in self.scenes if key in s.themes]

    def get(self, scene_id: str) -> Scene | None:
        return next((s for s in self.scenes if s.id == scene_id), None)


def load(path: Path) -> Library:
    return Library.from_json(json.loads(path.read_text())) if path.exists() else Library()


def save(lib: Library, path: Path) -> None:
    path.write_text(json.dumps(lib.to_json(), indent=2) + "\n")


def pick(lib: Library, day: date, themes: list[Theme] | None = None, *,
         avoid: str | None = None) -> tuple[Scene, Theme] | None:
    """The scene for `day` and the theme it was picked for, or None.

    Walks the day's themes most-specific first; the first theme with any
    scene wins. Within a theme the scenes rotate by date, so a month of
    Halloween nights cycles through every Halloween scene, and `avoid`
    (last night's) is skipped when there is another to play."""
    for theme in themes or themes_for(day):
        cands = sorted(lib.tagged(theme.key), key=lambda s: s.id)
        if not cands:
            continue
        i = day.toordinal() % len(cands)
        if len(cands) > 1 and cands[i].id == avoid:
            i = (i + 1) % len(cands)
        return cands[i], theme
    return None


def release_url(repo: str, tag: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def _get(url: str, dest: Path, timeout: int = 120) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "romanfeed"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest


def fetch_manifest(repo: str, tag: str, dest: Path) -> Library:
    """Download scenes.json. On failure, keep using the copy already on disk."""
    try:
        _get(release_url(repo, tag, MANIFEST), dest, timeout=30)
    except Exception as exc:
        log.warning("scene manifest not downloaded (%s); using %s", exc, "the local copy" if dest.exists() else "none")
    return load(dest)


def fetch_scene(repo: str, tag: str, scene: Scene, folder: Path) -> Path:
    """The scene's loop on disk, downloaded once."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / scene.file
    if not path.exists() or path.stat().st_size == 0:
        _get(release_url(repo, tag, scene.file), path)
    return path
