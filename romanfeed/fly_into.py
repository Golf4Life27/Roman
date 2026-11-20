"""Fly-into Shorts: a 20-30 second flight into one real galaxy or nebula picture.

Test 16 (config `shorts.fly_into_*`, Nov 21-27): 2 of the day's 8 Shorts are
made by the journey code (romanfeed/journey) instead of the slow pan. The
picture is one the channel's long videos showed that no Short and no journey
has used, chosen the way a journey chooses one: a nebula or galaxy
(journey_subject), from a known telescope (telescope_short), not a headline
title. Its depth is estimated (the model when it can run, else brightness
layering), and the camera opens already moving, lands on a bright region by
second 3, drifts, and ends on a push into the brightest part of the picture.

On screen: the picture's name and telescope once the flight has landed, the
subscribe plate over the last seconds, and the image credit throughout. The
title is the picture's plain name with the motion ("Flying into the
Whirlpool Galaxy"); the description says how the motion is made, in the same
words as the journey videos (it is one still image, and its depth is an
estimate, not a measured distance).

Fly-into Shorts are tagged `fly-into` and never take a hook, punch-in,
anticipation or plain-title treatment, so the weekly report keeps them out
of those tests and compares them against calm-style Shorts on their own
(romanfeed/publish/shorts_styles.py). They post to YouTube only. When one
cannot be made the slot is a normal Short and the log says why
(romanfeed/shorts.py run_shorts)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from romanfeed.render.captions import _font
from romanfeed.sources.base import ImageAsset

log = logging.getLogger(__name__)

FLY_TAG = "fly-into"
FLY_PARENT = "fly-into"   # shorts.parent_slug: keeps them out of the long videos' rotation
CENTRAL = ZoneInfo("America/Chicago")
TRIES = 4                 # pictures downloaded and checked before the slot gives up
MAX_UPSCALE = 1.12        # the widest view may enlarge the picture at most this much
NAME_FADE = 0.5
PLATE_SECONDS = 2.5       # the subscribe plate covers the end of the reveal


def in_window(s, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(CENTRAL).date().isoformat()
    return bool(s.fly_into_until and s.fly_into_slots and s.fly_into_per_day) and (
        not s.fly_into_from or day >= s.fly_into_from) and day <= s.fly_into_until


def slot_due(s, n: int, now: datetime | None = None) -> bool:
    """Whether Short number `n` (lifetime count) is a fly-into slot.

    The slots are positions in every run of 16 Shorts, like the sky-event
    ones (romanfeed/sky_events.py slot_due). Hook and calm alternate on n % 2
    (shorts.hook_every 2), the opening tests on n // 2 % 2 and the title test
    on n // 4 % 2. The sky-event slots [0, 6, 11, 13] take one of each
    combination whose three bits add up to an even number; the default
    [2, 4, 9, 15] takes one of each odd one. So each test loses the same
    number from both sides, and while both run the other 8 Shorts in 16 hold
    every combination exactly once. 4 in 16: 2 a day at 8 a day."""
    return in_window(s, now) and n % 16 in set(s.fly_into_slots or [])


def fits(width: int, height: int, out_w: int, out_h: int) -> bool:
    """Whether a picture is big enough to fly into on a vertical screen: the
    widest view enlarges it at most MAX_UPSCALE (the flight closes in from
    there, as far as the picture's detail allows)."""
    if width <= 0 or height <= 0:
        return False
    return max(out_w / width, out_h / height) <= MAX_UPSCALE


def name(asset: ImageAsset) -> str:
    """"the Whirlpool Galaxy", "NGC 602": the journey videos' way of naming it."""
    from romanfeed.journey.metadata import with_article
    from romanfeed.publish.metadata import display_name, lead_name

    return with_article(lead_name(display_name(asset), limit=60) or asset.title)


def title(asset: ImageAsset) -> str:
    return f"Flying into {name(asset)}"[:100]


def metadata(cfg, asset: ImageAsset, *, link_id: str | None, link_label: str, tracks, depth_source: str):
    """Title, description and tags, in the Shorts' usual shape."""
    from romanfeed.journey.metadata import DEPTH_WORDS, journey_subject
    from romanfeed.publish.metadata import VideoMetadata, lead_name
    from romanfeed.telescopes import telescope_line, telescope_short

    lead = lead_name(asset.title, limit=60) or asset.title
    tele = telescope_short(asset)
    line = telescope_line(asset)
    music = ", ".join(dict.fromkeys(t.title or t.id for t in tracks)) or "ambient"
    lines = []
    if link_id:
        lines += [f"Full {link_label} sleep video: https://youtu.be/{link_id}", ""]
    lines += [
        f"{lead}." + (f" {line}." if line else ""),
        f"Flying into {name(asset)}: every frame of this Short comes from one real "
        f"{tele + ' ' if tele else ''}image. Nothing is added or painted in. {DEPTH_WORDS[depth_source]}",
        "",
        f"Image: {asset.credit or asset.source}",
        f"Music: {music} (composed for this channel)",
    ]
    handle = cfg.channel.handle.strip()
    if handle:
        lines += ["", f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1"]
    lines += ["", "#shorts #space #sleep"]
    subject = journey_subject(asset) or "space"
    tags = list(dict.fromkeys([FLY_TAG, "shorts", "space", "flying into space", "space journey",
                               f"{subject} flythrough", lead.lower(), *(t.lower() for t in [tele] if t), subject,
                               "space for sleep", "relaxing space"]))
    return VideoMetadata(
        title=title(asset), description="\n".join(lines)[:5000], tags=tags[:20],
        category_id=cfg.publish.category_id, privacy=cfg.shorts.privacy, made_for_kids=cfg.publish.made_for_kids)


def credit_card(asset: ImageAsset, width: int, height: int) -> Image.Image:
    """The image credit, small, just above the Shorts player's bottom fifth."""
    from romanfeed.render.cards import _centered, _wrap

    s = width / 1080
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    small = _font(int(26 * s))
    lines = _wrap(d, f"Image: {asset.credit or asset.source}", small, width * 0.80, 2)
    y = int(height * 0.79) - len(lines) * int(34 * s)
    for ln in lines:
        y = _centered(layer, y, ln, small, (225, 230, 240, 230), blur=6) + int(8 * s)
    return layer


def overlay(cfg, asset: ImageAsset, *, seconds: float, link_label: str):
    """The cards, as one overlay(seconds, frame) for render_journey_short:
    nothing but the credit until the flight lands (it opens already moving,
    with no title card), the name and telescope until the final reveal
    starts, the subscribe plate over its last seconds."""
    from romanfeed import narration
    from romanfeed.curation.selector import is_earth
    from romanfeed.journey.render import alpha_overlay, chain
    from romanfeed.journey.short import PAYOFF_BY, REVEAL_FOR
    from romanfeed.render.cards import short_cta_card, short_overlay

    s = cfg.shorts
    handle = cfg.channel.handle or cfg.channel.name
    call = narration.subscribe_call(asset.asset_id, earth=is_earth(asset))[1]
    named = short_overlay(asset, s.width, s.height, full_label=link_label, handle=handle, cta=False)
    plate = short_cta_card(s.width, s.height, full_label=link_label, handle=handle, call=call)
    return chain(
        alpha_overlay(named, PAYOFF_BY, seconds - REVEAL_FOR, NAME_FADE),
        alpha_overlay(plate, seconds - PLATE_SECONDS, seconds + 1.0, 0.4),
        alpha_overlay(credit_card(asset, s.width, s.height), 0.0, seconds + 1.0, 0.0),
    )
