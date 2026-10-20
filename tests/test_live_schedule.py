"""The morning job that creates tonight's broadcast (romanfeed/live/schedule.py)."""
from __future__ import annotations

import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from romanfeed.config import load_config
from romanfeed.cozy import calendar as cal
from romanfeed.cozy import scenes as sc
from romanfeed.live import schedule

KEY = "abcd-1234-efgh"
CT = ZoneInfo("America/Chicago")


class _Call:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.result


class FakeYT:
    def __init__(self, upcoming=(), bind_fails=False):
        self.upcoming, self.bind_fails, self.calls = list(upcoming), bind_fails, []

    def _rec(self, name, result=None, exc=None):
        def f(**kw):
            self.calls.append((name, kw))
            return _Call(result, exc)
        return f

    def liveStreams(self):
        return types.SimpleNamespace(list=self._rec("streams.list", {"items": [
            {"id": "studio-sid", "cdn": {"ingestionInfo": {"streamName": KEY}}}]}))

    def liveBroadcasts(self):
        return types.SimpleNamespace(
            list=self._rec("list", {"items": self.upcoming}),
            insert=self._rec("insert", {"id": "new-bid"}),
            bind=self._rec("bind", exc=RuntimeError("bind refused") if self.bind_fails else None),
            delete=self._rec("delete", {}),
        )

    def names(self):
        return [n for n, _ in self.calls]


def _b(bid, title, when):
    return {"id": bid, "snippet": {"title": title, "scheduledStartTime": when}}


@pytest.fixture
def cfg(monkeypatch):
    c = load_config("config/channels/deep-space-ambient.yaml")
    c.live.enabled = True
    monkeypatch.setattr(schedule, "tonight_scene", lambda cfg, night, work: OBSERVATORY)
    return c


OBSERVATORY = (sc.Scene("halloween-observatory", "x.mp4", "Halloween Observatory", ["halloween"]),
               cal.Theme("halloween", "Halloween", cal.HOLIDAY))
TONIGHT = "Cozy Halloween Observatory on an October Night 🎃 10 Hours of Calm Space Music for Sleep · Live"


NOON = datetime(2026, 10, 13, 12, 0, tzinfo=CT)


def test_creates_tonights_broadcast_on_studios_stream(cfg):
    yt = FakeYT()
    out = schedule.run(cfg, yt, key=KEY, now=NOON)
    assert yt.names() == ["streams.list", "list", "insert", "bind"]
    body = yt.calls[2][1]["body"]
    assert body["snippet"]["title"] == TONIGHT
    assert body["snippet"]["scheduledStartTime"] == "2026-10-14T02:00:00Z"        # 21:00 CDT
    assert body["contentDetails"]["enableAutoStart"] and body["contentDetails"]["enableAutoStop"]
    assert body["status"]["privacyStatus"] == "public"
    assert "AI-animated scene" in body["snippet"]["description"]
    assert yt.calls[3][1]["streamId"] == "studio-sid" and "created" in out and KEY not in out


def test_does_nothing_once_tonights_exists_and_clears_stale_ones(cfg):
    yt = FakeYT(upcoming=[
        _b("tonight", TONIGHT, "2026-10-14T02:00:00Z"),
        _b("stale", "Space Screens Live · 10 Hours of Real Telescope Views & Calm Music for Sleep",
           "2026-10-12T02:00:00Z"),
        _b("someone-elses", "Q&A with the channel", "2026-10-12T02:00:00Z"),
    ])
    out = schedule.run(cfg, yt, key=KEY, now=NOON)
    assert "already exists" in out and "insert" not in yt.names()
    assert [kw["id"] for n, kw in yt.calls if n == "delete"] == ["stale"]       # never anyone else's


def test_a_regular_night_gets_the_space_title(cfg, monkeypatch):
    monkeypatch.setattr(schedule, "tonight_scene", lambda cfg, night, work: None)
    yt = FakeYT()
    schedule.run(cfg, yt, key=KEY, now=NOON)
    assert yt.calls[2][1]["body"]["snippet"]["title"].startswith("Space Screens Live · 10 Hours")


def test_a_refused_bind_leaves_no_orphan(cfg):
    yt = FakeYT(bind_fails=True)
    with pytest.raises(RuntimeError):
        schedule.run(cfg, yt, key=KEY, now=NOON)
    assert yt.names()[-1] == "delete" and yt.calls[-1][1] == {"id": "new-bid"}


def test_dry_run_and_switched_off_change_nothing(cfg):
    yt = FakeYT()
    assert "would create" in schedule.run(cfg, yt, key=KEY, now=NOON, dry_run=True)
    assert "insert" not in yt.names()
    cfg.live.enabled = False
    yt = FakeYT()
    assert "live.enabled is false" in schedule.run(cfg, yt, key=KEY, now=NOON) and yt.names() == []


def test_titles_are_recognised_as_ours():
    assert schedule.ours("Cozy Space Night · 10 Hours of Relaxing Space Music for Sleep · Live")
    assert schedule.ours("Space Screens Live · 10 Hours of Real Telescope Views & Calm Music for Sleep")
    assert not schedule.ours("Cozy Halloween Cabin · 3 Hours Relaxing Space Music for Sleep · Halloween Ambience")


def test_a_long_scene_name_still_fits_and_is_recognised(cfg, monkeypatch):
    long = sc.Scene("x", "x.mp4", "Martian Moons eXploration Launch Night Observation Deck Cabin", ["launch"])
    monkeypatch.setattr(schedule, "tonight_scene", lambda cfg, night, work: (long, cal.Theme("launch", "Launch", "event")))
    yt = FakeYT()
    schedule.run(cfg, yt, key=KEY, now=NOON)
    title = next(kw for n, kw in yt.calls if n == "insert")["body"]["snippet"]["title"]
    assert len(title) <= 100 and title.endswith("· Live") and schedule.ours(title)
    assert title.startswith("Cozy Martian") and "Cabin on an October Night 🚀" in title
    # A title YouTube already cut is still ours, by the description line.
    cut = "Cozy Martian Moons eXploration (MMX) Launch Night · 10 Hours of Relaxing Space Music for Sleep · Liv"
    assert schedule.ours(cut, f"...\n{schedule.MARK}\n") and not schedule.ours(cut)


def test_duplicates_for_tonight_are_removed_keeping_the_newest(cfg):
    cut = "Cozy Martian Moons eXploration (MMX) Launch Night · 10 Hours of Relaxing Space Music for Sleep · Liv"
    def b(bid, title, published, when="2026-10-14T02:00:00Z"):
        return {"id": bid, "snippet": {"title": title, "description": schedule.MARK, "scheduledStartTime": when,
                                       "publishedAt": published}}
    yt = FakeYT(upcoming=[
        b("dup1", cut, "2026-10-13T14:18:00Z"),
        b("keep", "Cozy Orionid Meteor Shower Night · 10 Hours of Relaxing Space Music for Sleep · Live",
          "2026-10-13T15:38:00Z"),
        b("dup2", cut, "2026-10-13T11:20:00Z"),
        b("old", cut, "2026-10-12T14:00:00Z", when="2026-10-13T02:00:00Z"),
    ])
    out = schedule.run(cfg, yt, key=KEY, now=NOON)
    assert "already exists" in out and "keep" in out and "insert" not in yt.names()
    assert sorted(kw["id"] for n, kw in yt.calls if n == "delete") == ["dup1", "dup2", "old"]
