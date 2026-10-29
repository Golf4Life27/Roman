"""The stream that never ends: one cozy room, around the clock.

The big cozy channels keep one stream live for weeks or months (a jazz café
with thousands watching at any hour). This is ours: a warm room with a
window onto space, calm composed music, a title that names what it is for,
and the season's look. Alex chose it on Oct 29 ("Run it unbroken"), so it is
the one stream not held under 12 hours: it leaves no replay, and it does not
need one.

How it stays up, with no YouTube login on the server:

  broadcast  the GitHub job (live/schedule.py, several runs a day) keeps one
             broadcast bound to the Studio stream key named live.always[].key_name:
             auto-start on, auto-stop OFF, so YouTube keeps it live through a
             gap while ffmpeg reconnects. When the season changes the job
             re-dresses it (title, tags, thumbnail) in place: same broadcast,
             same viewers, same chat.
  server     `romanfeed live always ID` under systemd (Restart=always). It
             streams the season's scene in segments: a segment runs until the
             scene changes (at CHANGE_LOCAL), at most SEGMENT_DAYS, then exits
             so systemd starts it again on the new scene and the latest code
             (its git pull). Inside a segment ffmpeg is reconnected without a
             limit, waiting longer each time it fails at once.

The scene comes from the date alone (the season's first theme that has a
scene, the first of them by id), so the job titles the room the server
streams without the two talking. Rocket launches, whose list changes daily,
are left out: the room changes only with the season.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from romanfeed.config import AlwaysSettings, ChannelConfig
from romanfeed.cozy import scenes as sc
from romanfeed.cozy import titles
from romanfeed.cozy.calendar import Theme, themes_for

log = logging.getLogger(__name__)

CHANGE_LOCAL = dtime(11, 0)   # the room changes late morning: the night's sleepers are up, work has started
SEGMENT_DAYS = 7              # restart at least weekly, for fresh music and the latest code
MIN_SEGMENT_S = 60 * 60       # a start this close to a change runs on to the next one
RETRY_FIRST_S = 5
RETRY_MAX_S = 120
QUICK_FAIL_S = 60             # ffmpeg out this fast: back off before the next try
_clock = time.monotonic
_sleep = time.sleep

LIVE = ("live", "liveStarting", "testing", "testStarting")
TITLE_TAIL = " · 24/7 Live"
USE_LINE = "for Sleep, Study & Focus"


def stream(cfg: ChannelConfig, stream_id: str) -> AlwaysSettings:
    for x in cfg.live.always:
        if x.id == stream_id:
            return x
    raise KeyError(f"no live.always stream {stream_id!r} (have: {[x.id for x in cfg.live.always]})")


def scene_day(now: datetime, tz: ZoneInfo) -> date:
    """The date whose scene is on at `now`: a day's room runs from CHANGE_LOCAL
    to CHANGE_LOCAL the next day."""
    local = now.astimezone(tz)
    return local.date() if local.time() >= CHANGE_LOCAL else local.date() - timedelta(days=1)


def scene_for(library: sc.Library, day: date) -> tuple[sc.Scene, Theme] | None:
    """The room for `day`: the most specific theme of the day that has a
    scene (Halloween, then Thanksgiving, then Christmas, then winter...), and
    of its scenes the first by id, so the room stays put all season."""
    themes = themes_for(day, launches=[])
    for theme in themes:
        found = sorted(library.tagged(theme.key), key=lambda s: s.id)
        if found:
            return found[0], theme
    if library.scenes:
        first = sorted(library.scenes, key=lambda s: s.id)[0]
        return first, themes[-1]
    return None


def segment_end(library: sc.Library, now: datetime, tz: ZoneInfo) -> datetime:
    """When this segment stops: the next CHANGE_LOCAL whose room differs
    from now's, at most SEGMENT_DAYS ahead, never under MIN_SEGMENT_S away."""
    local = now.astimezone(tz)
    day = scene_day(local, tz)
    current = scene_for(library, day)
    current_id = current[0].id if current else None
    end = None
    for i in range(1, SEGMENT_DAYS + 1):
        d = day + timedelta(days=i)
        at = datetime.combine(d, CHANGE_LOCAL, tzinfo=tz)
        if (at - local).total_seconds() < MIN_SEGMENT_S:
            continue
        nxt = scene_for(library, d)
        end = at
        if (nxt[0].id if nxt else None) != current_id:
            break
    return end or datetime.combine(day + timedelta(days=SEGMENT_DAYS + 1), CHANGE_LOCAL, tzinfo=tz)


