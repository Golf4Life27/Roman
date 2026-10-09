"""Writing files to a GitHub Release: the cozy scene library and the
social clips (romanfeed/crosspost.py).

Runs in GitHub Actions with the workflow's GITHUB_TOKEN (contents: write).
Reading needs none of this: the repo is public, so files download straight
from the release URLs.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


class Release:
    def __init__(self, repo: str, tag: str, token: str | None = None, session: requests.Session | None = None, *,
                 title: str = "Cozy scenes",
                 body: str = "Seamless 10-second cozy scene loops for the live stream and the weekly cozy video. "
                             "scenes.json lists them. Managed by .github/workflows/cozy-scenes.yml; do not edit by hand."):
        token = token or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN is not set (the workflow provides it)")
        self.repo, self.tag, self.title, self.body = repo, tag, title, body
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})
        self.data = self._ensure()

    def _ensure(self) -> dict:
        r = self.s.get(f"{API}/repos/{self.repo}/releases/tags/{self.tag}", timeout=30)
        if r.status_code == 200:
            return r.json()
        r = self.s.post(f"{API}/repos/{self.repo}/releases", timeout=30, json={
            "tag_name": self.tag, "name": self.title, "body": self.body,
            "prerelease": True,  # keeps it off the repo's "Latest release" badge
        })
        r.raise_for_status()
        log.info("created release %s", self.tag)
        return r.json()

    def assets(self) -> dict[str, dict]:
        r = self.s.get(f"{API}/repos/{self.repo}/releases/{self.data['id']}/assets?per_page=100", timeout=30)
        r.raise_for_status()
        return {a["name"]: a for a in r.json()}

    def upload(self, path: Path, name: str | None = None, *, replace: bool = True) -> str:
        """Attach `path` as `name`; an existing asset of that name is replaced."""
        name = name or path.name
        existing = self.assets().get(name)
        if existing:
            if not replace:
                return existing["browser_download_url"]
            self.s.delete(f"{API}/repos/{self.repo}/releases/assets/{existing['id']}", timeout=30).raise_for_status()
        ctype = {"json": "application/json", "mp4": "video/mp4", "jpg": "image/jpeg"}.get(name.rsplit(".", 1)[-1],
                                                                                         "application/octet-stream")
        with open(path, "rb") as f:
            r = self.s.post(f"{UPLOADS}/repos/{self.repo}/releases/{self.data['id']}/assets",
                            params={"name": name}, data=f, headers={"Content-Type": ctype}, timeout=600)
        r.raise_for_status()
        log.info("uploaded %s (%d bytes)", name, path.stat().st_size)
        return r.json()["browser_download_url"]

    def prune(self, older_than_days: int, *, keep: set[str] | None = None) -> list[str]:
        """Delete assets uploaded more than `older_than_days` ago."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        gone = []
        for name, a in self.assets().items():
            if keep and name in keep:
                continue
            if datetime.fromisoformat(a["created_at"].replace("Z", "+00:00")) < cutoff:
                self.s.delete(f"{API}/repos/{self.repo}/releases/assets/{a['id']}", timeout=30).raise_for_status()
                gone.append(name)
        return gone
