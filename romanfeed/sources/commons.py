"""Wikimedia Commons: free-licensed photos found by search.

The ocean channel's supplier. Commons is the one big archive with a clean
search API *and* a machine-readable licence on every file, so the licence
rule is enforced here, before anything is downloaded:

  * public domain (US government work such as NOAA, USFWS and NPS photos,
    "PD-self", CC0) is always fine;
  * CC BY (any version) is fine with a visible credit, which the description
    renders for every image in the video, licence included;
  * everything else -- CC BY-SA (share-alike would bind the video), NC, ND,
    GFDL-only, "fair use", unknown -- is dropped.

Search terms are subject-led ("humpback whale", "kelp forest"); each one is
read up to `max_per_query` results deep. Results come with their pixel size,
so photos too small for 1080p never cost a download.
"""
from __future__ import annotations

import html
import logging
import re
import time

import requests

from romanfeed.sources.base import USER_AGENT, ImageAsset, ImageSource

log = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
TAG_RE = re.compile(r"<[^>]+>")

# Licence classes, from extmetadata LicenseShortName (and License as a fallback).
ALLOWED = ("pd", "by")


def licence_class(short: str, code: str = "") -> str:
    """'pd', 'by', 'by-sa', 'nc-nd' or 'other' for a Commons licence label."""
    s = f"{short} {code}".lower().replace("_", " ")
    if "public domain" in s or re.search(r"\bpd\b|\bpd-|cc0|cc-zero|cc zero", s):
        return "pd"
    if re.search(r"\bnc\b|-nc\b|noncommercial|\bnd\b|-nd\b|noderiv", s):
        return "nc-nd"
    if re.search(r"by-sa|by sa|sharealike|share alike", s):
        return "by-sa"
    if re.search(r"\bcc[- ]by\b|\bcc by\b|attribution", s):
        return "by"
    return "other"


def clean_text(value: str, limit: int = 0) -> str:
    text = " ".join(html.unescape(TAG_RE.sub(" ", value or "")).split())
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


# File names make the on-screen caption: "File:Green sea turtle (Chelonia
# mydas) at Kona.jpg" -> "Green sea turtle (Chelonia mydas) at Kona".
_EXT_RE = re.compile(r"\.(jpe?g|png|tiff?|webp|gif)$", re.I)
_NOISE_RE = re.compile(r"\b(?:IMG|DSC|DSCN|DSCF|P\d{6,}|GOPR)[_ -]?\d+\b|\b\d{8,}\b|[_]+", re.I)


def title_from_file(name: str) -> str:
    t = name.split(":", 1)[-1]
    t = _EXT_RE.sub("", t)
    t = _NOISE_RE.sub(" ", t).replace(" - ", " · ")
    t = re.sub(r"\s*\(\s*\)\s*", " ", t)
    return " ".join(t.split()).strip(" -.,·")


def credit_line(artist: str, short: str, klass: str) -> str:
    who = clean_text(artist, 60) or "Unknown photographer"
    if klass == "pd":
        return f"{who} (public domain), via Wikimedia Commons"
    return f"{who}, {short or 'CC BY'}, via Wikimedia Commons"


class WikimediaCommons(ImageSource):
    name = "Wikimedia Commons"

    def __init__(self, settings):
        super().__init__(settings)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = int(settings.options.get("timeout", 60))
        self.subject = str(settings.options.get("subject", "ocean"))
        self.pause = float(settings.options.get("pause", 0.3))
        # The archive's 50-a-page search: max_per_query 300 = 6 requests a term.
        self.page_size = 50

    def _search(self, query: str) -> list[dict]:
        pages: list[dict] = []
        cont: dict = {}
        while len(pages) < self.settings.max_per_query:
            params = {
                "action": "query", "format": "json", "formatversion": 2,
                "generator": "search", "gsrnamespace": 6, "gsrlimit": self.page_size,
                "gsrsearch": f"{query} filetype:bitmap",
                "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
                "iiurlwidth": int(self.settings.options.get("download_width", 3000)),
                "iiextmetadatafilter": "LicenseShortName|License|Artist|ImageDescription|DateTimeOriginal|Categories",
                "maxlag": 5,
            }
            params.update(cont)
            resp = self.session.get(API, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:  # maxlag and friends: wait once, then give up on this term
                log.warning("commons search %r: %s", query, data["error"].get("info", data["error"]))
                break
            pages.extend((data.get("query") or {}).get("pages") or [])
            if "continue" not in data:
                break
            cont = data["continue"]
            time.sleep(self.pause)
        return pages[: self.settings.max_per_query]

    def to_asset(self, page: dict, query: str) -> ImageAsset | None:
        info = (page.get("imageinfo") or [None])[0]
        if not info or not str(info.get("mime", "")).startswith("image/"):
            return None
        meta = info.get("extmetadata") or {}
        short = (meta.get("LicenseShortName") or {}).get("value", "")
        code = (meta.get("License") or {}).get("value", "")
        klass = licence_class(short, code)
        if klass not in ALLOWED:
            return None
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        # Landscape only: a portrait photo letterboxed in a 16:9 frame wastes the screen.
        if w < self.settings.min_width or h <= 0 or w / h < 1.25:
            return None
        title = title_from_file(page.get("title", ""))
        if len(title) < 4:
            return None
        url = info.get("thumburl") if int(info.get("thumbwidth") or 0) >= self.settings.min_width else info.get("url")
        cats = clean_text((meta.get("Categories") or {}).get("value", "")).replace("|", ", ")
        return ImageAsset(
            asset_id=f"commons:{page.get('pageid')}",
            title=title,
            url=url or info.get("url"),
            source=self.name,
            credit=credit_line((meta.get("Artist") or {}).get("value", ""), short, klass),
            description=clean_text((meta.get("ImageDescription") or {}).get("value", ""), 600),
            date=clean_text((meta.get("DateTimeOriginal") or {}).get("value", ""))[:10],
            width=w, height=h,
            licence="public-domain" if klass == "pd" else "cc-by",
            keywords=[self.subject, query] + [c for c in cats.split(", ") if c][:12],
        )

    def fetch(self) -> list[ImageAsset]:
        out: dict[str, ImageAsset] = {}
        dropped = 0
        for q in self.settings.queries:
            try:
                pages = self._search(q)
            except requests.RequestException as e:  # pragma: no cover - network
                log.warning("commons search failed for %r: %s", q, e)
                continue
            for page in pages:
                asset = self.to_asset(page, q)
                if asset is None:
                    dropped += 1
                    continue
                out.setdefault(asset.asset_id, asset)
        log.info("commons: %d candidate photos from %d terms (%d turned away on licence, size or shape)",
                 len(out), len(self.settings.queries), dropped)
        return list(out.values())
