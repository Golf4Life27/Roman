"""The stream that never ends (romanfeed/live/always.py)."""
from __future__ import annotations

import types
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from romanfeed.config import load_config
from romanfeed.cozy import scenes as sc
from romanfeed.live import always
from romanfeed.live import broadcast as bc
from romanfeed.live import scene as scene_mod
from romanfeed.live import schedule
from romanfeed.publish import youtube as yt_mod

CT = ZoneInfo("America/Chicago")
KEY = "wxyz-2477-abcd"

LIB = sc.Library([sc.Scene(i, f"{i}.mp4", t, th) for i, t, th in [
    ("halloween-cabin", "Halloween Cabin", ["halloween"]),
    ("halloween-observatory", "Halloween Observatory", ["halloween"]),
    ("christmas-cabin", "Christmas Cabin", ["christmas"]),
    ("christmas-lighthouse", "Christmas Lighthouse", ["christmas", "winter"]),
    ("winter-stargazing-deck", "Winter Stargazing Deck", ["winter", "meteors"]),
    ("thanksgiving-library", "Harvest Library", ["thanksgiving", "autumn"]),
    ("evergreen-greenhouse", "Glass Greenhouse", ["evergreen"]),
]])


@pytest.fixture
def cfg():
    c = load_config("config/channels/deep-space-ambient.yaml")
    c.live.enabled = True
    return c


def test_the_channel_config_has_one_247_stream_with_its_own_key(cfg):
    (x,) = cfg.live.always
    assert (x.id, x.key_name, x.key_env) == ("cozy", "24/7", "YOUTUBE_STREAM_KEY_247")
    assert x.key_env not in {"YOUTUBE_STREAM_KEY"} | {lane.key_env for lane in cfg.live.lanes}


def test_a_key_shared_with_another_stream_is_refused(tmp_path):
    import yaml

    raw = yaml.safe_load(Path("config/channels/deep-space-ambient.yaml").read_text())
    raw["live"]["always"][0]["key_env"] = "YOUTUBE_STREAM_KEY_2"
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True))
    with pytest.raises(ValueError, match="own stream key"):
        load_config(p)


def test_the_room_follows_the_season_and_stays_put_within_it():
    rooms = {d: always.scene_for(LIB, date(2026, *d))[0].id for d in [(10, 29), (10, 31), (11, 3), (11, 25), (12, 5)]}
    assert rooms[(10, 29)] == rooms[(10, 31)] == "halloween-cabin"
    assert rooms[(11, 3)] == rooms[(11, 25)] == "thanksgiving-library"
    assert rooms[(12, 5)] == "christmas-cabin"


def test_the_room_changes_late_morning():
    assert always.scene_day(datetime(2026, 11, 1, 10, 59, tzinfo=CT), CT) == date(2026, 10, 31)
    assert always.scene_day(datetime(2026, 11, 1, 11, 0, tzinfo=CT), CT) == date(2026, 11, 1)


def test_a_segment_runs_to_the_change_of_room_and_at_most_a_week():
    end = always.segment_end(LIB, datetime(2026, 10, 29, 15, 0, tzinfo=CT), CT)
    assert end == datetime(2026, 11, 1, 11, 0, tzinfo=CT)        # Halloween to Harvest Library
    end = always.segment_end(LIB, datetime(2026, 11, 1, 11, 0, tzinfo=CT), CT)
    assert end == datetime(2026, 11, 8, 11, 0, tzinfo=CT)         # same room all week: a weekly restart
    end = always.segment_end(LIB, datetime(2026, 10, 31, 10, 30, tzinfo=CT), CT)
    assert end > datetime(2026, 10, 31, 11, 30, tzinfo=CT)        # never a segment under an hour


def test_titles_name_the_uses_fit_and_are_never_taken_for_a_night_broadcast():
    for s in LIB.scenes:
        theme = always.scene_for(sc.Library([s]), date(2026, 12, 20))[1]
        t = always.title(s, theme)
        assert len(t) <= 100 and t.endswith("· 24/7 Live") and "Sleep, Study & Focus" in t
        assert not schedule.ours(t, always.DESCRIPTION)
    assert schedule.MARK not in always.DESCRIPTION


