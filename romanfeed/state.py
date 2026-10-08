"""SQLite ledger of what has been used and what has been published.

This is the memory of the system. It guarantees the channel never re-uses an
image until the whole pool has cycled, and it records every render and upload
so a human can audit what went out without opening YouTube.
"""
from __future__ import annotations

import json
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
-- What each video showed, in order, with enough of each asset to find and
-- credit it again later: Shorts are cut from these long after the render's
-- working files are gone.
CREATE TABLE IF NOT EXISTS video_assets (
    video_slug TEXT NOT NULL,
    position   INTEGER NOT NULL,
    asset_id   TEXT NOT NULL,
    PRIMARY KEY (video_slug, position)
);
CREATE TABLE IF NOT EXISTS asset_meta (
    asset_id TEXT PRIMARY KEY,
    data     TEXT NOT NULL
);
-- Public channel counts, sampled whenever a job runs with the API key. With
-- no Analytics permission, week-over-week change comes from these.
CREATE TABLE IF NOT EXISTS channel_snapshots (
    taken_on    TEXT PRIMARY KEY,
    subscribers INTEGER NOT NULL,
    views       INTEGER NOT NULL,
    videos      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS video_views (
    taken_on TEXT NOT NULL,
    video_id TEXT NOT NULL,
    views    INTEGER NOT NULL,
    PRIMARY KEY (taken_on, video_id)
);
CREATE TABLE IF NOT EXISTS shorts (
    asset_id    TEXT NOT NULL,
    channel     TEXT NOT NULL,
    parent_slug TEXT NOT NULL,
    youtube_id  TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (asset_id, channel)
);
"""

META_FIELDS = ("asset_id", "title", "url", "source", "credit", "description", "date", "width", "height", "licence", "keywords")


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

    def record_video_assets(self, video_slug: str, assets: list) -> None:
        """Remember the running order and each asset's metadata (not the file)."""
        self.conn.execute("DELETE FROM video_assets WHERE video_slug = ?", (video_slug,))
        self.conn.executemany(
            "INSERT INTO video_assets VALUES (?, ?, ?)",
            [(video_slug, i, a.asset_id) for i, a in enumerate(assets)],
        )
        self.save_asset_meta(assets)

    def save_asset_meta(self, assets: list) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO asset_meta VALUES (?, ?)",
            [(a.asset_id, json.dumps({f: getattr(a, f) for f in META_FIELDS})) for a in assets],
        )
        self.conn.commit()

    def asset_meta(self, asset_ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(asset_ids), 500):
            chunk = asset_ids[i:i + 500]
            q = f"SELECT asset_id, data FROM asset_meta WHERE asset_id IN ({','.join('?' * len(chunk))})"
            out.update({r[0]: json.loads(r[1]) for r in self.conn.execute(q, chunk)})
        return out

    def assets_of_video(self, video_slug: str) -> list[str]:
        """Asset ids a video showed, in order when the order was recorded.

        Videos rendered before video_assets existed fall back to assets_used,
        which knows the set but not the order."""
        rows = self.conn.execute(
            "SELECT asset_id FROM video_assets WHERE video_slug = ? ORDER BY position", (video_slug,)
        ).fetchall()
        if rows:
            return [r[0] for r in rows]
        rows = self.conn.execute(
            "SELECT asset_id FROM assets_used WHERE video_slug = ? ORDER BY asset_id", (video_slug,)
        ).fetchall()
        return [r[0] for r in rows]

    # -- merging -------------------------------------------------------
    MERGE_TABLES = ("assets_used", "videos", "video_assets", "asset_meta", "shorts",
                    "channel_snapshots", "video_views")

    def merge_from(self, other: str | Path) -> dict[str, int]:
        """Union another copy of the ledger into this one.

        Jobs run in parallel (a 3-hour render overlaps Shorts slots) and each
        saves the copy it restored at the start plus its own additions; without
        a merge the last to finish erases the others' records -- which is how
        one image became two Shorts on 2026-10-07. Rows only ever get added,
        so a union is right; the one in-place update (a video getting its
        YouTube id) is carried over where this copy lacks it."""
        added: dict[str, int] = {}
        self.conn.execute("ATTACH DATABASE ? AS o", (str(other),))
        try:
            have = {r[0] for r in self.conn.execute("SELECT name FROM o.sqlite_master WHERE type='table'")}
            for t in self.MERGE_TABLES:
                if t not in have:
                    continue
                before = self.conn.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
                self.conn.execute(f"INSERT OR IGNORE INTO main.{t} SELECT * FROM o.{t}")
                added[t] = self.conn.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0] - before
            if "videos" in have:
                self.conn.execute(
                    "UPDATE main.videos SET youtube_id = (SELECT o.videos.youtube_id FROM o.videos "
                    "WHERE o.videos.video_slug = main.videos.video_slug), "
                    "published_at = (SELECT o.videos.published_at FROM o.videos "
                    "WHERE o.videos.video_slug = main.videos.video_slug) "
                    "WHERE youtube_id IS NULL AND EXISTS (SELECT 1 FROM o.videos WHERE "
                    "o.videos.video_slug = main.videos.video_slug AND o.videos.youtube_id IS NOT NULL)")
            self.conn.commit()
        finally:
            self.conn.execute("DETACH DATABASE o")
        return added

    # -- channel snapshots -------------------------------------------------
    def record_snapshot(self, taken_on: str, subscribers: int, views: int, videos: int) -> None:
        self.conn.execute("INSERT OR REPLACE INTO channel_snapshots VALUES (?, ?, ?, ?)",
                          (taken_on, subscribers, views, videos))
        self.conn.commit()

    def snapshot_on_or_before(self, day: str) -> tuple[str, int, int, int] | None:
        row = self.conn.execute(
            "SELECT taken_on, subscribers, views, videos FROM channel_snapshots WHERE taken_on <= ? "
            "ORDER BY taken_on DESC LIMIT 1", (day,)).fetchone()
        return tuple(row) if row else None

    def record_video_views(self, taken_on: str, views: dict[str, int]) -> None:
        self.conn.executemany("INSERT OR REPLACE INTO video_views VALUES (?, ?, ?)",
                              [(taken_on, vid, n) for vid, n in views.items()])
        self.conn.commit()

    def first_video_views_on_or_after(self, day: str) -> tuple[str, dict[str, int]] | None:
        row = self.conn.execute("SELECT MIN(taken_on) FROM video_views WHERE taken_on >= ?", (day,)).fetchone()
        if not row or not row[0]:
            return None
        rows = self.conn.execute("SELECT video_id, views FROM video_views WHERE taken_on = ?", (row[0],)).fetchall()
        return row[0], {r[0]: r[1] for r in rows}

    # -- shorts ---------------------------------------------------------
    def shorts_asset_ids(self, channel: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT asset_id FROM shorts WHERE channel = ?", (channel,))}

    def shorts_per_parent(self, channel: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT parent_slug, COUNT(*) FROM shorts WHERE channel = ? GROUP BY parent_slug", (channel,)
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def record_short(self, channel: str, asset_id: str, parent_slug: str, youtube_id: str | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO shorts VALUES (?, ?, ?, ?, ?)",
            (asset_id, channel, parent_slug, youtube_id, _now()),
        )
        self.conn.commit()

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
