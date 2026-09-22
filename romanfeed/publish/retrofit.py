"""Put a custom thumbnail on a video that is already on the channel.

New uploads get theirs from the pipeline; this is for the ones that went up
with YouTube's auto-picked frames, and for re-doing one that failed to set.

    romanfeed thumbnails VIDEO_ID path/or/https://url.jpg --length "8 HOURS"

thumbnails.set works with the upload-only token the daily workflow holds, so
no re-consent is needed."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def fetch_source(source: str, dest_dir: Path) -> Path:
    """A local path is used as-is; an http(s) URL is downloaded next to the output."""
    if not source.startswith(("http://", "https://")):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"no image at {path}")
        return path
    import requests

    from romanfeed.sources.base import USER_AGENT

    suffix = Path(source.split("?")[0]).suffix or ".jpg"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"source{suffix}"
    with requests.get(source, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


def retrofit_thumbnail(video_id: str, source: str, *, length: str, subject: str | None = None,
                       out_dir: Path = Path("output/thumbnails"), dry_run: bool = False, yt=None) -> int:
    """Build the thumbnail for one live video and send it with thumbnails.set."""
    from romanfeed.publish.youtube import client, set_thumbnail
    from romanfeed.render.thumbnail import make_thumbnail

    work = out_dir / video_id
    image = fetch_source(source, work)
    thumb = make_thumbnail(image, out_dir / f"{video_id}.thumb.jpg", length_label=length, subject=subject)
    print(f"thumbnail: {thumb} ({thumb.stat().st_size // 1024} KB)")
    if dry_run:
        print(f"== {video_id}: dry run, not sent")
        return 0
    try:
        set_thumbnail(yt or client(), video_id, thumb)
    except Exception as exc:  # googleapiclient HttpError; not imported so CI's dev extra suffices
        print(f"ERROR: thumbnails.set on {video_id} failed: {exc}")
        if "forbidden" in str(exc).lower() or getattr(getattr(exc, "resp", None), "status", None) == 403:
            print("  (custom thumbnails need a phone-verified channel: youtube.com/verify)")
        return 1
    print(f"== {video_id}: thumbnail set")
    return 0
