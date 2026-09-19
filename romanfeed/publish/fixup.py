"""Repair or reschedule videos that are already on YouTube.

Two jobs, both on videos that have already been uploaded:

* **fix-descriptions** -- runs #16-#19 published descriptions whose chapter,
  credit and music lines carry Python bytes reprs (`0:00 b'Carina Nebula'`),
  because ESA's Djangoplicity export serialises text as bytes. The source-side
  bug is fixed (see `_text` in romanfeed/sources/esa_archive.py, PR #12), but
  the videos already on the channel still read that way. The repair reads the
  live description back, runs it through the very same decoder, and writes it
  again -- no local copy is needed, and none exists: the ledger records a
  video's title but never its description.

* **schedule** -- a private video gets `status.publishAt` so YouTube flips it
  public on its own at the appointed minute.

Both use `videos.update`, which *replaces* the part it is given. Sending only
`description` wipes the title, the category and the tags, so the snippet body
is rebuilt from the live snippet with just the description swapped.

Scopes: `videos.update` is reachable with the upload-only token the daily
workflow already holds, but `videos.list` is not -- it needs youtube.readonly
or youtube.force-ssl. So `schedule` works today and `fix-descriptions` asks for
a re-minted token (docs/YOUTUBE_API_SETUP.md section 8).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from romanfeed.sources.esa_archive import _text

log = logging.getLogger(__name__)

# Saturday 2026-09-19 21:00 and 21:05 CDT (UTC-5). The 8h cut goes first and
# the 1h cut five minutes later, so the two do not surface in subscribers'
# feeds in the same minute.
DEFAULT_PUBLISH_AT = ("2026-09-20T02:00:00Z", "2026-09-20T02:05:00Z")

# `_text` only unwraps a string that is a bytes repr end to end. In a published
# description the repr sits *inside* a line -- after a chapter timestamp
# ("0:00 b'NGC 3324 (NIRCam Image)'") or a credit bullet -- so each repr is
# lifted out and decoded on its own. The lookbehind keeps "Webb's 25th" from
# being read as the start of a repr; a real repr is always preceded by a space,
# a bullet or the start of the line.
_EMBEDDED = re.compile(r"""(?<![\w'"])b(['"])(?:\\.|(?!\1).)*\1""")

# A chapter title is clipped to 80 characters, and in the broken uploads that
# clip was applied to the repr, so the last chapter-line repr on a long title
# can be missing its closing quote (and may end in the clip's ellipsis).
_TRUNCATED = re.compile(r"""(?<![\w'"])b(['"])((?:\\.|(?!\1)[^…])*)(…?)\s*$""")


def repair_text(text: str) -> str:
    """Turn a bytes-repr-riddled description back into what it should have said.

    Line by line, because a truncated repr can only be recognised at the end of
    one. Text with no reprs in it comes back unchanged.
    """
    return "\n".join(_repair_line(line) for line in text.split("\n"))


def _repair_line(line: str) -> str:
    fixed = _EMBEDDED.sub(lambda m: _text(m.group(0)), line)
    trunc = _TRUNCATED.search(fixed)
    if trunc:
        fixed = fixed[: trunc.start()] + _decode_truncated(trunc.group(1), trunc.group(2)) + trunc.group(3)
    return fixed


def _decode_truncated(quote: str, body: str) -> str:
    """Decode a repr whose closing quote the 80-character clip cut off.

    The clip can also land in the middle of a `\\xe2\\x80\\x93` escape, which
    would decode to a replacement character, so trailing characters are dropped
    one at a time until what is left decodes cleanly.
    """
    while body:
        literal = f"b{quote}{body}{quote}"
        out = _text(literal)
        if out != literal and "�" not in out:
            return out
        body = body[:-1]
    return ""


def snippet_update_body(video: dict) -> dict:
    """Build the `videos.update(part=snippet)` body that repairs one video.

    The update replaces the whole snippet, so title, categoryId and tags are
    resent as they came back from `videos.list` -- leaving any of them out
    blanks them on the video.
    """
    snippet = video.get("snippet") or {}
    body = {
        "id": video["id"],
        "snippet": {
            "title": snippet.get("title", ""),
            "description": repair_text(snippet.get("description", "")),
            "categoryId": snippet.get("categoryId", "28"),
            "tags": list(snippet.get("tags") or []),
        },
    }
    if snippet.get("defaultLanguage"):
        body["snippet"]["defaultLanguage"] = snippet["defaultLanguage"]
    return body


def rfc3339_utc(value: str) -> str:
    """Normalise a publish time to the `2026-09-20T02:00:00Z` form YouTube wants.

    A naive timestamp is rejected rather than guessed at: the difference
    between 21:00 local and 21:00 UTC is a video going live five hours early.
    """
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"not an RFC3339 timestamp: {value!r}") from exc
    if when.tzinfo is None:
        raise ValueError(f"publish time needs a timezone (append Z for UTC): {value!r}")
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def schedule_body(video_id: str, publish_at: str) -> dict:
    """Build the `videos.update(part=status)` body that schedules one video.

    `privacyStatus` stays private: that plus `publishAt` is what tells YouTube
    to flip it public itself. The whole status part is sent, made-for-kids
    included, because the update replaces it.
    """
    return {
        "id": video_id,
        "status": {
            "privacyStatus": "private",
            "publishAt": rfc3339_utc(publish_at),
            "selfDeclaredMadeForKids": False,
        },
    }


