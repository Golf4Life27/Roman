"""ESA/Hubble and ESA/Webb public archives (Djangoplicity).

A second supplier, because NASA's library alone is not deep enough. Eleven
search terms there returned 775 images; widening to 48 only reached 1212,
because the terms overlap. One hour-long video eats about 78 images, so that
pool is spent in a few days and the channel starts repeating itself.

These archives are a genuinely separate collection -- ESA/Hubble alone lists
over 5,500 images -- and they are browsed by category rather than searched,
so a handful of categories returns the whole set rather than the first 100
hits of each phrase.

Two things here are better than the NASA path:

  * Pixel dimensions arrive in the listing, so images too small for 1080p are
    dropped before we spend a download on them.
  * Every record carries an explicit credit and licence, so nothing has to be
    scraped out of prose.

Licence: ESA/Hubble and ESA/Webb images are CC BY 4.0 -- free to reuse with a
visible credit, which the description already renders for every image in the
video. See https://esahubble.org/copyright/.
"""
from __future__ import annotations

import ast
import logging
import re

import requests

from romanfeed.sources.base import USER_AGENT, ImageAsset, ImageSource

log = logging.getLogger(__name__)

# Djangoplicity serves a JSON array per category at this path. The plain
# /images/json/ endpoint ignores pagination and only ever returns the newest
# 100, which is why categories are used instead.
CATEGORY_JSON = "{base}/images/archive/category/{category}/json/"

# Sky only. The archives also carry `spacecraft`, `mission`, `illustrations`,
# `anniversary` and `misc`, which are hardware, artwork and event graphics --
# exactly what the curation filter exists to reject, so they are never fetched.
DEFAULT_CATEGORIES = ("nebulae", "galaxies", "starclusters", "stars", "cosmology", "blackholes")

# Resource entries, best first. "Large" is the full source resolution as a
# JPEG; "Original" is usually a TIFF, which is slower to fetch and decode for
# no visible gain at 1080p.
_RESOURCE_PREFERENCE = ("Large", "Original", "Screen", "Small")


# Djangoplicity's JSON export serialises Python bytes, so text fields arrive
# as the *repr* of a bytes object: the literal string `b'Galaxy NGC 2525'`,
# backslash escapes and all. Left alone it reaches the burned-in caption and
# the chapter list verbatim.
_BYTES_REPR_RE = re.compile(r"^b(['\"])(?:\\.|(?!\1).)*\1$", re.S)


def _text(value) -> str:
    """Unwrap a bytes-repr string back into real text.

    This matters beyond tidiness: the same quirk hits the Credit field, and
    ESA/Hubble and ESA/Webb images are CC BY 4.0, whose attribution term
    requires the credit be reproduced clearly and unaltered.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    if not _BYTES_REPR_RE.match(value):
        return value
    try:
        decoded = ast.literal_eval(value)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return value
    if isinstance(decoded, bytes):
        return decoded.decode("utf-8", "replace")
    return str(decoded)


# ESA concatenates the acknowledgement onto the credit with no separator,
# giving "…A. Riess and the SH0ES teamAcknowledgment: Mahdi Zamani". The
# credit is burned into every frame, so it needs the break put back.
_ACK_RE = re.compile(r"(?<=[a-z)\].])(Acknowledge?ments?\s*:)", re.I)


def _credit(value) -> str:
    return _ACK_RE.sub(r" — \1", _text(value)).strip()


def _dimension(value) -> int:
    """Dimensions arrive as numbers or as strings like "1663.0"."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _best_resource(record: dict) -> tuple[str, int, int]:
    """Pick the largest usable JPEG and its dimensions, from metadata alone."""
    by_type = {}
    for res in record.get("Resources") or []:
        if res.get("MediaType") != "Image" or not res.get("URL"):
            continue
        by_type.setdefault(res.get("ResourceType"), res)

    for wanted in _RESOURCE_PREFERENCE:
        res = by_type.get(wanted)
        if not res:
            continue
        url = str(res["URL"])
        # A TIFF original is fine for Pillow but costs several times the bytes;
        # take it only when there is no JPEG at all.
        if url.lower().endswith((".tif", ".tiff")) and any(
            t in by_type for t in ("Large", "Screen", "Small")
        ):
            continue
        dims = res.get("Dimensions") or []
        w = _dimension(dims[0]) if len(dims) > 0 else 0
        h = _dimension(dims[1]) if len(dims) > 1 else 0
        return url, w, h

    # No Resources array: fall back to the format map.
    formats = record.get("formats_url") or {}
    for key in ("large", "screen", "banner1920"):
        if formats.get(key):
            return str(formats[key]), 0, 0
    return "", 0, 0


def _keywords(record: dict) -> list[str]:
    """Category and object name, flattened. These feed the curation filter,
    which needs at least one sky word to accept an image at all."""
    out: list[str] = []
    for field in ("Subject.Category", "Subject.Name", "Type"):
        value = record.get(field)
        if isinstance(value, list):
            out.extend(_text(v) for v in value if v)
        elif value:
            out.append(_text(value))
    # "Galaxies > Interacting" -> both halves, so the filter sees "galaxies".
    flat: list[str] = []
    for k in out:
        flat.extend(part.strip() for part in str(k).split(">") if part.strip())
    return list(dict.fromkeys(flat))[:20]


class EsaArchive(ImageSource):
    """One ESA Djangoplicity archive. Configure one source block per site."""

    name = "ESA Archive"

    def __init__(self, settings):
        super().__init__(settings)
        opts = settings.options or {}
        self.base = str(opts.get("base_url", "https://esahubble.org")).rstrip("/")
        self.prefix = str(opts.get("id_prefix", "esa"))
        self.name = str(opts.get("label", "ESA/Hubble"))
        self.categories = list(opts.get("categories") or DEFAULT_CATEGORIES)
        self.timeout = int(opts.get("timeout", 60))
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _category(self, category: str) -> list[dict]:
        resp = self.session.get(
            CATEGORY_JSON.format(base=self.base, category=category), timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def fetch(self) -> list[ImageAsset]:
        out: dict[str, ImageAsset] = {}
        too_small = 0
        for category in self.categories:
            try:
                records = self._category(category)
            except (requests.RequestException, ValueError) as e:
                # One unreachable category must not cost us the other five.
                log.warning("%s: category %r failed: %s", self.name, category, e)
                continue

            kept = 0
            for record in records:
                image_id = record.get("ID")
                if not image_id:
                    continue
                asset_id = f"{self.prefix}:{image_id}"
                if asset_id in out:
                    continue  # an image can sit in several categories
                url, width, height = _best_resource(record)
                if not url:
                    continue
                # Dimensions come with the listing, so undersized images cost
                # nothing to reject -- unlike the NASA path, which must
                # download first to find out.
                if width and width < self.settings.min_width:
                    too_small += 1
                    continue
                out[asset_id] = ImageAsset(
                    asset_id=asset_id,
                    title=_text(record.get("Title")) or image_id,
                    url=url,
                    source=self.name,
                    credit=_credit(record.get("Credit")) or self.name,
                    description=_text(record.get("Description")) or _text(record.get("Headline")),
                    date=_text(record.get("Date"))[:10],
                    width=width,
                    height=height,
                    licence="cc-by",
                    keywords=_keywords(record),
                )
                kept += 1
            log.debug("%s: %s -> %d of %d records", self.name, category, kept, len(records))

        log.info(
            "%s: %d candidate assets from %d categories (%d below min_width %d)",
            self.name, len(out), len(self.categories), too_small, self.settings.min_width,
        )
        return list(out.values())
