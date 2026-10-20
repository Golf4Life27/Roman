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

Titles are seasonal (cozy/titles.py) and always fit YouTube's 100
characters: a cut title once ended in "· Liv", the job stopped recognising
its own broadcast, and every run made another one. Our broadcasts are also
known by a line their descriptions carry.

The live network (live/lanes.py) adds lane sessions: for every lane whose
stream key is set (YOUTUBE_STREAM_KEY_2, ...), each session starting in the
next 24 hours gets its broadcast here too, titled with the scene the server
will stream. A broadcast belongs to a session when it is bound to the lane's
stream and scheduled within 30 minutes of the session's start.
"""
from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from romanfeed.config import ChannelConfig
from romanfeed.cozy import titles
from romanfeed.live import broadcast as bc

log = logging.getLogger(__name__)

REGULAR_TITLE = "Space Screens Live · {hours} Hours of Real Telescope Views & Calm Music for Sleep"
REGULAR_DESCRIPTION = """{tagline}

Tonight's live stream: {hours} hours of slow, drifting views of real space telescope images with calm ambient music. Put it on, dim the lights, and fall asleep under the stars. The stream ends on its own and stays on the channel to replay any night.

Subscribe for a new space video every few days and a live sleep stream every night:
{subscribe}

{keywords}

Real images from Hubble, Webb and other public NASA and ESA archives.
Not affiliated with or endorsed by NASA or ESA.
"""
COZY_DESCRIPTION = """{tagline}

Tonight's live stream: {hours} hours in a cozy {scene} with the stars outside the window and calm original music. Put it on, dim the lights, and stay as long as you like. The stream ends on its own and stays on the channel to replay any night.

Subscribe for a new space video every few days and a live sleep stream every night:
{subscribe}

{keywords}

