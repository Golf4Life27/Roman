"""Nancy Grace Roman Space Telescope feed.

Roman's science operations run through STScI (MAST archive) and IPAC. Public
imagery will be released through NASA's science site, the NASA Image Library,
and STScI press releases once commissioning is complete.

Until that day this source simply polls the NASA Image Library for anything
tagged with the Roman mission (currently: test images, integration photos, and
artist concepts). Those are filtered out of the sleep-screen pool by default
via `allow_pre_launch`, but the poller is the hook: when real Roman science
images appear, they flow into the channel automatically on the next run.

TODO when Roman imagery goes live:
  * Add an STScI/MAST press-release scraper (or RSS) as a second query path.
  * Confirm `science_after` against the actual first-image release date.
"""
from __future__ import annotations

import logging

from romanfeed.config import SourceSettings
from romanfeed.sources.base import ImageAsset, ImageSource
from romanfeed.sources.nasa_images import NasaImageLibrary

log = logging.getLogger(__name__)

ROMAN_QUERIES = ["Roman Space Telescope", "Nancy Grace Roman"]
# Mission-ops photography (rockets, clean rooms, briefings) is not sky imagery.
PRE_LAUNCH_HINTS = (
    "artist", "concept", "illustration", "clean room", "cleanroom", "integration", "test", "rendering",
    "launch", "prelaunch", "arrival", "briefing", "rocket", "falcon", "fairing", "payload", "vertical",
    "spacecraft", "engineer", "technician", "facility", "goddard", "kennedy", "pad", "encapsulat",
)
# Roman launched 2026-08-30 and cruises ~3 months to L2; NASA expects first
# observations by early 2027. Anything dated before this is mission-ops.
DEFAULT_SCIENCE_AFTER = "2026-12-01"


class RomanTelescopeSource(ImageSource):
    name = "NASA Roman Space Telescope"

    def __init__(self, settings: SourceSettings):
        super().__init__(settings)
        self.allow_pre_launch = bool(settings.options.get("allow_pre_launch", False))
        self.science_after = str(settings.options.get("science_after", DEFAULT_SCIENCE_AFTER))
        inner = SourceSettings(
            type="nasa_images",
            queries=settings.queries or ROMAN_QUERIES,
            min_width=settings.min_width,
            max_per_query=settings.max_per_query,
            options=settings.options,
        )
        self._lib = NasaImageLibrary(inner)

    def looks_pre_launch(self, asset: ImageAsset) -> bool:
        if asset.date and asset.date < self.science_after:
            return True
        blob = f"{asset.title} {asset.description}".lower()
        return any(h in blob for h in PRE_LAUNCH_HINTS)

    def fetch(self) -> list[ImageAsset]:
        assets = self._lib.fetch()
        for a in assets:
            a.source = self.name
            a.keywords = list(a.keywords) + ["roman"]
        if not self.allow_pre_launch:
            before = len(assets)
            assets = [a for a in assets if not self.looks_pre_launch(a)]
            log.info("roman: %d/%d assets look like real science imagery", len(assets), before)
        return assets
