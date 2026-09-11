"""Pick the images for one video.

Rules, in order:
  1. Never re-use an asset the ledger says this channel already used
     (the pool auto-resets when it runs dry, so the channel never stalls).
  2. Drop assets that are too small to look good at the target resolution
     (checked after download; we don't trust API metadata for dimensions).
  2b. Drop assets with a solid block of pure black in frame -- usually the
     missing quadrant of a Hubble WFPC2 mosaic, which reads on screen as a
     rectangular hole. Metadata cannot see this; only the pixels can.
  2c. Rejections in 2 and 2b only show up after download, so a pool that
     looked big enough can still come up short. When that happens the ledger
     is recycled and the run takes a second pass rather than failing.
  2d. No two clips in one video share a caption; the archive reuses titles
     across distinct entries, and chapter markers come straight from them.
  3. Prefer Roman assets over warm-up assets when both are present.
  4. Shuffle deterministically by date so a re-run on the same day gives the
     same video (idempotent daily job), but each day differs.
"""
from __future__ import annotations

import logging
import random
import re
from datetime import date

from PIL import Image

from romanfeed.sources.base import ImageAsset
from romanfeed.state import Ledger

log = logging.getLogger(__name__)

# Anything that is not the sky: people, hardware, ceremonies, Earth from orbit.
BLOCKLIST_HINTS = (
    "logo", "portrait", "headshot", "diagram", "chart", "infographic", "poster", "screenshot", "meeting",
    "press conference", "briefing", "visit", "ceremony", "reveal", "engineer", "technician", "clean room",
    "cleanroom", "work stand", "lift", "launch", "prelaunch", "rocket", "falcon", "fairing", "payload",
    "shuttle", "orbiter", "astronaut", "crew", "iss ", "space station", "earth observation", "ksc-",
    "artist", "concept", "illustration", "rendering", "mockup", "model of", "facility", "assembly",
    "integration", "testing", "test of", "administrator", "director", "senator", "congress", "employees",
)
# At least one of these must appear for an image to count as astronomy.
SKY_HINTS = (
    "nebula", "galaxy", "galaxies", "cluster", "supernova", "star-forming", "star forming", "stellar",
    "deep field", "light-years", "light years", "protostar", "quasar", "pulsar", "black hole", "exoplanet",
    "planetary", "milky way", "andromeda", "orion", "carina", "pillars", "cosmic", "interstellar",
    "spiral", "dwarf galaxy", "globular", "remnant", "molecular cloud", "starburst", "constellation",
    "jupiter", "saturn", "mars", "neptune", "uranus", "comet", "aurora", "solar flare", "sun's",
)


# Bare NASA-center photo IDs ("ARC-2010-ACD10-0054-002", "KSC-2012-3155",
# "GSFC_20171208_Archive") make useless captions. Catalog names ("NGC 6302",
# "IC 1396", "M 31") must stay, so match only known center prefixes.
_PHOTO_ID_RE = re.compile(r"^(ARC|KSC|GSFC|JSC|MSFC|NHQ|GRC|LRC|LARC|AFRC|DFRC|SSC|WSTF|JPL|EC|ED|S\d{2})[-_ ]?\d", re.I)
# Posters, legacy retrospectives and event graphics that mention the sky in the title.
_TITLE_BLOCK = ("legacy", "anniversary", "celebrat", "future of", "mission", "team", "workshop", "conference",
                "history of", "timeline", "retrospective", "in memoriam", "award")
# Archive filenames used as titles ("hs-2007-16-e-full_jpg", "opo0501a", "heic1104a.tif").
# No spaces, no capitals, and either an image extension or a run of digits.
_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_IMAGE_EXT_RE = re.compile(r"[._](jpg|jpeg|png|tif|tiff|gif|webp)$", re.I)
# Category labels the archive uses when an item has no real caption. They pass
# the keyword test but read as filler on screen.
_GENERIC_TITLES = frozenset({
    "space science", "astronomy", "astrophysics", "universe", "space", "science",
    "hubble", "hubble space telescope", "webb", "james webb space telescope",
    "nasa", "esa", "spitzer", "chandra", "untitled", "image", "photo",
})


def _normalized_title(title: str) -> str:
    """Lowercase, whitespace-collapsed, punctuation-trimmed form used both to
    spot generic captions and to keep two clips in one video from sharing one."""
    return re.sub(r"\s+", " ", title).strip().strip(".:-\u2013\u2014").lower()


def _is_filename_title(title: str) -> bool:
    if " " in title or title != title.lower():
        return False
    return bool(_IMAGE_EXT_RE.search(title) or (_FILENAME_RE.match(title) and re.search(r"\d", title)))