def test_the_broadcast_never_stops_on_a_gap(cfg):
    s = LIB.scenes[0]
    b = always.body(cfg, s, always.scene_for(LIB, date(2026, 10, 29))[1], datetime(2026, 10, 29, tzinfo=CT))
    assert b["contentDetails"]["enableAutoStart"] and not b["contentDetails"]["enableAutoStop"]
    assert b["status"]["privacyStatus"] == "public" and "around the clock" in b["snippet"]["description"]


# --- the GitHub job keeps one broadcast ---------------------------------------------------------------

class _Call:
    def __init__(self, result=None):
        self.result = result

    def execute(self):
        return self.result


class FakeYT:
    def __init__(self, broadcasts=(), streams=None, active=False):
        self.broadcasts, self.calls = list(broadcasts), []
        self.streams = streams if streams is not None else [
            {"id": "studio-sid", "snippet": {"title": "Default stream key"}},
            {"id": "sid-247", "snippet": {"title": "24/7"}, "status": {"streamStatus": "active" if active else "inactive"}}]

    def _rec(self, name, result=None):
        def f(**kw):
            self.calls.append((name, kw))
            return _Call(result(kw) if callable(result) else result)
        return f

    def liveStreams(self):
        return types.SimpleNamespace(list=self._rec("streams.list", {"items": self.streams}))

    def liveBroadcasts(self):
        def listing(kw):
            want = {"active": ("live", "liveStarting"), "upcoming": ("created", "ready")}[kw["broadcastStatus"]]
            return {"items": [b for b in self.broadcasts if b["status"]["lifeCycleStatus"] in want]}
        return types.SimpleNamespace(list=self._rec("list", listing), insert=self._rec("insert", {"id": "new-bid"}),
                                     bind=self._rec("bind", {}), delete=self._rec("delete", {}),
                                     transition=self._rec("transition", {}))

    def videos(self):
        return types.SimpleNamespace(update=self._rec("videos.update", {}))

    def names(self):
        return [n for n, _ in self.calls]


def _b(bid, title, state, *, stream="sid-247", published="2026-10-29T10:00:00Z"):
    return {"id": bid, "snippet": {"title": title, "publishedAt": published},
            "status": {"lifeCycleStatus": state}, "contentDetails": {"boundStreamId": stream}}


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=CT)
HALLOWEEN = always.title(LIB.scenes[0], always.scene_for(LIB, date(2026, 10, 29))[1])


@pytest.fixture
def job(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "fetch_manifest", lambda repo, tag, dest: LIB)
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"jpg")
    monkeypatch.setattr(bc, "scene_thumbnail", lambda *a, **kw: thumb)
    monkeypatch.setattr(yt_mod, "set_thumbnail", lambda yt, vid, path: yt.calls.append(("thumbnails.set", vid)))
    return cfg, cfg.live.always[0], tmp_path


def test_creates_the_broadcast_once_on_the_247_key(job):
    cfg, x, work = job
    yt = FakeYT()
    out = always.ensure_broadcast(cfg, yt, x, NOW, work)
    assert "created" in out and HALLOWEEN in out
    body = next(kw["body"] for n, kw in yt.calls if n == "insert")
    assert body["snippet"]["title"] == HALLOWEEN and not body["contentDetails"]["enableAutoStop"]
    assert next(kw for n, kw in yt.calls if n == "bind")["streamId"] == "sid-247"
    assert "transition" not in yt.names()          # no video yet: auto-start will do it


def test_video_already_arriving_moves_the_new_broadcast_to_live(job):
    cfg, x, work = job
    yt = FakeYT(active=True)
    always.ensure_broadcast(cfg, yt, x, NOW, work)
    assert next(kw for n, kw in yt.calls if n == "transition")["broadcastStatus"] == "live"


def test_a_live_broadcast_with_the_seasons_title_is_left_alone(job):
    cfg, x, work = job
    yt = FakeYT([_b("b1", HALLOWEEN, "live")])
    out = always.ensure_broadcast(cfg, yt, x, NOW, work)
    assert "live https://youtu.be/b1" in out
    assert not {"insert", "delete", "videos.update", "transition"} & set(yt.names())


