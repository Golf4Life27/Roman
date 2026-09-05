"""SQLite ledger of what has been used and what has been published.

This is the memory of the system. It guarantees the channel never re-uses an
image until the whole pool has cycled, and it records every render and upload
so a human can audit what went out without opening YouTube.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets_used (
    asset_id   TEXT NOT NULL,
    channel    TEXT NOT NULL,
    used_at    TEXT NOT NULL,
    video_slug TEXT NOT NULL,
    PRIMARY KEY (asset_id, channel)
);
CREATE TABLE IF NOT EXISTS videos (
    video_slug   TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,
    path         TEXT NOT NULL,
    duration_s   REAL NOT NULL,
    rendered_at  TEXT NOT NULL,
    youtube_id   TEXT,
    published_at TEXT,
    title        TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class VideoRecord:
    video_slug: str
    channel: str
    path: str
    duration_s: float
    rendered_at: str
    youtube_id: str | None = None
    published_at: str | None = None
    title: str | None = None


class Ledger:
    def __init__(self, db_path: str | Path = "data/state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- assets ---------------------------------------------------------
    def used_asset_ids(self, channel: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT asset_id FROM assets_used WHERE channel = ?", (channel,)
        ).fetchall()
        return {r[0] for r in rows}

    def mark_assets_used(self, channel: str, asset_ids: list[str], video_slug: str) -> None:
        now = _now()
        self.conn.executemany(
            "INSERT OR REPLACE INTO assets_used VALUES (?, ?, ?, ?)",
            [(a, channel, now, video_slug) for a in asset_ids],
        )
        self.conn.commit()

    def reset_assets(self, channel: str) -> int:
        """Forget usage for a channel so the pool can cycle again."""
        cur = self.conn.execute("DELETE FROM assets_used WHERE channel = ?", (channel,))
        self.conn.commit()
        return cur.rowcount

    # -- videos ---------------------------------------------------------
    def record_video(self, rec: VideoRecord) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.video_slug, rec.channel, rec.path, rec.duration_s,
                rec.rendered_at, rec.youtube_id, rec.published_at, rec.title,
            ),
        )
        self.conn.commit()

    def mark_published(self, video_slug: str, youtube_id: str, title: str) -> None:
        self.conn.execute(
            "UPDATE videos SET youtube_id = ?, published_at = ?, title = ? WHERE video_slug = ?",
            (youtube_id, _now(), title, video_slug),
        )
        self.conn.commit()

    def videos(self, channel: str | None = None) -> list[VideoRecord]:
        q = "SELECT video_slug, channel, path, duration_s, rendered_at, youtube_id, published_at, title FROM videos"
        args: tuple = ()
        if channel:
            q += " WHERE channel = ?"
            args = (channel,)
        q += " ORDER BY rendered_at DESC"
        return [VideoRecord(*r) for r in self.conn.execute(q, args).fetchall()]
