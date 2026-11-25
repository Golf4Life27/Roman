"""Space rhyme Shorts for kids: a four-line rhyme over a real NASA or ESA picture.

Test 23 (config `shorts.rhyme_*`, from Nov 26): three Shorts inside the day's
8, no extra slots, in calm slots only. Each is 12 to 15 seconds: one of
RHYMES read aloud by a bright voice over a bouncy tune, its lines on screen
one at a time as they are read, over a slow pan across a NASA or ESA picture
of the thing the rhyme is about.

Everything is our own: the rhymes are written here (not drawn from or set to
any existing rhyme, song or tune) and the music is composed note by note by
romanfeed/workshop/music.py from a seed, sleigh bells off. Every fact a rhyme
states sits in its `facts` with the NASA page it was read at, the day it was
read and the page's own words; the tests keep a rhyme from going out without
one.

These three, and only these, are uploaded made for kids, like the Santa's
Workshop videos. Made-for-kids Shorts lose comments, the notification bell
and personalised ads, so they earn less per view and a subscribe nudge works
differently: the report judges them on subscribers per 1,000 views and views
per Short against the usual Shorts of the same days. Tagged `space-rhyme`,
posted to YouTube only, and kept out of every other test's numbers
(romanfeed/publish/shorts_styles.py). When one cannot be made the slot is a
normal Short and the log says why."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

RHYME_TAG = "space-rhyme"
RHYME_PARENT = "space-rhyme"   # shorts.parent_slug: kept out of the long videos' rotation
CENTRAL = ZoneInfo("America/Chicago")

SECONDS = 14.0          # 12 to 15 s, the length the test asks for
MIN_SECONDS = 12.0
MAX_SECONDS = 15.0
VOICE_START = 0.6
TAIL = 2.2              # music and the closing plate after the last line
VOICE = "en-US-Chirp3-HD-Leda"   # a bright, warm voice for a children's rhyme
VOICE_RATE = 1.0


@dataclass(frozen=True)
class Fact:
    claim: str         # what the rhyme says, in plain words
    org: str           # "NASA", "ESA"
    url: str
    read: str          # the day it was read, and the page's own date
    quote: str         # the page's own words


@dataclass(frozen=True)
class Rhyme:
    id: str
    subject: str                 # moon, nebula or galaxy: what the picture must show
    lines: tuple[str, str, str, str]
    title: str
    facts: tuple[Fact, ...]

    @property
    def text(self) -> str:
        return " ".join(self.lines)


MOONLIGHT = Fact(
    "The Moon shines with sunlight it reflects, not its own light", "NASA",
    "https://science.nasa.gov/moon/moonlight/", "read Nov 25, 2026 (page updated Feb 12, 2026)",
    'The Moon does not make its own light. "Moonlight" is actually reflected sunlight.')
MOON_WALKERS = Fact(
    "Twelve people have walked on the Moon", "NASA",
    "https://science.nasa.gov/moon/moon-walkers", "read Nov 25, 2026 (page updated May 27, 2026)",
    'Neil Armstrong and Edwin "Buzz" Aldrin were the first of 12 human beings to walk on the Moon.')
NEBULA = Fact(
    "A nebula is a cloud of dust and gas where new stars can form", "NASA",
    "https://spaceplace.nasa.gov/nebula/en", "read Nov 25, 2026 (page updated Jul 31, 2026)",
    "A nebula is a giant cloud of dust and gas in space. ... Other nebulae are regions where new stars "
    "are beginning to form.")
GALAXY = Fact(
    "A galaxy holds billions of stars, and ours is the Milky Way", "NASA",
    "https://spaceplace.nasa.gov/galaxy/en/", "read Nov 25, 2026 (page updated Jul 31, 2026)",
    "A galaxy is a huge collection of gas, dust, and billions of stars and their solar systems, all held "
    "together by gravity. ... It's a small part of the Milky Way Galaxy.")

RHYMES: tuple[Rhyme, ...] = (
    Rhyme(
        id="moon", subject="moon",
        lines=("Hello, Moon, up high and bright,",
               "you borrow sunshine for your light.",
               "Twelve explorers found their way:",
               "they walked on you, then flew away!"),
        title="Hello, Moon! A Space Rhyme for Kids",
        facts=(MOONLIGHT, MOON_WALKERS)),
    Rhyme(
        id="nebula", subject="nebula",
        lines=("A swirly cloud of dust and gas,",
               "glowing bright like colored glass.",
               "Deep inside it, very far,",
               "dust and gas grow into stars!"),
        title="The Star Cloud: A Space Rhyme for Kids",
        facts=(NEBULA,)),
    Rhyme(
        id="galaxy", subject="galaxy",
        lines=("A galaxy's a home for stars,",
               "more than you could fit in jars!",
               "We live in one, the Milky Way:",
               "billions of stars, hip hip hooray!"),
        title="A Home for Stars: A Space Rhyme for Kids",
        facts=(GALAXY,)),
)

# A nebula picture for the nebula rhyme must be one where stars are forming,
# since that is what the rhyme says happens inside it.
STAR_FORMING = re.compile(
    r"\b(star[- ]forming|star formation|forming stars|stars? (?:are|is) (?:being )?born|young stars|"
    r"newborn stars|baby stars|protostars?|stellar nurser(?:y|ies)|nurser(?:y|ies)|pillars? of creation)\b",
    re.I)
MOON_PICTURES = "moon"   # the hand-checked NASA Moon photos in data/sky_events.yaml


# --- when -------------------------------------------------------------------

def in_window(s, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(CENTRAL).date().isoformat()
    return bool(s.rhyme_from and s.rhyme_total and s.rhyme_slots) and day >= s.rhyme_from and (
        not s.rhyme_until or day <= s.rhyme_until)


def slot_due(s, n: int, posted: int, now: datetime | None = None) -> bool:
    """Short number `n` (lifetime count) is a rhyme slot, with `posted`
    already up. Slots 0 and 4 of every 16 are calm (n even, and calm under
    any 6-hook/2-calm split that keeps n % 8 in {0, 4} calm), carry no end
    card (n % 4 == 0) and are not you-are-here or how-long slots. The rhyme
    check runs after the fly-into and sky-event checks, so while those tests
    run they keep their slots (sky-event 0 to Nov 25, fly-into 4 to Nov 27)."""
    return posted < s.rhyme_total and in_window(s, now) and n % 16 in set(s.rhyme_slots)


def next_rhyme(done_ids: list[str]) -> Rhyme | None:
    done = set(done_ids)
    return next((r for r in RHYMES if asset_id(r) not in done), None)


def asset_id(r: Rhyme) -> str:
    return f"rhyme:{r.id}"


# --- pictures -----------------------------------------------------------------

def nasa_or_esa(asset) -> bool:
    """A NASA or ESA picture: never a Wikimedia Commons file, whoever made it."""
    if asset.asset_id.startswith("commons:"):
        return False
    return bool(re.search(r"\b(NASA|ESA)\b", f"{asset.source} {asset.credit}"))


def fits(rhyme: Rhyme, asset) -> bool:
    """Whether the picture shows what the rhyme is about."""
    from romanfeed.curation.selector import is_earth
    from romanfeed.why import subject

    if not nasa_or_esa(asset) or is_earth(asset):
        return False
    if subject(asset) != rhyme.subject:
        return False
    if rhyme.subject == "nebula":
        text = " ".join([asset.title, " ".join(asset.keywords), asset.description])
        return bool(STAR_FORMING.search(text))
    return True


# --- timing -------------------------------------------------------------------

def length(voice_secs: float | None) -> float:
    """The Short's length: the voice plus the tail, held inside 12 to 15 s."""
    if voice_secs is None:
        return SECONDS
    return min(MAX_SECONDS, max(MIN_SECONDS, VOICE_START + voice_secs + TAIL))


