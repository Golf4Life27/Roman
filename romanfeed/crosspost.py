"""Cross-posting: each Short's social cut to TikTok and Instagram.

Every Shorts run renders two cuts of the same image: the YouTube Short
(subscribe call, "on the channel") and a social cut whose voice and end card
point at YouTube instead ("The full 8-hour version is on YouTube, at Space
Screens"). The social cut goes to TikTok and Instagram:

  1. attached to the `social-clips` release on this public repo, so it has a
     public HTTPS URL for Zernio to fetch
  2. sent to the relay (a Make scenario holding the Zernio key; its webhook
     URL is the CROSSPOST_RELAY_URL secret), which creates one Zernio post for
     both platforms, scheduled for the next slot in `crosspost.slots`

The four Shorts runs a day feed the four slots, so there is no queue to run
dry: a run that fails just leaves its slot empty, and GitHub says so.

`crosspost.mode` is the owner's switch: off (render only), test (a TikTok
draft in the app's Creator Inbox), on (both, public). A failure here never fails
the Shorts job: the YouTube Short and its ledger row matter more, so problems
are reported as workflow annotations instead.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from romanfeed.config import ChannelConfig
from romanfeed.publish.metadata import lead_name
from romanfeed.sources.base import ImageAsset
from romanfeed.telescopes import telescope_line, telescopes_for

log = logging.getLogger(__name__)

RELAY_ENV = "CROSSPOST_RELAY_URL"
MODE_ENV = "CROSSPOST_MODE"   # a manual run may force "test"; nothing can force "on"


def effective_mode(cfg: ChannelConfig) -> str:
    """crosspost.mode, unless a manual run asked for a private test."""
    return "test" if os.environ.get(MODE_ENV, "").strip() == "test" else cfg.crosspost.mode


def social_script(youtube_script: str, *, full_label: str) -> str:
    """The YouTube script with its closing swapped for one that points at YouTube.

    The facts stay word for word; only "The full ... version is on the
    channel. Subscribe ..." changes."""
    facts = youtube_script.rsplit(" The full ", 1)[0]
    return f"{facts} The full {full_label} version is on YouTube, at Space Screens. Follow for more space to fall asleep to."


def end_card(full_label: str, handle: str) -> tuple[str, str]:
    """(call, line under it) for the social cut's closing plate."""
    return f"Full {full_label} version on YouTube", handle or "Space Screens"


