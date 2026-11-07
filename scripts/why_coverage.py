"""How many of the channel's images have a caption that answers a "why".

Read-only: prints a summary and writes every deep-space caption to a JSON file
(public archive text) so the matcher can be tuned against real captions.

    python scripts/why_coverage.py data/state.db out/captions.json
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

from romanfeed import why
from romanfeed.shorts import asset_from_meta, short_lead
from romanfeed.state import Ledger


def main(db: str, out: str) -> None:
    with Ledger(db) as led:
        slugs = [r[0] for r in led.conn.execute(
            "SELECT DISTINCT video_slug FROM video_assets WHERE video_slug LIKE 'deep-space-ambient-%'")]
        ids = sorted({a for s in slugs for a in led.assets_of_video(s)})
        meta = led.asset_meta(ids)
    rows, tally = [], Counter()
    for a in ids:
        if a not in meta:
            tally["no metadata"] += 1
            continue
        asset = asset_from_meta(meta[a])
        lead = short_lead(asset)
        found = why.find_why(asset, lead)
        page = why.source_page(asset)
        tally["total"] += 1
        tally["archive page" if page else "no archive page"] += 1
        tally[f"prefix {a.split(':')[0]}"] += 1
        if not asset.description.strip():
            tally["empty caption"] += 1
        if found:
            tally["why found"] += 1
        rows.append({"id": a, "title": asset.title, "source": asset.source, "lead": lead,
                     "description": asset.description, "why": found.question if found else None,
                     "answer": found.answer if found else None})
    print(f"{len(slugs)} videos, {len(ids)} images")
    for k, v in sorted(tally.items()):
        print(f"  {k}: {v}")
    for r in [r for r in rows if r["why"]][:10]:
        print(f"HIT {r['id']}: {r['why']} | {r['answer'][:160]}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(rows, indent=1))
    if os.environ.get("PRINT_CAPTIONS"):
        # The artifact store can be out of reach; the job log never is.
        for r in rows:
            print("CAPTION " + json.dumps(r))


if __name__ == "__main__":
    main(*sys.argv[1:3])
