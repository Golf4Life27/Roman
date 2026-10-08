"""Title, description, and tags for one video.

Everything comes from the channel config templates plus the actual assets in
the video, so every upload is unique and carries real image credits. That
credit list doubles as the value-add YouTube's inauthentic-content policy
looks for: a viewer can see what they are looking at and who imaged it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from romanfeed.audio.library import Track
from romanfeed.config import ChannelConfig
from romanfeed.sources.base import ImageAsset
from romanfeed.telescopes import TELESCOPES, telescope_short, telescopes_for

DEFAULT_DESCRIPTION = """{hook}

{subscribe}

{tagline}

IN THIS VIDEO
{chapters}

IMAGE CREDITS
{credits}

MUSIC
{music}

{roman_note}

New space sleep videos every week.
https://spacescreens.app
Not affiliated with or endorsed by NASA or ESA.

{hashtags}
"""

# The first two lines are what search shows under the title, so they carry the
# phrases sleep-searchers type, in a sentence a person would write.
HOOK_SLEEP = (
    "{length} of deep sleep music with slow views of real {telescopes} space telescope images. "
    "The screen fades to black after {dark_after} so the light won't keep you awake; the music plays on."
)
HOOK_SLEEP_NO_DARK = "{length} of deep sleep music with slow views of real {telescopes} space telescope images. Put it on, dim the lights, and let it run."
HOOK = (
    "{length} of relaxing space music for sleep, study and calm: slow views across {n_images} real "
    "{telescopes} space telescope images, opening on {lead}."
)
HASHTAGS = "#sleepmusic #space #deepsleep"

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


# Archive titles carry instrument tags ("(NIRCam Image)") and long subtitles
# after a dash ("Westerlund 2 - Hubble's 25th anniversary image"). Neither
# belongs in a video title.
_PAREN_SUFFIX = re.compile(r"\s*\([^()]*\)\s*$")
_DASH_TAIL = re.compile(r"\s+[-–—]\s+.*$", re.DOTALL)
_TRAILING_JUNK = " \t.,;:-–—…"


def _shorten(text: str, limit: int) -> str:
    """Cut to at most `limit` characters, at a word boundary where there is one."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    head = text[:limit]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(_TRAILING_JUNK) or head.rstrip(_TRAILING_JUNK)


def lead_name(title: str, limit: int = 40) -> str:
    """A short human name for one image, for the title's {lead} token.

    Chapters show the asset title nearly verbatim; a title has far less room
    and is read at a glance, so the archive furniture comes off: a trailing
    parenthetical instrument tag, anything after a dash, trailing ellipsis
    from an already-clipped title."""
    text = " ".join(str(title or "").split())
    for _ in range(2):  # a tag can hide a dash and vice versa
        text = _DASH_TAIL.sub("", text)
        while True:
            stripped = _PAREN_SUFFIX.sub("", text)
            if stripped == text:
                break
            text = stripped
    text = text.rstrip(_TRAILING_JUNK)
    return _shorten(text, limit)


def _fit_title(template: str, lead: str, tokens: dict, limit: int = 100) -> str:
    """Render the title, shrinking {lead} until it fits.

    YouTube cuts titles at 100 characters. The search phrases live at the tail,
    so overflow comes out of the lead -- at a word boundary -- not off the end."""
    title = template.format(lead=lead, **tokens)
    if len(title) <= limit or not lead:
        return title
    # A whole name beats a fixed tail phrase: drop a last " · " segment that
    # carries no token ("· Telescope Screensaver") before cutting the name.
    parts = re.split(r"(\s+·\s+)", template)
    if len(parts) >= 3 and "{" not in parts[-1]:
        shorter = "".join(parts[:-2]).format(lead=lead, **tokens)
        if len(shorter) <= limit:
            return shorter
    lead = _shorten(lead, len(lead) - (len(title) - limit))
    # "Cosmic Cliffs in the" reads as a mistake; end on a content word.
    lead = re.sub(r"(?:\s+(?:the|of|in|and|a|an|at|with|from|by|on|&))+$", "", lead, flags=re.IGNORECASE)
    return template.format(lead=lead, **tokens)


def drop_segment(template: str, token: str) -> str:
    """Remove the ' · '/' | '-separated part of a title template holding `token`.

    The sleep template says "Dark Screen After {dark_after}"; a sleep cut that
    does not go dark (older uploads, or the setting off) must not claim it."""
    if token not in template:
        return template
    parts = re.split(r"(\s+[·|]\s+)", template)
    keep: list[str] = []
    for i in range(0, len(parts), 2):
        if token in parts[i]:
            continue
        if keep:
            keep.append(parts[i - 1])
        keep.append(parts[i])
    return "".join(keep)


def _minutes(seconds: float) -> str:
    return f"{int(round(seconds / 60))} Min"


