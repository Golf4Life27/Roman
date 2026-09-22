"""YouTube Data API v3 upload with a dry-run path.

Dry run writes the exact request body next to the video so a human can review
what would have been uploaded. Real uploads need the `youtube` extra and an
OAuth client (see .env.example). Uploads from an unverified API project land
as private regardless of the requested privacy, which is what we want during
warm-up anyway: review, then flip to public in Studio."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from romanfeed.publish.metadata import VideoMetadata

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Editing metadata on a video that is already up needs more than upload:
# videos.update tolerates the upload scope, videos.list does not. force-ssl
# covers both. The upload scope is kept alongside it deliberately -- a token
# granted only force-ssl would make the daily upload path fail its refresh,
# because google-auth refuses a refresh whose granted scopes do not include
# the ones it asked for.
MANAGE_SCOPES = SCOPES + ["https://www.googleapis.com/auth/youtube.force-ssl"]


def request_body(meta: VideoMetadata) -> dict:
    return {
        "snippet": {
            "title": meta.title,
            "description": meta.description,
            "tags": meta.tags,
            "categoryId": meta.category_id,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": meta.privacy,
            "selfDeclaredMadeForKids": meta.made_for_kids,
            "license": "youtube",
            "embeddable": True,
        },
    }


def publish(video_path: Path, meta: VideoMetadata, *, mode: str = "dry-run", thumbnail: Path | None = None) -> str | None:
    body = request_body(meta)
    sidecar = video_path.with_suffix(".upload.json")
    sidecar.write_text(json.dumps(body, indent=2))
    if mode == "dry-run":
        log.info("dry-run: wrote %s (no upload)%s", sidecar, f", thumbnail {thumbnail}" if thumbnail else "")
        return None
    return _upload(video_path, body, thumbnail=thumbnail)


def token_path() -> Path:
    return Path(os.environ.get("YOUTUBE_TOKEN_PATH", "secrets/youtube.token.json"))


def _run_flow(scopes: list[str]):
    """Browser consent for `scopes`, saved to the token path."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret_path = Path(os.environ.get("YOUTUBE_CLIENT_SECRET_PATH", "secrets/client_secret.json"))
    flow = InstalledAppFlow.from_client_secrets_file(secret_path, scopes)
    creds = flow.run_local_server(port=0)
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    return creds


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path()
    creds = Credentials.from_authorized_user_file(path, SCOPES) if path.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        creds = _run_flow(SCOPES)
    return creds


def stored_credentials():
    """Credentials carrying whatever scopes the saved token was actually granted.

    The upload path pins SCOPES so a refresh fails loudly if the token ever
    stops covering uploads. The metadata and thumbnail tools must not do that:
    they run against the same saved token and only need to know what it can
    already do, so the scopes are read out of the token file rather than
    asserted. No browser flow here either -- in CI there is nobody to click
    Allow.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path()
    if not path.exists():
        raise SystemExit(f"no YouTube token at {path}; run `romanfeed auth` or set YOUTUBE_TOKEN_PATH")
    creds = Credentials.from_authorized_user_file(path)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def client(creds=None):
    """A YouTube Data API v3 service, built on the saved token by default.

    The one service builder for every caller: the daily upload, thumbnails,
    fix-metadata and schedule all come through here. The default reads the
    scopes out of the token file instead of asserting them, and never opens a
    browser, which is what CI needs -- a runner has nobody to click Allow, so
    the interactive flow belongs to `romanfeed auth` alone. A token that has
    lost a scope fails on the API call with YouTube's own message.
    """
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds or stored_credentials())


def mint_token(scopes: list[str] | None = None) -> Path:
    """Run the OAuth flow once (opens a browser) and save the refresh token.

    Asking for scopes beyond the upload one always re-runs consent: an
    already-granted token cannot be widened in place, and the saved one would
    otherwise be reused as-is.
    """
    if scopes and scopes != SCOPES:
        _run_flow(scopes)
    else:
        _credentials()
    return token_path()


def set_thumbnail(yt, video_id: str, thumbnail: Path) -> None:
    """thumbnails.set on one video. Raises on failure; callers decide how loud.

    The upload scope is enough for this call, but YouTube only accepts custom
    thumbnails from channels with intermediate features (phone-verified), and
    answers 403 otherwise."""
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(thumbnail), mimetype="image/jpeg")
    yt.thumbnails().set(videoId=video_id, media_body=media).execute()
    log.info("thumbnail set on %s from %s", video_id, thumbnail)


def _upload(video_path: Path, body: dict, *, thumbnail: Path | None = None) -> str:
    from googleapiclient.http import MediaFileUpload

    yt = client()
    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log.info("upload %.0f%%", status.progress() * 100)
    vid = resp["id"]
    log.info("uploaded https://youtu.be/%s", vid)
    if thumbnail:
        # The video is already up; a missing thumbnail is a cosmetic problem
        # to fix later with `romanfeed thumbnails`, never a reason to fail the run.
        try:
            set_thumbnail(yt, vid, thumbnail)
        except Exception as exc:  # googleapiclient HttpError, IO, anything
            log.warning("could not set thumbnail on %s (%s): %s", vid, thumbnail, exc)
    return vid
