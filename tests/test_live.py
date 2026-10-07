"""Nightly live stream: the caps, the switch, the playlist, the API calls.

Nothing here talks to YouTube or GitHub, or runs a real stream. The YouTube
client is a fake patched over youtube.client (the sandbox cannot import
googleapiclient's crypto), the same pattern as test_thumbnail.py.
"""
from __future__ import annotations

import io
import json
import stat
import sys
import types
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from romanfeed import cli
from romanfeed.config import LIVE_MAX_HOURS, load_config
from romanfeed.live import broadcast as bc
from romanfeed.live import library, playlist, run
from romanfeed.live.library import ReadyFile
from romanfeed.publish import youtube

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/channels/deep-space-ambient.yaml"
KEY = "abcd-efgh-ijkl-mnop-qrst"


def _files(tmp_path: Path, n: int, minutes: float = 60.0) -> list[ReadyFile]:
    return [ReadyFile(tmp_path / f"deep-space-ambient-2026-09-{i + 1:02d}.mp4", minutes * 60, f"Lead {i}")
            for i in range(n)]


def _library(tmp_path: Path, n: int, minutes: float = 60.0) -> Path:
    """A library dir with n ready files (empty mp4s + real sidecars)."""
    lib = tmp_path / "lib"
    ready = lib / "ready"
    ready.mkdir(parents=True)
    for f in _files(ready, n, minutes):
        f.path.write_bytes(b"")
        f.path.with_suffix(".json").write_text(json.dumps({"duration_s": f.duration_s, "lead": f.lead}))
    return lib


def _cfg(enabled: bool = False, hours: float = 10.0):
    cfg = load_config(CONFIG)
    cfg.live.enabled = enabled
    cfg.live.hours = hours
    return cfg


# --- config -----------------------------------------------------------------

def test_shipped_live_block_is_capped():
    # The owner switched the stream on 2026-10-05; whatever the switch, the
    # shipped block must stay a ~10 h night that ends well before 12 h.
    cfg = load_config(CONFIG)
    assert cfg.live.hours == 10.0
    from romanfeed.config import LiveSettings
    assert LiveSettings().enabled is False  # the code default stays off
    assert cfg.live.timezone == "America/Chicago"


@pytest.mark.parametrize("block, match", [
    ("{hours: 24}", "live.hours"),
    ("{hours: 12}", "live.hours"),
    ("{start_local: '9pm'}", "start_local"),
    ("{timezone: Mars/Olympus}", "timezone"),
    ("{forever: true}", "forever"),
])
def test_bad_live_block_rejected(tmp_path, block, match):
    p = tmp_path / "c.yaml"
    p.write_text(f"channel: {{slug: x, name: X}}\nlive: {block}\n")
    with pytest.raises(ValueError, match=match):
        load_config(p)


# --- playlist ---------------------------------------------------------------

@pytest.mark.parametrize("n, minutes", [(12, 60), (3, 60), (1, 60), (20, 37), (5, 140)])
def test_playlist_hits_target_within_one_file(tmp_path, n, minutes):
    files = _files(tmp_path, n, minutes)
    pl = playlist.build_playlist(files, 10.0, seed="2026-10-01")
    total = sum(f.duration_s for f in pl)
    target = 10 * 3600
    assert total >= target
    assert total - pl[-1].duration_s < target  # the last file started before the target
    assert playlist.stream_seconds(pl, 10.0) == target


@pytest.mark.parametrize("hours", [10.0, 11.5, 12.0, 24.0, 1000.0])
def test_stream_never_exceeds_the_cap(tmp_path, hours):
    pl = playlist.build_playlist(_files(tmp_path, 30), hours, seed="x")
    assert playlist.stream_seconds(pl, hours) <= LIVE_MAX_HOURS * 3600 == 41400
    assert sum(f.duration_s for f in pl) - pl[-1].duration_s < 41400


def test_no_repeats_when_the_library_covers_the_night(tmp_path):
    pl = playlist.build_playlist(_files(tmp_path, 12), 10.0, seed="2026-10-01")
    names = [f.name for f in pl]
    assert len(names) == len(set(names)) == 10


