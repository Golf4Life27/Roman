"""Titles and descriptions for the fly-into journeys.

Feeling first, then the journey, then the proof it is real:
"Flying Into the Pillars of Creation | Real Webb Image | Space Ambient".

Two honesty rules are enforced here, not left to the template:
  * "3D" only appears when the depth was estimated per pixel by the model;
    with brightness layering it is struck from the title.
  * The description says plainly that the motion is made from one still
    image and that its depth is an estimate, not a measured distance."""
from __future__ import annotations

import re

from romanfeed.audio.library import Track
from romanfeed.config import ChannelConfig
from romanfeed.journey.camera import Leg
from romanfeed.publish.metadata import VideoMetadata, _fit_title, _hms, display_name, lead_name, length_text
from romanfeed.sources.base import ImageAsset
from romanfeed.telescopes import telescope_short

NEBULA_HINTS = ("nebula", "nebulae", "pillars of creation", "cosmic cliffs", "star-forming region",
                "star forming region", "stellar nursery", "supernova remnant", "planetary nebula")
GALAXY_HINTS = ("galaxy", "galaxies")
# Archive titles that read as headlines rather than names make poor journeys.
_HEADLINE = re.compile(r"^(?:hubble|webb|nasa|esa)(?:'s)?\s+(?:captures?|spies|sees|snaps|views?|reveals?|finds?|"
                       r"spots?|peers?|images?|observes?|catches)\b", re.I)


def journey_subject(asset: ImageAsset) -> str | None:
    """"nebula" or "galaxy" for a picture a journey may fly into, else None.
    Judged on the title and keywords, like the selector's sky test."""
    head = f"{asset.title} {' '.join(asset.keywords)}".lower()
    if any(h in head for h in NEBULA_HINTS):
        return "nebula"
    if any(h in head for h in GALAXY_HINTS):
        return "galaxy"
    return None


def is_headline(title: str) -> bool:
    return bool(_HEADLINE.match(title.strip()))


_CATALOGUE = re.compile(r"^(?:NGC|IC|M|Messier|Sh2|LDN|LBN|Abell|Arp|ESO|UGC|PGC|RCW|Westerlund|Hen|LEDA|N)\s?-?\d", re.I)


def with_article(name: str) -> str:
    """"the Pillars of Creation", "the Orion Nebula"; but "NGC 602",
    "Stephan's Quintet", "Westerlund 2" stand alone."""
    n = name.strip()
    first = n.split(" ", 1)[0]
    if not n or _CATALOGUE.match(n) or first.lower() in {"the", "a", "an"} or first.endswith(("'s", "’s")):
        return n
    return "the " + n


def journey_verb(sleep: bool) -> str:
    return "Drifting" if sleep else "Flying"


def journey_title(cfg: ChannelConfig, asset: ImageAsset, *, total_seconds: float, sleep: bool, depth_source: str) -> str:
    j = cfg.journey
    template = (j.sleep_title_template if sleep else "") or j.title_template
    tele = telescope_short(asset) or "Telescope"
    lead = with_article(lead_name(display_name(asset), limit=60))
    title = _fit_title(template, lead, {"verb": journey_verb(sleep), "telescope": tele,
                                        "length": length_text(total_seconds), "channel": cfg.channel.name})
    return strip_3d(title, depth_source)[:100]


def strip_3d(title: str, depth_source: str) -> str:
    """A title may say 3D only when the depth was estimated per pixel."""
    if depth_source == "model":
        return title
    t = re.sub(r"\s*\b3-?D\b\s*", " ", title, flags=re.I)
    t = re.sub(r"\s+([|·])", r" \1", t)
    t = re.sub(r"([|·])(\s*[|·])+", r"\1", t)          # a separator left with nothing between
    return re.sub(r"\s{2,}", " ", t).strip(" |·")


DEPTH_WORDS = {
    "model": ("The picture is split into depth layers from a per-pixel depth estimate (made by a small depth-"
              "estimation model, Depth Anything V2 Small), and the layers move at different rates as the camera "
              "pushes in. That depth is a model's estimate from how the picture looks, not a measurement of "
              "real distances."),
    "brightness": ("The picture is split into depth layers by brightness: the stars as the nearest layer, the "
                   "bright gas in the middle and the dark sky farthest, and the layers move at different rates as "
                   "the camera pushes in. That layering is artistic, not a measurement of real distances."),
}

