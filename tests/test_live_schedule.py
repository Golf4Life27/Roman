"""The morning job that creates tonight's broadcast (romanfeed/live/schedule.py)."""
from __future__ import annotations

import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from romanfeed.config import load_config
from romanfeed.cozy import calendar as cal
from romanfeed.cozy import scenes as sc
from romanfeed.live import broadcast as bc
from romanfeed.live import schedule
from romanfeed.publish import youtube as yt_mod

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
    def __init__(self, upcoming=(), bind_fails=False, update_fails=False, tags=None):
        self.upcoming, self.bind_fails, self.calls = list(upcoming), bind_fails, []
        self.update_fails, self.tags = update_fails, tags

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

    def videos(self):
        snippet = {"title": TONIGHT, "description": "old"} | ({"tags": self.tags} if self.tags else {})
        return types.SimpleNamespace(
            list=self._rec("videos.list", {"items": [{"id": "tonight", "snippet": snippet}]}),
            update=self._rec("videos.update", {}, exc=RuntimeError("quota") if self.update_fails else None))

    def names(self):
        return [n for n, _ in self.calls]


def _b(bid, title, when):
    return {"id": bid, "snippet": {"title": title, "scheduledStartTime": when}}


THUMBS: list = []


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    c = load_config("config/channels/deep-space-ambient.yaml")
    c.live.enabled = True
    monkeypatch.setattr(schedule, "tonight_scene", lambda cfg, night, work: OBSERVATORY)
    thumb = tmp_path / "live.jpg"
    thumb.write_bytes(b"jpg")
    monkeypatch.setattr(bc, "scene_thumbnail", lambda cfg, scene, **kw: (THUMBS.append((scene.id, kw)), thumb)[1])
    monkeypatch.setattr(yt_mod, "set_thumbnail", lambda yt, vid, path: yt.calls.append(("thumbnails.set", {
        "videoId": vid, "path": path})))
    THUMBS.clear()
    return c


OBSERVATORY = (sc.Scene("halloween-observatory", "x.mp4", "Halloween Observatory", ["halloween"]),
               cal.Theme("halloween", "Halloween", cal.HOLIDAY))
TONIGHT = "Cozy Halloween Observatory on an October Night 🎃 10 Hours of Calm Space Music for Sleep · Live"


NOON = datetime(2026, 10, 13, 12, 0, tzinfo=CT)


def test_creates_tonights_broadcast_on_studios_stream(cfg):
    yt = FakeYT()
    out = schedule.run(cfg, yt, key=KEY, now=NOON)
    assert yt.names() == ["streams.list", "list", "insert", "bind", "videos.update", "thumbnails.set"]
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


def test_a_new_broadcast_gets_tags_music_category_and_a_scene_thumbnail(cfg):
    yt = FakeYT()
    out = schedule.run(cfg, yt, key=KEY, now=NOON)
    upd = dict(yt.calls)["videos.update"]
    snip = upd["body"]["snippet"]
    assert upd["part"] == "snippet" and upd["body"]["id"] == "new-bid"
    assert snip["categoryId"] == "10" and snip["title"] == TONIGHT
    assert snip["description"] == yt.calls[2][1]["body"]["snippet"]["description"]   # the insert's, unchanged
    tags = snip["tags"]
    assert tags[:2] == ["halloween observatory", "halloween"] and "live sleep music" in tags
    assert sum(len(t) + (2 if " " in t else 0) for t in tags) + len(tags) - 1 <= 500
    assert len(tags) == len({t.lower() for t in tags}) >= 20
    assert THUMBS == [("halloween-observatory", {"hours_label": "10", "subject": "Live · Cozy Halloween",
                                                 "work": THUMBS[0][1]["work"]})]
    assert dict(yt.calls)["thumbnails.set"]["videoId"] == "new-bid"
    assert "custom thumbnail" in out


def test_the_description_lists_uses_and_hashtags_and_keeps_the_mark(cfg):
    yt = FakeYT()
    schedule.run(cfg, yt, key=KEY, now=NOON)
    desc = yt.calls[2][1]["body"]["snippet"]["description"]
    assert bc.USES in desc and desc.rstrip().endswith(bc.LIVE_HASHTAGS) and schedule.MARK in desc


def test_a_failed_tag_update_never_stops_the_night(cfg):
    yt = FakeYT(update_fails=True)
    out = schedule.run(cfg, yt, key=KEY, now=NOON)
    assert "created" in out and "tags not set" in out and "delete" not in yt.names()


def test_a_regular_night_gets_tags_but_no_scene_thumbnail(cfg, monkeypatch):
    monkeypatch.setattr(schedule, "tonight_scene", lambda cfg, night, work: None)
    yt = FakeYT()
    schedule.run(cfg, yt, key=KEY, now=NOON)
    assert "thumbnails.set" not in yt.names() and THUMBS == []
    assert "space music for sleep" in dict(yt.calls)["videos.update"]["body"]["snippet"]["tags"]


def test_dry_run_dresses_nothing(cfg):
    yt = FakeYT()
    schedule.run(cfg, yt, key=KEY, now=NOON, dry_run=True)
    assert "videos.update" not in yt.names() and THUMBS == []


def test_fit_tags_drops_repeats_and_stays_under_the_limit():
    assert bc.fit_tags(["a b", "A B", "c"], ["c", "d"]) == ["a b", "c", "d"]
    long = [f"tag number {i}" for i in range(100)]
    tags = bc.fit_tags(long)
    assert sum(len(t) + 2 for t in tags) + len(tags) - 1 <= bc.TAG_BUDGET < 500


def test_an_existing_broadcast_without_tags_is_dressed_once(cfg):
    yt = FakeYT(upcoming=[_b("tonight", TONIGHT, "2026-10-14T02:00:00Z")])
    out = schedule.run(cfg, yt, key=KEY, now=NOON)
    assert "insert" not in yt.names() and "dressed it now" in out
    upd = dict(yt.calls)["videos.update"]["body"]
    assert upd["id"] == "tonight" and upd["snippet"]["title"] == TONIGHT and upd["snippet"]["tags"]
    assert bc.USES in upd["snippet"]["description"] and dict(yt.calls)["thumbnails.set"]["videoId"] == "tonight"

    yt = FakeYT(upcoming=[_b("tonight", TONIGHT, "2026-10-14T02:00:00Z")], tags=["space"])
    out = schedule.run(cfg, yt, key=KEY, now=NOON)
    assert "videos.update" not in yt.names() and "thumbnails.set" not in yt.names() and "dressed" not in out


def test_dry_run_never_dresses_an_existing_broadcast(cfg):
    yt = FakeYT(upcoming=[_b("tonight", TONIGHT, "2026-10-14T02:00:00Z")])
    schedule.run(cfg, yt, key=KEY, now=NOON, dry_run=True)
    assert "videos.list" not in yt.names() and "videos.update" not in yt.names()
