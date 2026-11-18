"""The September winner format for long videos (publish.winner_* in the channel YAML).

The ten September telescope uploads still carry about two thirds of the
channel's watch hours at 4-5% thumbnail click-through; November's mixed sets
sit at 0.5-1.7%. Those winners differed in three ways this module restores
for a set run of days: one subject per video (all nebulae or all galaxies, so
the title's subject is true of every picture), the full title tail
("| Telescope Screensaver") kept with the name shortened to fit, and no
Earth-from-orbit days.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from romanfeed.config import ChannelConfig
from romanfeed.sources.base import ImageAsset

HINTS = {"nebula": ("nebula", "nebulae"), "galaxy": ("galaxy", "galaxies")}
LABELS = {"nebula": "Nebulae", "galaxy": "Galaxies"}


@dataclass
class DayPlan:
    theme: str
    subject: str
    title_template: str
    lead: str | None = None   # a fixed name for the title (the remake), else the lead image's
    remake: bool = False


def _blob(a: ImageAsset) -> str:
    return f"{a.title} {' '.join(a.keywords)}".lower()


def matches(a: ImageAsset, theme: str) -> bool:
    """Pictures of `theme` only: an image that also names the other theme
    ("a nebula in a nearby galaxy") is left out, so the title stays true."""
    blob = _blob(a)
    if not any(h in blob for h in HINTS[theme]):
        return False
    return not any(h in blob for other, hints in HINTS.items() if other != theme for h in hints)


def plan_for(cfg: ChannelConfig, day: date) -> DayPlan | None:
    p = cfg.publish
    template = p.winner_title_template or p.title_template
    if p.remake_date and day == date.fromisoformat(p.remake_date) and p.remake_theme in HINTS:
        return DayPlan(p.remake_theme, LABELS[p.remake_theme], template, lead=p.remake_lead or None, remake=True)
    if not (p.winner_from and p.winner_until and p.winner_themes):
        return None
    start, end = date.fromisoformat(p.winner_from), date.fromisoformat(p.winner_until)
    if not start <= day <= end:
        return None
    theme = p.winner_themes[(day - start).days % len(p.winner_themes)]
    if theme not in HINTS:
        return None
    return DayPlan(theme, LABELS[theme], template)


def narrow(candidates: list[ImageAsset], theme: str, used: set[str], count: int) -> list[ImageAsset] | None:
    """The candidates of one theme, or None when too few never-shown ones are
    left for a whole video (the run then goes ahead with the usual mix)."""
    themed = [a for a in candidates if matches(a, theme)]
    fresh = [a for a in themed if a.asset_id not in used]
    # Some fail the size and black-block checks after download; ask for headroom.
    if len(fresh) < count * 1.5:
        return None
    return themed
