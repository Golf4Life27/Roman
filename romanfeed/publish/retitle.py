"""Re-title and re-describe the videos already on the channel for sleep searches.

New uploads get the sleep-first titles and descriptions from the channel
config. This brings the back catalogue in line, from what YouTube itself holds
(the ledger keeps titles but not descriptions, and not every upload is in it):

  title        rebuilt from the channel's templates: the 1h template for short
               videos, the sleep template for long ones. The object's name is
               the first " | " part of the old title, so the rebuilt title keeps
               naming the same image. Old sleep cuts never went dark, so the
               "Dark Screen After" part is dropped for them.
  description  the opening (everything above "IN THIS VIDEO") is replaced with
               the sleep-search hook and the subscribe link; chapters, credits
               and music are kept as they are; the old "every day" footer line
               is corrected; hashtags are added. Bytes-repr damage from runs
               #16-#19 is repaired on the way through.
  tags         the sleep tags are merged in.

Shorts (60 s or less) are left alone. Reading uses the project API key
(GOOGLE_TTS_API_KEY): public videos need no OAuth scope, and adding
youtube.force-ssl to a production app now means Google's app review. Without
the key it falls back to a force-ssl token. Writing is one
videos.update (50 quota units) per changed video. Dry run is the default in
the workflow: it prints every before/after and writes nothing."""
from __future__ import annotations

import re

from romanfeed.config import ChannelConfig
from romanfeed.publish.fixup import SCOPE_HELP, _is_scope_error, repair_text
from romanfeed.publish.metadata import (
    HASHTAGS, HOOK_SLEEP_NO_DARK, _fit_title, drop_segment, lead_name, length_text,
)
from romanfeed.sources.base import ImageAsset
from romanfeed.telescopes import telescope_short

_ISO = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
_CHAPTER = re.compile(r"^\d+:\d\d(?::\d\d)?\s", re.M)
OLD_FOOTER = "New space video every day. Subscribe to keep the sky on."
NEW_FOOTER = "New space sleep videos every week."
SLEEP_TAGS = ["sleep music", "deep sleep music", "space music for sleep", "fall asleep fast", "relaxing music for sleep"]
HOOK_LONGFORM = ("{length} of relaxing space music for sleep, study and calm: slow views across {n} real "
                 "space telescope images, opening on {lead}.")


def iso_seconds(duration: str) -> float:
    m = _ISO.fullmatch(duration or "")
    if not m:
        return 0.0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _old_lead(title: str) -> tuple[str, str | None]:
    """(image name, telescope if the title already says it) from any of our templates.

    Old: "Carina Nebula | Nebulae | 1 Hour Relaxing ..." -> first " | " part.
    New: "1 Hour Relaxing Space Music for Sleep · Carina Nebula by Webb · ..."
    -> the " · " part that is not a search phrase, split at " by ". Reading
    the new form back is what makes a second run a no-op."""
    if " · " in title:
        parts = title.split(" · ")
        mid = next((p for p in parts[1:] if not re.search(r"(?i)sleep|screen|screensaver|asleep", p)), "")
        m = re.match(r"(.*?)\s+by\s+((?:Roman|Webb|Hubble|Spitzer|Chandra|Euclid|WISE|GALEX|Herschel)(?: & \w+)?)$", mid)
        return (m.group(1).strip(), m.group(2)) if m else (mid.strip(), None)
    return title.split(" | ")[0].strip(), None


# The earliest uploads led with the subject, not the image ("Galaxies | 8 Hours
# ..."). Retitling those from the title alone would name five videos
# "Galaxies"; their first chapter is the image the video opens on.
GENERIC_LEADS = {"nebulae", "galaxies", "star clusters", "deep fields", "planets", "deep space",
                 "roman telescope images", "space", "space screens"}
_FIRST_CHAPTER = re.compile(r"^0:00\s+(.+)$", re.M)


def _lead_source(title: str, description: str) -> tuple[str, str | None]:
    raw, said = _old_lead(title)
    if raw.lower() in GENERIC_LEADS or not raw:
        m = _FIRST_CHAPTER.search(repair_text(description or ""))
        if m:
            return m.group(1).strip(), None
    return raw, said


def new_title(cfg: ChannelConfig, old_title: str, seconds: float, description: str = "") -> str:
    raw, said = _lead_source(old_title, description)
    lead = lead_name(raw)
    tele = said or telescope_short(ImageAsset(asset_id="x:x", title=raw, url="", source=""))
    sleep = seconds >= 2 * 3600
    template = (cfg.publish.sleep_title_template if sleep else "") or cfg.publish.title_template
    template = drop_segment(template, "{dark_after}")  # nothing on the channel today goes dark
    title_lead = lead
    if "{lead_by}" in template:
        template = template.replace("{lead_by}", "{lead}")
        title_lead = f"{lead} by {tele}" if tele else lead
    title = _fit_title(template, title_lead, {
        "length": length_text(seconds), "subject": "Deep Space", "date": "", "channel": cfg.channel.name,
        "telescope": tele or "Telescope", "dark_after": "",
    })
    return re.sub(r"\s+by(?=\s*(?:[|·]|$))", "", title)[:100]


