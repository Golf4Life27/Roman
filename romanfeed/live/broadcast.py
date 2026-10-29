"""The YouTube side of a night: one broadcast, one reusable stream, one stop.

A liveBroadcast is the public event viewers see (title, description, the VOD
it becomes). A liveStream is the ingest point ffmpeg pushes to. The stream is
created once and reused every night, so its key never changes and never has
to be handed around; its id, ingest address and key live in a local state
file readable only by the service user. Both resources need the
youtube.force-ssl scope -- mint the server's token with
`romanfeed auth --scope manage`.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from romanfeed.publish.metadata import _fit_title

log = logging.getLogger(__name__)

SUBSCRIBE_URL = "https://www.youtube.com/@SpaceScreens?sub_confirmation=1"

TITLE_TEMPLATE = "{lead} | {hours} Hours of Space for Sleep | Live Telescope Screensaver | {date}"
TITLE_NO_LEAD = "{hours} Hours of Space for Sleep | Live Telescope Screensaver | {date}"

DESCRIPTION = """{tagline}

Tonight's live stream: {hours} hours of slow, drifting views of real space telescope images with calm ambient music. Put it on, dim the lights, and fall asleep under the stars. The stream ends on its own and stays on the channel to replay any night.

TONIGHT'S LINE-UP
{lineup}

Subscribe for a new space video every day and a live sleep stream every night:
{subscribe}

{keywords}

Real images from Hubble, Webb and other public NASA and ESA archives; full image credits are in each video's own description.
Not affiliated with or endorsed by NASA or ESA.
"""


@dataclass
class StreamInfo:
    """The persistent ingest point. The key is kept out of repr() so it
    cannot leak through a log line or a traceback's locals."""
    stream_id: str
    ingestion_address: str
    stream_name: str = field(repr=False)

    @property
    def rtmp_url(self) -> str:
        return f"{self.ingestion_address.rstrip('/')}/{self.stream_name}"


def mask(text: str, secret: str | None) -> str:
    return text.replace(secret, "****") if secret else text


def _hours_label(hours: float) -> str:
    return f"{hours:g}"


def build_title(night: date, lead: str, hours: float) -> str:
    tokens = {"hours": _hours_label(hours), "date": f"{night:%b} {night.day}"}
    if lead:
        return _fit_title(TITLE_TEMPLATE, lead, tokens)
    return TITLE_NO_LEAD.format(**tokens)


def build_description(*, tagline: str, hours: float, leads: list[str], tags: list[str]) -> str:
    seen: list[str] = []
    for lead in leads:
        if lead and lead not in seen:
            seen.append(lead)
    lineup = "\n".join(f"- {x}" for x in seen) or "- Hubble and Webb highlights from the channel's library"
    keywords = " · ".join(tags)
    return DESCRIPTION.format(tagline=tagline, hours=_hours_label(hours), lineup=lineup,
                              subscribe=SUBSCRIBE_URL, keywords=keywords).strip() + "\n"


def broadcast_body(*, title: str, description: str, start: datetime) -> dict:
    """liveBroadcasts.insert body.

    enableAutoStart: YouTube goes live when ffmpeg's data arrives, so nothing
    has to poll and transition. enableAutoStop: when ffmpeg stops (its -t
    expires), YouTube ends the broadcast and archives it -- the first of the
    two API-side stops. recordFromStart keeps the whole night in the VOD.
    """
    return {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "scheduledStartTime": start.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
        "contentDetails": {
            "enableAutoStart": True,
            "enableAutoStop": True,
            "enableDvr": True,
            "recordFromStart": True,
            "latencyPreference": "normal",
            "monitorStream": {"enableMonitorStream": False},
        },
    }


STREAM_BODY = {
    "snippet": {"title": "Space Screens nightly"},
    "cdn": {"ingestionType": "rtmp", "resolution": "1080p", "frameRate": "variable"},
    "contentDetails": {"isReusable": True},
}


def load_stream(state_path: Path) -> StreamInfo | None:
    try:
        d = json.loads(state_path.read_text())
        return StreamInfo(d["stream_id"], d["ingestion_address"], d["stream_name"])
    except (OSError, ValueError, KeyError):
        return None


