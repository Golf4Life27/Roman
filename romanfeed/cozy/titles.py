"""Seasonal titles for cozy scenes: place, month, time of day, mood, use.

The pattern the big cozy channels use: a specific place in a specific season
at a time of day, an emoji, and what it is for ("Sleep", "Work & Study"):

  Cozy Halloween Observatory on an October Night 🎃 10 Hours of Calm Space Music for Sleep · Live

Always under YouTube's 100 characters (a cut title once made the broadcast
job lose track of its own broadcasts): the optional parts go first, then the
place shrinks word by word. Live titles keep "Cozy " at the front and
"· Live" at the end, which live/schedule.py looks for.
"""
from __future__ import annotations

from datetime import date, datetime

TITLE_MAX = 100
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
EMOJI = {"halloween": "🎃", "christmas": "🎄", "new_year": "✨", "thanksgiving": "🍂", "autumn": "🍂",
         "winter": "❄️", "meteors": "🌠", "eclipse": "🌘", "launch": "🚀", "valentines": "🕯️",
         "spring": "🌸", "easter": "🌸", "summer": "🌙", "independence_day": "🎆"}
SLEEP, WORK = "sleep", "work"
USE_PHRASE = {SLEEP: "for Sleep", WORK: "for Work & Study"}


def part_of_day(hour: int) -> str:
    if 5 <= hour < 11:
        return "Morning"
    if 11 <= hour < 17:
        return "Afternoon"
    if 17 <= hour < 21:
        return "Evening"
    return "Night"


def use_for(hour: int) -> str:
    """What a stream starting at `hour` (local) is for: daytime is work and study."""
    return WORK if 5 <= hour < 17 else SLEEP


def emoji_for(theme_key: str) -> str:
    return EMOJI.get(theme_key, "🌌")


def _article(word: str) -> str:
    return "an" if word[:1] in "AEIOU" else "a"


def seasonal(scene: str, *, when: date | datetime, theme_key: str, hours_label: str, use: str = SLEEP,
             part: str = "Night", live: bool = False) -> str:
    """'Cozy {scene} on an October Night 🎃 10 Hours of Calm Space Music for Sleep[ · Live]'."""
    month = MONTHS[when.month - 1]
    tail = " · Live" if live else ""
    place = scene.split()
    music = [f"{hours_label} Hours of Calm Space Music {USE_PHRASE[use]}",
             f"{hours_label} Hours of Space Music {USE_PHRASE[use]}",
             f"Space Music {USE_PHRASE[use]}"]
    while True:
        for m in music:
            title = f"Cozy {' '.join(place)} on {_article(month)} {month} {part} {emoji_for(theme_key)} {m}{tail}"
            if len(title) <= TITLE_MAX:
                return title
        if len(place) == 1:
            return title[:TITLE_MAX - len(tail)] + tail
        del place[-2]                       # keep the last word: "Observatory", "Cabin"
