"""Command line entry point.

  romanfeed run config/channels/deep-space-ambient.yaml            # full daily run
  romanfeed run config/... --images 6 --seconds 8 --dry-run        # quick local proof
  romanfeed fetch config/...                                        # list candidate images
  romanfeed ledger                                                  # what has been rendered/published
  romanfeed auth                                                    # mint the YouTube OAuth token (one time, local)
  romanfeed music add track.m4a --licence generated --genre ambient # register a track (licence required)
  romanfeed fix-metadata --video ID --video ID                      # decode bytes-repr chapters on live videos
  romanfeed schedule --video ID --at 2026-09-20T02:00:00Z           # let YouTube publish a private video itself
  romanfeed thumbnails VIDEO_ID image.jpg --length "8 HOURS"        # custom thumbnail on a video already up
  romanfeed shorts config/... [--count 1] [--dry-run]                # vertical Shorts cut from published videos
  romanfeed retitle --all [--dry-run]                               # sleep-search titles on videos already up
  romanfeed stats [--email] [--dry-run]                             # weekly subscriber / watch-hour report
  romanfeed compose piece.m4a --seconds 120 --seed foo              # compose one original ambient piece
  romanfeed live sync|plan|start [--dry-run]                        # nightly 10 h live stream (server only)
"""
from __future__ import annotations

import argparse
import logging
import sys
import os
from pathlib import Path

from romanfeed.config import load_config


