"""Explainer Shorts: one space question answered in under a minute.

Test from Nov 21 (main chat, under the owner's Nov 4 order to test daily):
three Shorts, one a day on the dates in `shorts.explainer_dates`, each opening
on one question about the pictured object and answering it in about 40-55
seconds. Measured against the regular Shorts on views and subscribers per
1,000 views; dropped if they gain nothing within 7 days of the third.

Nothing is made up. The question is a why the image's own NASA or ESA caption
answers (romanfeed/why.py), and the explanation read out is that answer plus
the caption's following sentences about the same object, word for word. The
voice is synthetic (text to speech), so these uploads carry YouTube's altered
or synthetic content disclosure. The pictures are the telescopes' own.

Explainers never take an anticipation or punch-in opening, and carry their own
tag, so they stay out of those tests' numbers.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from romanfeed import why as why_mod
from romanfeed.narration import clean_caption, sentences

EXPLAINER_TAG = "explainer"
# The explanation (question + caption sentences) in words; at the voice's
# ~150 words a minute this is ~30-50 s, under a minute with the music.
WORDS = (55, 125)
MAX_SECONDS = 58.0


def _central_day(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now.astimezone(timezone.utc) - timedelta(hours=5)).date().isoformat()


def explainer_due(s, posted_days: set[str], now: datetime | None = None) -> bool:
    """True when today (Central) is an explainer date with no explainer posted yet."""
    day = _central_day(now)
    return day in (s.explainer_dates or []) and day not in posted_days


def reserving(s, now: datetime | None = None) -> bool:
    """True while an explainer date is today or still ahead: the library holds
    only a few captions long enough (Nov 15 coverage run: 3 unasked), so
    regular Shorts leave those images alone until the test is done."""
    day = _central_day(now)
    return any(d >= day for d in (s.explainer_dates or []))


def explanation(asset, w: "why_mod.Why") -> list[str] | None:
    """The caption's answer and the sentences after it, word for word, within WORDS.

    Sentences are taken in the caption's own order from the answer on; one
    with a guess in it, a link or a credit ends the run. None when the
    caption has too little to say for a full explainer."""
    text = re.sub(r"(?<=[a-z])\.(?=[A-Z])", ". ", clean_caption(asset.description))
    ss = sentences(text)
    if w.answer not in ss:
        return None
    out, words = [w.answer], len(w.question.split()) + len(w.answer.split())
    for s in ss[ss.index(w.answer) + 1:]:
        n = len(s.split())
        if n < 5 or re.search(r"https?://|\bwww\.|@|\bCredit\b|\bcolon\b", s):
            break
        if words + n > WORDS[1]:
            break
        out.append(s)
        words += n
    return out if words >= WORDS[0] else None


def explainer_script(asset, w: "why_mod.Why", body: list[str], *, full_label: str) -> str:
    """Question, the caption's explanation, the pointer and the usual subscribe call."""
    from romanfeed.narration import subscribe_call
    from romanfeed.telescopes import telescopes_for

    earth = bool(telescopes_for(asset)) and telescopes_for(asset)[0] == "iss"
    return " ".join([w.question, *body, f"The full {full_label} version is on the channel.",
                     subscribe_call(asset.asset_id, earth=earth)[0]])


def find_explainer(asset, lead: str = ""):
    """(Why, explanation sentences) for an image that makes a full explainer, or None."""
    w = why_mod.find_why(asset, lead)
    if not w:
        return None
    body = explanation(asset, w)
    return (w, body) if body else None
