"""The spoken script for one event, written from its source facts only.

Accuracy is the brand, so the writer is checked rather than trusted: every
number and every capitalised name in the script must appear in the source
text (the event line plus the linked articles' summaries). A script that
fails is rewritten once with the offending words named; a second failure
falls back to the template, which only re-orders the source's own words.

The writer is Claude (ANTHROPIC_API_KEY). Without a key, or if the API
refuses, the template is used.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date

from romanfeed.history.events import Event

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FOLLOW = "Follow for one day in history, every day."
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
# Words a script may use without the source saying them.
FREE_WORDS = {"follow", "today", "on", "in", "the", "a", "an", "and", "but", "it", "its", "this", "that", "they",
              "he", "she", "his", "her", "their", "one", "day", "history", "every", "american", "english", "french",
              "british", "i", "we", "you", "what", "who", "when", "where", "why", "how", "then", "now", "there",
              "here", "just", "back", "years", "later", "after", "before", "for", "of", "at", "by", "with", "from",
              "so", "no", "not", "nobody", "everyone", "some", "most", "all", "earth", "world", "today's", "if", "as",
              "minutes", "moments", "seconds", "within", "inside", "over", "under", "up", "down", "out", "yes",
              "tv", "america", "americans", "europe", "while", "meanwhile", "still", "once", "today,", "people"}
SYSTEM = """You write the narration for a 30-second vertical history video for a channel called "On This Day".

Rules:
- Use ONLY facts stated in the SOURCE. Do not add names, numbers, dates, places, quotes or claims that are not in it. If a detail is not in the SOURCE, leave it out.
- 50 to 75 words. Plain, warm, curious; short sentences that read well aloud. No hype words like "incredible" or "mind-blowing".
- Begin exactly with the date line given in DATE_LINE.
- End exactly with: "Follow for one day in history, every day."
- headline: 2 to 5 words for the screen, e.g. "Faster than sound". subtitle: the main name(s) from the SOURCE, under 45 characters.
"""
SCHEMA = {
    "type": "object",
    "properties": {
        "script": {"type": "string"},
        "headline": {"type": "string"},
        "subtitle": {"type": "string"},
    },
    "required": ["script", "headline", "subtitle"],
    "additionalProperties": False,
}


@dataclass
class Script:
    text: str
    headline: str
    subtitle: str
    writer: str          # "claude" or "template"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def date_line(day: date, year: int) -> str:
    return f"{MONTHS[day.month - 1]} {ordinal(day.day)}, {year}."


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


def unsupported(script: str, source: str, *, day: date, year: int) -> list[str]:
    """Numbers and capitalised words in `script` that `source` never says.

    Numbers are compared without separators (1,100 = 1100) and must appear as
    whole numbers; a capitalised word is fine if it appears anywhere in the
    source (any case) or is a month, the date line, or a common word."""
    src = _norm(source)
    src_nums = set(re.findall(r"\d+(?:\.\d+)?", source.replace(",", "")))
    body = script.replace(date_line(day, year), " ").replace(FOLLOW, " ")
    bad = []
    for num in re.findall(r"\d[\d,]*(?:\.\d+)?", body):
        n = num.replace(",", "").rstrip(".")
        if n and n not in src_nums:
            bad.append(num)
    for word in re.findall(r"\b[A-Z][a-zA-Z'’]*(?:-[A-Za-z0-9]+)*", body):
        if len(word) < 2:
            continue
        w = re.sub(r"['’](s|d|ll|re|ve)$", "", word.lower().strip("'’"))
        if w in FREE_WORDS or word in MONTHS:
            continue
        if f" {_norm(w).strip()} " not in f" {src} ":
            bad.append(word)
    return sorted(set(bad))


def template(event: Event, day: date) -> Script:
    """The source's own words: the date, the event line, one summary sentence."""
    text = event.text.replace(" (pictured)", "").rstrip(".") + "."
    extra = ""
    for p in event.pages[:1]:
        first = re.split(r"(?<=[.!?])\s+", p.get("extract", "").strip())[0] if p.get("extract") else ""
        if first and len((text + " " + first).split()) <= 60 and first[:30] not in text:
            extra = " " + first
    title = event.pages[0]["title"] if event.pages else " ".join(event.text.split()[:4]).rstrip(",.;:")
    return Script(f"{date_line(day, event.year)} {text}{extra} {FOLLOW}", title[:32], "", "template")


def _ask(client, event: Event, day: date, feedback: str = "") -> dict:
    user = (f"DATE_LINE: {date_line(day, event.year)}\n\nSOURCE:\n{event.source_text()[:24000]}"
            + (f"\n\nYour previous script used words the SOURCE does not contain: {feedback}. "
               "Rewrite it using only the SOURCE." if feedback else ""))
    resp = client.beta.messages.create(
        model=MODEL, max_tokens=16000, system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("the writer declined")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def write(event: Event, day: date, *, client=None) -> Script:
    """Claude's script if it passes the fact check, else the template."""
    if client is None and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic

            client = anthropic.Anthropic()
        except ImportError:
            log.warning("anthropic is not installed; using the template script")
    if client is None:
        return template(event, day)
    source = event.source_text()
    feedback = ""
    for attempt in range(2):
        try:
            out = _ask(client, event, day, feedback)
        except Exception as exc:   # an API hiccup must not cost the day's video
            log.warning("script writer failed for %s (%s); using the template", event.key, exc)
            return template(event, day)
        text = re.sub(r"\s+", " ", out["script"]).strip()
        if not text.startswith(date_line(day, event.year)):
            text = f"{date_line(day, event.year)} {text}"
        if not text.endswith(FOLLOW):
            text = f"{text} {FOLLOW}"
        bad = unsupported(text, source, day=day, year=event.year)
        if not bad and 35 <= len(text.split()) <= 95:
            return Script(text, out["headline"].strip()[:32], out["subtitle"].strip()[:45], "claude")
        feedback = ", ".join(bad) or f"length {len(text.split())} words"
        log.info("script for %s rejected (attempt %d): %s", event.key, attempt + 1, feedback)
    return template(event, day)
