"""`romanfeed live sync|plan|start`: the nightly stream's three verbs.

The one rule this module exists to keep: a night always ends, and ends under
12 hours, so YouTube archives it as a public VOD. Three independent caps:

  1. ffmpeg's own `-t` -- it stops sending at the planned length
     (min(live.hours, 11.5) h), and YouTube's auto-stop ends the broadcast.
  2. A wall-clock watchdog here: ffmpeg is killed if it is still running
     15 minutes past that (stuck socket, clock trouble, a -re that drifted).
  3. The API: liveBroadcasts.transition(complete) in a `finally`, so the
     broadcast is ended even when ffmpeg crashed or the watchdog fired.

systemd's RuntimeMaxSec=12h on the service is a fourth, outside Python.
Nothing here loops or restarts: one night, one broadcast, then exit.
"""
from __future__ import annotations

import json
import logging
import signal
import subprocess
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from romanfeed.config import ChannelConfig
from romanfeed.live import broadcast as bc
from romanfeed.live.library import ReadyFile, load_ready, sync
from romanfeed.live.playlist import (
    MAX_SECONDS, build_playlist, read_last_first, stream_seconds, write_concat, write_last_first,
)
from romanfeed.render.ffmpeg import ffmpeg_path

log = logging.getLogger(__name__)

WATCHDOG_GRACE_S = 15 * 60
PLACEHOLDER_URL = "rtmp://a.rtmp.youtube.com/live2/<stream-key>"


@dataclass
class NightPlan:
    night: date
    start: datetime
    playlist: list[ReadyFile]
    duration_s: int
    concat_path: Path
    body: dict

    @property
    def lead(self) -> str:
        return self.playlist[0].lead if self.playlist else ""


def library_dir(cfg: ChannelConfig, override: str | None = None) -> Path:
    return Path(override or cfg.live.library_dir)


def plan_night(cfg: ChannelConfig, lib: Path, *, now: datetime | None = None) -> NightPlan:
    """Tonight's playlist and broadcast body. Writes only the local concat list."""
    live = cfg.live
    tz = ZoneInfo(live.timezone)
    start = (now or datetime.now(tz)).astimezone(tz)
    night = start.date()
    files = load_ready(lib)
    playlist = build_playlist(files, live.hours, seed=night.isoformat(),
                              avoid_first=read_last_first(lib / "playlist-state.json"))
    duration = stream_seconds(playlist, live.hours)
    concat = write_concat(playlist, lib / "tonight.ffconcat")
    title = bc.build_title(night, playlist[0].lead, round(duration / 3600, 1))
    desc = bc.build_description(tagline=cfg.channel.tagline, hours=round(duration / 3600, 1),
                                leads=[f.lead for f in playlist], tags=cfg.publish.tags)
    body = bc.broadcast_body(title=title, description=desc, start=start)
    return NightPlan(night, start, playlist, duration, concat, body)


def ffmpeg_command(concat: Path, duration_s: int, rtmp_url: str) -> list[str]:
    """Stream copy of the concat list, real-time paced, hard-stopped by -t.

    The ready files were transcoded to identical parameters, so -c copy
    across the joins is safe and the CPU cost is negligible."""
    if not 0 < duration_s <= MAX_SECONDS:
        raise ValueError(f"refusing a {duration_s} s stream: the cap is {MAX_SECONDS} s")
    return [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-re", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-t", str(duration_s),
        "-c", "copy",
        "-flvflags", "no_duration_filesize",
        "-f", "flv", rtmp_url,
    ]


def _pump(stream, secret: str | None) -> None:
    """ffmpeg names the output URL in its errors; the URL holds the key."""
    for line in stream:
        line = line.rstrip()
        if line:
            log.warning("ffmpeg: %s", bc.mask(line, secret))


