"""Pick the images for one video.

Rules, in order:
  1. Never re-use an asset the ledger says this channel already used
     (the pool auto-resets when it runs dry, so the channel never stalls).
  2. Drop assets that are too small to look good at the target resolution
     (checked after download; we don't trust API metadata for dimensions).
  3. Prefer Roman assets over warm-up assets when both are present.
  4. Shuffle deterministically by date so a re-run on the same day gives the
     same video (idempotent daily job), but each day differs.
"""
from __future__ import annotations

import logging
import random
from datetime import date

from PIL import Image

from romanfeed.sources.base import ImageAsset
from romanfeed.state import Ledger

log = logging.getLogger(__name__)

BLOCKLIST_HINTS = ("logo", "portrait", "headshot", "diagram", "chart", "infographic", "poster", "screenshot", "meeting", "press conference")


def looks_unsuitable(asset: ImageAsset) -> bool:
    blob = f"{asset.title} {asset.description} {' '.join(asset.keywords)}".lower()
    return any(h in blob for h in BLOCKLIST_HINTS)


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
        chosen.append(asset)

    if len(chosen) < count:
        log.warning("only %d/%d assets selected for %s", len(chosen), count, channel)
    return chosen