def new_description(cfg: ChannelConfig, old: str, *, seconds: float, lead: str) -> str:
    text = repair_text(old or "")
    length = length_text(seconds)
    if seconds >= 2 * 3600:
        hook = HOOK_SLEEP_NO_DARK.format(length=length, telescopes="Hubble and Webb")
    else:
        n = len(_CHAPTER.findall(text)) or 80
        hook = HOOK_LONGFORM.format(length=length, n=n, lead=lead or "deep space")
    handle = cfg.channel.handle.strip()
    head = [hook]
    if handle:
        head.append(f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1")
    if cfg.channel.tagline:
        head.append(cfg.channel.tagline)
    marker = text.find("IN THIS VIDEO")
    body = text[marker:] if marker >= 0 else text
    body = body.replace(OLD_FOOTER, NEW_FOOTER)
    if HASHTAGS not in body:
        body = body.rstrip() + "\n\n" + HASHTAGS
    out = "\n\n".join(head) + "\n\n" + body.strip() + "\n"
    # Over YouTube's 5000 characters: chapters give way from the end (credits
    # are a licence condition and stay), with a line saying the list goes on.
    lines = out.split("\n")
    chapter_idx = [i for i, ln in enumerate(lines) if _CHAPTER.match(ln + " ")]
    dropped = False
    while len("\n".join(lines)) > 4990 and len(chapter_idx) > 1:
        lines.pop(chapter_idx.pop())
        dropped = True
    if dropped:
        lines.insert(chapter_idx[-1] + 1, "…")
    out = "\n".join(lines)
    return out if len(out) <= 5000 else text


def update_body(cfg: ChannelConfig, video: dict) -> dict | None:
    """The videos.update(part=snippet) body for one video, or None to skip it."""
    snip = dict(video.get("snippet") or {})
    seconds = iso_seconds((video.get("contentDetails") or {}).get("duration", ""))
    if seconds <= 60 or "#shorts" in (snip.get("description") or "").lower():
        return None
    title = new_title(cfg, snip.get("title", ""), seconds, snip.get("description", ""))
    desc = new_description(cfg, snip.get("description", ""), seconds=seconds,
                           lead=lead_name(_lead_source(snip.get("title", ""), snip.get("description", ""))[0]))
    tags = list(dict.fromkeys((snip.get("tags") or []) + SLEEP_TAGS))
    while len(",".join(tags)) > 450 and len(tags) > 1:  # YouTube caps tags at ~500 characters
        tags.pop()
    out = {k: snip[k] for k in ("categoryId", "defaultLanguage", "defaultAudioLanguage") if snip.get(k)}
    out.update(title=title, description=desc, tags=tags)
    return {"id": video["id"], "snippet": out}


def channel_video_ids(yt, handle: str | None = None) -> list[str]:
    """Every upload on the channel, newest first. With `handle`, by public
    lookup (API key); without, as the token's own channel (needs force-ssl)."""
    query = {"forHandle": handle} if handle else {"mine": True}
    ch = yt.channels().list(part="contentDetails", **query).execute().get("items", [])
    if not ch:
        return []
    uploads = ch[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        page = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            return ids


def retitle(cfg: ChannelConfig, video_ids: list[str] | None = None, *, dry_run: bool = True, yt=None, reader=None) -> int:
    """Reads go through the API key when there is one (public videos, no extra
    scope); the write is videos.update, which the upload-only token can do."""
    from romanfeed.publish.youtube import api_key, client, public_client

    handle = None
    if reader is None and yt is None and api_key():
        reader, handle = public_client(), (cfg.channel.handle or None)
    elif reader is not None:
        handle = cfg.channel.handle or None
    if yt is None and not dry_run:
        yt = client()
    reader = reader or yt or client()
    yt = yt or reader
    try:
        ids = video_ids or channel_video_ids(reader, handle)
        items = []
        for i in range(0, len(ids), 50):
            items += reader.videos().list(part="snippet,contentDetails,status", id=",".join(ids[i:i + 50])).execute().get("items", [])
    except Exception as exc:  # HttpError, not imported: CI's dev install lacks it
        if _is_scope_error(exc):
            print(f"ERROR: {SCOPE_HELP}")
            return 2
        raise
    changed = 0
    for video in items:
        vid = video["id"]
        body = update_body(cfg, video)
        old = video.get("snippet") or {}
        if body is None:
            print(f"-- {vid}: Short, left alone")
            continue
        new = body["snippet"]
        if new["title"] == old.get("title") and new["description"] == old.get("description") and new["tags"] == old.get("tags"):
            print(f"== {vid}: already current")
            continue
        changed += 1
        print(f"\n=== {vid} ({(video.get('status') or {}).get('privacyStatus', '?')})")
        print(f"title  BEFORE: {old.get('title')}")
        print(f"title  AFTER:  {new['title']}")
        print("--- description AFTER (first 600 characters) ---")
        print(new["description"][:600])
        if dry_run:
            print(f"== {vid}: dry run, not written")
            continue
        yt.videos().update(part="snippet", body=body).execute()
        print(f"== {vid}: updated")
    print(f"\n{changed} video(s) {'would change' if dry_run else 'updated'}, {len(items)} read")
    return 0