def test_short_library_repeats_but_never_back_to_back(tmp_path):
    pl = playlist.build_playlist(_files(tmp_path, 3), 10.0, seed="2026-10-01")
    names = [f.name for f in pl]
    assert len(names) == 10
    assert all(a != b for a, b in zip(names, names[1:]))


def test_avoids_last_nights_opener(tmp_path):
    files = _files(tmp_path, 6)
    for day in range(1, 31):
        seed = f"2026-10-{day:02d}"
        natural = playlist.build_playlist(files, 10.0, seed=seed)[0].name
        again = playlist.build_playlist(files, 10.0, seed=seed, avoid_first=natural)
        assert again[0].name != natural


def test_same_night_same_order(tmp_path):
    files = _files(tmp_path, 12)
    a = playlist.build_playlist(files, 10.0, seed="2026-10-01")
    b = playlist.build_playlist(files, 10.0, seed="2026-10-01")
    c = playlist.build_playlist(files, 10.0, seed="2026-10-02")
    assert [f.name for f in a] == [f.name for f in b] != [f.name for f in c]


def test_empty_library_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        playlist.build_playlist([], 10.0, seed="x")


def test_concat_list_escapes_quotes(tmp_path):
    f = ReadyFile(tmp_path / "it's.mp4", 10.0)
    text = playlist.write_concat([f], tmp_path / "l.ffconcat").read_text()
    assert text.startswith("ffconcat version 1.0\n")
    assert "it'\\''s.mp4'" in text


# --- artifacts --------------------------------------------------------------

def test_artifact_filter_keeps_the_1h_and_skips_long_cuts():
    names = [
        "deep-space-ambient/deep-space-ambient-2026-09-30-8h.mp4",
        "deep-space-ambient/deep-space-ambient-2026-09-30.mp4",
        "deep-space-ambient/deep-space-ambient-2026-09-30.thumb.jpg",
        "deep-space-ambient/deep-space-ambient-2026-09-30.upload.json",
        "deep-space-ambient-2026-09-30-3h.mp4",
    ]
    assert library.pick_video(names) == "deep-space-ambient/deep-space-ambient-2026-09-30.mp4"
    assert library.pick_video(["deep-space-ambient-2026-09-30-8h.mp4", "x.mp4"]) is None
    assert library.pick_video(["deep-space-ambient-2026-09-30-10.5h.mp4"]) is None


def _zip(tmp_path: Path, title: str, privacy: str = "public") -> Path:
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("deep-space-ambient/deep-space-ambient-2026-09-30.mp4", b"raw")
        zf.writestr("deep-space-ambient/deep-space-ambient-2026-09-30-8h.mp4", b"long")
        zf.writestr("deep-space-ambient/deep-space-ambient-2026-09-30.upload.json",
                    json.dumps({"snippet": {"title": title}, "status": {"privacyStatus": privacy}}))
    return z


def test_ingest_extracts_the_1h_transcodes_and_drops_the_raw(tmp_path, monkeypatch):
    calls = []

    def fake_transcode(src, dst):
        calls.append(src.read_bytes())
        dst.write_bytes(b"ready")

    monkeypatch.setattr(library, "transcode", fake_transcode)
    monkeypatch.setattr(library, "probe_duration_fast", lambda p: 3600.0)
    work = tmp_path / "w"
    work.mkdir()
    got = library.ingest_zip(_zip(tmp_path, "Carina Nebula | Galaxies | 1 Hour"), tmp_path / "lib", artifact_id=7, work=work)
    assert calls == [b"raw"]  # the 1h member, not the 8h one
    assert got.name == "deep-space-ambient-2026-09-30.mp4" and got.lead == "Carina Nebula"
    assert not list(work.iterdir())
    assert [f.name for f in library.load_ready(tmp_path / "lib")] == [got.name]


def test_ingest_skips_private_test_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "transcode", lambda s, d: pytest.fail("transcoded a test render"))
    work = tmp_path / "w"
    work.mkdir()
    z = _zip(tmp_path, "[TEST] Carina | 1 Hour", privacy="private")
    assert library.ingest_zip(z, tmp_path / "lib", artifact_id=1, work=work) is None


