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
a dark night leaves nothing stale on the channel, and any extra broadcast
for tonight (the newest one is kept), so a night never splits its viewers.

Titles must fit YouTube's 100 characters: a long event name ("Martian Moons
eXploration (MMX) Launch") once cut the title to "... · Liv", the job no
longer recognised its own broadcast, and every run made another one.
"""
from __future__ import annotations

import logging
import re
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


TITLE_MAX = 100
# A line both descriptions carry: recognises our broadcasts even if a title was cut.
MARK = "The stream ends on its own and stays on the channel to replay any night."


def ours(title: str, description: str = "") -> bool:
    """A broadcast this job made (the only ones it may delete)."""
    if title.startswith("Space Screens Live ·"):
        return True
    return title.startswith("Cozy ") and (title.endswith("· Live") or MARK in description)


def cozy_title(theme: str, hours: str) -> str:
    """COZY_TITLE within 100 characters: drop "(...)" asides, then words
    before the theme's last one ("Launch", "Meteor Shower" keeps its end)."""
    def fill(name: str) -> str:
        return COZY_TITLE.format(theme=name, hours=hours)

    name = re.sub(r"\s*\([^)]*\)", "", theme).strip() if len(fill(theme)) > TITLE_MAX else theme
    words = name.split()
    while len(fill(" ".join(words))) > TITLE_MAX and len(words) > 1:
        del words[-2]
    return fill(" ".join(words))[:TITLE_MAX]


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
        title = cozy_title(name, hours)
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

    stale, tonight = [], []
    for b in upcoming(yt):
        snip = b.get("snippet", {})
        when = snip.get("scheduledStartTime", "")
        if not ours(snip.get("title", ""), snip.get("description", "")) or not when:
            continue
        day = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(tz).date()
        if day < night:
            stale.append((b["id"], "an earlier night that never went live"))
        elif day == night:
            tonight.append(b)
    tonight.sort(key=lambda b: b.get("snippet", {}).get("publishedAt", ""))
    stale += [(b["id"], "a duplicate of tonight's") for b in tonight[:-1]]
    tonight_id = tonight[-1]["id"] if tonight else None
    for bid, why in stale:
        if dry_run:
            log.info("would delete broadcast %s (%s)", bid, why)
        else:
            yt.liveBroadcasts().delete(id=bid).execute()
            log.info("deleted broadcast %s (%s)", bid, why)
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