def publish_times(video_ids: list[str], at: list[str] | None) -> list[str]:
    """One publish time per video: given, repeated, or the recorded defaults."""
    if not at:
        if len(video_ids) > len(DEFAULT_PUBLISH_AT):
            raise ValueError(f"--at is required for more than {len(DEFAULT_PUBLISH_AT)} videos")
        return [rfc3339_utc(t) for t in DEFAULT_PUBLISH_AT[: len(video_ids)]]
    if len(at) == 1:
        return [rfc3339_utc(at[0])] * len(video_ids)
    if len(at) != len(video_ids):
        raise ValueError(f"got {len(at)} --at values for {len(video_ids)} videos")
    return [rfc3339_utc(t) for t in at]


SCOPE_HELP = (
    "the saved YouTube token cannot read video metadata: re-mint it with "
    "youtube.force-ssl (`romanfeed auth --scope manage`) and update the "
    "YOUTUBE_TOKEN_JSON secret -- see docs/YOUTUBE_API_SETUP.md section 8"
)


def _is_scope_error(exc) -> bool:
    """Tell a missing-scope 401/403 from a quota or ownership one."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    blob = str(exc).lower()
    return status in (401, 403) and ("insufficient" in blob or "scope" in blob)


def fix_descriptions(video_ids: list[str], *, dry_run: bool = True, yt=None) -> int:
    """Read each video's description back, decode it, and write it again.

    Nothing local can stand in for the read: the ledger keeps a video's title
    but never its description, and data/state.db is git-ignored anyway.
    """
    from googleapiclient.errors import HttpError

    from romanfeed.publish.youtube import client

    yt = yt or client()
    try:
        items = yt.videos().list(part="snippet", id=",".join(video_ids)).execute().get("items", [])
    except HttpError as exc:
        if _is_scope_error(exc):
            print(f"ERROR: {SCOPE_HELP}")
            return 2
        raise

    found = {v["id"]: v for v in items}
    rc = 0
    for vid in video_ids:
        video = found.get(vid)
        if video is None:
            print(f"!! {vid}: not found (wrong id, or the token owns a different channel)")
            rc = 1
            continue
        body = snippet_update_body(video)
        before = (video.get("snippet") or {}).get("description", "")
        after = body["snippet"]["description"]
        print(f"\n=== {vid}: {body['snippet']['title']}")
        print("--- BEFORE ---")
        print(before)
        print("--- AFTER ---")
        print(after)
        if after == before:
            print(f"== {vid}: already clean, nothing to write")
            continue
        if dry_run:
            print(f"== {vid}: dry run, not written")
            continue
        print(f"== {vid}: sending videos.update(part=snippet) with "
              f"title={body['snippet']['title']!r} categoryId={body['snippet']['categoryId']} "
              f"tags={body['snippet']['tags']}")
        yt.videos().update(part="snippet", body=body).execute()
        print(f"== {vid}: description updated")
    return rc


def schedule_videos(video_ids: list[str], at: list[str] | None = None, *, dry_run: bool = True, yt=None) -> int:
    """Give each private video a publishAt so YouTube makes it public itself."""
    import json

    from romanfeed.publish.youtube import client

    times = publish_times(video_ids, at)
    yt = yt or (None if dry_run else client())
    for vid, when in zip(video_ids, times):
        body = schedule_body(vid, when)
        print(f"\n=== {vid}: videos.update(part=status)")
        print(json.dumps(body, indent=2))
        if dry_run:
            print(f"== {vid}: dry run, not written")
            continue
        yt.videos().update(part="status", body=body).execute()
        print(f"== {vid}: scheduled for {when}")
    return 0
