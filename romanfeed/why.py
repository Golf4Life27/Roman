""""Why" Shorts: open on a question the source itself answers.

Test Nov 6-13 (main chat, under the owner's Nov 4 order to test daily): part
of the Shorts open on a short "why" question, such as "Why does the Cat's
Head Nebula glow red?", answered by a sentence from NASA's or ESA's own
caption for the image, with that source named and linked in the description.

Nothing is generated. A question is only asked when one sentence of the
caption gives the cause in so many words ("glows red because...", "dark lanes
of dust block..."), and the answer read out is that sentence. An image whose
caption holds no such sentence is never a "why" Short; the picker moves on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from romanfeed.narration import _DANGLING, _usable, clean_caption, sentences
from romanfeed.sources.base import ImageAsset

WHY_TAG = "why-caption"

# The sentence has to say the cause, not just mention two things together.
_CAUSE = re.compile(
    r"\b(because|due to|caused by|comes? from|came from|produced by|powered by|energi[sz]ed by|excited by|"
    r"ioni[sz]ed by|illuminated by|lit (?:up )?by|heated by|as a result of|which is why|thanks to|"
    r"emitted by|given off by)\b", re.I)
_COLOUR = r"(red|pink|blue|green|orange|purple|violet|golden|yellow)"
# The colour has to sit right next to the glow: "glows red", "red glow",
# "glowing pink", not a red star somewhere else in the sentence.
_GLOW_COLOUR = re.compile(rf"\b(?:glow(?:s|ing)?|shines?|shining)\s+(?:a\s+)?(?:deep\s+|bright\s+|faint\s+)?{_COLOUR}\b|"
                          rf"\b{_COLOUR}(?:dish)?\s+glow\b", re.I)
_GLOW = re.compile(r"\b(glow(?:s|ing)?|shines?|shining)\b", re.I)
_DARK_DUST = re.compile(r"\bdark\b.*\bdust\b|\bdust\b.*\bdark\b", re.I)
_BLOCKS = re.compile(r"\b(block|obscur|absorb|hid(?:e|es|ing|den))\w*", re.I)
_SHAPED = re.compile(r"\b(shaped|sculpted|carved|blown|distorted|stretched|warped|pulled|twisted)\s+"
                     r"(?:out\s+|apart\s+|into\s+[a-z ]+?\s+)?by\b", re.I)
_STARBIRTH = re.compile(r"\b(star formation|star[- ]forming|stars? (?:are|were) (?:being )?(?:born|formed|forming)|"
                        r"new stars|stellar nursery|birth of (?:new )?stars)\b", re.I)
_TRIGGER = re.compile(r"\b(trigger\w*|compress\w*|collaps\w*|squeez\w*|sparked)\b", re.I)


@dataclass(frozen=True)
class Why:
    question: str   # "Why does the Cat's Head Nebula glow red?"
    answer: str     # the caption's own sentence that says why
    source: str     # "ESA/Hubble"
    url: str        # the image's page in that archive


def source_page(asset: ImageAsset) -> str | None:
    """The image's own page in the archive its caption came from, or None."""
    prefix, _, ident = asset.asset_id.partition(":")
    if not ident:
        return None
    if prefix == "esa":
        return f"https://esahubble.org/images/{ident}/"
    if prefix == "esawebb":
        return f"https://esawebb.org/images/{ident}/"
    if prefix == "nasa":
        return f"https://images.nasa.gov/details/{ident}"
    return None   # Wikimedia Commons and the like are not the primary source


def _subject(lead: str) -> tuple[str, str]:
    """("does"/"do", lead) so the question reads right for "the Pillars of Creation"."""
    head = re.split(r"\s+of\s+", lead, maxsplit=1)[0]     # "the Pillars" of "the Pillars of Creation"
    last = head.split()[-1].lower() if head.split() else ""
    plural = last.endswith("s") and not last.endswith(("ss", "us", "is", "'s")) and not re.match(r"^\d", last)
    return ("do" if plural else "does"), re.sub(r"^The\b", "the", lead)   # mid-sentence: "Why does the ..."


