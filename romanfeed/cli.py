"""Command line entry point.

  romanfeed run config/channels/deep-space-ambient.yaml            # full daily run
  romanfeed run config/... --images 6 --seconds 8 --dry-run        # quick local proof
  romanfeed fetch config/...                                        # list candidate images
  romanfeed ledger                                                  # what has been rendered/published
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from romanfeed.config import load_config


def _cmd_run(args) -> int:
    from romanfeed.pipeline import RunOptions, run

    cfg = load_config(args.config)
    res = run(cfg, RunOptions(
        images=args.images, seconds_per_image=args.seconds, dry_run=True if args.dry_run else None,
        seed=args.seed, data_dir=Path(args.data_dir), output_dir=Path(args.output_dir), keep_work=args.keep_work,
    ))
    print(f"video:    {res.video_path}")
    print(f"metadata: {res.metadata_path}")
    print(f"duration: {res.duration_s:.1f}s across {res.n_images} images")
    print(f"youtube:  {res.youtube_id or '(dry run, not uploaded)'}")
    return 0


def _cmd_fetch(args) -> int:
    from romanfeed.sources import build_source

    cfg = load_config(args.config)
    for s in cfg.enabled_sources:
        src = build_source(s)
        assets = src.fetch()
        print(f"== {src.name}: {len(assets)} candidates")
        for a in assets[: args.limit]:
            print(f"  {a.asset_id:28} {a.date:10} {a.title[:70]}")
    return 0


def _cmd_ledger(args) -> int:
    from romanfeed.state import Ledger

    with Ledger(Path(args.data_dir) / "state.db") as ledger:
        vids = ledger.videos()
        if not vids:
            print("no videos recorded yet")
        for v in vids:
            state = f"youtube:{v.youtube_id}" if v.youtube_id else "not published"
            print(f"{v.rendered_at}  {v.video_slug:40} {v.duration_s:8.0f}s  {state}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="romanfeed", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--data-dir", default="data")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="render (and optionally upload) today's video")
    r.add_argument("config")
    r.add_argument("--images", type=int, help="override images_per_video")
    r.add_argument("--seconds", type=float, help="override seconds_per_image")
    r.add_argument("--dry-run", action="store_true", help="never upload, allow placeholder audio")
    r.add_argument("--seed", help="deterministic selection seed (default: today's date)")
    r.add_argument("--output-dir", default="output")
    r.add_argument("--keep-work", action="store_true", help="keep intermediate clips/frames")
    r.set_defaults(fn=_cmd_run)

    f = sub.add_parser("fetch", help="list candidate images from each source")
    f.add_argument("config")
    f.add_argument("--limit", type=int, default=20)
    f.set_defaults(fn=_cmd_fetch)

    l = sub.add_parser("ledger", help="show rendered/published videos")
    l.set_defaults(fn=_cmd_ledger)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
