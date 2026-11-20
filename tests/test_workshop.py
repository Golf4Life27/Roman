"""Santa's Workshop stream (romanfeed/live/workshop.py)."""
from __future__ import annotations

import json
import types
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from romanfeed.config import WorkshopSettings, load_config
from romanfeed.live import broadcast as bc
from romanfeed.live import workshop as ws

CT = ZoneInfo("America/Chicago")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    c = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    c.live.workshop = WorkshopSettings(enabled=True, first_day="2026-11-21", last_day="2026-12-26")
    return c


def test_the_channel_config_turns_it_on_and_pauses_the_night_through_it():
    c = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    w = c.live.workshop
    assert w.enabled and w.made_for_kids and w.hours < 12 and w.key_env == "YOUTUBE_STREAM_KEY"
    last = date.fromisoformat(w.last_day)
    assert c.live.night_paused(last) and c.live.night_paused(last + timedelta(days=1))


def test_sessions_stay_under_12_hours_and_leave_a_gap_for_auto_stop(cfg):
    ss = ws.sessions(cfg, datetime(2026, 11, 21, tzinfo=CT), datetime(2026, 11, 23, tzinfo=CT))
    assert [s.start.hour for s in ss] == [6, 18, 6, 18]
    for a, b in zip(ss, ss[1:]):
        assert a.seconds < 12 * 3600 and (b.start - a.end) >= timedelta(minutes=10)


def test_no_sessions_outside_the_season(cfg):
    assert ws.sessions(cfg, datetime(2026, 11, 19, tzinfo=CT), datetime(2026, 11, 21, tzinfo=CT)) == []
    late = ws.sessions(cfg, datetime(2026, 12, 26, 12, tzinfo=CT), datetime(2027, 1, 2, tzinfo=CT))
    assert [s.start for s in late] == [datetime(2026, 12, 26, 6, tzinfo=CT), datetime(2026, 12, 26, 18, tzinfo=CT)]
    cfg.live.workshop = replace(cfg.live.workshop, enabled=False)
    assert ws.sessions(cfg, datetime(2026, 11, 22, tzinfo=CT), datetime(2026, 11, 23, tzinfo=CT)) == []


def test_current_and_upcoming(cfg):
    now = datetime(2026, 11, 21, 17, 55, tzinfo=CT)        # in the gap
    assert ws.current(cfg, now) is None
    assert ws.upcoming(cfg, now).start == datetime(2026, 11, 21, 18, tzinfo=CT)
    assert ws.current(cfg, now + timedelta(minutes=10)).start.hour == 18
    # the night session runs past midnight
    assert ws.current(cfg, datetime(2026, 11, 22, 2, tzinfo=CT)).start == datetime(2026, 11, 21, 18, tzinfo=CT)


def test_sleeps_until_christmas():
    assert ws.sleeps(date(2026, 11, 21)) == 34
    assert ws.sleeps(date(2026, 12, 24)) == 1
    assert ws.sleeps(date(2026, 12, 25)) == 0 and ws.sleeps(date(2026, 12, 26)) == 0


MANIFEST = {
    "scenes": [{"id": f"s{i}", "title": f"S{i}", "file": f"scene-s{i}.mp4"} for i in range(8)],
    "cards": {str(n): f"countdown-{n:02d}.mp4" for n in range(36)},
    "music": "workshop-music.m4a",
    "thumbnail": "thumbnail.jpg",
}


def test_playlist_has_the_days_card_twice_and_every_room(cfg):
    pl = ws.playlist(MANIFEST, date(2026, 11, 21), 10)
    assert pl.count("countdown-34.mp4") == 2 and len(pl) == 8 * 10 + 2
    assert {n for n in pl if n.startswith("scene-")} == {s["file"] for s in MANIFEST["scenes"]}
    assert pl[0] == "countdown-34.mp4"
    other = ws.playlist(MANIFEST, date(2026, 11, 22), 10)
    assert other[1] != pl[1]                                 # each day opens on another room


def test_stream_command_copies_and_refuses_12_hours(tmp_path):
    cmd = ws.stream_command(tmp_path / "reel.mp4", tmp_path / "m.m4a", 3600, "rtmp://x/key")
    assert cmd[cmd.index("-t") + 1] == "3600" and "copy" in cmd and cmd[-1] == "rtmp://x/key"
    with pytest.raises(ValueError):
        ws.stream_command(tmp_path / "reel.mp4", tmp_path / "m.m4a", 12 * 3600, "rtmp://x/key")