def save_stream(state_path: Path, info: StreamInfo) -> None:
    """Write the stream state 0600: it holds the stream key, which lets anyone
    broadcast on the channel."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"stream_id": info.stream_id, "ingestion_address": info.ingestion_address,
                   "stream_name": info.stream_name}, fh, indent=2)
    os.chmod(state_path, 0o600)


def ensure_stream(yt, state_path: Path) -> StreamInfo:
    """The cached stream if YouTube still has it, else a new one (cached)."""
    info = load_stream(state_path)
    if info:
        resp = yt.liveStreams().list(part="id", id=info.stream_id).execute()
        if resp.get("items"):
            return info
        log.warning("cached live stream %s is gone; creating a new one", info.stream_id)
    resp = yt.liveStreams().insert(part="snippet,cdn,contentDetails", body=STREAM_BODY).execute()
    ing = resp["cdn"]["ingestionInfo"]
    info = StreamInfo(resp["id"], ing["ingestionAddress"], ing["streamName"])
    save_stream(state_path, info)
    log.info("created live stream %s (key cached in %s)", info.stream_id, state_path)
    return info



SCENE_DESCRIPTION = """{tagline}

Tonight's live stream: {hours} hours in the {scene}, a cozy {theme} room with the stars outside the window and calm original music. Put it on, dim the lights, and stay as long as you like. The stream ends on its own and stays on the channel to replay any night.

Subscribe for a new space video every day and a live sleep stream every night:
{subscribe}

{keywords}

AI-animated scene, original music.
"""


def scene_body(*, scene: str, theme: str, hours: float, start: datetime, tagline: str, tags: list[str],
               theme_key: str = "") -> dict:
    """The broadcast body for a cozy scene night: a seasonal title naming the
    room on screen, and the description says the scene is AI-animated."""
    from romanfeed.cozy import titles

    title = titles.seasonal(scene, when=start, theme_key=theme_key, hours_label=_hours_label(hours), part="Night",
                            live=True)
    desc = SCENE_DESCRIPTION.format(tagline=tagline, hours=_hours_label(hours), scene=scene, theme=theme.lower(),
                                    subscribe=SUBSCRIBE_URL, keywords=" · ".join(tags)).strip() + "\n"
    return broadcast_body(title=title, description=desc, start=start)


def find_stream_id(yt, key: str, cache: Path) -> str:
    """The id of the liveStream whose key is Studio's stream key (the one the
    server sends to), cached by key hash. The id is not a secret; the key is
    never written here."""
    import hashlib

    tag = hashlib.sha256(key.encode()).hexdigest()[:16]
    try:
        d = json.loads(cache.read_text())
        if d.get("key_hash") == tag and d.get("stream_id"):
            return d["stream_id"]
    except (OSError, ValueError):
        pass
    token = None
    while True:
        resp = yt.liveStreams().list(part="id,cdn", mine=True, maxResults=50,
                                     **({"pageToken": token} if token else {})).execute()
        for item in resp.get("items", []):
            if item.get("cdn", {}).get("ingestionInfo", {}).get("streamName") == key:
                cache.write_text(json.dumps({"stream_id": item["id"], "key_hash": tag}))
                log.info("Studio stream key belongs to live stream %s", item["id"])
                return item["id"]
        token = resp.get("nextPageToken")
        if not token:
            raise LookupError("no live stream on the channel uses this stream key")


def create_broadcast(yt, body: dict) -> str:
    resp = yt.liveBroadcasts().insert(part="snippet,status,contentDetails", body=body).execute()
    log.info("created broadcast https://youtu.be/%s", resp["id"])
    return resp["id"]


# What liveBroadcasts.insert cannot set. A broadcast is also a video, so its
# tags and category go on with videos.update and its thumbnail with
# thumbnails.set, right after it is created (50 quota units each, once a
# stream). The big cozy streams carry 20-30 tags, a "good for" list, hashtags
# and a custom thumbnail; ours went out with none of these.
LIVE_CATEGORY = "10"    # Music: what a cozy music stream is browsed and suggested as
USES = "Good for: sleep, reading, study, work, meditation and stress relief."
LIVE_HASHTAGS = "#sleepmusic #cozy #ambientmusic #studymusic #relaxingmusic #space"
TAG_BUDGET = 480        # YouTube allows 500 characters of tags, counting quotes and commas

COZY_TAGS = ["cozy ambience", "live sleep music", "cozy live stream", "sleep music", "space music for sleep",
             "relaxing music", "calm music", "ambient music", "study music", "music for sleep", "stress relief",
             "cozy night", "deep sleep music", "relaxing ambience"]
WORK_TAGS = ["focus music", "work music", "study with me", "background music"]
SPACE_TAGS = ["live sleep music", "space music for sleep", "relaxing music", "ambient music", "deep sleep music",
              "calm music", "music for sleep", "stress relief"]


def fit_tags(*groups: list[str], budget: int = TAG_BUDGET) -> list[str]:
    """The tags in order, without repeats, cut to YouTube's tag limit: each
    tag counts its length, plus 2 for the quotes a tag with a space gets,
    plus 1 for the comma between tags."""
    out: list[str] = []
    used = 0
    for tag in (t.strip() for g in groups for t in g):
        if not tag or tag.lower() in (o.lower() for o in out):
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if out else 0)
        if used + cost > budget:
            continue
        out.append(tag)
        used += cost
    return out


@dataclass
class Extras:
    tags: list[str]
    category_id: str = LIVE_CATEGORY
    thumbnail: Callable[[], Path | None] | None = None   # made only once the broadcast exists


def thumbnail_subject(theme_key: str, theme_label: str) -> str:
    return "Live · Cozy Space Night" if theme_key == "evergreen" else f"Live · Cozy {theme_label}"


def scene_thumbnail(cfg, scene, *, hours_label: str, subject: str, work: Path,
                    length_label: str | None = None) -> Path | None:
    """A custom thumbnail from the scene's first frame with the stream's
    length on it, as the weekly cozy videos get. None if anything fails: the
    broadcast then keeps YouTube's own frame."""
    from romanfeed.cozy import scenes as sc
    from romanfeed.render import ffmpeg
    from romanfeed.render.thumbnail import make_thumbnail

    try:
        src = sc.fetch_scene(cfg.cozy.repo, cfg.cozy.release_tag, scene, work / "scenes")
        still = work / f"{scene.id}-still.png"
        ffmpeg.run(["-y", "-i", str(src), "-frames:v", "1", str(still)])
        return make_thumbnail(still, work / f"{scene.id}-live.jpg",
                              length_label=length_label or f"{hours_label} Hours", subject=subject)
    except Exception as exc:
        log.warning("no custom thumbnail for %s (%s)", scene.id, exc)
        return None


