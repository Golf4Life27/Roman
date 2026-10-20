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
from romanfeed.live import scene as scene_mod
from romanfeed.live.library import ReadyFile, load_ready, sync
from romanfeed.live.playlist import (
    MAX_SECONDS, build_playlist, read_last_first, stream_seconds, write_concat, write_last_first,
)
from romanfeed.render.ffmpeg import ffmpeg_path

log = logging.getLogger(__name__)

WATCHDOG_GRACE_S = 15 * 60
PLACEHOLDER_URL = "rtmp://a.rtmp.youtube.com/live2/<stream-key>"
INGEST = "rtmp://a.rtmp.youtube.com/live2"


def studio_key() -> str | None:
    """The channel's persistent stream key from Studio, if configured.

    With it, no YouTube API call is made at all: Studio's default stream,
    with Auto-start and Auto-stop on, goes live when ffmpeg connects and ends
    (and saves as a video) when ffmpeg stops. That avoids liveBroadcasts,
    which needs youtube.force-ssl -- gated behind Google's app review."""
    import os

    return os.environ.get("YOUTUBE_STREAM_KEY", "").strip() or None


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


def tonight(cfg: ChannelConfig) -> date:
    return datetime.now(ZoneInfo(cfg.live.timezone)).date()


def print_scene(night: scene_mod.SceneNight | None) -> None:
    if night is None:
        print("scene:    none tonight (regular night)")
        return
    peak = ", peak night" if night.theme.peak else ""
    print(f"scene:    {night.scene.title} ({night.scene.id}) for {night.theme.label}{peak}")
    print(f"loop:     {night.loop}")
    print(f"music:    {night.music}")


def _themes_fn(lib: Path):
    from romanfeed.cozy.calendar import fetch_launches, themes_for

    launches = fetch_launches(cache=lib / "launches.json")
    return lambda d: themes_for(d, launches=launches)


def prepare_lanes(cfg: ChannelConfig, lib: Path, now: datetime) -> None:
    """Encode the next day's lane loops ahead of time (cached per scene), so a
    lane's start only composes its music. Never fails the sync."""
    from datetime import timedelta

    from romanfeed.cozy import scenes as sc
    from romanfeed.live import lanes

    if not cfg.live.lanes:
        return
    try:
        folder = lib / "scenes"
        library = lanes.library_for(cfg, folder)
        themes_fn = _themes_fn(lib)
        for s in lanes.sessions(cfg, now, now + timedelta(hours=24)):
            scene, _ = lanes.pick(cfg, library, s, themes_fn=themes_fn)
            src = sc.fetch_scene(cfg.cozy.repo, cfg.cozy.release_tag, scene, folder)
            scene_mod.prepare_loop(src, folder / "ready", channel_line=channel_line(cfg))
            print(f"lane {s.lane} {s.start:%a %H:%M}: {scene.title}")
    except Exception:
        log.exception("could not prepare the lanes' loops; each lane will at its start")


def channel_line(cfg: ChannelConfig) -> str:
    sub = cfg.video.subscribe_line
    return f"{cfg.channel.name}  ·  {sub}" if sub else cfg.channel.name


def cmd_sync(cfg: ChannelConfig, lib: Path) -> int:
    live = cfg.live
    added = sync(lib, repo=live.artifact_repo, keep=live.keep_files, min_free_gb=live.min_free_gb)
    files = load_ready(lib)
    print(f"added {len(added)}; library holds {len(files)} files, "
          f"{sum(f.duration_s for f in files) / 3600:.1f} h")
    # Tonight's scene, if it is a scene night: download, encode, compose now,
    # so 21:00 finds it ready.
    print_scene(scene_mod.prepare(cfg, lib, tonight(cfg)))
    prepare_lanes(cfg, lib, datetime.now(ZoneInfo(cfg.live.timezone)))
    return 0


def cmd_plan(cfg: ChannelConfig, lib: Path) -> int:
    print_scene(scene_mod.prepare(cfg, lib, tonight(cfg)))
    print_plan(plan_night(cfg, lib))
    return 0


SERVER_TOKEN = Path("/etc/romanfeed/youtube.token.json")   # where deploy/live/setup.sh installs it


def server_token() -> Path:
    import os

    return Path(os.environ.get("YOUTUBE_TOKEN_PATH") or SERVER_TOKEN)


