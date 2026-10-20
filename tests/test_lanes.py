"""The live network: extra cozy streams beside the nightly one (romanfeed/live/lanes.py)."""
from __future__ import annotations

import types
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from romanfeed.config import load_config
from romanfeed.cozy import calendar as cal
from romanfeed.cozy import scenes as sc
from romanfeed.live import lanes, schedule
from romanfeed.live import run as live_run

CFG = "config/channels/deep-space-ambient.yaml"
CT = ZoneInfo("America/Chicago")
KEY, KEY2 = "abcd-1234-efgh", "lane-2222-key"
# The scene library as it is today (cozy-scenes release).
TAGS = {"halloween-cabin": ["halloween"], "halloween-observatory": ["halloween"], "christmas-cabin": ["christmas"],
        "christmas-starship-lounge": ["christmas", "new_year"], "winter-stargazing-deck": ["winter", "meteors"],
        "christmas-lighthouse": ["christmas", "winter"], "thanksgiving-library": ["thanksgiving", "autumn"],
        "evergreen-greenhouse": ["evergreen"], "launch-cabin": ["launch"]}
LIB = sc.Library([sc.Scene(i, f"{i}.mp4", i.replace("-", " ").title(), t) for i, t in TAGS.items()])


def no_launches(d):
    return cal.themes_for(d, launches=[])


@pytest.fixture
def cfg():
    c = load_config(CFG)
    c.live.enabled = True
    return c


def test_config_has_two_lanes_and_rejects_bad_ones(cfg, tmp_path):
    assert [(x.id, x.key_env, x.starts) for x in cfg.live.lanes] == [
        ("two", "YOUTUBE_STREAM_KEY_2", ["07:30", "19:30"]), ("three", "YOUTUBE_STREAM_KEY_3", ["01:30", "13:30"])]
    bad = tmp_path / "c.yaml"
    bad.write_text(Path(CFG).read_text().replace("key_env: YOUTUBE_STREAM_KEY_2", "key_env: YOUTUBE_STREAM_KEY"))
    with pytest.raises(ValueError, match="own stream key"):
        load_config(bad)
    bad.write_text(Path(CFG).read_text().replace('starts: ["07:30", "19:30"]\n      hours: 11.5',
                                                 'starts: ["07:30", "19:30"]\n      hours: 12'))
    with pytest.raises(ValueError, match="hours"):
        load_config(bad)


def test_sessions_and_which_one_a_timer_start_belongs_to(cfg):
    day = datetime(2026, 10, 21, 0, 0, tzinfo=CT)
    got = [(s.lane, f"{s.start:%H:%M}") for s in lanes.sessions(cfg, day, day + timedelta(days=1))]
    assert got == [("three", "01:30"), ("two", "07:30"), ("three", "13:30"), ("two", "19:30")]
    s = lanes.current(cfg, "two", datetime(2026, 10, 21, 7, 20, tzinfo=CT))      # the timer, 10 min early
    assert s and f"{s.start:%H:%M}" == "07:30" and s.end - s.start == timedelta(hours=11.5)
    assert lanes.current(cfg, "two", datetime(2026, 10, 21, 12, 0, tzinfo=CT)) is None


def test_streams_on_at_the_same_time_show_different_scenes(cfg):
    t, end = datetime(2026, 10, 21, tzinfo=CT), datetime(2026, 11, 20, tzinfo=CT)
    while t < end:
        on = []
        for back in (0, 1):
            n = lanes.night_session(cfg, (t - timedelta(days=back)).date())
            if n.start <= t < n.end:
                on.append(lanes.night_pick(LIB, n.start.date(), no_launches(n.start.date())).id)
        for s in lanes.sessions(cfg, t - timedelta(hours=12), t + timedelta(minutes=1)):
            if s.start <= t < s.end:
                on.append(lanes.pick(cfg, LIB, s, themes_fn=no_launches)[0].id)
        assert len(on) == len(set(on)) >= 1, (t, on)
        t += timedelta(hours=1)


def test_titles_are_seasonal_honest_and_fit(cfg):
    day = datetime(2026, 10, 21, tzinfo=CT)
    for s in lanes.sessions(cfg, day, day + timedelta(days=1)):
        scene, theme = lanes.pick(cfg, LIB, s, themes_fn=no_launches)
        t = lanes.title(scene, theme, s)
        assert len(t) <= 100 and t.startswith("Cozy ") and t.endswith("· Live") and schedule.ours(t)
        assert "11 Hours" in t and "12 Hours" not in t and "October" in t
        assert ("Work & Study" in t) == (5 <= s.start.hour < 17)
        body = lanes.body(cfg, scene, theme, s)
        assert schedule.MARK in body["snippet"]["description"]
        assert body["contentDetails"]["enableAutoStart"] and body["contentDetails"]["enableAutoStop"]