def test_transcode_command_is_stream_ready():
    cmd = " ".join(library.transcode_command(Path("in.mp4"), Path("out.mp4")))
    for part in ["-b:v 4500k", "-maxrate 4500k", "-bufsize 9000k", "-g 48", "-keyint_min 48",
                 "-sc_threshold 0", "-pix_fmt yuv420p", "-b:a 160k", "-ar 44100"]:
        assert part in cmd


def test_prune_keeps_the_newest(tmp_path):
    lib = _library(tmp_path, 5)
    gone = library.prune(lib, 3)
    assert gone == ["deep-space-ambient-2026-09-01.mp4", "deep-space-ambient-2026-09-02.mp4"]
    assert len(library.load_ready(lib)) == 3


class _Resp:
    def __init__(self, data=None, body=b""):
        self.data, self.body = data, body

    def raise_for_status(self):
        pass

    def json(self):
        return self.data

    def iter_content(self, chunk_size):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Session:
    def __init__(self, artifacts, zips):
        self.artifacts, self.zips, self.downloads = artifacts, zips, []

    def get(self, url, **kw):
        if url.endswith("/actions/artifacts"):
            return _Resp({"artifacts": self.artifacts})
        self.downloads.append(url)
        return _Resp(body=self.zips[url])


def test_sync_downloads_each_render_artifact_once(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "transcode", lambda s, d: d.write_bytes(b"ready"))
    monkeypatch.setattr(library, "probe_duration_fast", lambda p: 3600.0)
    zbytes = _zip(tmp_path, "Orion Nebula | x").read_bytes()
    arts = [
        {"id": 1, "name": "render-111", "archive_download_url": "https://dl/1", "created_at": "2026-09-30", "size_in_bytes": 10},
        {"id": 2, "name": "something-else", "archive_download_url": "https://dl/2", "created_at": "2026-09-30"},
        {"id": 3, "name": "render-222", "expired": True, "archive_download_url": "https://dl/3", "created_at": "2026-09-29"},
    ]
    s = _Session(arts, {"https://dl/1": zbytes})
    lib = tmp_path / "lib"
    added = library.sync(lib, repo="o/r", session=s, min_free_gb=0)
    assert [f.lead for f in added] == ["Orion Nebula"]
    assert s.downloads == ["https://dl/1"]
    assert library.sync(lib, repo="o/r", session=s, min_free_gb=0) == []
    assert s.downloads == ["https://dl/1"]


def test_sync_stops_at_the_disk_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "free_bytes", lambda p: 1024**3)
    arts = [{"id": 1, "name": "render-1", "archive_download_url": "https://dl/1", "created_at": "x", "size_in_bytes": 10}]
    s = _Session(arts, {})
    assert library.sync(tmp_path / "lib", repo="o/r", session=s, min_free_gb=5) == []
    assert s.downloads == []


# --- broadcast --------------------------------------------------------------

