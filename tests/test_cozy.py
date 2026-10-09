"""Cozy scenes: the calendar, scene picking, scene nights, the generator's
budget, the Runway client and the weekly video's metadata."""
from __future__ import annotations

import io
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from romanfeed.config import load_config
from romanfeed.cozy import calendar as cal
from romanfeed.cozy import generate as gen
from romanfeed.cozy import runway
from romanfeed.cozy import scenes as sc
from romanfeed.cozy import video as cv
from romanfeed.live import scene as live_scene
from romanfeed.publish.youtube import request_body

CFG = "config/channels/deep-space-ambient.yaml"


def keys(day, **kw):
    return [t.key for t in cal.themes_for(day, **kw)]


def test_movable_holidays():
    assert [cal.easter(y) for y in (2025, 2026, 2027)] == [date(2025, 4, 20), date(2026, 4, 5), date(2027, 3, 28)]
    assert [cal.thanksgiving(y) for y in (2026, 2027)] == [date(2026, 11, 26), date(2027, 11, 25)]


def test_theme_order_peak_first_then_events_holidays_season():
    assert keys(date(2026, 10, 8)) == ["halloween", "autumn", "evergreen"]
    assert keys(date(2026, 10, 21))[:2] == ["meteors", "halloween"]           # Orionid peak
    assert keys(date(2026, 10, 31))[0] == "halloween" and cal.themes_for(date(2026, 10, 31))[0].peak
    assert keys(date(2026, 12, 14))[:2] == ["meteors", "christmas"]           # Geminid peak
    assert keys(date(2026, 12, 25))[:2] == ["christmas", "winter"]
    assert keys(date(2027, 1, 1))[0] == "new_year"
    assert keys(date(2026, 8, 12))[0] in {"meteors", "eclipse"}                # Perseids + eclipse
    assert keys(date(2026, 7, 15)) == ["summer", "evergreen"]


def test_only_notable_firm_launches_make_a_theme():
    t = datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc)
    launches = [cal.Launch("Falcon 9 Block 5 | Starlink Group 15-25", t, "Go"),
                cal.Launch("SLS Block 1 | Artemis III", t, "TBD"),
                cal.Launch("Falcon 9 Block 5 | Crew-13", t, "Go")]
    ts = cal.themes_for(date(2026, 11, 10), launches=launches)
    assert ts[0].key == "launch" and ts[0].peak and ts[0].label == "Crew-13 Launch"
    assert keys(date(2026, 11, 9), launches=launches)[0] == "launch"           # the night before
    assert "launch" not in keys(date(2026, 11, 11), launches=launches)
    assert cal.parse_launches({"results": [{"name": "X | Y", "net": "2026-10-19T19:41:03Z",
                                            "status": {"abbrev": "Go"}}, {"name": "bad"}]})[0].mission == "Y"


def _lib(*items):
    return sc.Library([sc.Scene(i, f"{i}.mp4", i.title(), list(t)) for i, t in items])


def test_pick_rotates_falls_back_and_avoids_last_night():
    lib = _lib(("obs", ["halloween"]), ("cabin", ["halloween"]), ("deck", ["winter", "meteors"]))
    a = sc.pick(lib, date(2026, 10, 8))
    b = sc.pick(lib, date(2026, 10, 9))
    assert {a[0].id, b[0].id} == {"obs", "cabin"} and a[1].key == "halloween"
    assert sc.pick(lib, date(2026, 10, 8), avoid=a[0].id)[0].id != a[0].id
    assert sc.pick(lib, date(2026, 10, 21))[0].id == "deck"                    # meteor peak beats Halloween
    assert sc.pick(lib, date(2026, 7, 15)) is None                              # nothing for summer yet
    assert sc.Library.from_json(lib.to_json()).scenes == lib.scenes


def test_scene_night_switch():
    plain = cal.Theme("autumn", "Autumn", cal.SEASON)
    peak = cal.Theme("halloween", "Halloween", cal.HOLIDAY, peak=True)
    even, odd = date(2026, 10, 8), date(2026, 10, 9)
    on = [d for d in (even, odd) if live_scene.wants_scene("alternate", d, plain)]
    assert len(on) == 1
    assert all(live_scene.wants_scene("alternate", d, peak) for d in (even, odd))
    assert not live_scene.wants_scene("off", even, peak) and live_scene.wants_scene("always", odd, plain)