def title(scene: sc.Scene, theme: Theme) -> str:
    """'Cozy Halloween Cabin 🎃 Calm Space Music & Starry Ambience for Sleep, Study & Focus · 24/7 Live'.

    Under YouTube's 100 characters: the optional words go first, then the
    room's name shrinks word by word (keeping its last word, "Cabin"). Never
    ends "· Live", so the night's clean-up never takes it for one of its own."""
    place = scene.title.split()
    music = ["Calm Space Music & Starry Ambience", "Calm Space Music", "Space Music"]
    emoji = titles.emoji_for(theme.key)
    while True:
        for m in music:
            t = f"Cozy {' '.join(place)} {emoji} {m} {USE_LINE}{TITLE_TAIL}"
            if len(t) <= titles.TITLE_MAX:
                return t
        if len(place) == 1:
            return t[:titles.TITLE_MAX - len(TITLE_TAIL)] + TITLE_TAIL
        del place[-2]


DESCRIPTION = """{tagline}

Live around the clock: a cozy {scene} with the stars outside the window and calm original music that never stops. Keep it on while you sleep, study, read or work, and come back any time. The room changes with the seasons.

{uses}

Subscribe for a new space video every day and cozy live streams around the clock:
{subscribe}

{keywords}

AI-animated scene, original music.

{hashtags}
"""


def description(cfg: ChannelConfig, scene: sc.Scene) -> str:
    from romanfeed.live import broadcast as bc

    return DESCRIPTION.format(tagline=cfg.channel.tagline, scene=scene.title.lower(), uses=bc.USES,
                              subscribe=bc.SUBSCRIBE_URL, keywords=" · ".join(cfg.publish.tags),
                              hashtags=bc.LIVE_HASHTAGS).strip() + "\n"


def body(cfg: ChannelConfig, scene: sc.Scene, theme: Theme, start: datetime) -> dict:
    """The broadcast: auto-start, and auto-stop off so it never ends on a gap."""
    from romanfeed.live import broadcast as bc

    b = bc.broadcast_body(title=title(scene, theme), description=description(cfg, scene), start=start)
    b["contentDetails"].update({"enableAutoStop": False, "recordFromStart": False})
    return b


def extras(cfg: ChannelConfig, scene: sc.Scene, theme: Theme, work: Path):
    from romanfeed.live import broadcast as bc

    tags = bc.fit_tags([scene.title.lower(), theme.label.lower(), "24/7 live", "24 7 cozy stream"],
                       bc.COZY_TAGS, bc.WORK_TAGS, cfg.publish.tags)
    subject = bc.thumbnail_subject(theme.key, theme.label)
    return bc.Extras(tags, thumbnail=lambda: bc.scene_thumbnail(cfg, scene, hours_label="24/7", subject=subject,
                                                                work=work, length_label="24/7 Live"))


# --- the GitHub side: keep the broadcast -------------------------------------------------------------

def _norm(name: str) -> str:
    return "".join(name.lower().split())


def stream_names(yt) -> list[str]:
    """Every live stream (Studio stream key) name on the channel. Names are
    not secrets; the keys themselves are never read here."""
    out, token = [], None
    while True:
        resp = yt.liveStreams().list(part="id,snippet", mine=True, maxResults=50,
                                     **({"pageToken": token} if token else {})).execute()
        out += [item.get("snippet", {}).get("title", "") for item in resp.get("items", [])]
        token = resp.get("nextPageToken")
        if not token:
            return out


