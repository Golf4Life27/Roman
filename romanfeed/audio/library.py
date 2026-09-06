"""Music library with licence enforcement.

A single Content ID claim redirects a video's revenue to someone else, so the
pipeline refuses to use any track whose manifest entry lacks an explicit
commercial licence. The manifest (assets/music/manifest.yaml) is the only
place a track can enter the system.

Accepted licence values:
  owned            you own the master and composition (commissioned or self-made)
  generated        produced by a generator whose paid plan grants commercial rights
                   (keep the plan receipt; note provider + plan in `notes`)
  licensed         a subscription/licence service that clears YouTube monetisation
                   (Epidemic Sound, Artlist, etc.; note licence id in `notes`)
  cc0              public domain / CC0
  placeholder      synthesised test tone; never publishable
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from romanfeed.render import ffmpeg

# Delivery format for the library: AAC 192k in .m4a, loudness-normalised to
# TARGET_LUFS so every track sits at the same quiet level under a sleep video.
# Small enough to commit (~9 MB per 6-minute track) so the CI runner has it.
TARGET_LUFS = -18.0
TRANSCODE_SUFFIXES = {".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg", ".m4a", ".aac"}

PUBLISHABLE = {"owned", "generated", "licensed", "cc0"}


@dataclass
class Track:
    id: str
    path: Path
    genre: str
    licence: str
    title: str = ""
    artist: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return self.licence in PUBLISHABLE


class MusicLibrary:
    def __init__(self, manifest_path: str | Path):
        self.manifest_path = Path(manifest_path)
        self.tracks: list[Track] = []
        if self.manifest_path.exists():
            raw = yaml.safe_load(self.manifest_path.read_text()) or {}
            base = self.manifest_path.parent
            for t in raw.get("tracks", []):
                if "licence" not in t:
                    raise ValueError(f"track {t.get('id')} has no licence; refusing to load")
                self.tracks.append(Track(
                    id=t["id"], path=base / t["path"], genre=t.get("genre", "ambient"),
                    licence=t["licence"], title=t.get("title", ""), artist=t.get("artist", ""),
                    notes=t.get("notes", ""), tags=list(t.get("tags", [])),
                ))

    def for_genre(self, genre: str, *, publishable_only: bool = True) -> list[Track]:
        out = [t for t in self.tracks if t.genre == genre and t.path.exists()]
        if publishable_only:
            out = [t for t in out if t.publishable]
        return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "track"


def register_track(
    manifest_path: Path, src: Path, *, licence: str, genre: str = "ambient",
    title: str = "", artist: str = "", notes: str = "", tags: list[str] | None = None,
    normalise: bool = True,
) -> dict:
    """Ingest `src` into <manifest dir>/<genre>/<id>.m4a and append a manifest entry.

    The file is transcoded to AAC and loudness-normalised (two-pass EBU R128
    to TARGET_LUFS). The licence is mandatory and must be publishable; this
    is the only supported way to add music, so every track has provenance."""
    if licence not in PUBLISHABLE:
        raise ValueError(f"licence must be one of {sorted(PUBLISHABLE)}")
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(src)
    raw = yaml.safe_load(manifest_path.read_text()) if manifest_path.exists() else {}
    raw = raw or {}
    tracks = raw.setdefault("tracks", []) or []
    raw["tracks"] = tracks
    base_id = _slug(title or src.stem)
    track_id, n = base_id, 2
    while any(t.get("id") == track_id for t in tracks):
        track_id, n = f"{base_id}-{n}", n + 1
    dest_rel = Path(genre) / f"{track_id}.m4a"
    dest = manifest_path.parent / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        if src.suffix.lower() in TRANSCODE_SUFFIXES and normalise:
            normalise_track(src, dest)
        else:
            shutil.copy2(src, dest)
    entry = {
        "id": track_id, "path": dest_rel.as_posix(), "title": title or src.stem, "artist": artist,
        "genre": genre, "licence": licence, "notes": notes, "tags": list(tags or []),
    }
    tracks.append(entry)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return entry


def normalise_track(src: Path, dest: Path, *, target_lufs: float = TARGET_LUFS) -> Path:
    """Two-pass loudnorm to target_lufs, encoded as AAC 192k stereo 44.1 kHz."""
    import json
    import re as _re
    import subprocess

    measure = subprocess.run(
        [ffmpeg.ffmpeg_path(), "-hide_banner", "-nostats", "-i", str(src),
         "-af", f"loudnorm=I={target_lufs}:TP=-2:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    blocks = _re.findall(r"\{[^{}]*\}", measure.stderr)
    if not blocks:
        raise RuntimeError(f"loudnorm measurement failed for {src}: {measure.stderr[-400:]}")
    st = json.loads(blocks[-1])
    af = (
        f"loudnorm=I={target_lufs}:TP=-2:LRA=11:"
        f"measured_I={st['input_i']}:measured_TP={st['input_tp']}:measured_LRA={st['input_lra']}:"
        f"measured_thresh={st['input_thresh']}:offset={st['target_offset']}:linear=true:print_format=summary"
    )
    ffmpeg.run(["-i", str(src), "-af", af, "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "192k", str(dest)])
    return dest
