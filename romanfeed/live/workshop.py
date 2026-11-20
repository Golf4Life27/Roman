"""Santa's Workshop: a holiday live stream for kids, Nov 21 to Dec 26.

Alex's idea on Nov 20: "a spin off live stream of different looks inside
Santa's workshop while the elves build toys over the next 5-6 weeks", to
"capture a lot of kids on YouTube and shorts".

What plays: eight workshop rooms drawn by our own code (romanfeed/workshop),
each a seamless 16 s loop shown for a few minutes, with a "N sleeps until
Christmas" card between them, under original music. Everything is rendered
ahead of time and published on the repo's release `live.workshop.release_tag`
with a manifest; the server only downloads, joins and streams (stream copy).

Sessions: from each of `starts` (local) for `hours` (< 12 h, so YouTube
saves each one as a replay and its watch time counts), first_day..last_day.
The gap between sessions lets auto-stop end one broadcast before the next
one's video arrives.

  GitHub  ensure_sessions(), called by live/schedule.py: for the session on
          now and the next one, one broadcast each, bound to the stream of
          `key_env` (the nightly key, free while the nightly stream is
          paused): public, made for kids, auto-start, auto-stop.
  server  run_sessions() in a thread of the 24/7 process (live/always.py),
          which systemd keeps up for good; it streams each session to its end.
          The daily sync and the nightly start call nudge(): when no thread
          has written a heartbeat lately, they stop the 24/7 process, which
          systemd starts again on the latest code (about 30 s of the cozy
          stream lost; its broadcast waits through the gap).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from romanfeed.config import ChannelConfig, WorkshopSettings

log = logging.getLogger(__name__)

MANIFEST = "workshop.json"
CHRISTMAS = (12, 25)
HEARTBEAT = "heartbeat.json"
HEARTBEAT_EVERY_S = 60
HEARTBEAT_STALE_S = 10 * 60
MATCH = timedelta(minutes=30)       # a broadcast belongs to the session starting this close
NEXT_AHEAD = timedelta(hours=14)    # the next session gets its broadcast this far ahead
LATE_LIMIT = timedelta(minutes=30)  # a running session this close to its end gets no new broadcast
WORK_TITLE = "Santa's Workshop LIVE 🎅 Watch the Elves Build Toys | Christmas Countdown for Kids"
MARK = "Santa's workshop is open: watch the elves at work, live."


def settings(cfg: ChannelConfig) -> WorkshopSettings | None:
    ws = cfg.live.workshop
    return ws if ws and ws.enabled and ws.first_day and ws.last_day else None


# --- sessions ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Session:
    start: datetime
    end: datetime

    @property
    def seconds(self) -> int:
        return int((self.end - self.start).total_seconds())


def sessions(cfg: ChannelConfig, frm: datetime, to: datetime) -> list[Session]:
    """Sessions that overlap [frm, to), by start."""
    ws = settings(cfg)
    if ws is None:
        return []
    tz = ZoneInfo(cfg.live.timezone)
    first, last = date.fromisoformat(ws.first_day), date.fromisoformat(ws.last_day)
    out = []
    d = max(first, (frm.astimezone(tz) - timedelta(hours=ws.hours + 1)).date())
    while d <= min(last, to.astimezone(tz).date()):
        for hhmm in ws.starts:
            hh, mm = (int(x) for x in hhmm.split(":"))
            start = datetime.combine(d, dtime(hh, mm), tzinfo=tz)
            s = Session(start, start + timedelta(hours=ws.hours))
            if s.end > frm and s.start < to:
                out.append(s)
        d += timedelta(days=1)
    return sorted(out, key=lambda s: s.start)


def current(cfg: ChannelConfig, now: datetime) -> Session | None:
    for s in sessions(cfg, now, now + timedelta(seconds=1)):
        if s.start <= now < s.end:
            return s
    return None


def upcoming(cfg: ChannelConfig, now: datetime, ahead: timedelta = NEXT_AHEAD) -> Session | None:
    later = [s for s in sessions(cfg, now, now + ahead) if s.start > now]
    return later[0] if later else None


def active(cfg: ChannelConfig, now: datetime) -> bool:
    """A session is on or starts within a day: the server should be ready."""
    return bool(sessions(cfg, now, now + timedelta(hours=24)))


def sleeps(day: date) -> int:
    """Sleeps until Christmas from `day` (0 on Christmas Day and after)."""
    xmas = date(day.year, *CHRISTMAS)
    if day > xmas:
        return 0
    return (xmas - day).days


# --- what plays ----------------------------------------------------------------------------------------

def release_url(repo: str, tag: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def _get(url: str, dest: Path, timeout: int = 300) -> Path:
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "romanfeed"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest


def fetch(cfg: ChannelConfig, folder: Path, name: str, *, refresh: bool = False) -> Path:
    path = folder / name
    if refresh or not path.exists() or path.stat().st_size == 0:
        _get(release_url(cfg.cozy.repo, settings(cfg).release_tag, name), path)
    return path


def load_manifest(cfg: ChannelConfig, folder: Path) -> dict:
    """The release's manifest; the copy on disk when GitHub is out of reach."""
    try:
        fetch(cfg, folder, MANIFEST, refresh=True)
    except Exception as exc:
        log.warning("workshop manifest not downloaded (%s); using the copy on disk", exc)
    return json.loads((folder / MANIFEST).read_text())