def test_loop_trim_and_stream_command(tmp_path):
    assert live_scene.loop_frames(10.04) == 240     # 241 frames, the duplicate closing one dropped
    night = live_scene.SceneNight(date(2026, 10, 8), sc.Scene("a", "a.mp4", "A", []),
                                  cal.Theme("a", "A", cal.SEASON), tmp_path / "loop.mp4", tmp_path / "m.m4a")
    cmd = live_scene.stream_command(night, 36000, "rtmp://x/live2/KEY")
    assert cmd.count("-stream_loop") == 2 and cmd[cmd.index("-t") + 1] == "36000" and "copy" in cmd
    with pytest.raises(ValueError):
        live_scene.stream_command(night, 12 * 3600, "rtmp://x")


def test_generator_wants_specific_themes_first_and_never_repeats_a_room(monkeypatch):
    lib = _lib(("halloween-observatory", ["halloween"]))
    want = gen.wanted(lib, date(2026, 11, 1), days=45, per_theme=2)
    assert want[:2] == ["thanksgiving", "thanksgiving"] and want[-1] == "evergreen"
    assert "halloween" not in want
    p1 = gen.next_prompt(lib, "halloween")
    assert p1.id != "halloween-observatory" and p1.id.startswith("halloween-")
    assert "no people" in p1.image and p1.motion.startswith("Locked-off static camera")


class FakeRelease:
    def __init__(self):
        self.files = {}

    def upload(self, path, name=None, **kw):
        self.files[name or path.name] = Path(path).read_bytes()
        return f"https://example/{name}"


class FakeClient:
    def __init__(self, balance=10_000):
        self.calls, self._balance = [], balance

    def balance(self):
        return self._balance

    def image(self, prompt, model):
        self.calls.append(("image", model))
        return "https://img"

    def loop(self, uri, prompt, model):
        assert uri.startswith("data:image/jpeg;base64,")
        self.calls.append(("loop", model))
        return "https://vid"


def test_generator_stops_at_the_monthly_cap_and_records_spend(monkeypatch, tmp_path):
    cfg = load_config(CFG)
    cfg.cozy.monthly_credits = 900          # two scenes at ~420
    lib = _lib(("halloween-observatory", ["halloween"]))
    monkeypatch.setattr(gen.sc, "fetch_manifest", lambda *a, **k: lib)
    monkeypatch.setattr(gen, "fetch_launches", lambda **k: [])
    png = io.BytesIO()
    Image.new("RGB", (64, 36), (30, 20, 60)).save(png, "PNG")

    def fake_download(url, dest):
        dest.write_bytes(png.getvalue() if url == "https://img" else b"mp4")
        return dest

    monkeypatch.setattr(gen, "_download", fake_download)
    monkeypatch.setattr(gen, "_check_loop", lambda p: None)
    rel, client = FakeRelease(), FakeClient()
    made = gen.generate(cfg, today=date(2026, 11, 1), dry_run=False, client=client, release=rel)
    assert len(made) == 2 and len({s.id for s in made}) == 2
    assert client.calls == [("image", "gemini_image3_pro"), ("loop", "seedance2")] * 2
    saved = sc.Library.from_json(__import__("json").loads(rel.files["scenes.json"]))
    assert saved.spend == {"2026-11": 840} and len(saved.scenes) == 3
    # a low API balance stops it before spending
    made = gen.generate(cfg, today=date(2026, 12, 1), dry_run=False, client=FakeClient(balance=100), release=rel)
    assert made == []


def test_runway_client_polls_and_reports_failure_codes():
    class Resp:
        def __init__(self, data, code=200):
            self.data, self.status_code, self.ok, self.text = data, code, code < 400, str(data)

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self, polls):
            self.headers, self.polls, self.posted = {}, list(polls), []

        def post(self, url, json, timeout):
            self.posted.append((url, json))
            return Resp({"id": "t1"})

        def get(self, url, timeout):
            return Resp(self.polls.pop(0))

    s = Session([{"status": "RUNNING"}, {"status": "SUCCEEDED", "output": ["https://out.mp4"]}])
    c = runway.Client("k", session=s, poll_s=0)
    assert c.loop("data:x", "calm") == "https://out.mp4"
    url, body = s.posted[0]
    assert url.endswith("/v1/image_to_video") and body["audio"] is False and body["ratio"] == "1920:1080"
    assert [p["position"] for p in body["promptImage"]] == ["first", "last"]
    assert s.headers["X-Runway-Version"] == "2024-11-06"
    s2 = Session([{"status": "FAILED", "failure": "no", "failureCode": "SAFETY.INPUT.TEXT"}])
    with pytest.raises(runway.RunwayError) as e:
        runway.Client("k", session=s2, poll_s=0).image("x")
    assert e.value.code == "SAFETY.INPUT.TEXT" and not e.value.retryable
    assert runway.scene_cost("gemini_image3_pro", "seedance2") == 420
    big = io.BytesIO()
    Image.effect_noise((2752, 1536), 90).convert("RGB").save(big, "PNG")
    assert len(runway.data_uri(big.getvalue())) < 4_100_000