def telescope_names(assets: list[ImageAsset]) -> str:
    """'Hubble and Webb' -- the telescopes behind most of the set, for prose."""
    counts: dict[str, int] = {}
    for a in assets:
        for k in telescopes_for(a):
            counts[k] = counts.get(k, 0) + 1
    short = {k: s for k, _f, s, _p in TELESCOPES}
    top = [short[k] for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:2]]
    return " and ".join(sorted(top)) if top else "NASA and ESA"


def build_metadata(cfg: ChannelConfig, assets: list[ImageAsset], tracks: list[Track], *, seconds_per_image: float,
                   when: date | None = None, chapter_limit: int | None = None, total_seconds: float | None = None,
                   sleep: bool = False, dark_after: float | None = None) -> VideoMetadata:
    """`total_seconds` is the running time when it is longer than the images
    shown (a sleep cut that goes dark); `dark_after` is when it goes dark."""
    when = when or date.today()
    total = total_seconds or seconds_per_image * len(assets)
    length = length_text(total)
    subject = pick_subject(assets)

    chapters = [(i * seconds_per_image, _clip_title(a.title)) for i, a in enumerate(assets)]
    # An 8-hour cut has hundreds of chapters; the description caps at 5000
    # characters, so listing them all would silently swallow the credits.
    shown = chapters if chapter_limit is None else chapters[:chapter_limit]

    def chapter_block(n: int) -> str:
        lines = [f"{_hms(t)} {title}" for t, title in shown[:n]]
        if dark_after:
            if n < len(shown):
                lines.append("…")
            lines.append(f"{_hms(dark_after)} Screen fades to black, music continues to {_hms(total)}")
        elif n < len(chapters) or total > seconds_per_image * len(assets):
            lines.append(f"(then the sequence continues to {_hms(total)})")
        return "\n".join(lines)
    credits = "\n".join(sorted({f"- {a.credit or a.source}" for a in assets}))
    music = "\n".join(f"- {t.title or t.id}" + (f" — {t.artist}" if t.artist else "") for t in {t.id: t for t in tracks}.values()) or "- (none)"

    # The first chapter names the video: the same set of images with a different
    # opener gets a different title, so runs do not stack up identical uploads.
    lead = lead_name(assets[0].title, limit=60) if assets else ""
    tele = telescope_short(assets[0]) if assets else None
    template = (cfg.publish.sleep_title_template if sleep else "") or cfg.publish.title_template
    if not dark_after:
        template = drop_segment(template, "{dark_after}")
    title_lead = lead
    if "{lead_by}" in template:
        # "{lead_by}" is "{lead} by Webb" when the telescope is known. It goes
        # through the same shrink-to-fit as {lead}, and a shrink that eats the
        # telescope must not leave a dangling "by".
        template = template.replace("{lead_by}", "{lead}")
        title_lead = f"{lead} by {tele}" if tele and not any(t.lower() in lead.lower() for t in tele.split(" & ")) else lead
    dark_label = _minutes(dark_after) if dark_after else ""
    title = _fit_title(
        template, title_lead,
        {"length": length, "subject": subject, "date": when.isoformat(), "channel": cfg.channel.name,
         "telescope": tele or "Telescope", "dark_after": dark_label},
    )
    title = re.sub(r"\s+by(?=\s*(?:[|·]|$))", "", title)
    telescopes = telescope_names(assets)
    if sleep:
        hook = (HOOK_SLEEP if dark_after else HOOK_SLEEP_NO_DARK).format(
            length=length, telescopes=telescopes, dark_after=_minutes(dark_after).replace("Min", "minutes") if dark_after else "")
    else:
        hook = HOOK.format(length=length, n_images=len(assets), telescopes=telescopes, lead=lead or "deep space")
    handle = cfg.channel.handle.strip()
    subscribe = (f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1"
                 if handle else "")
    template = cfg.publish.description_template or DEFAULT_DESCRIPTION
    def render(n: int) -> str:
        text = template.format(
            tagline=cfg.channel.tagline, length=length, n_images=len(assets), genre=cfg.audio.genre,
            chapters=chapter_block(n), credits=credits, music=music,
            roman_note=ROMAN_NOTE.format(channel_upper=cfg.channel.name.upper()), date=when.isoformat(),
            hook=hook, subscribe=subscribe, hashtags=HASHTAGS, lead=lead,
        )
        return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    # YouTube stops at 5000 characters. Chapters are the only part that can
    # give way: credits are a licence condition (CC BY), and the dark-screen
    # chapter and the hashtags matter more than chapter 61.
    n = len(shown)
    description = render(n)
    while len(description) > 5000 and n > 1:
        n = max(1, n - max(1, (len(description) - 5000) // 60))
        description = render(n)
    extra = ["sleep music", "deep sleep music", "black screen sleep music", "fall asleep fast"] if sleep else ["space music for sleep"]
    tags = list(dict.fromkeys(cfg.publish.tags + extra + [subject.lower(), "space", "sleep screen", "ambient"]))[:30]
    return VideoMetadata(
        title=title[:100], description=description[:5000], tags=tags, category_id=cfg.publish.category_id,
        privacy=cfg.publish.privacy, made_for_kids=cfg.publish.made_for_kids, chapters=chapters,
    )