DESCRIPTION = """{hook}

{subscribe}

HOW THIS IS MADE
Every frame comes from one real image: {name}, from {telescope_full}. Nothing is added or painted in. {depth_words}

IN THIS VIDEO
{chapters}

IMAGE CREDIT
- {credit}

MUSIC
{music}
Original music composed for this channel. It opens bright and settles into calm by about {calm} minutes{sleep_note}.

{about}

{hashtags}
"""

HOOK = ("{length} flying into {name}: one real {telescope} image, explored slowly with a camera that pushes in "
        "through its layers, with a new region revealed every few minutes.")
HOOK_SLEEP = ("{length} drifting into {name}, one real {telescope} image, to fall asleep to. The flight settles "
              "within {calm} minutes{dark}.")
ABOUT = ("ABOUT {channel_upper}\nReal images from Hubble, Webb and other public NASA and ESA archives, shown "
         "slowly. Not affiliated with or endorsed by NASA or ESA.")
HASHTAGS = "#space #ambient #sleepmusic"


def leg_chapters(legs: list[Leg], *, dark_after: float | None, total: float) -> list[tuple[float, str]]:
    """One chapter per pass; the first is the approach, the rest are reveals."""
    out: list[tuple[float, str]] = []
    for leg in legs:
        if dark_after is not None and leg.start >= dark_after:
            break
        out.append((leg.start, "Approach" if leg.index == 0 else f"Reveal {leg.index}"))
    if dark_after is not None and dark_after < total:
        out.append((dark_after, f"Screen fades to black, music continues to {_hms(total)}"))
    return out


def build_journey_metadata(cfg: ChannelConfig, asset: ImageAsset, tracks: list[Track], legs: list[Leg], *,
                           total_seconds: float, depth_source: str, sleep: bool = False,
                           dark_after: float | None = None) -> VideoMetadata:
    from romanfeed.telescopes import telescope_line

    title = journey_title(cfg, asset, total_seconds=total_seconds, sleep=sleep, depth_source=depth_source)
    name = with_article(lead_name(display_name(asset), limit=80))
    tele = telescope_short(asset) or "telescope"
    tele_full = (telescope_line(asset) or "a public NASA/ESA archive").removeprefix("Imaged by ")
    calm = int(round(cfg.journey.calm_minutes))
    length = length_text(total_seconds)
    if sleep:
        dark = f", and the screen fades to black after {int(round(dark_after / 60))} minutes while the music plays on" if dark_after else ""
        hook = HOOK_SLEEP.format(length=length, name=name, telescope=tele, calm=calm, dark=dark)
    else:
        hook = HOOK.format(length=length, name=name, telescope=tele)
    chapters = leg_chapters(legs, dark_after=dark_after, total=total_seconds)
    handle = cfg.channel.handle.strip()
    subscribe = (f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1"
                 if handle else "")
    music = "\n".join(f"- {t.title or t.id}" + (f" — {t.artist}" if t.artist else "")
                      for t in {t.id: t for t in tracks}.values()) or "- (none)"
    sleep_note = ", then stays calm for the rest of the video" if sleep else ""
    description = DESCRIPTION.format(
        hook=hook, subscribe=subscribe, name=name, telescope_full=tele_full, depth_words=DEPTH_WORDS[depth_source],
        chapters="\n".join(f"{_hms(t)} {c}" for t, c in chapters), credit=asset.credit or asset.source,
        music=music, calm=calm, sleep_note=sleep_note,
        about=ABOUT.format(channel_upper=cfg.channel.name.upper()), hashtags=HASHTAGS,
    )
    description = re.sub(r"\n{3,}", "\n\n", description).strip() + "\n"
    subject = journey_subject(asset) or "space"
    tags = list(dict.fromkeys([
        "space ambient", "flying into space", "space journey", f"{subject} flythrough", subject, tele.lower(),
        "james webb" if "Webb" in tele else "hubble", "space video", "relaxing space", "space music for sleep",
        "ambient", "sleep music" if sleep else "focus music",
    ] + cfg.publish.tags))[:30]
    return VideoMetadata(
        title=title, description=description[:5000], tags=tags, category_id=cfg.publish.category_id,
        privacy=cfg.publish.privacy, made_for_kids=cfg.publish.made_for_kids, chapters=chapters,
    )