def dress(yt, broadcast_id: str, snippet: dict, extras: Extras) -> list[str]:
    """Put the tags, category and thumbnail on a new broadcast. Each step is
    best effort: a broadcast without them still goes live, so a failure here
    is reported, never raised."""
    notes: list[str] = []
    try:
        yt.videos().update(part="snippet", body={"id": broadcast_id, "snippet": {
            "title": snippet["title"], "description": snippet["description"],
            "tags": extras.tags, "categoryId": extras.category_id}}).execute()
        notes.append(f"{len(extras.tags)} tags, category {extras.category_id}")
    except Exception as exc:
        log.warning("tags and category not set on %s (%s)", broadcast_id, exc)
        notes.append("tags not set")
    thumb = extras.thumbnail() if extras.thumbnail else None
    if thumb:
        try:
            from romanfeed.publish.youtube import set_thumbnail

            set_thumbnail(yt, broadcast_id, thumb)
            notes.append("custom thumbnail")
        except Exception as exc:
            log.warning("thumbnail not set on %s (%s)", broadcast_id, exc)
            notes.append("thumbnail not set")
    return notes


def bind(yt, broadcast_id: str, stream_id: str) -> None:
    yt.liveBroadcasts().bind(part="id,contentDetails", id=broadcast_id, streamId=stream_id).execute()


def complete(yt, broadcast_id: str) -> str:
    """End the broadcast, whatever state it is in. Returns what was done.

    Normally auto-stop has already ended it and this is a no-op. A broadcast
    still live (auto-stop did not fire) is transitioned to complete, which is
    what turns it into the archived VOD. One that never went live -- ffmpeg
    failed to connect -- is deleted, so a dead "upcoming" event does not sit
    on the channel page."""
    resp = yt.liveBroadcasts().list(part="status", id=broadcast_id).execute()
    items = resp.get("items") or []
    status = items[0]["status"].get("lifeCycleStatus", "") if items else "missing"
    if status in {"complete", "revoked", "missing"}:
        log.info("broadcast %s already %s", broadcast_id, status)
        return status
    if status in {"created", "ready"}:
        yt.liveBroadcasts().delete(id=broadcast_id).execute()
        log.warning("broadcast %s never went live; deleted it", broadcast_id)
        return "deleted"
    yt.liveBroadcasts().transition(broadcastStatus="complete", id=broadcast_id, part="status").execute()
    log.info("broadcast %s transitioned to complete (was %s)", broadcast_id, status)
    return "completed"