def test_nudge_leaves_a_fresh_heartbeat_alone(cfg, tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(ws, "_always_pids", lambda: [4242])
    monkeypatch.setattr(ws.os, "kill", lambda pid, sig: killed.append(pid))
    now = datetime(2026, 11, 21, 15, tzinfo=CT)
    (tmp_path / "workshop").mkdir()
    (tmp_path / "workshop" / ws.HEARTBEAT).write_text(json.dumps({"at": ws.time.time(), "state": "x"}))
    assert "running" in ws.nudge(cfg, tmp_path, now) and killed == []
    (tmp_path / "workshop" / ws.HEARTBEAT).write_text(json.dumps({"at": ws.time.time() - 3600}))
    assert "restarted" in ws.nudge(cfg, tmp_path, now) and killed == [4242]


def test_nudge_does_nothing_out_of_season(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "_always_pids", lambda: pytest.fail("looked for the process"))
    assert "not in season" in ws.nudge(cfg, tmp_path, datetime(2027, 1, 5, tzinfo=CT))


# --- broadcasts -----------------------------------------------------------------------------------------

class _Call:
    def __init__(self, result=None):
        self.result = result

    def execute(self):
        return self.result


class FakeYT:
    def __init__(self, broadcasts=(), active=False):
        self.broadcasts, self.calls = list(broadcasts), []
        self.active = active

    def _rec(self, name, result=None):
        def f(**kw):
            self.calls.append((name, kw))
            return _Call(result(kw) if callable(result) else result)
        return f

    def liveStreams(self):
        st = {"id": "night-sid", "status": {"streamStatus": "active" if self.active else "inactive"},
              "cdn": {"ingestionInfo": {"streamName": "night-key"}}}
        return types.SimpleNamespace(list=self._rec("streams.list", {"items": [st]}))

    def liveBroadcasts(self):
        def listing(kw):
            want = {"active": ("live", "liveStarting"), "upcoming": ("created", "ready")}[kw["broadcastStatus"]]
            return {"items": [b for b in self.broadcasts if b["status"]["lifeCycleStatus"] in want]}
        return types.SimpleNamespace(list=self._rec("list", listing), insert=self._rec("insert", {"id": "new"}),
                                     bind=self._rec("bind", {}), delete=self._rec("delete", {}),
                                     transition=self._rec("transition", {}))

    def videos(self):
        return types.SimpleNamespace(update=self._rec("videos.update", {}))

    def names(self):
        return [n for n, _ in self.calls]


def _b(bid, when, state, *, mark=True, stream="night-sid"):
    return {"id": bid, "snippet": {"title": ws.WORK_TITLE, "scheduledStartTime": when,
                                   "description": ws.MARK if mark else "other"},
            "status": {"lifeCycleStatus": state}, "contentDetails": {"boundStreamId": stream}}


@pytest.fixture
def job(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(ws, "extras", lambda cfg, work: bc.Extras(["t"], category_id=ws.CATEGORY))
    return cfg, tmp_path


def test_creates_the_current_and_next_session_made_for_kids(job):
    cfg, work = job
    yt = FakeYT()
    out = ws.ensure_sessions(cfg, yt, "night-key", datetime(2026, 11, 21, 9, tzinfo=CT), work)
    inserts = [kw["body"] for n, kw in yt.calls if n == "insert"]
    assert len(inserts) == 2 and all(b["status"]["selfDeclaredMadeForKids"] for b in inserts)
    assert all(b["contentDetails"]["enableAutoStop"] for b in inserts)
    assert inserts[1]["snippet"]["scheduledStartTime"] == "2026-11-22T00:00:00Z"     # 18:00 CT
    assert {kw["streamId"] for n, kw in yt.calls if n == "bind"} == {"night-sid"}
    assert "transition" not in yt.names() and len(out) == 2


def test_late_job_moves_the_current_session_to_live_when_video_arrives(job):
    cfg, work = job
    yt = FakeYT(active=True)
    ws.ensure_sessions(cfg, yt, "night-key", datetime(2026, 11, 21, 9, tzinfo=CT), work)
    assert [kw["broadcastStatus"] for n, kw in yt.calls if n == "transition"] == ["live"]


def test_existing_sessions_are_left_alone(job):
    cfg, work = job
    yt = FakeYT([_b("cur", "2026-11-21T12:00:00Z", "live"), _b("nxt", "2026-11-22T00:00:00Z", "ready")])
    out = ws.ensure_sessions(cfg, yt, "night-key", datetime(2026, 11, 21, 9, tzinfo=CT), work)
    assert not {"insert", "delete", "transition"} & set(yt.names())
    assert "live https://youtu.be/cur" in out[0] and "ready https://youtu.be/nxt" in out[1]


def test_a_session_nearly_over_gets_no_new_broadcast(job):
    cfg, work = job
    yt = FakeYT()
    ws.ensure_sessions(cfg, yt, "night-key", datetime(2026, 11, 21, 17, 30, tzinfo=CT), work)
    assert [kw["body"]["snippet"]["scheduledStartTime"] for n, kw in yt.calls if n == "insert"] == \
        ["2026-11-22T00:00:00Z"]


def test_a_stale_waiting_broadcast_of_ours_goes_and_others_stay(job):
    cfg, work = job
    yt = FakeYT([_b("old", "2026-11-20T12:00:00Z", "ready"),
                 _b("theirs", "2026-11-20T12:00:00Z", "ready", mark=False)])
    ws.ensure_sessions(cfg, yt, "night-key", datetime(2026, 11, 21, 9, tzinfo=CT), work)
    assert [kw["id"] for n, kw in yt.calls if n == "delete"] == ["old"]