def test_a_new_season_redresses_the_same_live_broadcast(job):
    cfg, x, work = job
    yt = FakeYT([_b("b1", HALLOWEEN, "live")])
    out = always.ensure_broadcast(cfg, yt, x, datetime(2026, 11, 2, 12, 0, tzinfo=CT), work)
    upd = next(kw for n, kw in yt.calls if n == "videos.update")["body"]
    assert upd["id"] == "b1" and upd["snippet"]["title"].startswith("Cozy Harvest Library")
    assert "insert" not in yt.names() and ("thumbnails.set", "b1") in yt.calls and "now 'Cozy Harvest" in out


def test_extra_waiting_broadcasts_go_and_other_streams_are_never_touched(job):
    cfg, x, work = job
    yt = FakeYT([_b("live1", HALLOWEEN, "live"), _b("spare", HALLOWEEN, "ready"),
                 _b("night", "Cozy Halloween Cabin on an October Night · Live", "ready", stream="studio-sid")])
    always.ensure_broadcast(cfg, yt, x, NOW, work)
    assert [kw["id"] for n, kw in yt.calls if n == "delete"] == ["spare"]


def test_no_247_key_in_studio_yet_is_skipped_and_names_the_keys_there(job):
    cfg, x, work = job
    yt = FakeYT(streams=[{"id": "studio-sid", "snippet": {"title": "Default stream key"}}])
    out = always.ensure_broadcast(cfg, yt, x, NOW, work)
    assert "no stream key named '24/7'" in out and "'Default stream key'" in out
    assert set(yt.names()) == {"streams.list"}


def test_the_key_name_ignores_case_and_spaces(job):
    cfg, x, work = job
    yt = FakeYT(streams=[{"id": "sid-247", "snippet": {"title": " 24 / 7 "}, "status": {}}])
    assert "created" in always.ensure_broadcast(cfg, yt, x, NOW, work)


def test_dry_run_changes_nothing(job):
    cfg, x, work = job
    yt = FakeYT()
    assert "would create" in always.ensure_broadcast(cfg, yt, x, NOW, work, dry_run=True)
    assert not {"insert", "bind", "delete", "videos.update"} & set(yt.names())


def test_the_morning_job_reports_the_247_stream(job, monkeypatch):
    cfg, x, work = job
    cfg.live.lanes = []
    monkeypatch.setattr(schedule, "tonight_scene", lambda cfg, night, work: None)
    monkeypatch.setattr(always, "ensure_broadcast", lambda cfg, yt, x, now, work, dry_run=False: "24/7 cozy: ok")
    out = schedule.run(cfg, FakeYT(), key="k", now=datetime(2026, 10, 29, 22, 0, tzinfo=CT))
    assert out.endswith("24/7 cozy: ok")


# --- the server streams it -----------------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_ffmpeg_is_restarted_without_a_limit_and_backs_off_on_quick_failures(monkeypatch):
    from romanfeed.live import run

    clock, waits, runs = _Clock(), [], []
    monkeypatch.setattr(always, "_clock", clock)
    monkeypatch.setattr(always, "_sleep", lambda s: (waits.append(s), setattr(clock, "t", clock.t + s)))

    def fake_stream(cmd, *, timeout_s, secret=None):
        runs.append(cmd[0])
        clock.t += 10 if len(runs) <= 4 else 3000      # four quick failures, then long runs
        return 1

    monkeypatch.setattr(run, "run_stream", fake_stream)
    always.stream_forever(lambda s: [s], 4 * 3600)
    assert waits[:4] == [10, 20, 40, 80] and waits[4] == always.RETRY_FIRST_S
    assert len(runs) > 8                                # more than the night's reconnect limit
    assert runs[0] == 4 * 3600 and all(r <= 4 * 3600 for r in runs)