AI-animated scene, original music.
"""


# A line both descriptions carry: recognises our broadcasts even if a title was cut.
MARK = "The stream ends on its own and stays on the channel to replay any night."


def ours(title: str, description: str = "") -> bool:
    """A broadcast this job made (the only ones it may delete)."""
    if title.startswith("Space Screens Live ·"):
        return True
    return title.startswith("Cozy ") and (title.endswith("· Live") or MARK in description)


def start_time(cfg: ChannelConfig, night: date) -> datetime:
    hh, mm = (int(x) for x in cfg.live.start_local.split(":"))
    return datetime.combine(night, time(hh, mm), tzinfo=ZoneInfo(cfg.live.timezone))


def tonight_scene(cfg: ChannelConfig, night: date, work: Path):
    """(scene, theme) if tonight is a scene night, else None. The same
    stateless rule as the server (live/scene.py choose), so the title can
    name the room the server will stream."""
    from romanfeed.live import scene as scene_mod

    try:
        return scene_mod.choose(cfg, work, night)
    except Exception as exc:  # no manifest, no network: call it a regular night
        log.warning("could not tell whether tonight is a scene night (%s)", exc)
        return None


def body_for(cfg: ChannelConfig, night: date, picked) -> dict:
    hours = f"{cfg.live.hours:g}"
    common = {"tagline": cfg.channel.tagline, "hours": hours, "subscribe": bc.SUBSCRIBE_URL,
              "keywords": " · ".join(cfg.publish.tags)}
    start = start_time(cfg, night)
    if picked:
        scene, theme = picked
        title = titles.seasonal(scene.title, when=start, theme_key=theme.key, hours_label=hours, use=titles.SLEEP,
                                part="Night", live=True)
        desc = COZY_DESCRIPTION.format(scene=scene.title.lower(), **common)
    else:
        title = REGULAR_TITLE.format(hours=hours)
        desc = REGULAR_DESCRIPTION.format(**common)
    return bc.broadcast_body(title=title, description=desc.strip() + "\n", start=start)


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


@dataclass
class Slot:
    """One stream start that needs a broadcast: tonight's, or a lane session's."""
    name: str
    stream_id: str
    start: datetime
    make_body: Callable[[], dict]


MATCH = timedelta(minutes=30)    # a broadcast belongs to a slot starting this close
STALE = timedelta(hours=1)       # an upcoming broadcast this far past its start never went live
AHEAD = timedelta(hours=24)      # lane sessions get their broadcast up to a day ahead


def _when(b: dict) -> datetime | None:
    when = b.get("snippet", {}).get("scheduledStartTime", "")
    return datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None


def lane_slots(cfg: ChannelConfig, yt, lane_keys: dict[str, str], now: datetime, work: Path,
               lines: list[str]) -> list[Slot]:
    from romanfeed.cozy.calendar import fetch_launches, themes_for
    from romanfeed.live import lanes

    out: list[Slot] = []
    wanted = [x for x in cfg.live.lanes if lane_keys.get(x.id)]
    for x in cfg.live.lanes:
        if not lane_keys.get(x.id):
            lines.append(f"lane {x.id}: no {x.key_env} secret yet; skipped")
    if not wanted:
        return out
    library = lanes.library_for(cfg, work)
    launches = fetch_launches(cache=work / "launches.json")

    def themes_fn(d):
        return themes_for(d, launches=launches)

    for x in wanted:
        stream_id = bc.find_stream_id(yt, lane_keys[x.id], work / f"stream-{x.id}.json")
        for sess in lanes.sessions(cfg, now, now + AHEAD, lanes=[x]):
            def make(sess=sess):
                scene, theme = lanes.pick(cfg, library, sess, themes_fn=themes_fn)
                return lanes.body(cfg, scene, theme, sess)
            out.append(Slot(f"lane {x.id}", stream_id, sess.start, make))
    return out


def run(cfg: ChannelConfig, yt, *, key: str, now: datetime | None = None, dry_run: bool = False,
        lane_keys: dict[str, str] | None = None) -> str:
    """Make sure tonight's broadcast and every lane session's in the next day
    exist, once each. Returns what happened, one line per stream start."""
    tz = ZoneInfo(cfg.live.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    night = now.date()
    if not cfg.live.enabled:
        return "live.enabled is false: no broadcast"

    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        slots: list[Slot] = []
        start = start_time(cfg, night)
        if start > now:
            night_stream = bc.find_stream_id(yt, key, work / "studio-stream.json")
            slots.append(Slot("tonight", night_stream, start,
                              lambda: body_for(cfg, night, tonight_scene(cfg, night, work))))
        slots += lane_slots(cfg, yt, lane_keys or {}, now, work, lines)

        groups: list[list[dict]] = [[] for _ in slots]
        doomed: list[tuple[str, str]] = []
        for b in upcoming(yt):
            snip = b.get("snippet", {})
            when = _when(b)
            if when is None or not ours(snip.get("title", ""), snip.get("description", "")):
                continue
            bound = b.get("contentDetails", {}).get("boundStreamId")
            i = next((i for i, sl in enumerate(slots)
                      if abs(when - sl.start) < MATCH and bound in (None, sl.stream_id)), None)
            if i is not None:
                groups[i].append(b)
            elif when < now - STALE:
                doomed.append((b["id"], "a stream start that never went live"))
        for sl, group in zip(slots, groups):
            group.sort(key=lambda b: b.get("snippet", {}).get("publishedAt", ""))
            doomed += [(b["id"], f"a duplicate of {sl.name}'s") for b in group[:-1]]
        for bid, why in doomed:
            if dry_run:
                log.info("would delete broadcast %s (%s)", bid, why)
            else:
                yt.liveBroadcasts().delete(id=bid).execute()
                log.info("deleted broadcast %s (%s)", bid, why)

        for sl, group in zip(slots, groups):
            if group:
                if sl.name == "tonight":
                    lines.insert(0, f"tonight's broadcast already exists: https://youtu.be/{group[-1]['id']}")
                else:
                    lines.append(f"{sl.name} {sl.start:%a %H:%M}: exists https://youtu.be/{group[-1]['id']}")
                continue
            body = sl.make_body()
            label = "tonight" if sl.name == "tonight" else f"{sl.name} {sl.start:%a %H:%M}"
            if dry_run:
                lines.append(f"would create '{body['snippet']['title']}' at {sl.start:%Y-%m-%d %H:%M %Z} "
                             f"on stream {sl.stream_id} ({label})")
                continue
            broadcast_id = bc.create_broadcast(yt, body)
            try:
                bc.bind(yt, broadcast_id, sl.stream_id)
            except Exception:
                yt.liveBroadcasts().delete(id=broadcast_id).execute()
                raise
            lines.append(f"created https://youtu.be/{broadcast_id} '{body['snippet']['title']}' "
                         f"for {sl.start:%Y-%m-%d %H:%M %Z} ({label})")
    return "\n".join(lines) or "nothing to schedule"
