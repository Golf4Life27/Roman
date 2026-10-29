"""Upload access for a second (third, ...) channel, without a new secret.

Every channel needs its own OAuth token: Google ties a token to the one
channel picked on the consent screen. Space Screens' token lives in the
YOUTUBE_TOKEN_JSON repository secret, which only the owner can paste. Asking
him to paste a new secret per channel is the step that stalls a channel
network, so sibling channels work like this instead:

  1. `romanfeed channel-link <slug>` (the Connect channel workflow) prints a
     consent address built from the Space Screens OAuth client (client id
     only; nothing secret). The owner opens it, picks the new channel, and
     allows. Google sends the browser to http://localhost:8765/?code=...,
     which does not load -- that address is what he hands back.
  2. `romanfeed channel-finish <slug> <address>` trades the code for a token
     with the client secret (read from YOUTUBE_TOKEN_JSON at run time),
     checks which channel it is for, encrypts it and stores the ciphertext
     as a release asset (channel-tokens/<slug>.token.enc).
  3. Every job for that channel runs `romanfeed channel-token <slug>`, which
     downloads and decrypts it to the token path.

Why this is safe to keep in a public repository:
  * the token file is AES-256 encrypted (openssl, PBKDF2) with a key derived
     from the OAuth client secret, which exists only inside the Actions
     secret -- the ciphertext is useless without it;
  * the consent code in step 2 is single-use, expires in minutes, and is
     bound to a PKCE verifier that is itself derived from the client secret,
     so a code seen in a run log cannot be redeemed by anyone else.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import subprocess
import urllib.parse
from pathlib import Path

import requests

from romanfeed.publish.youtube import MANAGE_SCOPES

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
DEVICE_URL = "https://oauth2.googleapis.com/device/code"
# Google's code-on-another-screen sign-in offers only these two YouTube scopes
# (not youtube.upload); `youtube` covers uploads and reading the channel.
DEVICE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT = "http://localhost:8765/"
RELEASE_TAG = "channel-tokens"


def _client(main_token: Path) -> tuple[str, str]:
    data = json.loads(Path(main_token).read_text())
    cid, secret = data.get("client_id"), data.get("client_secret")
    if not cid or not secret:
        raise SystemExit(f"{main_token} has no client_id/client_secret")
    return cid, secret


def _verifier(secret: str, slug: str) -> str:
    """PKCE verifier only this repository's Actions can compute (43+ chars)."""
    mac = hmac.new(secret.encode(), f"romanfeed-pkce:{slug}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac + mac[:8]).decode().rstrip("=")


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _key(secret: str, slug: str) -> str:
    return hmac.new(secret.encode(), f"romanfeed-channel-token:{slug}".encode(), hashlib.sha256).hexdigest()


def consent_url(main_token: Path, slug: str, scopes: list[str] | None = None) -> str:
    cid, secret = _client(main_token)
    q = {
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": " ".join(scopes or MANAGE_SCOPES), "access_type": "offline",
        # select_account: the channel picker; consent: always issue a refresh token.
        "prompt": "select_account consent", "include_granted_scopes": "false",
        "state": slug, "code_challenge": _challenge(_verifier(secret, slug)), "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def code_from(address: str) -> str:
    """The code out of the address the browser landed on (or a bare code)."""
    address = address.strip()
    if "code=" not in address:
        return address
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(address).query)
    if qs.get("error"):
        raise SystemExit(f"consent was not given: {qs['error'][0]}")
    return qs["code"][0]


def exchange(main_token: Path, slug: str, address: str) -> dict:
    cid, secret = _client(main_token)
    resp = requests.post(TOKEN_URL, data={
        "code": code_from(address), "client_id": cid, "client_secret": secret, "redirect_uri": REDIRECT,
        "grant_type": "authorization_code", "code_verifier": _verifier(secret, slug),
    }, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"Google refused the code ({resp.status_code}): {resp.json().get('error_description', resp.text[:200])}")
    tok = resp.json()
    if not tok.get("refresh_token"):
        raise SystemExit("Google sent no refresh token; run the consent link again")
    # The same shape google-auth writes, so every existing loader reads it.
    return {
        "token": tok.get("access_token"), "refresh_token": tok["refresh_token"], "token_uri": TOKEN_URL,
        "client_id": cid, "client_secret": secret, "scopes": tok.get("scope", "").split(),
    }


def device_client(path: Path) -> tuple[str, str]:
    """(client id, secret) of the "TVs and Limited Input devices" client, from
    the JSON Google's console downloads ({"installed": {...}}) or a flat one."""
    data = json.loads(Path(path).read_text())
    data = data.get("installed") or data.get("web") or data
    cid, secret = data.get("client_id"), data.get("client_secret")
    if not cid or not secret:
        raise SystemExit(f"{path} has no client_id/client_secret")
    return cid, secret


def device_start(client: Path, scopes: list[str] | None = None) -> dict:
    """Ask Google for a short code the owner types at google.com/device.
    Returns device_code (kept private), user_code, verification_url,
    expires_in and interval."""
    cid, _ = device_client(client)
    resp = requests.post(DEVICE_URL, data={"client_id": cid, "scope": " ".join(scopes or DEVICE_SCOPES)}, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"Google refused the device sign-in ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def device_finish(client: Path, start: dict, *, sleep=None, clock=None) -> dict:
    """Wait until the owner has entered the code and allowed, then return the
    token in google-auth's authorized-user shape."""
    import time

    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    cid, secret = device_client(client)
    interval = int(start.get("interval", 5))
    deadline = clock() + int(start.get("expires_in", 1800))
    while clock() < deadline:
        sleep(interval)
        resp = requests.post(TOKEN_URL, data={
            "client_id": cid, "client_secret": secret, "device_code": start["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }, timeout=30)
        tok = resp.json()
        err = tok.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err:
            raise SystemExit(f"sign-in not completed: {err}")
        if not tok.get("refresh_token"):
            raise SystemExit("Google sent no refresh token; start again")
        return {
            "token": tok.get("access_token"), "refresh_token": tok["refresh_token"], "token_uri": TOKEN_URL,
            "client_id": cid, "client_secret": secret, "scopes": tok.get("scope", "").split(),
        }
    raise SystemExit("the code expired before it was entered; start again")


def channel_of(token: dict) -> tuple[str, str]:
    """(channel id, title) the token uploads to."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_info(token)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
    if not items:
        raise SystemExit("that Google sign-in has no YouTube channel; pick the new channel on the consent screen")
    return items[0]["id"], items[0]["snippet"]["title"]


def channel_by_test_upload(token: dict, work: Path) -> tuple[str, str]:
    """(channel id, title) for an upload-only token, which cannot ask "which
    channel am I?" directly: upload a 4-second PRIVATE clip and read the
    channel off the reply. It stays private on that channel; nobody sees it."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    work.mkdir(parents=True, exist_ok=True)
    clip = work / "connection-check.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x0b3d5c:s=1280x720:d=4",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "4", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
    creds = Credentials.from_authorized_user_info(token)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {"snippet": {"title": "[TEST] connection check", "description": "Private test upload.", "categoryId": "22"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    resp = yt.videos().insert(part="snippet,status", body=body,
                              media_body=MediaFileUpload(str(clip), mimetype="video/mp4")).execute()
    snip = resp.get("snippet") or {}
    return snip.get("channelId", ""), snip.get("channelTitle", "")


def _openssl(args: list[str], data: bytes, key: str) -> bytes:
    env = dict(os.environ, ROMANFEED_TOKEN_KEY=key)
    res = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256",
                          "-pass", "env:ROMANFEED_TOKEN_KEY", *args], input=data, capture_output=True, env=env)
    if res.returncode:
        raise SystemExit(f"openssl failed: {res.stderr.decode()[-200:]}")
    return res.stdout


def encrypt(token: dict, main_token: Path, slug: str) -> bytes:
    _, secret = _client(main_token)
    return _openssl(["-salt"], json.dumps(token).encode(), _key(secret, slug))


def decrypt(blob: bytes, main_token: Path, slug: str) -> dict:
    _, secret = _client(main_token)
    return json.loads(_openssl(["-d"], blob, _key(secret, slug)))


def asset_name(slug: str) -> str:
    return f"{slug}.token.enc"