def night_body(cfg: ChannelConfig, plan: NightPlan, scene) -> dict:
    """Tonight's broadcast body: the space-video one, or the cozy room's."""
    if scene is None:
        return plan.body
    return bc.scene_body(scene=scene.scene.title, theme=scene.theme.label, theme_key=scene.theme.key,
                         hours=round(plan.duration_s / 3600, 1),
                         start=plan.start, tagline=cfg.channel.tagline, tags=cfg.publish.tags)


def server_youtube():
    """A YouTube client on the server's token (youtube.force-ssl), or None."""
    from romanfeed.publish import youtube

    path = server_token()
    if not path.exists():
        return None
    return youtube.client(youtube.stored_credentials(path))


def open_broadcast(cfg: ChannelConfig, lib: Path, plan: NightPlan, scene, key: str):
    """Create tonight's broadcast (auto-start, auto-stop) on Studio's stream.

    YouTube only shows a stream that arrives into a broadcast, and Studio
    makes one only while its Stream page is open. Returns (client, id), or
    (None, None) when there is no token or the API refuses: the night then
    streams as before, so a YouTube hiccup never costs the stream itself."""
    try:
        yt = server_youtube()
    except (Exception, SystemExit) as exc:  # stored_credentials exits on a missing file
        log.warning("YouTube token unusable (%s); streaming without creating a broadcast", exc)
        return None, None
    if yt is None:
        log.warning("no YouTube token at %s; streaming without creating a broadcast "
                    "(it shows on YouTube only if Studio's Stream page has one ready)", server_token())
        return None, None
    broadcast_id = None
    try:
        stream_id = bc.find_stream_id(yt, key, lib / "studio-stream.json")
        broadcast_id = bc.create_broadcast(yt, night_body(cfg, plan, scene))
        bc.bind(yt, broadcast_id, stream_id)
        return yt, broadcast_id
    except Exception:
        log.exception("could not set up tonight's broadcast; streaming without one")
        if broadcast_id:
            try:
                yt.liveBroadcasts().delete(id=broadcast_id).execute()
            except Exception:
                log.warning("could not delete the half-made broadcast %s", broadcast_id)
        return None, None


def cmd_start(cfg: ChannelConfig, lib: Path, *, dry_run: bool = False) -> int:
    """One night's broadcast, or a printout of it with --dry-run."""
    if not cfg.live.enabled and not dry_run:
        print("refusing to start: live.enabled is false in the channel config "
              "(switch it on at 100 subscribers or 15 watch hours/day; see docs/LIVE_STREAM.md)")
        return 2
    plan = plan_night(cfg, lib)
    state = lib / "stream.json"
    timeout = plan.duration_s + WATCHDOG_GRACE_S
    scene = scene_mod.prepare(cfg, lib, plan.night)

    key = studio_key()
    if dry_run:
        info = None if key else bc.load_stream(state)
        url = f"{INGEST}/{key}" if key else (info.rtmp_url if info else PLACEHOLDER_URL)
        secret = key or (info.stream_name if info else None)
        if key:
            print("mode: Studio stream key" + (" + a broadcast created through the API (auto-start, auto-stop)"
                                               if server_token().exists()
                                               else f" only: no YouTube token at {server_token()}, so no broadcast"))
        if not cfg.live.enabled:
            print("NOTE: live.enabled is false; this is a preview only, `start` would refuse.")
        print_scene(scene)
        if scene is None:
            print_plan(plan)
        print("broadcast body:")
        print(json.dumps(night_body(cfg, plan, scene), indent=2))
        print(f"watchdog: {timeout} s")
        cmd = (scene_mod.stream_command(scene, plan.duration_s, url) if scene
               else ffmpeg_command(plan.concat_path, plan.duration_s, url))
        print("ffmpeg:   " + bc.mask(" ".join(cmd), secret))
        return 0

    signal.signal(signal.SIGTERM, _sigterm)
    if key:
        # Same caps as the API path: ffmpeg -t, the watchdog, systemd's
        # RuntimeMaxSec. Ending the stream ends the broadcast (Auto-stop).
        url = f"{INGEST}/{key}"
        if scene:
            cmd = scene_mod.stream_command(scene, plan.duration_s, url)
            scene_mod.record_played(lib, scene)
            log.info("scene night: %s for %s", scene.scene.id, scene.theme.label)
        else:
            cmd = ffmpeg_command(plan.concat_path, plan.duration_s, url)
            write_last_first(lib / "playlist-state.json", plan.playlist[0].name, plan.night.isoformat())
        yt, broadcast_id = open_broadcast(cfg, lib, plan, scene, key)
        rc = 1
        try:
            log.info("streaming %d s with the Studio stream key (watchdog %d s)", plan.duration_s, timeout)
            rc = run_stream(cmd, timeout_s=timeout, secret=key)
            log.info("ffmpeg exited %d", rc)
        finally:
            if broadcast_id:
                try:
                    bc.complete(yt, broadcast_id)
                except Exception:
                    log.exception("could not complete broadcast %s; relying on YouTube auto-stop", broadcast_id)
        return 0 if rc == 0 else 1

    from romanfeed.publish import youtube

    yt = youtube.client()
    info = bc.ensure_stream(yt, state)
    if scene:
        cmd = scene_mod.stream_command(scene, plan.duration_s, info.rtmp_url)
        scene_mod.record_played(lib, scene)
    else:
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


