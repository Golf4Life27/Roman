"""The server's rolling library of stream-ready videos.

The daily workflow keeps each 1-hour render as a GitHub artifact for only
three days, so the live server has to pull them down while they exist and
keep its own copy. Each one is transcoded once, offline, into a copy YouTube
live ingest is happy with (2 s keyframes, constant 4.5 Mbps); the nightly
run is then a plain stream copy that a 2-vCPU box can do in its sleep.

Layout under the library dir:
  ready/<slug>.mp4    stream-ready copies, played by the nightly run
  ready/<slug>.json   sidecar: duration, lead object, source artifact
  sync-state.json     artifact ids already handled, so nothing downloads twice
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from romanfeed.render.ffmpeg import ffmpeg_path

log = logging.getLogger(__name__)

API = "https://api.github.com"

# The base 1-hour cut is <channel-slug>-YYYY-MM-DD.mp4. Extra-length cuts end
# in -<n>h.mp4 and are excluded from the artifact by the workflow, but are
# filtered here too in case that ever changes: they are 13 GB each.
_DATED = re.compile(r"^(?P<slug>[a-z0-9-]+?-\d{4}-\d{2}-\d{2})\.mp4$")
_LONG_CUT = re.compile(r"-\d+(?:\.\d+)?h\.mp4$")

# A private [TEST] smoke upload is a few minutes long and also lands in a
# render-* artifact. It must never reach a public stream.
MIN_SOURCE_SECONDS = 30 * 60

# Settings for the stream-ready copy. YouTube live wants keyframes every 4 s
# or less (2 s recommended) and a steady bitrate; the renders have ~10 s GOPs
# and CRF rate control. -g 48 at 24 fps is a keyframe every 2 s, and
# sc_threshold 0 stops scene cuts from adding extra ones, so the concat of
# many files stays on the same cadence. 4500k is inside YouTube's 1080p range.
TRANSCODE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast",
    "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
    "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
    "-movflags", "+faststart",
]


@dataclass
class ReadyFile:
    path: Path
    duration_s: float
    lead: str = ""

    @property
    def name(self) -> str:
        return self.path.name


def pick_video(names: list[str]) -> str | None:
    """The base 1-hour mp4 among an artifact's files, or None.

    Matches on the basename so the artifact's directory layout
    (deep-space-ambient/<file>) does not matter."""
    hits = []
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if _LONG_CUT.search(base):
            continue
        if _DATED.match(base):
            hits.append(n)
    return sorted(hits)[-1] if hits else None


def lead_from_title(title: str) -> str:
    """The lead object is the first `|`-separated part of the upload title."""
    head = title.split(" | ", 1)[0].strip()
    return "" if head.startswith("[TEST]") else head


def probe_duration_fast(path: Path) -> float:
    """Container duration from the header, without decoding the file.

    render.ffmpeg.probe_duration decodes everything, which is minutes for an
    hour of video; the header is exact enough for playlist arithmetic."""
    res = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if not m:
        raise RuntimeError(f"could not read duration of {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def transcode_command(src: Path, dst: Path) -> list[str]:
    return [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-i", str(src), *TRANSCODE_ARGS, str(dst)]


def transcode(src: Path, dst: Path) -> None:
    """Write the stream-ready copy via a temp name, so a half-written file
    (killed sync, full disk) is never mistaken for a ready one."""
    tmp = dst.with_name(dst.stem + ".part.mp4")
    try:
        subprocess.run(transcode_command(src, tmp), check=True)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)


def ready_dir(lib: Path) -> Path:
    return lib / "ready"


def load_ready(lib: Path) -> list[ReadyFile]:
    """Every complete stream-ready file, oldest first (by date in the name)."""
    out = []
    for side in sorted(ready_dir(lib).glob("*.json")):
        mp4 = side.with_suffix(".mp4")
        if not mp4.exists():
            continue
        try:
            meta = json.loads(side.read_text())
            out.append(ReadyFile(mp4, float(meta["duration_s"]), meta.get("lead", "")))
        except (ValueError, KeyError) as exc:
            log.warning("skipping %s: unreadable sidecar (%s)", mp4.name, exc)
    return out


def prune(lib: Path, keep: int) -> list[str]:
    """Drop the oldest ready files beyond `keep`. Returns the names removed."""
    files = load_ready(lib)
    gone = []
    for f in files[: max(0, len(files) - keep)]:
        f.path.unlink(missing_ok=True)
        f.path.with_suffix(".json").unlink(missing_ok=True)
        gone.append(f.name)
        log.info("pruned %s", f.name)
    return gone


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


# --- GitHub ------------------------------------------------------------------

def github_session(token: str | None = None):
    """A requests session for the Actions artifacts API.

    Needs a fine-grained PAT with Actions: read on the repo. requests drops
    the Authorization header when the download redirects to blob storage on
    another host, which is what that storage expects."""
    import requests

    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is not set (fine-grained PAT, Actions: read on the repo)")
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return s


def list_render_artifacts(session, repo: str) -> list[dict]:
    """Unexpired render-* artifacts, oldest first."""
    arts: list[dict] = []
    url = f"{API}/repos/{repo}/actions/artifacts"
    for page in range(1, 4):  # 300 is far more than three days of runs
        r = session.get(url, params={"per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        batch = r.json().get("artifacts", [])
        arts += batch
        if len(batch) < 100:
            break
    keep = [a for a in arts if a.get("name", "").startswith("render-") and not a.get("expired")]
    return sorted(keep, key=lambda a: a.get("created_at", ""))


def download(session, artifact: dict, dest: Path) -> Path:
    with session.get(artifact["archive_download_url"], stream=True, timeout=60) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    return dest


def _read_state(lib: Path) -> dict:
    p = lib / "sync-state.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"seen": []}


def _write_state(lib: Path, state: dict) -> None:
    p = lib / "sync-state.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def ingest_zip(zip_path: Path, lib: Path, *, artifact_id: int | None = None, work: Path) -> ReadyFile | None:
    """Pull the 1-hour mp4 out of one artifact zip and make its ready copy.

    Returns None for an artifact with nothing streamable in it (a failed run,
    a smoke test, a day already in the library)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        member = pick_video(names)
        if not member:
            log.info("artifact %s: no 1-hour mp4 in %s", artifact_id, names)
            return None
        base = member.rsplit("/", 1)[-1]
        dst = ready_dir(lib) / base
        if dst.exists():
            log.info("artifact %s: %s already in the library", artifact_id, base)
            return None
        title = ""
        sidecar = member[: -len(".mp4")] + ".upload.json"
        if sidecar in names:
            body = json.loads(zf.read(sidecar))
            title = body.get("snippet", {}).get("title", "")
            if title.startswith("[TEST]") or body.get("status", {}).get("privacyStatus") == "private":
                log.info("artifact %s: %s is a private/test render; skipped", artifact_id, base)
                return None
        raw = work / base
        with zf.open(member) as src, raw.open("wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
    try:
        dur = probe_duration_fast(raw)
        if dur < MIN_SOURCE_SECONDS:
            log.info("artifact %s: %s is %.0f s, too short to stream; skipped", artifact_id, base, dur)
            return None
        ready_dir(lib).mkdir(parents=True, exist_ok=True)
        log.info("transcoding %s for streaming", base)
        transcode(raw, dst)
    finally:
        raw.unlink(missing_ok=True)
    dur = probe_duration_fast(dst)
    lead = lead_from_title(title)
    dst.with_suffix(".json").write_text(json.dumps({
        "file": base, "duration_s": dur, "lead": lead, "artifact_id": artifact_id,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))
    return ReadyFile(dst, dur, lead)


def sync(lib: Path, *, repo: str, keep: int = 12, min_free_gb: float = 5.0, session=None) -> list[ReadyFile]:
    """Download, transcode and file every render artifact not yet handled.

    The disk floor is checked before each download against the artifact's own
    size times three (zip + extracted raw + transcoded copy all exist at
    once); below it the sync stops for today rather than filling the disk the
    nightly stream is reading from."""
    lib.mkdir(parents=True, exist_ok=True)
    ready_dir(lib).mkdir(parents=True, exist_ok=True)
    session = session or github_session()
    state = _read_state(lib)
    seen = set(state.get("seen", []))
    added: list[ReadyFile] = []
    artifacts = list_render_artifacts(session, repo)
    for art in artifacts:
        if art["id"] in seen:
            continue
        need = int(min_free_gb * 1024**3) + 3 * int(art.get("size_in_bytes", 0))
        if free_bytes(lib) < need:
            prune(lib, max(1, keep - 1))
            if free_bytes(lib) < need:
                log.warning("disk floor: %.1f GB free, need %.1f GB for %s; stopping sync",
                            free_bytes(lib) / 1024**3, need / 1024**3, art["name"])
                break
        with tempfile.TemporaryDirectory(dir=lib, prefix="sync-") as tmp:
            work = Path(tmp)
            zpath = download(session, art, work / f"{art['name']}.zip")
            got = ingest_zip(zpath, lib, artifact_id=art["id"], work=work)
        if got:
            added.append(got)
            log.info("added %s (%.0f min, lead %r)", got.name, got.duration_s / 60, got.lead)
        seen.add(art["id"])
        # Forget ids GitHub no longer lists (expired) so the state stays small.
        state["seen"] = sorted(seen & {a["id"] for a in artifacts})
        _write_state(lib, state)
        prune(lib, keep)
    return added