def looks_unsuitable(asset: ImageAsset) -> bool:
    """True if the asset is not sky imagery. Titles and keywords carry the
    most signal; descriptions mention 'galaxy' even for press photos, so the
    positive test uses title+keywords, and the blocklist scans everything."""
    title = asset.title.strip()
    if _PHOTO_ID_RE.match(title) or any(h in title.lower() for h in _TITLE_BLOCK):
        return True
    if _is_filename_title(title) or _normalized_title(title) in _GENERIC_TITLES:
        return True
    head = f"{title} {' '.join(asset.keywords)}".lower()
    blob = f"{head} {asset.description}".lower()
    if any(h in blob for h in BLOCKLIST_HINTS):
        return True
    return not any(h in head for h in SKY_HINTS)


def flat_black_fraction(asset: ImageAsset, sample: int = 256) -> float:
    """Fraction of the frame that is exactly black.

    Sky is noisy -- real background pixels are almost never exactly 0, so a
    large count of them means a synthetic fill rather than empty space. The
    common case is the stair-step notch left by Hubble's WFPC2 detector.
    Sampled with NEAREST so exact zeros survive the resize."""
    if asset.local_path is None:
        raise ValueError("asset must be downloaded before probing")
    with Image.open(asset.local_path) as im:
        small = im.convert("L").resize((sample, sample), Image.NEAREST)
    return small.histogram()[0] / float(sample * sample)


def probe_dimensions(asset: ImageAsset) -> tuple[int, int]:
    if asset.local_path is None:
        raise ValueError("asset must be downloaded before probing")
    with Image.open(asset.local_path) as im:
        asset.width, asset.height = im.size
    return asset.width, asset.height


def select_assets(
    candidates: list[ImageAsset],
    *,
    channel: str,
    ledger: Ledger,
    count: int,
    min_width: int,
    cache_dir: str,
    seed: str | None = None,
    prefer_keyword: str = "roman",
    max_flat_black: float = 0.02,
) -> list[ImageAsset]:
    used = ledger.used_asset_ids(channel)
    suitable = [a for a in candidates if not looks_unsuitable(a)]
    pool = [a for a in suitable if a.asset_id not in used]
    recycled = False
    if len(pool) < count and used:
        log.warning("pool exhausted (%d fresh of %d); resetting ledger for %s", len(pool), len(candidates), channel)
        ledger.reset_assets(channel)
        pool, recycled = suitable, True

    rng = random.Random(seed or date.today().isoformat())

    def ordered(items: list[ImageAsset]) -> list[ImageAsset]:
        out = list(items)
        rng.shuffle(out)
        # Stable partition: preferred assets first, then everything else.
        out.sort(key=lambda a: 0 if prefer_keyword in {k.lower() for k in a.keywords} else 1)
        return out

    chosen: list[ImageAsset] = []
    rejected: set[str] = set()
    titles: set[str] = set()

    def take(items: list[ImageAsset]) -> None:
        for asset in ordered(items):
            if len(chosen) >= count:
                return
            # Distinct archive entries share captions ("Space Science" three
            # times over). Chapter titles come straight from here, so two clips
            # in one video must never carry the same one. Metadata-only, so it
            # costs nothing -- check before spending a download on it.
            key = _normalized_title(asset.title)
            if key in titles:
                log.debug("skipping %s: duplicate title %r", asset.asset_id, asset.title)
                rejected.add(asset.asset_id)
                continue
            try:
                asset.download(cache_dir)
                w, h = probe_dimensions(asset)
            except Exception as e:  # noqa: BLE001 - one bad asset must not kill the run
                log.warning("skipping %s: %s", asset.asset_id, e)
                rejected.add(asset.asset_id)
                continue
            if w < min_width:
                log.debug("skipping %s: %dx%d below min_width %d", asset.asset_id, w, h, min_width)
                rejected.add(asset.asset_id)
                continue
            if max_flat_black > 0:
                try:
                    black = flat_black_fraction(asset)
                except Exception as e:  # noqa: BLE001 - a probe failure must not kill the run
                    log.warning("black-region check failed for %s: %s", asset.asset_id, e)
                    black = 0.0
                if black > max_flat_black:
                    log.info("skipping %s: %.0f%% of frame is a flat black block", asset.asset_id, black * 100)
                    rejected.add(asset.asset_id)
                    continue
            titles.add(key)
            chosen.append(asset)

    take(pool)

    # Everything above is judged on metadata; min_width and the black-block
    # check only speak up once a file is on disk. If those rejections left us
    # short, the fresh-asset rule is what has to give -- a repeated image beats
    # no video at all.
    if len(chosen) < count and used and not recycled:
        log.warning("only %d/%d after download checks; recycling used assets for %s", len(chosen), count, channel)
        ledger.reset_assets(channel)
        seen = {a.asset_id for a in chosen} | rejected
        take([a for a in suitable if a.asset_id not in seen])

    if len(chosen) < count:
        log.warning("only %d/%d assets selected for %s", len(chosen), count, channel)
    return chosen