def find_stream(yt, key_name: str) -> dict | None:
    """The channel's live stream (Studio stream key) named key_name: its id
    and whether video is arriving on it (status.streamStatus "active").
    Case and spaces don't matter: "24/7", "24 / 7" and " 24/7 " all match."""
    token = None
    while True:
        resp = yt.liveStreams().list(part="id,snippet,status", mine=True, maxResults=50,
                                     **({"pageToken": token} if token else {})).execute()
        for item in resp.get("items", []):
            if _norm(item.get("snippet", {}).get("title", "")) == _norm(key_name):
                return item
        token = resp.get("nextPageToken")
        if not token:
            return None


def go_live(yt, broadcast_id: str, stream: dict) -> str:
    """Auto-start fires when video starts arriving; when it was already
    arriving before the broadcast existed, move the broadcast to live here.
    Best effort: the next run tries again."""
    if stream.get("status", {}).get("streamStatus") != "active":
        return "waiting for the server's video"
    try:
        yt.liveBroadcasts().transition(broadcastStatus="live", id=broadcast_id, part="status").execute()
        return "video was arriving; moved it to live"
    except Exception as exc:
        log.warning("could not move %s to live (%s); auto-start may still do it", broadcast_id, exc)
        return "not moved to live yet"


def bound_broadcasts(yt, stream_id: str) -> list[dict]:
    """Our broadcasts on that stream that are live or waiting to go live."""
    out = []
    for status in ("active", "upcoming"):
        token = None
        while True:
            resp = yt.liveBroadcasts().list(part="id,snippet,status,contentDetails", broadcastStatus=status,
                                            broadcastType="event", maxResults=50,
                                            **({"pageToken": token} if token else {})).execute()
            out += [b for b in resp.get("items", [])
                    if b.get("contentDetails", {}).get("boundStreamId") == stream_id]
            token = resp.get("nextPageToken")
            if not token:
                break
    return out


def ensure_broadcast(cfg: ChannelConfig, yt, x: AlwaysSettings, now: datetime, work: Path, *,
                     dry_run: bool = False) -> str:
    """One line on what was done. Creates the broadcast if there is none,
    re-dresses it when the season's room changed, removes extra waiting ones."""
    tz = ZoneInfo(cfg.live.timezone)
    found_stream = find_stream(yt, x.key_name)
    if found_stream is None:
        names = ", ".join(repr(n) for n in stream_names(yt)) or "none"
        return f"24/7 {x.id}: no stream key named {x.key_name!r} in Studio yet (keys: {names}); skipped"
    library = sc.fetch_manifest(cfg.cozy.repo, cfg.cozy.release_tag, work / "always" / sc.MANIFEST)
    picked = scene_for(library, scene_day(now, tz))
    if picked is None:
        return f"24/7 {x.id}: the scene library is empty; skipped"
    scene, theme = picked
    want = title(scene, theme)
    stream_id = found_stream["id"]
    found = bound_broadcasts(yt, stream_id)
    found.sort(key=lambda b: b.get("snippet", {}).get("publishedAt", ""), reverse=True)    # newest first,
    found.sort(key=lambda b: b.get("status", {}).get("lifeCycleStatus") not in LIVE)     # a live one before all
    keep, extra = (found[0], found[1:]) if found else (None, [])
    notes = []
    for b in extra:
        if b.get("status", {}).get("lifeCycleStatus") in ("created", "ready"):
            if not dry_run:
                yt.liveBroadcasts().delete(id=b["id"]).execute()
            notes.append(f"removed an extra waiting broadcast {b['id']}")
    if keep is not None:
        bid = keep["id"]
        state = keep.get("status", {}).get("lifeCycleStatus", "?")
        if state in ("created", "ready") and not dry_run:
            notes.append(go_live(yt, bid, found_stream))
        if keep.get("snippet", {}).get("title") == want:
            return f"24/7 {x.id}: {state} https://youtu.be/{bid} ({scene.title})" + "".join(f"; {n}" for n in notes)
        if dry_run:
            return f"24/7 {x.id}: would re-dress https://youtu.be/{bid} as '{want}'"
        from romanfeed.live import broadcast as bc

        done = bc.dress(yt, bid, {"title": want, "description": description(cfg, scene)},
                        extras(cfg, scene, theme, work))
        return f"24/7 {x.id}: {state} https://youtu.be/{bid}, now '{want}' ({', '.join(done)})"
    b = body(cfg, scene, theme, now + timedelta(minutes=2))
    if dry_run:
        return f"24/7 {x.id}: would create '{want}' on stream {stream_id}"
    from romanfeed.live import broadcast as bc

    bid = bc.create_broadcast(yt, b)
    try:
        bc.bind(yt, bid, stream_id)
    except Exception:
        yt.liveBroadcasts().delete(id=bid).execute()
        raise
    done = bc.dress(yt, bid, b["snippet"], extras(cfg, scene, theme, work))
    done.append(go_live(yt, bid, found_stream))
    return f"24/7 {x.id}: created https://youtu.be/{bid} '{want}' ({', '.join(done)})"


