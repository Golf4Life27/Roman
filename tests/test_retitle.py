"""Back-catalogue retitle: sleep-first titles, kept credits, idempotent."""
from __future__ import annotations

from pathlib import Path

from romanfeed.config import load_config
from romanfeed.publish import retitle as rt

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT / "config/channels/deep-space-ambient.yaml")

OLD_DESC = """Real space telescope imagery, slowly.

8 Hours of slow, drifting views across 80 space telescope images with calm ambient music. Put it on, dim the lights, and let it run.

IN THIS VIDEO
0:00 b'Carina Nebula'
0:45 Ring Nebula

IMAGE CREDITS
- NASA, ESA, CSA, STScI

MUSIC
- Drift — Space Screens

New space video every day. Subscribe to keep the sky on.
"""


def _video(vid, title, duration, desc=OLD_DESC, tags=None):
    return {"id": vid, "snippet": {"title": title, "description": desc, "categoryId": "28", "tags": tags or ["space"]},
            "contentDetails": {"duration": duration}, "status": {"privacyStatus": "public"}}


def test_iso_duration():
    assert rt.iso_seconds("PT8H0M3S") == 28803 and rt.iso_seconds("PT59M58S") == 3598 and rt.iso_seconds("P0D") == 0


def test_long_video_gets_the_sleep_title_without_a_false_dark_screen_claim():
    body = rt.update_body(CFG, _video("A", "Carina Nebula (NIRCam Image) | Nebulae | 8 Hours Relaxing Space Video for Sleep | Telescope Screensaver", "PT8H0M1S"))
    s = body["snippet"]
    assert s["title"] == "8 Hours Deep Sleep Music · Fall Asleep in Space · Carina Nebula"
    assert "Dark Screen" not in s["title"]
    assert s["description"].startswith("8 Hours of deep sleep music")
    assert "0:00 Carina Nebula" in s["description"]           # bytes repr repaired
    assert "- NASA, ESA, CSA, STScI" in s["description"]      # credits kept
    assert "every day" not in s["description"] and "#sleepmusic" in s["description"]
    assert s["categoryId"] == "28" and "deep sleep music" in s["tags"] and "space" in s["tags"]


def test_hour_video_names_the_telescope_and_rerun_is_a_no_op():
    v = _video("B", "Ring Nebula (NIRCam Image) | Nebulae | 1 Hour Relaxing Space Video for Sleep | Telescope Screensaver", "PT1H0M2S")
    first = rt.update_body(CFG, v)["snippet"]
    assert first["title"] == "1 Hour Relaxing Space Music for Sleep · Ring Nebula by Webb · Telescope Screensaver"
    again = rt.update_body(CFG, _video("B", first["title"], "PT1H0M2S", desc=first["description"], tags=first["tags"]))["snippet"]
    assert again == first


def test_shorts_are_left_alone():
    assert rt.update_body(CFG, _video("C", "Pillars by Webb | Calm Space for Sleep", "PT35S")) is None


class _Req:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeYT:
    def __init__(self, videos):
        self._videos, self.updates = videos, []

    def channels(self):
        return self

    def playlistItems(self):
        return self

    def videos(self):
        return self

    def list(self, part, **kw):
        if "mine" in kw:
            return _Req({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        if "playlistId" in kw:
            return _Req({"items": [{"contentDetails": {"videoId": v["id"]}} for v in self._videos]})
        ids = kw["id"].split(",")
        return _Req({"items": [v for v in self._videos if v["id"] in ids]})

    def update(self, part, body):
        self.updates.append(body)
        return _Req({})


def test_dry_run_writes_nothing_and_real_run_writes_changed_only(capsys):
    vids = [_video("A", "Carina Nebula | Nebulae | 8 Hours Relaxing Space Video for Sleep | Telescope Screensaver", "PT8H"),
            _video("S", "Short", "PT30S")]
    yt = FakeYT(vids)
    assert rt.retitle(CFG, None, dry_run=True, yt=yt) == 0 and yt.updates == []
    assert rt.retitle(CFG, None, dry_run=False, yt=yt) == 0
    assert [b["id"] for b in yt.updates] == ["A"]


def test_missing_scope_exits_2(capsys):
    class Denied(Exception):
        resp = type("R", (), {"status": 403})()

    class YT(FakeYT):
        def list(self, part, **kw):
            raise Denied("insufficientPermissions: Request had insufficient authentication scopes")

    assert rt.retitle(CFG, ["A"], yt=YT([])) == 2
    assert "manage" in capsys.readouterr().out
