""""Why" Shorts: open on a question the source itself answers.

Test Nov 6-13 (main chat, under the owner's Nov 4 order to test daily): part
of the Shorts open on a short "why" question, such as "Why does the Cat's
Head Nebula glow red?", answered by a sentence from NASA's or ESA's own
caption for the image, with that source named and linked in the description.

Nothing is generated. A question is only asked when one sentence of the
caption gives the cause in so many words ("glows because it is illuminated by
a nearby hot star", "so named because..."), and the answer read out is that
sentence. An image whose caption holds no such sentence is never a "why"
Short; the picker moves on.

Nov 7 rebuild: across the channel's 1,274 images the first version matched 28
captions and most read badly ("Why is new stars forming in Hubble Finds Mature
Galaxy Masquerading as Toddler?"), because it asked about the image's press
headline. Now the question names the object itself (a catalogue number or a
proper name like "the Seagull Nebula"), the answer has to be about that
object, and guesses ("may have triggered") are never read out as answers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from romanfeed.narration import _DANGLING, _usable, clean_caption, sentences
from romanfeed.sources.base import ImageAsset

WHY_TAG = "why-caption"

_COLOUR = r"(red|pink|blue|green|orange|purple|violet|golden|yellow)"


@dataclass(frozen=True)
class Why:
    question: str   # "Why does the Horsehead Nebula glow?"
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


_KIND = r"(?:Nebula|Galaxy|Cluster|Pillars?|Bubble|Cloud|Clouds|Ring|Remnant|Veil|Loop|Arc|Arcs|Quintet|Sextet|Triplet|Group)"
_CATALOG = (r"(?:NGC|IC|Messier|M|Abell|Arp|LDN|LBN|RCW|Sh ?2-|Sharpless|Westerlund|Trumpler|Terzan|Hickson Compact Group|HCG|ESO|"
            r"UGC|PGC|LEDA|N|SN|Cassiopeia|Sagittarius|Herbig-Haro|HH|Gum|Barnard|vdB|Collinder|Pismis|Haffner|Hodge|Mrk|Markarian)"
            r" ?\d+[A-Za-z]?(?:-\d+)?")
# "the Cat's Head Nebula", "the Pillars of Creation", "Westerlund 1", "NGC 4183"
_PROPER = re.compile(rf"(?:\b[Tt]he )?(?:[A-Z][\w'-]*\s+){{1,3}}{_KIND}\b(?:\s+of\s+[A-Z][\w'-]+)?|\b{_CATALOG}\b")
_HEADLINE = re.compile(r"\b(?:Hubble|Webb|Spitzer|Chandra|NASA|ESA|image|images|view|views|shows?|reveals?|captures?|portrays?|"
                       r"spots?|spies|finds?|sees?|peers?|eyes|offers?|beholds?|mixes|bursts?|risen|new|labelled|annotated|compass|scale|"
                       r"3D|composite|wide-field|close-up|detail|details)\b", re.I)
_CATALOG_ONLY = re.compile(rf"\b{_CATALOG}\b")
_BAD_NAME = re.compile(r"^(?:the )?(?:Milky Way(?: Galaxy)?|Local Group|Virgo Cluster|Galaxy|Nebula|Cluster)$", re.I)


def object_name(asset: ImageAsset) -> str | None:
    """The object's own name, said the way a person would ("the Seagull Nebula",
    "NGC 4183"), or None when neither the title nor the caption's opening gives one.

    A press headline ("Hubble portrays a dusty spiral galaxy") is no name: the
    question would read as nonsense, so the image is passed over instead."""
    pieces = re.split(r"\s+(?:--|-|:|\|)\s+|\s*\(|,", asset.title or "")
    texts = [p for p in pieces if p] + [" ".join(sentences(clean_caption(asset.description))[:2])]
    for k, text in enumerate(texts):
        # A headline title ("Hubble Spies a Scintillating Globular Cluster") is
        # in Title Case, so only a catalogue number is trusted from it.
        headline = k < len(texts) - 1 and (_HEADLINE.search(text) or len(text.split()) > 6)
        for m in (_CATALOG_ONLY if headline else _PROPER).finditer(text):
            name = m.group(0).strip()
            if _HEADLINE.search(name) or _BAD_NAME.match(name):
                continue
            if re.match(r"^[Tt]he\b", name) or not re.search(rf"\b{_KIND}\b", name):
                name = re.sub(r"^[Tt]he\b", "the", name)
            else:
                name = "the " + name
            if re.match(rf"^the {_CATALOG}$", name):
                name = name[4:]   # "the NGC 4183" -> "NGC 4183"
            return name
    return None


def _is(name: str) -> str:
    return "are" if _does(name) == "do" else "is"


def _does(name: str) -> str:
    head = re.split(r"\s+of\s+", name, maxsplit=1)[0].split()[-1].lower()
    return "do" if head in ("pillars", "clouds", "arcs", "rings", "galaxies") else "does"


_ABOUT = r"(?:nebula|galaxy|galaxies|cluster|cloud|clouds|gas|dust|knots?|pillar|pillars|bubble|shell|ring|region|remnant|object|it|its|this|these|the image|this image)"
_NAME_WHY = re.compile(r"\b(?:so[- ]named because|named (?:for|after)|(?:gets|got|draws|takes|took|earned|owes|derives) its (?:common |popular |nick)?name)\b", re.I)
_COLOUR_FROM = re.compile(rf"\b{_COLOUR}\b[^.;]*?\b(?:colou?r|hue|glow|light|regions?|areas?|patches|tint)\b[^.;]*?"
                          r"\b(?:comes? from|shows? light from|is light from|is from|due to|caused by|produced by|emitted by|given off by)\b", re.I)
_GLOW_WHY = re.compile(r"\b(?:glow(?:s|ing)?|shines?|shining)\b[^.;]*?\b(?:because|due to|caused by|powered by|energi[sz]ed by|"
                       r"excited by|ioni[sz]ed by|heated by|as a result of)\b|\b(?:because|due to)\b[^.;]*?\bglow", re.I)
_SHAPE_WHY = re.compile(r"\b(?:shaped|sculpted|carved|blown(?: out)?|eroded)\b(?:\s+\w+){0,3}\s+by\b|"
                        r"\b(?:sculpting|carving|eroding)\b", re.I)
_TRIGGER_WHY = re.compile(r"\btrigger\w*\b[^.;]*\b(?:star formation|new stars|stars? to form|birth)\b|"
                          r"\b(?:star formation|new stars)\b[^.;]*\btrigger\w*\b", re.I)
_DARK_WHY = re.compile(r"\bdark\b[^.;]*\b(?:dust|gas)\b[^.;]*\b(?:block|obscur|absorb|hid)\w*|"
                       r"\b(?:dust|gas)\b[^.;]*\b(?:block|obscur|absorb)\w*[^.;]*\bdark\b", re.I)
_BLOCK_WHY = re.compile(r"\bdust\b[^.;]*\b(?:block|obscur|absorb)\w*[^.;]*\blight\b", re.I)
_UNSURE = re.compile(r"\b(?:may|might|could|possibly|perhaps|probably|thought to|believed to|suggests?|unclear|don't understand|"
                     r"not (?:yet )?understood|mystery|whether)\b", re.I)
_ANSWER_WORDS = (8, 40)


def question_for(sentence: str, name: str) -> str | None:
    """The question this caption sentence answers about `name`, or None.

    Each question asks exactly what the sentence states: a name's origin, what
    a colour is, what makes the gas glow, what shaped it, what set off new
    stars, or what the dark patches are."""
    if _UNSURE.search(sentence):
        return None   # "may have triggered" is a guess, not an answer
    s = sentence
    core = re.sub(r"^the\s+", "", name, flags=re.I).lower()
    if _NAME_WHY.search(s):
        # "Hickson Compact Group 90, named after Paul Hickson" names the group,
        # not the galaxy in the picture: the name has to be the one explained.
        m = _NAME_WHY.search(s)
        at = s.lower().find(core)
        between = s[at:m.start()] if 0 <= at < m.start() else ""
        if not between or re.search(r"\b(?:group|constellation|sequence|system|catalogu?e)\b", between, re.I):
            return None
        return f"Why is it called {name}?"
    m = _COLOUR_FROM.search(s)
    if m:
        return f"Why {_is(name)} {name} {m.group(1).lower()}?"
    if _GLOW_WHY.search(s) and re.search(r"\b(?:gas|hydrogen|oxygen|cloud|clouds|nebula|atoms?|knots?)\b", s, re.I):
        whole = s.lower().startswith(("the nebula", "it ", core, "the " + core))
        return f"Why {_does(name)} {name} glow?" if whole else f"Why does the gas in {name} glow?"
    if _SHAPE_WHY.search(s):
        return f"What shaped {name}?"
    if _TRIGGER_WHY.search(s):
        return f"Why are new stars forming in {name}?"
    if _DARK_WHY.search(s):
        return f"Why are there dark patches in {name}?"
    if _BLOCK_WHY.search(s):
        return f"What is blocking the light in {name}?"
    return None


def _about_object(s: str, prev: str, name: str) -> bool:
    """The sentence is about the pictured object, not some other thing named in the caption."""
    core = re.sub(r"^the\s+", "", name, flags=re.I).lower()
    low = s.lower()
    if core in low:
        return True
    if re.match(r"^(?:It|Its|This|These|The)\b", s) and re.search(rf"\b{_ABOUT}\b", low):
        return core in prev.lower() or bool(re.match(r"^(?:This|These|The)\b", s))
    return False


def find_why(asset: ImageAsset, lead: str = "") -> Why | None:
    """The first caption sentence that answers a why about the pictured object, as a Why."""
    url = source_page(asset)
    name = object_name(asset)
    if not url or not name:
        return None
    prev = ""
    # "...central stars.A pair of stars..." -- two sentences glued together.
    text = re.sub(r"(?<=[a-z])\.(?=[A-Z])", ". ", clean_caption(asset.description))
    for s in sentences(text):
        s_prev, prev = prev, s
        n = len(s.split())
        if not (_ANSWER_WORDS[0] <= n <= _ANSWER_WORDS[1]) or not _usable_answer(s):
            continue
        if re.search(r"\bcolon\b", s):
            continue   # a caption typo for "colour" would be read out as "colon"
        if not _about_object(s, s_prev, name):
            continue
        q = question_for(s, name)
        if q:
            return Why(question=q[0].upper() + q[1:], answer=s, source=asset.source, url=url)
    return None


def _usable_answer(s: str) -> bool:
    # _usable's word limits are for an opening line; the answer only has to
    # read cleanly aloud.
    return _usable(s) or not re.search(r"https?://|\bwww\.|@|\bCredit\b", s)


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