def cmd_lane(cfg: ChannelConfig, lib: Path, lane_id: str, *, dry_run: bool = False,
             now: datetime | None = None) -> int:
    """One session of a live lane (romanfeed/live/lanes.py): the scene the
    rotation gives it, under freshly composed music, to the lane's own key.

    The broadcast was made ahead of time by the GitHub job (auto-start,
    auto-stop), so this makes no YouTube call at all. Same caps as the night:
    ffmpeg -t (the session's planned end, never over 11.5 h), the watchdog,
    and systemd's RuntimeMaxSec."""
    import os

    from romanfeed.cozy import scenes as sc
    from romanfeed.live import lanes

    if not cfg.live.enabled and not dry_run:
        print("refusing to start: live.enabled is false in the channel config")
        return 2
    x = lanes.lane(cfg, lane_id)
    key = os.environ.get(x.key_env, "").strip()
    if not key and not dry_run:
        print(f"refusing to start: no {x.key_env} in the environment (deploy/live/setup.sh lanes)")
        return 2
    now = (now or datetime.now(ZoneInfo(cfg.live.timezone))).astimezone(ZoneInfo(cfg.live.timezone))
    s = lanes.current(cfg, lane_id, now)
    if s is None:
        print(f"lane {lane_id}: no session starts near {now:%H:%M} (starts: {', '.join(x.starts)})")
        return 2
    duration = int(min(MAX_SECONDS, (s.end - now).total_seconds()))
    if duration < 600:
        print(f"lane {lane_id}: the {s.start:%H:%M} session is all but over; not starting")
        return 2
    folder = lib / "scenes"
    library = lanes.library_for(cfg, folder)
    scene, theme = lanes.pick(cfg, library, s, themes_fn=_themes_fn(lib))
    src = sc.fetch_scene(cfg.cozy.repo, cfg.cozy.release_tag, scene, folder)
    loop = scene_mod.prepare_loop(src, folder / "ready", channel_line=channel_line(cfg))
    music = scene_mod.prepare_music(folder / "music", s.start.date(), cfg.live.music_minutes, tag=f"lane-{lane_id}",
                                    seed=f"live-{lane_id}-{s.start.isoformat()}")
    night = scene_mod.SceneNight(s.start.date(), scene, theme, loop, music)
    url = f"{INGEST}/{key or '<stream-key>'}"
    cmd = scene_mod.stream_command(night, duration, url)
    timeout = duration + WATCHDOG_GRACE_S
    print(f"lane {lane_id}: {s.start:%Y-%m-%d %H:%M} session, {duration / 3600:.2f} h, "
          f"{scene.title} ({scene.id}) for {theme.label}")
    print(f"title:  {lanes.title(scene, theme, s)}")
    if dry_run:
        print("ffmpeg: " + bc.mask(" ".join(cmd), key or None))
        return 0
    signal.signal(signal.SIGTERM, _sigterm)
    log.info("lane %s: streaming %d s (watchdog %d s)", lane_id, duration, timeout)
    rc = run_stream(cmd, timeout_s=timeout, secret=key)
    log.info("lane %s: ffmpeg exited %d", lane_id, rc)
    return 0 if rc == 0 else 1