def voice_fits(voice_secs: float) -> bool:
    return VOICE_START + voice_secs + 1.0 <= MAX_SECONDS


def line_times(rhyme: Rhyme, *, start: float, spoken: float) -> list[tuple[float, float, str]]:
    """Each line on screen while it is read: the reading time split by length."""
    weights = [len(ln) for ln in rhyme.lines]
    total = sum(weights)
    out, t = [], start
    for ln, w in zip(rhyme.lines, weights):
        d = spoken * w / total
        out.append((round(t, 3), round(t + d, 3), ln))
        t += d
    return out


# --- what goes up -------------------------------------------------------------

def description(rhyme: Rhyme, asset, *, handle: str) -> str:
    lines = [*rhyme.lines, "",
             "A short space rhyme for kids, over a real space picture.",
             f"Picture: {asset.title} ({asset.credit or asset.source}).", ""]
    lines.append("Every fact in the rhyme, checked:")
    for f in rhyme.facts:
        lines.append(f"- {f.claim}. {f.org}: {f.url}")
    lines += ["", "The rhyme and the music are our own.", f"More space every day: {handle}",
              "", "#space #kids #shorts"]
    return "\n".join(lines)


def metadata(cfg, rhyme: Rhyme, asset):
    """Made for kids, like the Santa's Workshop videos, and only these Shorts."""
    from romanfeed.publish.metadata import VideoMetadata

    handle = cfg.channel.handle or cfg.channel.name
    return VideoMetadata(
        title=rhyme.title, description=description(rhyme, asset, handle=handle),
        tags=[RHYME_TAG, "space rhyme", "kids space", "space for kids", "nursery rhyme", rhyme.subject,
              "shorts"],
        category_id=cfg.publish.category_id, privacy=cfg.shorts.privacy, made_for_kids=True)