# --- the server side: stream it -----------------------------------------------------------------------

@dataclass
class Segment:
    scene: sc.Scene
    theme: Theme
    end: datetime
    seconds: int


def music_path(lib: Path, x: AlwaysSettings, scene: sc.Scene) -> Path:
    return lib / "scenes" / "music" / f"always-{x.id}-{scene.id}-{x.music_minutes}m.m4a"


def prepare(cfg: ChannelConfig, lib: Path, x: AlwaysSettings, scene: sc.Scene) -> tuple[Path, Path]:
    """The room's loop and music on disk, both cached: the loop by scene, the
    music by stream and scene (composed once a season, never per restart)."""
    from romanfeed.audio.composer import compose_piece
    from romanfeed.live import scene as scene_mod
    from romanfeed.live.run import channel_line

    folder = lib / "scenes"
    src = sc.fetch_scene(cfg.cozy.repo, cfg.cozy.release_tag, scene, folder)
    loop = scene_mod.prepare_loop(src, folder / "ready", channel_line=channel_line(cfg))
    music = music_path(lib, x, scene)
    if not (music.exists() and music.stat().st_size > 0):
        tmp = music.with_name(f".{music.stem}.part.m4a")
        compose_piece(tmp, seconds=x.music_minutes * 60, seed=f"always-{x.id}-{scene.id}")
        tmp.replace(music)
    return loop, music


def prune_music(lib: Path, x: AlwaysSettings, keep: set[Path]) -> None:
    """Drop this stream's music for rooms that are out of season."""
    for old in (lib / "scenes" / "music").glob(f"always-{x.id}-*.m4a"):
        if old not in keep:
            old.unlink(missing_ok=True)


def prepare_ahead(cfg: ChannelConfig, lib: Path, now: datetime) -> None:
    """For the daily sync: today's and tomorrow's rooms ready, so a change of
    season only restarts ffmpeg. Never fails the sync, never deletes."""
    if not cfg.live.always:
        return
    tz = ZoneInfo(cfg.live.timezone)
    try:
        library = sc.fetch_manifest(cfg.cozy.repo, cfg.cozy.release_tag, lib / "scenes" / sc.MANIFEST)
        day = scene_day(now, tz)
        for x in cfg.live.always:
            for d in {day, day + timedelta(days=1)}:
                picked = scene_for(library, d)
                if picked:
                    prepare(cfg, lib, x, picked[0])
                    print(f"24/7 {x.id} {d:%a}: {picked[0].title}")
    except Exception:
        log.exception("could not prepare the 24/7 room; it will be prepared when the stream restarts")


