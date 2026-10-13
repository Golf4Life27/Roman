"""`romanfeed live schedule`: create tonight's broadcast from GitHub.

YouTube shows a stream only when a broadcast exists for it, and Studio makes
one only while its Stream page is open. The server streams to Studio's key
at 21:00 Chicago and holds no YouTube login, so a morning job here
(.github/workflows/live-broadcast.yml, with the repo's YouTube token and the
YOUTUBE_STREAM_KEY secret) creates the night's broadcast ahead of time:
public, auto-start, auto-stop, bound to the key's stream. When the server's
video arrives at 9 PM, YouTube goes live on its own, and auto-stop ends it.

The job runs several times a morning (GitHub starts scheduled runs late or
drops them) and does nothing once tonight's broadcast exists. Each run also
deletes our upcoming broadcasts from earlier nights that never went live, so
a dark night leaves nothing stale on the channel.
"""
from __future__ import annotations

import logging
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from romanfeed.config import ChannelConfig
from romanfeed.live import broadcast as bc

log = logging.getLogger(__name__)

REGULAR_TITLE = "Space Screens Live · {hours} Hours of Real Telescope Views & Calm Music for Sleep"
COZY_TITLE = "Cozy {theme} Night · {hours} Hours of Relaxing Space Music for Sleep · Live"
REGULAR_DESCRIPTION = """{tagline}

Tonight's live stream: {hours} hours of slow, drifting views of real space telescope images with calm ambient music. Put it on, dim the lights, and fall asleep under the stars. The stream ends on its own and stays on the channel to replay any night.

Subscribe for a new space video every few days and a live sleep stream every night:
{subscribe}

{keywords}

Real images from Hubble, Webb and other public NASA and ESA archives.
Not affiliated with or endorsed by NASA or ESA.
"""
COZY_DESCRIPTION = """{tagline}

Tonight's live stream: {hours} hours in a cozy {theme} room with the stars outside the window and calm original music. Put it on, dim the lights, and stay as long as you like. The stream ends on its own and stays on the channel to replay any night.

Subscribe for a new space video every few days and a live sleep stream every night:
{subscribe}

{keywords}

AI-animated scene, original music.
"""


def ours(title: str) -> bool:
    """A broadcast this job made (the only ones it may delete)."""
    return title.startswith("Space Screens Live ·") or (title.startswith("Cozy ") and title.endswith("· Live"))


def start_time(cfg: ChannelConfig, night: date) -> datetime:
    hh, mm = (int(x) for x in cfg.live.start_local.split(":"))
    return datetime.combine(night, time(hh, mm), tzinfo=ZoneInfo(cfg.live.timezone))


def tonight_theme(cfg: ChannelConfig, night: date, work: Path) -> str | None:
    """The cozy theme label if tonight is a scene night, else None. Same rule
    as the server (live/scene.py); the exact room is the server's pick, so
    the title names the theme, not the room."""
    from romanfeed.live import scene as scene_mod

    try:
        picked = scene_mod.choose(cfg, work, night)
    except Exception as exc:  # no manifest, no network: call it a regular night
        log.warning("could not tell whether tonight is a scene night (%s)", exc)
        return None
    return picked[1].label if picked else None


def body_for(cfg: ChannelConfig, night: date, theme: str | None) -> dict:
    hours = f"{cfg.live.hours:g}"
    common = {"tagline": cfg.channel.tagline, "hours": hours, "subscribe": bc.SUBSCRIBE_URL,
              "keywords": " · ".join(cfg.publish.tags)}
    if theme:
        name = "Space" if theme == "Cozy" else theme   # the evergreen theme is labelled "Cozy"
        title = COZY_TITLE.format(theme=name, hours=hours)
        desc = COZY_DESCRIPTION.format(theme=name.lower(), **common)
    else:
        title = REGULAR_TITLE.format(hours=hours)
        desc = REGULAR_DESCRIPTION.format(**common)
    return bc.broadcast_body(title=title, description=desc.strip() + "\n", start=start_time(cfg, night))


def upcoming(yt) -> list[dict]:
    items, token = [], None
    while True:
        resp = yt.liveBroadcasts().list(part="id,snippet,status,contentDetails", broadcastStatus="upcoming",
                                        broadcastType="event", maxResults=50,
                                        **({"pageToken": token} if token else {})).execute()
        items += resp.get("items", [])
        token = resp.get("nextPageToken")
        if not token:
            return items


def run(cfg: ChannelConfig, yt, *, key: str, now: datetime | None = None, dry_run: bool = False) -> str:
    """Make sure tonight's broadcast exists. Returns one line saying what happened."""
    tz = ZoneInfo(cfg.live.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    night = now.date()
    start = start_time(cfg, night)
    if not cfg.live.enabled:
        return "live.enabled is false: no broadcast"

    stale, tonight_id = [], None
    for b in upcoming(yt):
        title = b.get("snippet", {}).get("title", "")
        when = b.get("snippet", {}).get("scheduledStartTime", "")
        if not ours(title) or not when:
            continue
        day = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(tz).date()
        if day < night:
            stale.append(b["id"])
        elif day == night:
            tonight_id = b["id"]
    for bid in stale:
        if dry_run:
            log.info("would delete stale broadcast %s", bid)
        else:
            yt.liveBroadcasts().delete(id=bid).execute()
            log.info("deleted stale broadcast %s (an earlier night that never went live)", bid)
    if tonight_id:
        return f"tonight's broadcast already exists: https://youtu.be/{tonight_id}"

    with tempfile.TemporaryDirectory() as tmp:
        stream_id = bc.find_stream_id(yt, key, Path(tmp) / "studio-stream.json")
        body = body_for(cfg, night, tonight_theme(cfg, night, Path(tmp)))
    if dry_run:
        return f"would create '{body['snippet']['title']}' at {start:%Y-%m-%d %H:%M %Z} on stream {stream_id}"
    broadcast_id = bc.create_broadcast(yt, body)
    try:
        bc.bind(yt, broadcast_id, stream_id)
    except Exception:
        yt.liveBroadcasts().delete(id=broadcast_id).execute()
        raise
    return f"created https://youtu.be/{broadcast_id} '{body['snippet']['title']}' for {start:%Y-%m-%d %H:%M %Z}"