class _Call:
    def __init__(self, result=None):
        self.result = result

    def execute(self):
        return self.result


class FakeYT:
    def __init__(self, upcoming=()):
        self.upcoming, self.calls, self.n = list(upcoming), [], 0

    def _rec(self, name, result=None):
        def f(**kw):
            self.calls.append((name, kw))
            if name == "insert":
                self.n += 1
                return _Call({"id": f"new-{self.n}"})
            return _Call(result)
        return f

    def liveStreams(self):
        return types.SimpleNamespace(list=self._rec("streams.list", {"items": [
            {"id": "studio-sid", "cdn": {"ingestionInfo": {"streamName": KEY}}},
            {"id": "lane2-sid", "cdn": {"ingestionInfo": {"streamName": KEY2}}}]}))

    def liveBroadcasts(self):
        return types.SimpleNamespace(list=self._rec("list", {"items": self.upcoming}), insert=self._rec("insert"),
                                     bind=self._rec("bind"), delete=self._rec("delete"))


def test_the_broadcast_job_makes_each_lane_session_once(cfg, monkeypatch):
    monkeypatch.setattr(lanes, "library_for", lambda cfg, work: LIB)
    monkeypatch.setattr(cal, "fetch_launches", lambda **kw: [])
    monkeypatch.setattr(schedule, "tonight_scene", lambda cfg, night, work: lanes.sc.pick(LIB, night, no_launches(night)))
    noon = datetime(2026, 10, 13, 12, 0, tzinfo=CT)
    yt = FakeYT()
    out = schedule.run(cfg, yt, key=KEY, now=noon, lane_keys={"two": KEY2, "three": ""})
    inserts = [kw["body"] for n, kw in yt.calls if n == "insert"]
    binds = [kw["streamId"] for n, kw in yt.calls if n == "bind"]
    starts = [b["snippet"]["scheduledStartTime"] for b in inserts]
    assert starts == ["2026-10-14T02:00:00Z", "2026-10-14T00:30:00Z", "2026-10-14T12:30:00Z"]  # 21:00, 19:30, 07:30 CDT
    assert binds == ["studio-sid", "lane2-sid", "lane2-sid"]
    assert "lane three: no YOUTUBE_STREAM_KEY_3 secret yet" in out and KEY2 not in out

    # Next run: all three exist (bound to their streams), nothing is made or deleted.
    made = [{"id": f"b{i}", "snippet": {**b["snippet"], "publishedAt": "2026-10-13T17:00:00Z"},
             "contentDetails": {"boundStreamId": sid}} for i, (b, sid) in enumerate(zip(inserts, binds))]
    yt = FakeYT(upcoming=made)
    out = schedule.run(cfg, yt, key=KEY, now=noon + timedelta(hours=3), lane_keys={"two": KEY2})
    assert [n for n, _ in yt.calls if n in ("insert", "delete")] == []
    assert "already exists" in out and out.count("exists https://youtu.be/") == 2


def test_the_server_runs_a_lane_session(cfg, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(lanes, "library_for", lambda cfg, work: LIB)
    monkeypatch.setattr(cal, "fetch_launches", lambda **kw: [])
    monkeypatch.setattr(sc, "fetch_scene", lambda repo, tag, scene, folder: tmp_path / f"{scene.id}.mp4")
    monkeypatch.setattr(live_run.scene_mod, "prepare_loop", lambda src, out, channel_line: tmp_path / "loop.mp4")
    monkeypatch.setattr(live_run.scene_mod, "prepare_music", lambda *a, **k: tmp_path / "music.m4a")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY_2", KEY2)
    at = datetime(2026, 10, 21, 7, 20, tzinfo=CT)
    assert live_run.cmd_lane(cfg, tmp_path, "two", dry_run=True, now=at) == 0
    out = capsys.readouterr().out
    assert "2026-10-21 07:30 session, 11.50 h" in out          # never more than 11.5 h, however early
    assert "Work & Study · Live" in out and KEY2 not in out and "-t 41400" in out
    monkeypatch.delenv("YOUTUBE_STREAM_KEY_2")
    assert live_run.cmd_lane(cfg, tmp_path, "two", now=at) == 2          # no key: refuses, sends nothing
    assert live_run.cmd_lane(cfg, tmp_path, "two", dry_run=True, now=at.replace(hour=12)) == 2