def test_weekly_video_metadata_and_pick():
    cfg = load_config(CFG)
    scene = sc.Scene("halloween-observatory", "x.mp4", "Halloween Observatory", ["halloween"])
    md = cv.metadata(cfg, scene, cal.Theme("halloween", "Halloween", cal.HOLIDAY), 3)
    assert md.title == "Cozy Halloween Observatory · 3 Hours Relaxing Space Music for Sleep · Halloween Ambience"
    assert len(md.title) <= 100 and md.synthetic and "AI-generated" in md.description
    assert request_body(md)["status"]["containsSyntheticMedia"] is True
    lib = _lib(("obs", ["halloween"]), ("cabin", ["halloween"]))
    day = date(2026, 10, 10)
    first = cv.choose(lib, day, {})[0].id
    other = cv.choose(lib, day, {first: date(2026, 10, 3)})[0].id
    assert other != first                                    # shown last week -> the other one
    assert cv.choose(lib, day, {"obs": date(2026, 10, 3), "cabin": date(2026, 10, 3)}) is not None


def test_config_rejects_a_bad_scene_mode(tmp_path):
    text = Path(CFG).read_text().replace("scenes: alternate", "scenes: sometimes")
    bad = tmp_path / "c.yaml"
    bad.write_text(text)
    with pytest.raises(ValueError, match="live.scenes"):
        load_config(bad)


def test_manifest_download_creates_its_folder(monkeypatch, tmp_path):
    import json as _json

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = _json.dumps({"scenes": [{"id": "a", "file": "a.mp4", "title": "A", "themes": ["autumn"]}]}).encode()
    monkeypatch.setattr(sc.urllib.request, "urlopen", lambda req, timeout: R(body))
    lib = sc.fetch_manifest("o/r", "t", tmp_path / "fresh" / "scenes" / sc.MANIFEST)
    assert [s.id for s in lib.scenes] == ["a"]


def test_cozy_clip_caption_and_posting(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    from romanfeed import crosspost as cp
    from romanfeed.cozy import clip

    cfg = load_config(CFG)
    scene = sc.Scene("halloween-cabin", "halloween-cabin.mp4", "Halloween Cabin", ["halloween"])
    text = clip.caption(cfg, scene, cal.Theme("halloween", "Halloween", cal.HOLIDAY))
    assert text.startswith("Cozy Halloween Cabin 🎃")
    assert "Cozy nights live on YouTube: @SpaceScreens" in text and "AI-animated scene" in text
    assert "#halloween" in text.split("\n\n")[-1]

    lib = sc.Library([scene])
    monkeypatch.setattr(clip.sc, "fetch_manifest", lambda *a, **k: lib)
    monkeypatch.setattr(clip.sc, "fetch_scene", lambda *a, **k: tmp_path / "loop.mp4")
    monkeypatch.setattr(clip, "fetch_launches", lambda **k: [])
    monkeypatch.setattr(clip, "render_clip", lambda loop, out, **kw: out.write_bytes(b"mp4") or out)
    sent = []
    monkeypatch.setattr(cp, "send", lambda relay, body: sent.append(body) or cp.Posted("p9", "scheduled", "", ["tiktok"]))

    class Rel:
        def upload(self, path, name=None, **kw):
            return f"https://example/{name}"

        def prune(self, days):
            return []

    now = datetime(2026, 10, 11, 0, 20, tzinfo=timezone.utc)   # Saturday 19:20 CDT
    out, posted = clip.run(cfg, today=date(2026, 10, 10), output_dir=tmp_path, now=now, release=Rel(), relay="r")
    assert posted is None and sent == []                       # crosspost.mode is off
    cfg.crosspost.mode = "on"
    out, posted = clip.run(cfg, today=date(2026, 10, 10), output_dir=tmp_path, now=now, release=Rel(), relay="r")
    assert posted.post_id == "p9" and out.name.startswith("cozy-clip-2026-10-10-halloween-cabin")
    assert sent[0]["scheduledFor"] == "2026-10-11T01:15:00Z"   # 20:15 CDT, its own slot
    assert sent[0]["content"] == text
