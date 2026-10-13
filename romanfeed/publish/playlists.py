"""Channel playlists, kept up to date from the uploads (`romanfeed playlists`).

Playlists keep a viewer watching the next video, which is what turns a
viewer into a subscriber. Each one is a rule over the channel's public
uploads; the job creates any that are missing and adds what each is missing,
newest at the top.

Quota: playlistItems.insert costs 50 units of the 10,000 a day, and the
Shorts already use ~6,400, so each run adds at most `max_inserts` and runs
on days without a daily render (playlists.yml). A backlog fills over a few
runs; after that each run only adds the week's new videos. Needs a token with
youtube.force-ssl (`romanfeed auth --scope manage`).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

SUBSCRIBE = "Subscribe for a new space video every few days and a live sleep stream every night: " \
            "https://www.youtube.com/@SpaceScreens?sub_confirmation=1"


@dataclass
class Video:
    id: str
    title: str
    published: str        # ISO time, sorts as text
    seconds: int
    live: bool            # a live stream replay


@dataclass
class Playlist:
    title: str
    description: str
    rule: Callable[[Video], bool]


def _long(v: Video) -> bool:
    return v.seconds > 10 * 60 and not v.live


def _cozy(v: Video) -> bool:
    return v.title.startswith("Cozy ")


def _earth(v: Video) -> bool:
    return v.title.split("·")[0].strip().lower().endswith(("earth from space for sleep", "earth at night for deep sleep"))


def _space(v: Video) -> bool:
    return _long(v) and not _cozy(v) and not _earth(v)


PLAYLISTS: list[Playlist] = [
    Playlist("Space Videos for Sleep · Full Length",
             "Every full-length Space Screens video: slow views of real Hubble and Webb telescope images with calm "
             "ambient music. Newest first.\n\n" + SUBSCRIBE, _space),
    Playlist("James Webb Telescope Images for Sleep",
             "Videos that open on a James Webb Space Telescope image, with calm ambient music for sleep.\n\n" + SUBSCRIBE,
             lambda v: _space(v) and bool(re.search(r"\bWebb\b", v.title))),
    Playlist("Hubble Telescope Images for Sleep",
             "Videos that open on a Hubble Space Telescope image, with calm ambient music for sleep.\n\n" + SUBSCRIBE,
             lambda v: _space(v) and bool(re.search(r"\bHubble\b", v.title))),
    Playlist("Deep Sleep · Screen Goes Dark",
             "Long sleep cuts: the pictures fade to black partway through so the light won't keep you awake, and the "
             "music plays on.\n\n" + SUBSCRIBE,
             lambda v: _long(v) and "dark screen after" in v.title.lower()),
    Playlist("Cozy Space Nights",
             "Cozy rooms with the stars outside the window and calm original music, including the seasonal cozy live "
             "streams. AI-animated scenes.\n\n" + SUBSCRIBE,
             lambda v: v.seconds > 10 * 60 and _cozy(v)),
    Playlist("Earth from Space for Sleep",
             "Earth photographed by astronauts on the International Space Station: city lights, auroras and "
             "sunrises over the planet, with calm music for sleep.\n\n" + SUBSCRIBE,
             lambda v: _long(v) and _earth(v)),
    Playlist("Live Stream Replays · 10 Hours for Sleep",
             "Replays of the nightly Space Screens live stream, about ten hours each.\n\n" + SUBSCRIBE,
             lambda v: v.live),
]


def _seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _pages(request_fn, **kw):
    token = None
    while True:
        resp = request_fn(**kw, **({"pageToken": token} if token else {})).execute()
        yield from resp.get("items", [])
        token = resp.get("nextPageToken")
        if not token:
            return


def public_videos(yt) -> list[Video]:
    """Every public upload, newest first."""
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids = [it["contentDetails"]["videoId"]
           for it in _pages(yt.playlistItems().list, part="contentDetails", playlistId=uploads, maxResults=50)]
    out = []
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="snippet,contentDetails,status,liveStreamingDetails",
                                id=",".join(ids[i:i + 50])).execute()
        for v in resp.get("items", []):
            if v.get("status", {}).get("privacyStatus") != "public":
                continue
            if v.get("snippet", {}).get("liveBroadcastContent") in ("live", "upcoming"):
                continue  # not a replay yet
            out.append(Video(v["id"], v["snippet"].get("title", ""), v["snippet"].get("publishedAt", ""),
                             _seconds(v.get("contentDetails", {}).get("duration", "")),
                             "liveStreamingDetails" in v))
    return sorted(out, key=lambda v: v.published, reverse=True)


def existing_playlists(yt) -> dict[str, str]:
    return {p["snippet"]["title"]: p["id"] for p in _pages(yt.playlists().list, part="snippet", mine=True, maxResults=50)}


def playlist_videos(yt, playlist_id: str) -> list[str]:
    return [it["contentDetails"]["videoId"]
            for it in _pages(yt.playlistItems().list, part="contentDetails", playlistId=playlist_id, maxResults=50)]


def plan(videos: list[Video], have: dict[str, list[str]]) -> dict[str, list[tuple[Video, int | None]]]:
    """Per playlist title: (video, position) to add, in insertion order.

    Keeps each playlist newest-first: videos newer than everything already in
    it go to position 0 (oldest of them first, so the newest ends on top);
    older ones are appended newest-first."""
    by_id = {v.id: v for v in videos}
    out = {}
    for pl in PLAYLISTS:
        present = have.get(pl.title, [])
        newest_in = max((by_id[i].published for i in present if i in by_id), default="")
        missing = [v for v in videos if pl.rule(v) and v.id not in present]
        newer = sorted((v for v in missing if newest_in and v.published > newest_in), key=lambda v: v.published)
        older = sorted((v for v in missing if not (newest_in and v.published > newest_in)),
                       key=lambda v: v.published, reverse=True)
        out[pl.title] = [(v, 0) for v in newer] + [(v, None) for v in older]
    return out


def add(yt, playlist_id: str, video_id: str, position: int | None) -> None:
    """playlistItems.insert; a position only works on a manually sorted
    playlist (YouTube answers manualSortRequired otherwise), so a refused
    position falls back to appending."""
    snippet = {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}
    if position is not None:
        try:
            yt.playlistItems().insert(part="snippet", body={"snippet": {**snippet, "position": position}}).execute()
            return
        except Exception as exc:
            if "manualSortRequired" not in str(exc):
                raise
    yt.playlistItems().insert(part="snippet", body={"snippet": snippet}).execute()


def sync(yt, *, max_inserts: int = 30, dry_run: bool = False) -> list[str]:
    """Create missing playlists and add what each is missing, at most
    max_inserts additions per run. Returns report lines."""
    videos = public_videos(yt)
    found = existing_playlists(yt)
    lines = [f"{len(videos)} public videos"]
    ids, have = {}, {}
    for pl in PLAYLISTS:
        if pl.title in found:
            ids[pl.title] = found[pl.title]
            have[pl.title] = playlist_videos(yt, found[pl.title])
        elif any(pl.rule(v) for v in videos):
            if dry_run:
                lines.append(f"would create playlist '{pl.title}'")
                ids[pl.title], have[pl.title] = None, []
                continue
            resp = yt.playlists().insert(part="snippet,status", body={
                "snippet": {"title": pl.title, "description": pl.description[:5000]},
                "status": {"privacyStatus": "public"},
            }).execute()
            ids[pl.title], have[pl.title] = resp["id"], []
            lines.append(f"created playlist '{pl.title}' https://www.youtube.com/playlist?list={resp['id']}")
    budget = max_inserts
    for title, adds in plan(videos, have).items():
        if not adds or title not in ids:
            continue
        done = 0
        for video, position in adds:
            if budget <= 0:
                break
            if not dry_run:
                add(yt, ids[title], video.id, position)
            budget -= 1
            done += 1
        left = len(adds) - done
        lines.append(f"{'would add' if dry_run else 'added'} {done} to '{title}'" + (f" ({left} left for next run)" if left else ""))
    return lines