def _cmd_run(args) -> int:
    from romanfeed.pipeline import RunOptions, run

    cfg = load_config(args.config)
    res = run(cfg, RunOptions(
        images=args.images, seconds_per_image=args.seconds, dry_run=True if args.dry_run else None,
        seed=args.seed, data_dir=Path(args.data_dir), output_dir=Path(args.output_dir), keep_work=args.keep_work,
        private_test=args.private_test,
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


def _cmd_auth(args) -> int:
    from romanfeed.publish.youtube import MANAGE_SCOPES, mint_token

    path = mint_token(MANAGE_SCOPES if args.scope == "manage" else None)
    print(f"token written to {path}")
    print("Store its contents as the YOUTUBE_TOKEN_JSON GitHub secret to let the daily workflow upload.")
    return 0


def _split_ids(values: list[str] | None) -> list[str]:
    """Accept --video A --video B and --video A,B; the workflow passes a list."""
    return [v.strip() for value in (values or []) for v in value.split(",") if v.strip()]


def _cmd_fix_metadata(args) -> int:
    from romanfeed.publish.fixup import fix_descriptions

    ids = _split_ids(args.video)
    if not ids:
        print("no video ids given")
        return 1
    return fix_descriptions(ids, dry_run=args.dry_run)


def _cmd_schedule(args) -> int:
    from romanfeed.publish.fixup import schedule_videos

    ids = _split_ids(args.video)
    if not ids:
        print("no video ids given")
        return 1
    try:
        return schedule_videos(ids, _split_ids(args.at), dry_run=args.dry_run)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1


def _cmd_thumbnails(args) -> int:
    from romanfeed.publish.retrofit import retrofit_thumbnail

    return retrofit_thumbnail(
        args.video_id, args.image, length=args.length, subject=args.subject,
        out_dir=Path(args.out_dir), dry_run=args.dry_run,
    )


def _cmd_shorts(args) -> int:
    from romanfeed.shorts import run_shorts

    cfg = load_config(args.config)
    res = run_shorts(cfg, count=args.count, dry_run=args.dry_run, data_dir=Path(args.data_dir), output_dir=Path(args.output_dir))
    if not cfg.shorts.enabled:
        print("shorts.enabled is false in the channel config: rendered only, nothing uploaded")
    for r in res:
        print(f"{r.asset_id:32} from {r.parent_slug:34} {r.youtube_id or '(not uploaded)':14} {r.title}")
    if not res:
        print("no Shorts made (daily cap reached, or nothing left to cut from)")
    return 0


def _smtp_settings() -> tuple[dict | None, list[str]]:
    """SMTP settings from the environment, and the names of any missing ones."""
    env = {k: os.environ.get(k, "").strip() for k in ("STATS_EMAIL_TO", "SMTP_USER", "SMTP_PASSWORD")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        return None, missing
    port = os.environ.get("SMTP_PORT", "").strip() or "465"
    return {
        "to": env["STATS_EMAIL_TO"], "user": env["SMTP_USER"], "password": env["SMTP_PASSWORD"],
        "smtp_host": os.environ.get("SMTP_HOST", "").strip() or "smtp.gmail.com", "smtp_port": int(port),
    }, []


def _cmd_stats(args) -> int:
    """Print the weekly report; with --email, also mail it.

    SMTP settings are checked before any API call, so a misconfigured run
    fails in a second with the names of what is missing rather than after
    the report has been built.
    """
    from datetime import date

    from romanfeed.publish import stats as st
    from romanfeed.publish.fixup import _is_scope_error

    smtp = None
    if args.email and not args.dry_run:
        smtp, missing = _smtp_settings()
        if missing:
            print(f"ERROR: --email needs {', '.join(missing)} set in the environment "
                  "(SMTP_HOST/SMTP_PORT default to smtp.gmail.com:465) -- see docs/STATS_EMAIL.md")
            return 2

    today = date.today()
    try:
        data = st.fetch_stats(today)
    except Exception as exc:  # googleapiclient's HttpError, not imported (see fixup.py)
        blob = str(exc).lower()
        if "accessnotconfigured" in blob or "has not been used in project" in blob:
            print(f"ERROR: {st.API_DISABLED_HELP}")
            return 2
        if _is_scope_error(exc):
            print(f"ERROR: {st.SCOPE_HELP}")
            return 2
        raise

    subject, text, html = st.report(data, today)
    print(f"Subject: {subject}\n")
    print(text)
    if args.email and args.dry_run:
        print("(dry run: no email sent)")
    elif smtp:
        st.send_email(subject, text, html, **smtp)
        print("email sent")
    return 0


def _cmd_compose(args) -> int:
    import secrets

    from romanfeed.audio.composer import compose_piece, measure_loudness
    from romanfeed.render.ffmpeg import probe_duration

    seed = args.seed or secrets.token_hex(4)
    track = compose_piece(Path(args.out), seconds=args.seconds, seed=seed)
    print(f"title:    {track.title}")
    print(f"seed:     {seed}")
    print(f"duration: {probe_duration(str(track.path)):.1f}s")
    print(f"loudness: {measure_loudness(track.path):.1f} LUFS")
    print(f"file:     {track.path}")
    return 0


def _cmd_live(args) -> int:
    from romanfeed.live import run as live

    cfg = load_config(args.config)
    lib = live.library_dir(cfg, args.library_dir)
    if args.live_cmd == "sync":
        return live.cmd_sync(cfg, lib)
    try:
        if args.live_cmd == "plan":
            return live.cmd_plan(cfg, lib)
        return live.cmd_start(cfg, lib, dry_run=args.dry_run)
    except ValueError as exc:  # empty library, bad length
        print(f"ERROR: {exc}")
        return 1


def _cmd_retitle(args) -> int:
    from romanfeed.publish.retitle import retitle

    cfg = load_config(args.config)
    ids = _split_ids(args.video)
    if not ids and not args.all:
        print("give --video ID (repeatable) or --all")
        return 1
    return retitle(cfg, ids or None, dry_run=args.dry_run)


def _cmd_music_add(args) -> int:
    from romanfeed.audio.library import register_track

    entry = register_track(
        Path(args.manifest), Path(args.file), licence=args.licence, genre=args.genre,
        title=args.title, artist=args.artist, notes=args.notes, tags=args.tag or [],
    )
    print(f"registered {entry['id']} ({entry['licence']}) in {args.manifest}")
    return 0


def _cmd_music_list(args) -> int:
    from romanfeed.audio.library import MusicLibrary

    lib = MusicLibrary(args.manifest)
    if not lib.tracks:
        print("no tracks registered")
    for t in lib.tracks:
        flag = "ok " if t.publishable and t.path.exists() else ("NO-FILE" if not t.path.exists() else "UNPUBLISHABLE")
        print(f"{flag:13} {t.genre:10} {t.licence:10} {t.id:24} {t.title}")
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
    r.add_argument("--private-test", action="store_true", help="upload a PRIVATE [TEST] video to verify the chain; placeholder audio allowed")
    r.set_defaults(fn=_cmd_run)

    f = sub.add_parser("fetch", help="list candidate images from each source")
    f.add_argument("config")
    f.add_argument("--limit", type=int, default=20)
    f.set_defaults(fn=_cmd_fetch)

    l = sub.add_parser("ledger", help="show rendered/published videos")
    l.set_defaults(fn=_cmd_ledger)

    a = sub.add_parser("auth", help="run the one-time YouTube OAuth flow and save the token")
    a.add_argument("--scope", choices=["upload", "manage"], default="upload",
                   help="manage also grants youtube.force-ssl (read/edit metadata on videos already up) "
                        "and yt-analytics.readonly (the weekly stats email)")
    a.set_defaults(fn=_cmd_auth)

    fm = sub.add_parser("fix-metadata", help="re-decode bytes-repr text in the descriptions of videos already uploaded")
    fm.add_argument("--video", action="append", required=True, help="YouTube video id (repeat, or comma-separate)")
    fm.add_argument("--dry-run", action="store_true", help="print before/after and write nothing")
    fm.set_defaults(fn=_cmd_fix_metadata)

    sc = sub.add_parser("schedule", help="set publishAt on a private video so YouTube makes it public itself")
    sc.add_argument("--video", action="append", required=True, help="YouTube video id (repeat, or comma-separate)")
    sc.add_argument("--at", action="append",
                    help="RFC3339 publish time in UTC, e.g. 2026-09-20T02:00:00Z (one value, or one per --video; "
                         "omitted uses the recorded Saturday 21:00/21:05 CDT slots)")
    sc.add_argument("--dry-run", action="store_true", help="print the status body and write nothing")
    sc.set_defaults(fn=_cmd_schedule)


    t = sub.add_parser("thumbnails", help="build a custom thumbnail and set it on a video already uploaded")
    t.add_argument("video_id")
    t.add_argument("image", help="lead image: a local path or an http(s) URL")
    t.add_argument("--length", required=True, help='headline, e.g. "8 HOURS" or "1 HOUR"')
    t.add_argument("--subject", help='small line under the headline (default "SPACE FOR SLEEP")')
    t.add_argument("--out-dir", default="output/thumbnails")
    t.add_argument("--dry-run", action="store_true", help="build the jpg and send nothing")
    t.set_defaults(fn=_cmd_thumbnails)

    sh = sub.add_parser("shorts", help="cut vertical Shorts from published videos (uploads only when shorts.enabled)")
    sh.add_argument("config")
    sh.add_argument("--count", type=int, help="at most this many (default: what is left of shorts.per_day today)")
    sh.add_argument("--dry-run", action="store_true", help="render and write metadata, upload nothing")
    sh.add_argument("--output-dir", default="output")
    sh.set_defaults(fn=_cmd_shorts)

    st = sub.add_parser("stats", help="weekly subscriber / watch-hour report against the YPP goal")
    st.add_argument("--email", action="store_true",
                    help="also email it (env STATS_EMAIL_TO, SMTP_USER, SMTP_PASSWORD; SMTP_HOST, SMTP_PORT optional)")
    st.add_argument("--dry-run", action="store_true", help="print the report and send nothing")
    st.set_defaults(fn=_cmd_stats)

    co = sub.add_parser("compose", help="compose one original ambient piece (licence: owned)")
    co.add_argument("out", help="output .m4a")
    co.add_argument("--seconds", type=float, default=120.0)
    co.add_argument("--seed", help="same seed, same piece (default: random, printed)")
    co.set_defaults(fn=_cmd_compose)

    lv = sub.add_parser("live", help="nightly ~10 h live stream from the rendered library (always ends < 12 h)")
    lsub = lv.add_subparsers(dest="live_cmd", required=True)
    for name, text in [("sync", "pull new render artifacts from GitHub and make stream-ready copies"),
                       ("plan", "print tonight's playlist; nothing is sent"),
                       ("start", "run tonight's broadcast (refuses unless live.enabled is true)")]:
        lp = lsub.add_parser(name, help=text)
        lp.add_argument("--config", default="config/channels/deep-space-ambient.yaml")
        lp.add_argument("--library-dir", help="override live.library_dir (the server uses /var/lib/romanfeed/live)")
        if name == "start":
            lp.add_argument("--dry-run", action="store_true",
                            help="print the plan, broadcast body and ffmpeg command (key masked); send nothing")
    lv.set_defaults(fn=_cmd_live)

    rt = sub.add_parser("retitle", help="sleep-search titles/descriptions on videos already up (needs --scope manage token)")
    rt.add_argument("--config", default="config/channels/deep-space-ambient.yaml")
    rt.add_argument("--video", action="append", help="YouTube video id (repeat, or comma-separate)")
    rt.add_argument("--all", action="store_true", help="every upload on the channel (Shorts are skipped)")
    rt.add_argument("--dry-run", action="store_true", help="print before/after and write nothing")
    rt.set_defaults(fn=_cmd_retitle)

    m = sub.add_parser("music", help="manage the licensed music manifest")
    msub = m.add_subparsers(dest="music_cmd", required=True)
    ma = msub.add_parser("add", help="copy a track into assets/music/<genre>/ and register it")
    ma.add_argument("file")
    ma.add_argument("--licence", required=True, choices=["owned", "generated", "licensed", "cc0"])
    ma.add_argument("--genre", default="ambient")
    ma.add_argument("--title", default="")
    ma.add_argument("--artist", default="")
    ma.add_argument("--notes", default="", help="provider/plan/licence id; where the receipt lives")
    ma.add_argument("--tag", action="append")
    ma.add_argument("--manifest", default="assets/music/manifest.yaml")
    ma.set_defaults(fn=_cmd_music_add)
    ml = msub.add_parser("list", help="show registered tracks and whether they are publishable")
    ml.add_argument("--manifest", default="assets/music/manifest.yaml")
    ml.set_defaults(fn=_cmd_music_list)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