def next_slot(now: datetime, slots: list[str], tz: str, *, lead_minutes: int = 5) -> datetime:
    """The first slot at least `lead_minutes` after `now`, as an aware UTC time."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    earliest = local + timedelta(minutes=lead_minutes)
    for day in range(0, 3):
        d = (local + timedelta(days=day)).date()
        for t in sorted(slots):
            hh, mm = (int(x) for x in t.split(":"))
            at = datetime(d.year, d.month, d.day, hh, mm, tzinfo=zone)
            if at >= earliest:
                return at.astimezone(timezone.utc)
    raise ValueError("no posting slot in the next three days; check crosspost.slots")


def _tags(asset: ImageAsset, base: list[str]) -> list[str]:
    teles = set(telescopes_for(asset))
    extra = []
    if "webb" in teles:
        extra.append("#jwst")
    if "hubble" in teles:
        extra.append("#hubble")
    tags = list(dict.fromkeys(base + extra))
    return tags[:5]


def caption(asset: ImageAsset, *, script: str | None, full_label: str, handle: str, hashtags: list[str]) -> str:
    """Image name and telescope, one calm line, the YouTube pointer, the credit, 4-5 tags."""
    lead = lead_name(asset.title, limit=80) or asset.title.strip()
    tele = telescope_line(asset)
    first = lead + (f" · {tele}" if tele else "")
    calm = ""
    source = script.rsplit(" The full ", 1)[0] if script else (asset.short_description or "")
    m = re.match(r"(.+?[.!?])(\s|$)", source.strip())
    if m and len(m.group(1)) <= 220:
        calm = m.group(1)
    lines = [first]
    if calm:
        lines.append(calm)
    lines.append(f"Full {full_label} sleep video on YouTube: {handle or 'Space Screens'}")
    credit = asset.credit or asset.source
    if credit:
        lines.append(f"Image: {credit}")
    lines.append(" ".join(_tags(asset, hashtags)))
    return "\n\n".join(lines)[:2000]


def post_body(cfg: ChannelConfig, *, video_url: str, text: str, when: datetime | None) -> dict:
    """The Zernio POST /posts body for this run's mode. `when` None means
    "join the posting queue" (crosspost.queue_id): Zernio gives the post the
    next free slot itself."""
    c = cfg.crosspost
    platforms = []
    test = effective_mode(cfg) == "test"
    if c.tiktok_account:
        platforms.append({"platform": "tiktok", "accountId": c.tiktok_account})
    if c.instagram_account and not test:
        ig = {"platform": "instagram", "accountId": c.instagram_account}
        if c.instagram_trial:
            ig["platformSpecificData"] = {"trialParams": {"graduationStrategy": "SS_PERFORMANCE"}}
        platforms.append(ig)
    body = {
        "content": text,
        "mediaItems": [{"type": "video", "url": video_url}],
        "platforms": platforms,
        "timezone": c.timezone,
        "tiktokSettings": {
            "allowComment": True, "allowDuet": False, "allowStitch": False,
            "videoMadeWithAi": c.made_with_ai,
            "contentPreviewConfirmed": True, "expressConsentGiven": True,
        },
    }
    # TikTok lets this account post only PUBLIC_TO_EVERYONE through the API
    # (creator-info, 2026-10-09), so "only me" is refused. A test goes to the
    # TikTok app's Creator Inbox as a draft instead: private until the owner
    # posts or deletes it in the app.
    if when is None:
        body["queuedFromProfile"] = c.queue_profile
        body["queueId"] = c.queue_id
    else:
        body["scheduledFor"] = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if test:
        body["tiktokSettings"]["draft"] = True
    else:
        body["tiktokSettings"]["privacyLevel"] = "PUBLIC_TO_EVERYONE"
    return body


@dataclass
class Posted:
    post_id: str
    status: str
    scheduled_for: str
    platforms: list[str]


def send(relay_url: str, body: dict, *, timeout: int = 120) -> Posted:
    """Create the post through the relay. Raises on anything but a created post."""
    r = requests.post(relay_url, json={"method": "POST", "path": "/posts", "body": body}, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"relay answered HTTP {r.status_code} without JSON: {r.text[:120]!r}") from None
    if r.status_code != 200 or not data.get("ok") or not data.get("post_id"):
        raise RuntimeError(f"relay answered HTTP {r.status_code}: {data.get('error') or data.get('message') or data}")
    plats = data.get("platforms") or []
    if isinstance(plats, str):  # the relay joins them: "tiktok,instagram"
        plats = [p for p in plats.split(",") if p]
    return Posted(data["post_id"], data.get("post_status", ""), data.get("scheduled_for", ""), plats)


def relay_url() -> str | None:
    return os.environ.get(RELAY_ENV, "").strip() or None


def annotate(level: str, message: str) -> None:
    """A GitHub Actions annotation (shown on the run page), plus the log."""
    getattr(log, "error" if level == "error" else "warning")(message)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level}::{message}")


def post_clip(cfg: ChannelConfig, clip: Path, text: str, *, label: str, slots: list[str] | None = None,
              now: datetime | None = None, release=None, relay: str | None = None) -> Posted | None:
    """Upload `clip` to the release and schedule it with `text` at the next of
    `slots` (default crosspost.slots). None when switched off or not set up;
    never raises (see the module docstring). `label` names it in messages."""
    c = cfg.crosspost
    mode = effective_mode(cfg)
    if mode == "off":
        return None
    relay = relay or relay_url()
    if not relay:
        annotate("warning", f"crosspost mode is {mode} but the {RELAY_ENV} secret is not set; nothing posted")
        return None
    if not (c.tiktok_account or c.instagram_account):
        annotate("warning", "crosspost: no Zernio account ids in the channel config; nothing posted")
        return None
    try:
        if release is None:
            from romanfeed.cozy.release import Release

            release = Release(cfg.cozy.repo, c.release_tag, title="Social clips",
                              body="Vertical clips cross-posted to TikTok and Instagram (romanfeed/crosspost.py). "
                                   f"Deleted after {c.keep_days} days. Do not edit by hand.")
        url = release.upload(clip, clip.name)
        now = now or datetime.now(timezone.utc)
        # A test posts at once (a time already past publishes immediately), so
        # it can be checked on the spot. Shorts join Zernio's posting queue,
        # which hands out the 4 daily slots itself: GitHub's scheduled runs
        # start hours late, so a slot worked out from the run time is unreliable.
        # Clips with slots of their own (the cozy clip) are scheduled directly.
        if mode == "test":
            when = now
        elif slots is None and c.queue_id and c.queue_profile:
            when = None
        else:
            when = next_slot(now, slots or c.slots, c.timezone, lead_minutes=c.lead_minutes)
        posted = send(relay, post_body(cfg, video_url=url, text=text, when=when))
        log.info("crosspost %s: Zernio post %s (%s) for %s on %s", label, posted.post_id, posted.status,
                 posted.scheduled_for, ", ".join(posted.platforms) or "?")
        try:
            gone = release.prune(c.keep_days)
            if gone:
                log.info("crosspost: removed %d clips older than %d days", len(gone), c.keep_days)
        except Exception as exc:  # tidying is not worth a failure
            log.warning("crosspost: pruning old clips failed: %s", exc)
        return posted
    except Exception as exc:
        annotate("error", f"crosspost of {label} failed: {exc}")
        return None


def crosspost(cfg: ChannelConfig, clip: Path, asset: ImageAsset, *, script: str | None, full_label: str,
              now: datetime | None = None, release=None, relay: str | None = None) -> Posted | None:
    """A Short's social cut, captioned from its image and script."""
    text = caption(asset, script=script, full_label=full_label, handle=cfg.channel.handle,
                   hashtags=cfg.crosspost.hashtags)
    return post_clip(cfg, clip, text, label=asset.asset_id, now=now, release=release, relay=relay)