def test_broadcast_body_fields():
    body = bc.broadcast_body(title="T", description="D", start=datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc))
    assert body["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    cd = body["contentDetails"]
    assert cd["enableAutoStart"] is True and cd["enableAutoStop"] is True
    assert cd["enableDvr"] is True and cd["latencyPreference"] == "normal"
    assert body["snippet"]["scheduledStartTime"] == "2026-10-02T02:00:00Z"


def test_title_and_description():
    title = bc.build_title(date(2026, 10, 1), "The Very Long Name Of A Spectacular Interacting Galaxy Pair In Virgo", 10.0)
    assert len(title) <= 100 and title.endswith("| Oct 1") and "10 Hours" in title
    assert bc.build_title(date(2026, 10, 1), "", 10.0).startswith("10 Hours")
    desc = bc.build_description(tagline="Tag", hours=10, leads=["A", "B", "A"], tags=["space video for sleep"])
    assert "?sub_confirmation=1" in desc and "space video for sleep" in desc
    assert desc.count("- A") == 1


def test_stream_state_is_private_and_repr_hides_the_key(tmp_path):
    p = tmp_path / "stream.json"
    info = bc.StreamInfo("sid", "rtmp://a.rtmp.youtube.com/live2", KEY)
    bc.save_stream(p, info)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert bc.load_stream(p).rtmp_url == f"rtmp://a.rtmp.youtube.com/live2/{KEY}"
    assert KEY not in repr(info)


class _Call:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.result


class _FakeYouTube:
    def __init__(self, lifecycle="live"):
        self.calls: list[tuple[str, dict]] = []
        self.lifecycle = lifecycle

    def _rec(self, name, result):
        def f(**kw):
            self.calls.append((name, kw))
            return _Call(result)
        return f

    def liveStreams(self):
        return types.SimpleNamespace(
            list=self._rec("streams.list", {"items": []}),
            insert=self._rec("streams.insert", {"id": "sid", "cdn": {"ingestionInfo": {
                "ingestionAddress": "rtmp://a.rtmp.youtube.com/live2", "streamName": KEY}}}),
        )

    def liveBroadcasts(self):
        return types.SimpleNamespace(
            insert=self._rec("insert", {"id": "bid"}),
            bind=self._rec("bind", {}),
            list=self._rec("list", {"items": [{"status": {"lifeCycleStatus": self.lifecycle}}]}),
            transition=self._rec("transition", {}),
            delete=self._rec("delete", {}),
        )

    def names(self):
        return [n for n, _ in self.calls]


@pytest.fixture
def fake_api(monkeypatch):
    def install(yt):
        monkeypatch.setattr(youtube, "client", lambda: yt)
        return yt
    return install


def test_complete_transitions_a_live_broadcast():
    yt = _FakeYouTube("live")
    assert bc.complete(yt, "bid") == "completed"
    assert ("transition", {"broadcastStatus": "complete", "id": "bid", "part": "status"}) in yt.calls


def test_complete_is_a_noop_after_auto_stop():
    yt = _FakeYouTube("complete")
    assert bc.complete(yt, "bid") == "complete"
    assert "transition" not in yt.names()


def test_ensure_stream_creates_once_and_caches(tmp_path):
    yt = _FakeYouTube()
    info = bc.ensure_stream(yt, tmp_path / "stream.json")
    assert info.stream_name == KEY
    ins = [kw for n, kw in yt.calls if n == "streams.insert"][0]
    assert ins["body"]["cdn"] == {"ingestionType": "rtmp", "resolution": "1080p", "frameRate": "variable"}


# --- start ------------------------------------------------------------------

def test_start_refuses_when_disabled(tmp_path, fake_api, monkeypatch, capsys):
    fake_api(types.SimpleNamespace())  # any attribute access would fail
    monkeypatch.setattr(run, "run_stream", lambda *a, **k: pytest.fail("streamed while disabled"))
    lib = _library(tmp_path, 12)
    assert run.cmd_start(_cfg(enabled=False), lib) == 2
    assert "live.enabled is false" in capsys.readouterr().out


def test_cli_start_refuses_when_the_config_says_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(youtube, "client", lambda: pytest.fail("built a client"))
    off = tmp_path / "off.yaml"
    off.write_text(Path(CONFIG).read_text().replace("live:\n  enabled: true", "live:\n  enabled: false"))
    lib = _library(tmp_path, 12)
    rc = cli.main(["live", "start", "--config", str(off), "--library-dir", str(lib)])
    assert rc == 2


def test_ffmpeg_command_caps_and_copies():
    cmd = run.ffmpeg_command(Path("/l.ffconcat"), 36000, "rtmp://x/live2/k")
    t = int(cmd[cmd.index("-t") + 1])
    assert 0 < t <= 41400
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert "-re" in cmd and cmd[-1] == "rtmp://x/live2/k"
    with pytest.raises(ValueError):
        run.ffmpeg_command(Path("/l"), 41401, "rtmp://x")


def test_dry_run_masks_the_key_and_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(youtube, "client", lambda: pytest.fail("dry run built a client"))
    monkeypatch.setattr(run, "run_stream", lambda *a, **k: pytest.fail("dry run streamed"))
    lib = _library(tmp_path, 12)
    bc.save_stream(lib / "stream.json", bc.StreamInfo("sid", "rtmp://a.rtmp.youtube.com/live2", KEY))
    assert run.cmd_start(_cfg(enabled=True), lib, dry_run=True) == 0
    out = capsys.readouterr().out
    assert KEY not in out
    assert "rtmp://a.rtmp.youtube.com/live2/****" in out
    assert "-t 36000" in out and "-c copy" in out
    assert '"privacyStatus": "public"' in out
    assert not (lib / "playlist-state.json").exists()


def test_start_runs_one_capped_night(tmp_path, fake_api, monkeypatch):
    yt = fake_api(_FakeYouTube("live"))
    seen = {}

    def fake_stream(cmd, *, timeout_s, secret=None):
        seen.update(cmd=cmd, timeout=timeout_s, secret=secret)
        return 0

    monkeypatch.setattr(run, "run_stream", fake_stream)
    lib = _library(tmp_path, 12)
    assert run.cmd_start(_cfg(enabled=True), lib) == 0
    # No cached stream yet: create it, then broadcast, bind, and always end.
    assert yt.names() == ["streams.insert", "insert", "bind", "list", "transition"]
    assert seen["timeout"] == 36000 + 15 * 60
    assert seen["secret"] == KEY and seen["cmd"][-1].endswith(KEY)
    assert stat.S_IMODE((lib / "stream.json").stat().st_mode) == 0o600


def test_complete_is_called_even_when_ffmpeg_raises(tmp_path, fake_api, monkeypatch):
    yt = fake_api(_FakeYouTube("live"))

    def boom(*a, **k):
        raise OSError("ffmpeg exploded")

    monkeypatch.setattr(run, "run_stream", boom)
    with pytest.raises(OSError):
        run.cmd_start(_cfg(enabled=True), _library(tmp_path, 12))
    assert "transition" in yt.names()


def test_broadcast_that_never_went_live_is_deleted(tmp_path, fake_api, monkeypatch):
    yt = fake_api(_FakeYouTube("ready"))
    monkeypatch.setattr(run, "run_stream", lambda *a, **k: 1)
    assert run.cmd_start(_cfg(enabled=True), _library(tmp_path, 12)) == 1
    assert "delete" in yt.names() and "transition" not in yt.names()


def test_watchdog_kills_an_overrunning_ffmpeg():
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    t0 = datetime.now()
    assert run.run_stream(cmd, timeout_s=1) == -9
    assert (datetime.now() - t0).total_seconds() < 10


def test_ffmpeg_stderr_is_masked(caplog):
    run._pump(io.StringIO(f"rtmp://a.rtmp.youtube.com/live2/{KEY}: Broken pipe\n"), KEY)
    assert caplog.records and KEY not in caplog.text and "****" in caplog.text


def test_studio_key_mode_streams_with_no_api_and_the_same_caps(tmp_path, fake_api, monkeypatch):
    yt = fake_api(_FakeYouTube("live"))
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "abcd-1234-efgh")
    seen = {}

    def fake_stream(cmd, *, timeout_s, secret=None):
        seen.update(cmd=cmd, timeout=timeout_s, secret=secret)
        return 0

    monkeypatch.setattr(run, "run_stream", fake_stream)
    lib = _library(tmp_path, 12)
    assert run.cmd_start(_cfg(enabled=True), lib) == 0
    assert yt.names() == []                                   # not one API call
    assert seen["cmd"][-1] == "rtmp://a.rtmp.youtube.com/live2/abcd-1234-efgh"
    assert seen["secret"] == "abcd-1234-efgh"                 # masked in logs
    assert "-t" in seen["cmd"] and int(seen["cmd"][seen["cmd"].index("-t") + 1]) <= 41400
    assert seen["timeout"] == 36000 + 15 * 60


def test_studio_key_mode_still_refuses_when_disabled(tmp_path, fake_api, monkeypatch):
    fake_api(_FakeYouTube("live"))
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "abcd-1234-efgh")
    assert run.cmd_start(_cfg(enabled=False), _library(tmp_path, 12)) == 2