def stream_forever(make_cmd: Callable[[int], list[str]], seconds: int, *, secret: str | None = None,
                   label: str = "24/7") -> int:
    """Stream until `seconds` from now, starting ffmpeg again whenever it stops.

    No reconnect limit: the broadcast waits (auto-stop off). A run that fails
    within QUICK_FAIL_S doubles the wait before the next (up to RETRY_MAX_S),
    so a bad key or a down ingest is not hammered; a run that streamed for a
    while resets it."""
    from romanfeed.live.run import WATCHDOG_GRACE_S, run_stream

    end = _clock() + seconds
    wait = RETRY_FIRST_S
    rc = 0
    while True:
        left = int(end - _clock())
        if left < 60:
            return rc
        began = _clock()
        rc = run_stream(make_cmd(left), timeout_s=left + WATCHDOG_GRACE_S, secret=secret)
        if rc == -9:
            return rc
        if int(end - _clock()) < 60:
            return rc
        wait = min(wait * 2, RETRY_MAX_S) if _clock() - began < QUICK_FAIL_S else RETRY_FIRST_S
        log.warning("%s: ffmpeg stopped (exit %d); starting again in %d s", label, rc, wait)
        _sleep(wait)


def plan(cfg: ChannelConfig, library: sc.Library, now: datetime) -> Segment | None:
    tz = ZoneInfo(cfg.live.timezone)
    picked = scene_for(library, scene_day(now, tz))
    if picked is None:
        return None
    end = segment_end(library, now, tz)
    return Segment(picked[0], picked[1], end, int((end - now.astimezone(tz)).total_seconds()))


def cmd_always(cfg: ChannelConfig, lib: Path, stream_id: str, *, dry_run: bool = False,
               now: datetime | None = None) -> int:
    """One segment of the never-ending stream, then exit 0 for systemd to start the next."""
    from romanfeed.live import broadcast as bc
    from romanfeed.live import scene as scene_mod
    from romanfeed.live.run import INGEST, _sigterm

    if not cfg.live.enabled and not dry_run:
        print("refusing to start: live.enabled is false in the channel config")
        return 2
    x = stream(cfg, stream_id)
    key = os.environ.get(x.key_env, "").strip()
    if not key and not dry_run:
        print(f"refusing to start: no {x.key_env} in the environment (deploy/live/setup.sh always)")
        return 2
    tz = ZoneInfo(cfg.live.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    library = sc.fetch_manifest(cfg.cozy.repo, cfg.cozy.release_tag, lib / "scenes" / sc.MANIFEST)
    seg = plan(cfg, library, now)
    if seg is None:
        print("24/7: the scene library is empty; nothing to stream")
        return 2
    print(f"24/7 {x.id}: {seg.scene.title} ({seg.scene.id}) for {seg.theme.label}, "
          f"until {seg.end:%a %b %d %H:%M} ({seg.seconds / 3600:.1f} h)")
    print(f"title:  {title(seg.scene, seg.theme)}")
    url = f"{INGEST}/{key or '<stream-key>'}"
    if dry_run:
        loop, music = Path("<loop>"), Path("<music>")
        night = scene_mod.SceneNight(seg.end.date(), seg.scene, seg.theme, loop, music)
        print("ffmpeg: " + bc.mask(" ".join(scene_mod.stream_command(night, seg.seconds, url, cap=seg.seconds)),
                                   key or None))
        return 0
    loop, music = prepare(cfg, lib, x, seg.scene)
    nxt = scene_for(library, seg.end.date())
    prune_music(lib, x, {music} | ({music_path(lib, x, nxt[0])} if nxt else set()))
    night = scene_mod.SceneNight(seg.end.date(), seg.scene, seg.theme, loop, music)
    signal.signal(signal.SIGTERM, _sigterm)
    log.info("24/7 %s: streaming %s for %d s", x.id, seg.scene.id, seg.seconds)
    stream_forever(lambda s: scene_mod.stream_command(night, s, url, cap=s), seg.seconds, secret=key,
                   label=f"24/7 {x.id}")
    log.info("24/7 %s: segment over; restarting for the next one", x.id)
    return 0