def day_order(scene_ids: list[str], day: date) -> list[str]:
    """The day's room order: rotated by date so each day opens on another room."""
    ids = sorted(scene_ids)
    k = day.toordinal() % len(ids)
    return ids[k:] + ids[:k]


def playlist(manifest: dict, day: date, repeats: int) -> list[str]:
    """File names in play order for one pass of the reel: a countdown card,
    four rooms, the card again, the other four rooms."""
    card = manifest["cards"].get(str(min(sleeps(day), max(int(k) for k in manifest["cards"]))))
    order = day_order([s["id"] for s in manifest["scenes"]], day)
    files = {s["id"]: s["file"] for s in manifest["scenes"]}
    out: list[str] = []
    for i, sid in enumerate(order):
        if card and i % 4 == 0:
            out.append(card)
        out += [files[sid]] * repeats
    return out


def reel(cfg: ChannelConfig, lib: Path, day: date) -> tuple[Path, Path]:
    """The day's reel (joined by stream copy, cached per day) and the music."""
    from romanfeed.render import ffmpeg

    ws = settings(cfg)
    folder = lib / "workshop"
    manifest = load_manifest(cfg, folder)
    names = playlist(manifest, day, ws.scene_repeats)
    for n in dict.fromkeys(names):
        fetch(cfg, folder, n)
    music = fetch(cfg, folder, manifest["music"])
    dst = folder / f"reel-{day.isoformat()}.mp4"
    if not (dst.exists() and dst.stat().st_size > 0):
        lst = folder / f"reel-{day.isoformat()}.txt"
        lst.write_text("".join(f"file '{(folder / n).as_posix()}'\n" for n in names))
        tmp = folder / f".reel-{day.isoformat()}.part.mp4"
        subprocess.run([ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                        "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(tmp)],
                       check=True)
        tmp.replace(dst)
        lst.unlink(missing_ok=True)
    for old in folder.glob("reel-*.mp4"):
        if old != dst and old.name != f"reel-{(day + timedelta(days=1)).isoformat()}.mp4":
            old.unlink(missing_ok=True)
    return dst, music


def stream_command(reel_path: Path, music: Path, seconds: int, url: str) -> list[str]:
    from romanfeed.render import ffmpeg

    if not 0 < seconds < 12 * 3600:
        raise ValueError(f"refusing a {seconds} s workshop stream: sessions stay under 12 h")
    return [
        ffmpeg.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-re", "-stream_loop", "-1", "-i", str(reel_path),
        "-re", "-stream_loop", "-1", "-i", str(music),
        "-t", str(seconds),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c", "copy",
        "-flvflags", "no_duration_filesize",
        "-f", "flv", url,
    ]


# --- the server side -----------------------------------------------------------------------------------

def _beat(lib: Path, state: str) -> None:
    try:
        p = lib / "workshop" / HEARTBEAT
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pid": os.getpid(), "at": time.time(), "state": state}))
    except OSError:
        pass


def run_sessions(cfg: ChannelConfig, lib: Path, *, stop: threading.Event | None = None,
                 clock=lambda: datetime.now(ZoneInfo("UTC"))) -> None:
    """Stream every session as it comes, for as long as the process lives.
    Never raises: a failure is logged and tried again a minute later."""
    from romanfeed.live.run import INGEST, WATCHDOG_GRACE_S, run_stream

    stop = stop or threading.Event()
    ws = settings(cfg)
    if ws is None:
        return
    key = os.environ.get(ws.key_env, "").strip()
    tz = ZoneInfo(cfg.live.timezone)
    while not stop.is_set():
        try:
            if not key:
                _beat(lib, f"no {ws.key_env} on the server")
                stop.wait(HEARTBEAT_EVERY_S)
                continue
            now = clock().astimezone(tz)
            s = current(cfg, now)
            if s is None:
                nxt = upcoming(cfg, now, timedelta(hours=24))
                _beat(lib, f"waiting for {nxt.start:%a %H:%M}" if nxt else "season over")
                if nxt and (nxt.start - now).total_seconds() < 3 * 3600:
                    reel(cfg, lib, nxt.start.date())          # ready before it starts
                stop.wait(HEARTBEAT_EVERY_S)
                continue
            left = int((s.end - now).total_seconds())
            if left < 60:
                stop.wait(left + 1)
                continue
            reel_path, music = reel(cfg, lib, s.start.date())
            _beat(lib, f"streaming the {s.start:%a %H:%M} session")
            log.info("workshop: streaming the %s session for %d s", s.start.strftime("%a %H:%M"), left)
            done = threading.Event()

            def beat_while():
                while not done.wait(HEARTBEAT_EVERY_S):
                    _beat(lib, f"streaming the {s.start:%a %H:%M} session")
            threading.Thread(target=beat_while, daemon=True).start()
            try:
                began = time.monotonic()
                rc = run_stream(stream_command(reel_path, music, left, f"{INGEST}/{key}"),
                                timeout_s=left + WATCHDOG_GRACE_S, secret=key)
            finally:
                done.set()
            if rc != 0 and time.monotonic() - began < 60:
                log.warning("workshop: ffmpeg stopped at once (exit %d); trying again in 30 s", rc)
                stop.wait(30)
        except Exception:
            log.exception("workshop: session loop failed; trying again in a minute")
            _beat(lib, "error; retrying")
            stop.wait(60)


def start_thread(cfg: ChannelConfig, lib: Path) -> threading.Thread | None:
    if settings(cfg) is None:
        return None
    t = threading.Thread(target=run_sessions, args=(cfg, lib), name="workshop", daemon=True)
    t.start()
    log.info("workshop: session thread started")
    return t


def _always_pids() -> list[int]:
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            args = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        words = [a.decode(errors="replace") for a in args]
        if any(w.endswith("romanfeed") for w in words[:2]) and "live" in words and "always" in words:
            out.append(int(d.name))
    return out


def nudge(cfg: ChannelConfig, lib: Path, now: datetime | None = None) -> str:
    """Stop the 24/7 process when the workshop should be running and no
    session thread has written a heartbeat lately: it runs code from before
    the workshop existed. systemd (Restart=always) starts it again on the
    latest code. Best effort, never raises."""
    try:
        now = now or datetime.now(ZoneInfo(cfg.live.timezone))
        if settings(cfg) is None or not active(cfg, now):
            return "workshop: not in season"
        hb = lib / "workshop" / HEARTBEAT
        try:
            d = json.loads(hb.read_text())
            if time.time() - float(d.get("at", 0)) < HEARTBEAT_STALE_S:
                return f"workshop: running ({d.get('state')})"
        except (OSError, ValueError):
            pass
        pids = _always_pids()
        for pid in pids:
            os.kill(pid, signal.SIGTERM)
        return (f"workshop: no heartbeat; restarted the 24/7 process ({len(pids)}) to load it" if pids
                else "workshop: no heartbeat and no 24/7 process found")
    except Exception as exc:
        log.exception("workshop nudge failed")
        return f"workshop: nudge failed ({exc})"


# --- the GitHub side: broadcasts -----------------------------------------------------------------------

DESCRIPTION = """{mark} Peek inside Santa's workshop, where the elves build, paint, wrap and pack toys for Christmas Eve. The workshop changes rooms every few minutes: the toy assembly line, the gift wrapping room, the toy train test track, the teddy bear corner, the sleigh loading bay, the letters to Santa mail room, a cozy cocoa break and the North Pole at night, with a countdown to Christmas in between.

New sessions all day, every day until Christmas, each one saved to watch again.

Animated by Space Screens with our own original characters and original music.

#christmas #santasworkshop #elves #christmascountdown #kids
"""

TAGS = ["santa's workshop", "santas workshop live", "elves", "elves making toys", "christmas for kids",
        "christmas countdown", "north pole", "christmas live stream", "santa", "christmas animation",
        "toy workshop", "christmas music for kids", "christmas ambience", "sleigh", "reindeer"]
CATEGORY = "1"     # Film & Animation


def title() -> str:
    return WORK_TITLE[:100]


def body(cfg: ChannelConfig, start: datetime) -> dict:
    from romanfeed.live import broadcast as bc

    b = bc.broadcast_body(title=title(), description=DESCRIPTION.format(mark=MARK), start=start)
    b["status"]["selfDeclaredMadeForKids"] = bool(settings(cfg).made_for_kids)
    return b


def extras(cfg: ChannelConfig, work: Path):
    from romanfeed.live import broadcast as bc

    def thumb():
        try:
            folder = work / "workshop"
            m = load_manifest(cfg, folder)
            return fetch(cfg, folder, m["thumbnail"]) if m.get("thumbnail") else None
        except Exception as exc:
            log.warning("workshop thumbnail not fetched (%s)", exc)
            return None
    return bc.Extras(bc.fit_tags(TAGS), thumbnail=thumb, category_id=CATEGORY)


def _stream(yt, stream_id: str) -> dict:
    items = yt.liveStreams().list(part="id,status", id=stream_id).execute().get("items") or []
    return items[0] if items else {}


def _when(b: dict) -> datetime | None:
    when = b.get("snippet", {}).get("scheduledStartTime", "")
    return datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None


def is_ours(b: dict) -> bool:
    return MARK in b.get("snippet", {}).get("description", "")


def ensure_sessions(cfg: ChannelConfig, yt, key: str, now: datetime, work: Path, *,
                    dry_run: bool = False) -> list[str]:
    """One broadcast for the session on now (unless it is nearly over) and one
    for the next session if it starts within NEXT_AHEAD. Lines on what was done."""
    from romanfeed.live import always
    from romanfeed.live import broadcast as bc

    if settings(cfg) is None:
        return []
    want = []
    cur = current(cfg, now)
    if cur and cur.end - now > LATE_LIMIT:
        want.append(cur)
    nxt = upcoming(cfg, now)
    if nxt:
        want.append(nxt)
    if not want:
        return []
    stream_id = bc.find_stream_id(yt, key, work / "workshop-stream.json")
    mine = [b for b in always.bound_broadcasts(yt, stream_id) if is_ours(b)]
    live_now = [b for b in mine if b.get("status", {}).get("lifeCycleStatus") in always.LIVE]
    lines = []
    stream = None
    for s in want:
        have = [b for b in mine if (w := _when(b)) and abs(w - s.start) < MATCH]
        if s is cur and not have and live_now:
            have = live_now           # a session made late is live under an earlier scheduled time
        if have:
            b = have[0]
            state = b.get("status", {}).get("lifeCycleStatus", "?")
            note = ""
            if s is cur and state in ("created", "ready") and not live_now and not dry_run:
                stream = stream or _stream(yt, stream_id)
                note = "; " + always.go_live(yt, b["id"], stream or {})
            lines.append(f"workshop {s.start:%a %H:%M}: {state} https://youtu.be/{b['id']}{note}")
            continue
        start = max(now + timedelta(minutes=2), s.start)
        if dry_run:
            lines.append(f"workshop {s.start:%a %H:%M}: would create '{title()}' on stream {stream_id}")
            continue
        b = body(cfg, start)
        bid = bc.create_broadcast(yt, b)
        try:
            bc.bind(yt, bid, stream_id)
        except Exception:
            yt.liveBroadcasts().delete(id=bid).execute()
            raise
        notes = bc.dress(yt, bid, b["snippet"], extras(cfg, work))
        if s is cur and not live_now:
            stream = stream or _stream(yt, stream_id)
            notes.append(always.go_live(yt, bid, stream or {}))
        lines.append(f"workshop {s.start:%a %H:%M}: created https://youtu.be/{bid} ({', '.join(notes)})")
    # waiting broadcasts of ours for sessions already over never went live: remove them
    for b in mine:
        w = _when(b)
        state = b.get("status", {}).get("lifeCycleStatus")
        if w and state in ("created", "ready") and not any(abs(w - s.start) < MATCH for s in want) \
                and w < now - timedelta(hours=1):
            if not dry_run:
                yt.liveBroadcasts().delete(id=b["id"]).execute()
            lines.append(f"workshop: removed a waiting broadcast that never went live ({b['id']})")
    return lines
