"""NASA Image and Video Library (images-api.nasa.gov).

No API key required. Everything here is NASA-produced and public domain unless
the record's description says otherwise; ESA/partner images carry attribution
requirements, which we always render on screen anyway.
"""
from __future__ import annotations

import logging
import re

import requests

from romanfeed.sources.base import USER_AGENT, ImageAsset, ImageSource

log = logging.getLogger(__name__)

SEARCH_URL = "https://images-api.nasa.gov/search"
ASSET_URL = "https://images-api.nasa.gov/asset/{nasa_id}"

# Prefer originals, then the largest derivative.
_PREFERENCE = ("~orig", "~large", "~medium")

_TAG_RE = re.compile(r"<[^>]+>")
_CREDIT_RE = re.compile(r"(?:Image\s+)?[Cc]redit[s]?\s*[:：]\s*([^\n]+)")
# Sentence end = period followed by whitespace, unless the period ends a
# single-letter initial ("P. Kalas"); "CSA." is a real sentence end.
_SENTENCE_END_RE = re.compile(r"(?<![ .][A-Z])\.\s")


def _extract_credit(text: str, fallback: str, max_len: int = 90) -> str:
    clean = _TAG_RE.sub("", text or "")
    m = _CREDIT_RE.search(clean)
    if not m:
        return fallback
    credit = _SENTENCE_END_RE.split(" " + m.group(1), maxsplit=1)[0].strip().rstrip(".;,")
    if len(credit) > max_len:
        credit = credit[:max_len].rsplit(" ", 1)[0].rstrip(",;") + "…"
    return credit or fallback


EARTH_TAG = "earth from orbit"

# Most space-station photos are titled with their frame number
# ("iss069e032977") and captioned in the description:
#   "iss069e032977 (July 18, 2023) --- The city lights of the oasis city of Al
#    Ain in the United Arab Emirates are pictured from the International ..."
# Measured Oct 2026: about 3,000 of 5,247 Earth records looked like this, and
# all were turned away for lack of a usable title. The caption names the scene.
_FRAME_TITLE_RE = re.compile(r"^(?:iss|sts)\d{2,3}[a-z]?-?e?\d+$", re.I)
_CAPTION_RE = re.compile(r"^\S+\s*\([^)]*\)\s*-{2,3}\s*(.+)", re.S)
_CAPTION_CUT_RE = re.compile(
    r"\s+(?:is|are|was|were)\s+(?:pictured|seen|photographed|shown)\b|\s+pictured\b|\s+seen\s+from\b|"
    r"\s+from\s+the\s+(?:International\s+)?space\s+station\b|\s+as\s+the\s+(?:International\s+)?space\s+station\b|"
    r"\s+during\s+an?\s+orbital\b|\.\s", re.I)


def earth_caption(title: str, description: str, max_len: int = 80) -> str | None:
    """A scene title for a frame-numbered station photo, from its caption."""
    if not _FRAME_TITLE_RE.match((title or "").strip()):
        return None
    m = _CAPTION_RE.match(_TAG_RE.sub("", description or "").strip())
    if not m:
        return None
    head = _CAPTION_CUT_RE.split(" ".join(m.group(1).split()), maxsplit=1)[0]
    # Keep a place's region ("Detroit, Michigan"), drop an aside ("a Saudi city near...").
    parts = head.split(", ")
    out = parts[0]
    for part in parts[1:]:
        words = part.split()
        if not words or not words[0][:1].isupper() or len(words) > 3 or words[0].lower() in ("the", "a", "an"):
            break
        out += ", " + part
    out = out.strip(" ,.;:")
    if len(out) > max_len:
        out = out[:max_len].rsplit(" ", 1)[0].rstrip(",;")
    return out if len(out) >= 4 else None


class NasaImageLibrary(ImageSource):
    name = "NASA Image Library"

    def __init__(self, settings):
        super().__init__(settings)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = int(settings.options.get("timeout", 30))
        self.resolve_originals = bool(settings.options.get("resolve_originals", True))
        # subject: earth marks every asset from this source as an Earth-from-orbit
        # photo, so the selector keeps it out of deep-space videos and in Earth ones.
        self.subject = str(settings.options.get("subject", "space"))

    def _search(self, query: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        while len(items) < self.settings.max_per_query:
            resp = self.session.get(
                SEARCH_URL,
                params={"q": query, "media_type": "image", "page": page, "page_size": 100},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            coll = resp.json().get("collection", {})
            batch = coll.get("items", [])
            if not batch:
                break
            items.extend(batch)
            if not any(l.get("rel") == "next" for l in coll.get("links", [])):
                break
            page += 1
        return items[: self.settings.max_per_query]

    def resolve_original(self, nasa_id: str) -> str | None:
        """Look up the asset manifest and return the best full-res URL (lazy)."""
        try:
            resp = self.session.get(ASSET_URL.format(nasa_id=nasa_id), timeout=self.timeout)
            resp.raise_for_status()
            hrefs = [i["href"] for i in resp.json().get("collection", {}).get("items", [])]
            for tag in _PREFERENCE:
                for h in hrefs:
                    if tag in h and h.lower().endswith((".jpg", ".jpeg", ".png")):
                        return h.replace("http://", "https://")
        except requests.RequestException as e:  # pragma: no cover - network
            log.warning("asset manifest failed for %s: %s", nasa_id, e)
        return None

    def fetch(self) -> list[ImageAsset]:
        out: dict[str, ImageAsset] = {}
        for q in self.settings.queries:
            try:
                items = self._search(q)
            except requests.RequestException as e:  # pragma: no cover - network
                log.warning("search failed for %r: %s", q, e)
                continue
            for it in items:
                data = (it.get("data") or [{}])[0]
                nasa_id = data.get("nasa_id")
                if not nasa_id or f"nasa:{nasa_id}" in out:
                    continue
                preview = next((l.get("href") for l in it.get("links", []) if l.get("rel") == "preview"), None)
                if not preview:
                    continue
                url = preview.replace("http://", "https://")
                desc = data.get("description", "") or ""
                title = data.get("title", nasa_id)
                if self.subject == "earth":
                    title = earth_caption(title, desc) or title
                out[f"nasa:{nasa_id}"] = ImageAsset(
                    asset_id=f"nasa:{nasa_id}",
                    title=title,
                    url=url,
                    source=self.name,
                    credit=_extract_credit(desc, data.get("center", "NASA")),
                    description=desc,
                    date=(data.get("date_created") or "")[:10],
                    keywords=list(data.get("keywords") or []) + (
                        [EARTH_TAG, "International Space Station"] if self.subject == "earth" else []),
                    resolver=(lambda nid=nasa_id: self.resolve_original(nid)) if self.resolve_originals else None,
                )
        log.info("nasa_images: %d candidate assets from %d queries", len(out), len(self.settings.queries))
        return list(out.values())
