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


class NasaImageLibrary(ImageSource):
    name = "NASA Image Library"

    def __init__(self, settings):
        super().__init__(settings)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = int(settings.options.get("timeout", 30))
        self.resolve_originals = bool(settings.options.get("resolve_originals", True))

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
                out[f"nasa:{nasa_id}"] = ImageAsset(
                    asset_id=f"nasa:{nasa_id}",
                    title=data.get("title", nasa_id),
                    url=url,
                    source=self.name,
                    credit=_extract_credit(desc, data.get("center", "NASA")),
                    description=desc,
                    date=(data.get("date_created") or "")[:10],
                    keywords=list(data.get("keywords") or []),
                    resolver=(lambda nid=nasa_id: self.resolve_original(nid)) if self.resolve_originals else None,
                )
        log.info("nasa_images: %d candidate assets from %d queries", len(out), len(self.settings.queries))
        return list(out.values())
