"""Pick the images for one video.

Rules, in order:
  1. Never re-use an asset the ledger says this channel already used
     (the pool auto-resets when it runs dry, so the channel never stalls).
  2. Drop assets that are too small to look good at the target resolution
     (checked after download; we don't trust API metadata for dimensions).
  2b. Drop assets with a solid block of pure black in frame -- usually the
     missing quadrant of a Hubble WFPC2 mosaic, which reads on screen as a
     rectangular hole. Metadata cannot see this; only the pixels can.
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
_TITLE_BLOCK = ("legacy", "anniversary", "celebrat", "future of", "mission", "team", "workshop", "conference")


def looks_unsuitable(asset: ImageAsset) -> bool:
    """True if the asset is not sky imagery. Titles and keywords carry the
    most signal; descriptions mention 'galaxy' even for press photos, so the
    positive test uses title+keywords, and the blocklist scans everything."""
    title = asset.title.strip()
    if _PHOTO_ID_RE.match(title) or any(h in title.lower() for h in _TITLE_BLOCK):
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
    pool = [a for a in candidates if a.asset_id not in used and not looks_unsuitable(a)]
    if len(pool) < count and used:
        log.warning("pool exhausted (%d fresh of %d); resetting ledger for %s", len(pool), len(candidates), channel)
        ledger.reset_assets(channel)
        pool = [a for a in candidates if not looks_unsuitable(a)]

    rng = random.Random(seed or date.today().isoformat())
    rng.shuffle(pool)
    # Stable partition: preferred assets first, then everything else.
    pool.sort(key=lambda a: 0 if prefer_keyword in {k.lower() for k in a.keywords} else 1)

    chosen: list[ImageAsset] = []
    for asset in pool:
        if len(chosen) >= count:
            break
        try:
            asset.download(cache_dir)
            w, h = probe_dimensions(asset)
        except Exception as e:  # noqa: BLE001 - one bad asset must not kill the run
            log.warning("skipping %s: %s", asset.asset_id, e)
            continue
        if w < min_width:
            log.debug("skipping %s: %dx%d below min_width %d", asset.asset_id, w, h, min_width)
            continue
        if max_flat_black > 0:
            try:
                black = flat_black_fraction(asset)
            except Exception as e:  # noqa: BLE001 - a probe failure must not kill the run
                log.warning("black-region check failed for %s: %s", asset.asset_id, e)
                black = 0.0
            if black > max_flat_black:
                log.info("skipping %s: %.0f%% of frame is a flat black block", asset.asset_id, black * 100)
                continue
        chosen.append(asset)

    if len(chosen) < count:
        log.warning("only %d/%d assets selected for %s", len(chosen), count, channel)
    return chosen