def question_for(sentence: str, lead: str) -> str | None:
    """The why question this sentence answers about `lead`, or None."""
    verb, who = _subject(lead)
    be = "are" if verb == "do" else "is"
    if _GLOW_COLOUR.search(sentence) and _CAUSE.search(sentence):
        m = _GLOW_COLOUR.search(sentence)
        colour = next(g for g in m.groups() if g).lower()
        return f"Why {verb} {who} glow {colour}?"
    if _GLOW.search(sentence) and _CAUSE.search(sentence):
        return f"Why {verb} {who} glow?"
    if _DARK_DUST.search(sentence) and _BLOCKS.search(sentence):
        return f"What makes the dark patches in {who}?"
    if _SHAPED.search(sentence):
        return f"What shaped {who}?"
    if _STARBIRTH.search(sentence) and _TRIGGER.search(sentence):
        return f"Why {be} new stars forming in {who}?"
    return None


def find_why(asset: ImageAsset, lead: str) -> Why | None:
    """The first sentence of the caption that answers a why question, as a Why."""
    url = source_page(asset)
    if not url or not lead:
        return None
    prev = ""
    core = re.sub(r"^the\s+", "", lead, flags=re.I).lower()
    for s in sentences(clean_caption(asset.description)):
        prev, s_prev = s, prev
        if not _usable(s):
            continue
        if _DANGLING.match(s):
            # "Its red glow comes from..." answers the question only when the
            # sentence before it was about the object itself, not a star in it.
            about_it = re.match(r"^Its?\b", s) and (
                re.sub(r"^the\s+", "", s_prev, flags=re.I).lower().startswith(core)
                or re.match(r"^This (?:image|picture|nebula|galaxy|cluster|object|region|cloud)\b", s_prev))
            if not about_it:
                continue
        q = question_for(s, lead)
        if q:
            return Why(question=q, answer=s, source=asset.source, url=url)
    return None


SUBJECTS = ("nebula", "galaxy", "cluster", "other")
_SUBJECT_RE = (
    ("nebula", re.compile(r"\b(nebula|nebulae|supernova remnant|pillars?|star[- ]forming region)\b", re.I)),
    ("galaxy", re.compile(r"\b(galax(?:y|ies)|spiral|messier 8[1-9]|andromeda)\b", re.I)),
    ("cluster", re.compile(r"\b(cluster|globular|open cluster|ngc \d+ cluster)\b", re.I)),
)


def subject(asset: ImageAsset) -> str:
    """nebula, galaxy, cluster or other, from the title first, then keywords and caption."""
    for text in (asset.title, " ".join(asset.keywords), asset.description[:400]):
        for name, rx in _SUBJECT_RE:
            if rx.search(text or ""):
                return name
    return "other"


def why_script(asset: ImageAsset, why: Why, *, full_label: str) -> str:
    """Question, the caption's answer, the pointer and the usual subscribe call."""
    from romanfeed.narration import subscribe_call
    from romanfeed.telescopes import telescopes_for

    earth = bool(telescopes_for(asset)) and telescopes_for(asset)[0] == "iss"
    return " ".join([why.question, why.answer, f"The full {full_label} version is on the channel.",
                     subscribe_call(asset.asset_id, earth=earth)[0]])


def in_why_test(s, now=None) -> bool:
    """True while the why test runs (through `why_until`, Central time)."""
    from datetime import date, datetime, timedelta, timezone

    if not (s.why_every and s.why_until):
        return False
    now = now or datetime.now(timezone.utc)
    central = (now.astimezone(timezone.utc) - timedelta(hours=5)).date()
    return central <= date.fromisoformat(s.why_until)


def why_due(s, n: int, now=None) -> bool:
    """Whether the n-th Short (counted over every Short posted) is a why Short.

    Pairs of Shorts alternate: with hook_every 2 the order runs calm, hook,
    calm-why, hook-why, so the why group splits evenly across both styles."""
    if not in_why_test(s, now):
        return False
    pair = n // max(s.hook_every, 1)
    return pair % s.why_every == s.why_every - 1
