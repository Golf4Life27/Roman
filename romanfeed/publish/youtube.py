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


def publish(video_path: Path, meta: VideoMetadata, *, mode: str = "dry-run") -> str | None:
    body = request_body(meta)
    sidecar = video_path.with_suffix(".upload.json")
    sidecar.write_text(json.dumps(body, indent=2))
    if mode == "dry-run":
        log.info("dry-run: wrote %s (no upload)", sidecar)
        return None
    return _upload(video_path, body)


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = Path(os.environ.get("YOUTUBE_TOKEN_PATH", "secrets/youtube.token.json"))
    secret_path = Path(os.environ.get("YOUTUBE_CLIENT_SECRET_PATH", "secrets/client_secret.json"))
    creds = Credentials.from_authorized_user_file(token_path, SCOPES) if token_path.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
    return creds


def _upload(video_path: Path, body: dict) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    yt = build("youtube", "v3", credentials=_credentials())
    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log.info("upload %.0f%%", status.progress() * 100)
    vid = resp["id"]
    log.info("uploaded https://youtu.be/%s", vid)
    return vid
