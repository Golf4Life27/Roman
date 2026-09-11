"""Title, description, and tags for one video.

Everything comes from the channel config templates plus the actual assets in
the video, so every upload is unique and carries real image credits. That
credit list doubles as the value-add YouTube's inauthentic-content policy
looks for: a viewer can see what they are looking at and who imaged it."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from romanfeed.audio.library import Track
from romanfeed.config import ChannelConfig
from romanfeed.sources.base import ImageAsset

DEFAULT_DESCRIPTION = """{tagline}

{length} of slow, drifting views across {n_images} space telescope images with calm {genre} music. Put it on, dim the lights, and let it run.

IN THIS VIDEO
{chapters}

IMAGE CREDITS
{credits}

MUSIC
{music}

{roman_note}

New space video every day. Subscribe to keep the sky on.
https://spacescreens.app
Not affiliated with or endorsed by NASA or ESA.
"""

ROMAN_NOTE = (
    "ABOUT {channel_upper}\n"
    "Real images from Hubble, Webb and other public NASA and ESA archives, shown slowly. "
    "When NASA's Nancy Grace Roman Space Telescope begins releasing science images in 2027, "
    "they appear here automatically."
)


@dataclass
class VideoMetadata:
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    category_id: str = "28"
    privacy: str = "private"
    made_for_kids: bool = False
    chapters: list[tuple[float, str]] = field(default_factory=list)


def _hms(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def length_text(seconds: float) -> str:
    """'8 Hours', '1 Hour', '45 Minutes' — for titles and descriptions."""
    hours = seconds / 3600
    if hours >= 1:
        n = int(round(hours))
        return f"{n} Hour{'s' if n != 1 else ''}"
    mins = max(int(round(seconds / 60)), 1)
    return f"{mins} Minute{'s' if mins != 1 else ''}"


def pick_subject(assets: list[ImageAsset]) -> str:
    """A human subject for the title: the most common broad theme in the set."""
    themes = {
        "Nebulae": ("nebula",), "Galaxies": ("galaxy", "galaxies"), "Star Clusters": ("cluster",),
        "Deep Fields": ("deep field",), "Planets": ("jupiter", "saturn", "mars", "planet"),
        "Roman Telescope Images": ("roman",),
    }
    scores = {k: 0 for k in themes}
    for a in assets:
        blob = f"{a.title} {' '.join(a.keywords)}".lower()
        for theme, hints in themes.items():
            if any(h in blob for h in hints):
                scores[theme] += 1
    # Roman is the headline act: any Roman science image makes it the subject.
    if scores["Roman Telescope Images"] > 0:
        return "Roman Telescope Images"
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Deep Space"


def _clip_title(title: str, limit: int = 80) -> str:
    """Chapter captions are capped so the description stays under YouTube's
    5000 characters. A hard slice cuts mid-word ("...over 160,000 lig"), so
    fall back to the last word boundary and mark the cut."""
    text = " ".join(title.split())
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,;:-") + "\u2026"


def build_metadata(cfg: ChannelConfig, assets: list[ImageAsset], tracks: list[Track], *, seconds_per_image: float, when: date | None = None, chapter_limit: int | None = None) -> VideoMetadata:
    when = when or date.today()
    total = seconds_per_image * len(assets)
    length = length_text(total)
    subject = pick_subject(assets)

    chapters = [(i * seconds_per_image, _clip_title(a.title)) for i, a in enumerate(assets)]
    # An 8-hour cut has hundreds of chapters; the description caps at 5000
    # characters, so listing them all would silently swallow the credits.
    shown = chapters if chapter_limit is None else chapters[:chapter_limit]
    chapter_lines = "\n".join(f"{_hms(t)} {title}" for t, title in shown)
    if len(shown) < len(chapters):
        chapter_lines += f"\n(then the sequence continues to {_hms(seconds_per_image * len(assets))})"
    credits = "\n".join(sorted({f"- {a.credit or a.source}" for a in assets}))
    music = "\n".join(f"- {t.title or t.id}" + (f" — {t.artist}" if t.artist else "") for t in {t.id: t for t in tracks}.values()) or "- (none)"

    title = cfg.publish.title_template.format(length=length, subject=subject, date=when.isoformat(), channel=cfg.channel.name)
    template = cfg.publish.description_template or DEFAULT_DESCRIPTION
    description = template.format(
        tagline=cfg.channel.tagline, length=length, n_images=len(assets), genre=cfg.audio.genre,
        chapters=chapter_lines, credits=credits, music=music,
        roman_note=ROMAN_NOTE.format(channel_upper=cfg.channel.name.upper()), date=when.isoformat(),
    )
    tags = list(dict.fromkeys(cfg.publish.tags + [subject.lower(), "space", "sleep screen", "ambient"]))[:30]
    return VideoMetadata(
        title=title[:100], description=description[:5000], tags=tags, category_id=cfg.publish.category_id,
        privacy=cfg.publish.privacy, made_for_kids=cfg.publish.made_for_kids, chapters=chapters,
    )