def run_stream(cmd: list[str], *, timeout_s: int, secret: str | None = None) -> int:
    """Run ffmpeg under the wall-clock watchdog. Returns its exit code.

    Killed (SIGKILL) on expiry, and on any exception -- including the
    SIGTERM systemd sends at RuntimeMaxSec -- so no ffmpeg outlives us."""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, errors="replace")
    pump = threading.Thread(target=_pump, args=(proc.stderr, secret), daemon=True)
    pump.start()
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.error("watchdog: ffmpeg still running after %d s; killing it", timeout_s)
        proc.kill()
        proc.wait()
        return -9
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        pump.join(timeout=5)


def _sigterm(signum, frame):  # noqa: ARG001 - signal handler signature
    raise SystemExit(128 + signum)


def print_plan(plan: NightPlan) -> None:
    total = sum(f.duration_s for f in plan.playlist)
    print(f"night:    {plan.night.isoformat()} (start {plan.start.isoformat(timespec='minutes')})")
    print(f"length:   {plan.duration_s} s = {plan.duration_s / 3600:.2f} h (cap {MAX_SECONDS} s)")
    print(f"playlist: {len(plan.playlist)} files, {total / 3600:.2f} h before -t")
    for i, f in enumerate(plan.playlist, 1):
        print(f"  {i:2}. {f.name}  {f.duration_s / 60:5.1f} min  {f.lead}")
    print(f"concat:   {plan.concat_path}")


def cmd_sync(cfg: ChannelConfig, lib: Path) -> int:
    live = cfg.live
    added = sync(lib, repo=live.artifact_repo, keep=live.keep_files, min_free_gb=live.min_free_gb)
    files = load_ready(lib)
    print(f"added {len(added)}; library holds {len(files)} files, "
          f"{sum(f.duration_s for f in files) / 3600:.1f} h")
    return 0


def cmd_plan(cfg: ChannelConfig, lib: Path) -> int:
    print_plan(plan_night(cfg, lib))
    return 0


def cmd_start(cfg: ChannelConfig, lib: Path, *, dry_run: bool = False) -> int:
    """One night's broadcast, or a printout of it with --dry-run."""
    if not cfg.live.enabled and not dry_run:
        print("refusing to start: live.enabled is false in the channel config "
              "(switch it on at 100 subscribers or 15 watch hours/day; see docs/LIVE_STREAM.md)")
        return 2
    plan = plan_night(cfg, lib)
    state = lib / "stream.json"
    timeout = plan.duration_s + WATCHDOG_GRACE_S

    if dry_run:
        info = bc.load_stream(state)
        url = info.rtmp_url if info else PLACEHOLDER_URL
        secret = info.stream_name if info else None
        if not cfg.live.enabled:
            print("NOTE: live.enabled is false; this is a preview only, `start` would refuse.")
        print_plan(plan)
        print("broadcast body:")
        print(json.dumps(plan.body, indent=2))
        print(f"watchdog: {timeout} s")
        print("ffmpeg:   " + bc.mask(" ".join(ffmpeg_command(plan.concat_path, plan.duration_s, url)), secret))
        return 0

    from romanfeed.publish import youtube

    signal.signal(signal.SIGTERM, _sigterm)
    yt = youtube.client()
    info = bc.ensure_stream(yt, state)
    cmd = ffmpeg_command(plan.concat_path, plan.duration_s, info.rtmp_url)
    write_last_first(lib / "playlist-state.json", plan.playlist[0].name, plan.night.isoformat())
    broadcast_id = bc.create_broadcast(yt, plan.body)
    rc = 1
    try:
        bc.bind(yt, broadcast_id, info.stream_id)
        log.info("streaming %d s to broadcast %s (watchdog %d s)", plan.duration_s, broadcast_id, timeout)
        rc = run_stream(cmd, timeout_s=timeout, secret=info.stream_name)
        log.info("ffmpeg exited %d", rc)
    finally:
        try:
            bc.complete(yt, broadcast_id)
        except Exception:  # auto-stop is still there; say so loudly and move on
            log.exception("could not complete broadcast %s; relying on YouTube auto-stop", broadcast_id)
            rc = rc or 1
    return 0 if rc == 0 else 1
