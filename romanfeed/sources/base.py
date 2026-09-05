from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from romanfeed.config import SourceSettings

USER_AGENT = "RomanFeed/0.1 (+https://github.com/Golf4Life27/Roman)"


@dataclass
class ImageAsset:
    """One still image with everything needed to render and credit it."""

    asset_id: str  # stable, unique across sources (e.g. "nasa:PIA14417")
    title: str
    url: str  # full-resolution download URL
    source: str  # human-readable source name for on-screen credit
    credit: str = ""  # e.g. "NASA/ESA/CSA/STScI"
    description: str = ""
    date: str = ""
    width: int = 0
    height: int = 0
    licence: str = "public-domain"  # public-domain | cc-by | restricted
    keywords: list[str] = field(default_factory=list)
    local_path: Path | None = None
    # Optional lazy resolver: called once at download time to swap `url` for a
    # better (e.g. original-resolution) URL, so listing 800 candidates costs
    # 800 search rows, not 800 extra HTTP calls.
    resolver: Callable[[], str | None] | None = field(default=None, repr=False, compare=False)

    @property
    def short_description(self) -> str:
        text = " ".join(self.description.split())
        if len(text) <= 160:
            return text
        cut = text[:160].rsplit(" ", 1)[0]
        return cut.rstrip(",.;:") + "…"

    def download(self, cache_dir: str | Path, timeout: int = 60) -> Path:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        if self.resolver is not None:
            better = self.resolver()
            if better:
                self.url = better
            self.resolver = None
        digest = hashlib.sha1(self.url.encode()).hexdigest()[:12]
        suffix = Path(self.url.split("?")[0]).suffix or ".jpg"
        dest = cache_dir / f"{self.asset_id.replace(':', '_')}_{digest}{suffix}"
        if not dest.exists():
            with requests.get(self.url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}) as r:
                r.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                tmp.rename(dest)
        self.local_path = dest
        return dest


class ImageSource(ABC):
    name: str = "base"

    def __init__(self, settings: SourceSettings):
        self.settings = settings

    @abstractmethod
    def fetch(self) -> list[ImageAsset]:
        """Return candidate assets. Must be cheap enough to call daily."""