def test_the_watchdog_still_ends_a_stuck_ffmpeg(monkeypatch):
    from romanfeed.live import run

    monkeypatch.setattr(run, "run_stream", lambda cmd, *, timeout_s, secret=None: -9)
    assert always.stream_forever(lambda s: [s], 3600) == -9


def test_the_stream_command_takes_a_longer_cap_only_when_asked(tmp_path):
    night = scene_mod.SceneNight(date(2026, 10, 29), LIB.scenes[0], None, tmp_path / "l.mp4", tmp_path / "m.m4a")
    week = 7 * 24 * 3600
    with pytest.raises(ValueError):
        scene_mod.stream_command(night, week, "rtmp://x/k")
    cmd = scene_mod.stream_command(night, week, "rtmp://x/k", cap=week)
    assert cmd[cmd.index("-t") + 1] == str(week)


def test_start_refuses_without_its_key_or_when_switched_off(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("YOUTUBE_STREAM_KEY_247", raising=False)
    assert always.cmd_always(cfg, tmp_path, "cozy") == 2
    assert "YOUTUBE_STREAM_KEY_247" in capsys.readouterr().out
    cfg.live.enabled = False
    monkeypatch.setenv("YOUTUBE_STREAM_KEY_247", KEY)
    assert always.cmd_always(cfg, tmp_path, "cozy") == 2


def test_dry_run_prints_the_room_and_masks_the_key(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sc, "fetch_manifest", lambda repo, tag, dest: LIB)
    monkeypatch.setenv("YOUTUBE_STREAM_KEY_247", KEY)
    assert always.cmd_always(cfg, tmp_path, "cozy", dry_run=True, now=NOW) == 0
    out = capsys.readouterr().out
    assert "Halloween Cabin" in out and "24/7 Live" in out and KEY not in out


def test_a_segment_streams_the_room_and_exits_for_systemd(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "fetch_manifest", lambda repo, tag, dest: LIB)
    monkeypatch.setenv("YOUTUBE_STREAM_KEY_247", KEY)
    loop, music = tmp_path / "loop.mp4", tmp_path / "music.m4a"
    monkeypatch.setattr(always, "prepare", lambda cfg, lib, x, scene: (loop, music))
    seen = {}

    def fake_forever(make_cmd, seconds, *, secret=None, label=""):
        seen.update(cmd=make_cmd(seconds), seconds=seconds, secret=secret)
        return 0

    monkeypatch.setattr(always, "stream_forever", fake_forever)
    monkeypatch.setattr("signal.signal", lambda *a: None)
    assert always.cmd_always(cfg, tmp_path, "cozy", now=NOW) == 0
    assert seen["seconds"] == int((datetime(2026, 11, 1, 11, 0, tzinfo=CT) - NOW).total_seconds())
    assert str(loop) in seen["cmd"] and str(music) in seen["cmd"] and seen["secret"] == KEY
    assert seen["cmd"][-1].endswith("/" + KEY)


def test_music_for_other_rooms_is_pruned(cfg, tmp_path):
    x = cfg.live.always[0]
    folder = tmp_path / "scenes" / "music"
    folder.mkdir(parents=True)
    keep = always.music_path(tmp_path, x, LIB.scenes[0])
    old = always.music_path(tmp_path, x, LIB.scenes[2])
    night = folder / "music-night-2026-10-29.m4a"
    for f in (keep, old, night):
        f.write_bytes(b"x")
    always.prune_music(tmp_path, x, {keep})
    assert keep.exists() and night.exists() and not old.exists()


def test_the_server_unit_restarts_for_good_and_has_no_time_cap():
    unit = Path("deploy/live/romanfeed-always@.service").read_text()
    assert "Restart=always" in unit and "RuntimeMaxSec" not in unit.replace("No RuntimeMaxSec", "")
    assert "StartLimitIntervalSec=0" in unit and "RestartPreventExitStatus=2" in unit
    assert "live always %i" in unit
    assert "[Install]" in unit and "WantedBy=multi-user.target" in unit     # comes back after a reboot
    setup = Path("deploy/live/setup.sh").read_text()
    assert "always) ALWAYS=1" in setup and "romanfeed-always@cozy.service" in setup
