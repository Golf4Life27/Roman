"""Cross-posting the social cut: slots, caption, Zernio body, the switch."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from romanfeed import crosspost as cp
from romanfeed.config import load_config
from romanfeed.sources.base import ImageAsset

CFG = "config/channels/deep-space-ambient.yaml"
SLOTS = ["12:00", "19:00", "21:30", "23:30"]


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_each_shorts_run_lands_in_the_next_slot():
    # the four Shorts runs (shorts.yml crons, + ~5 min to render) each get their own slot
    for runs in ([utc(2026, 10, 9, 16, 15), utc(2026, 10, 9, 23, 15), utc(2026, 10, 10, 1, 45), utc(2026, 10, 10, 3, 45)],
                 [utc(2026, 12, 9, 16, 15), utc(2026, 12, 9, 23, 15), utc(2026, 12, 10, 1, 45), utc(2026, 12, 10, 3, 45)]):
        local = [cp.next_slot(t, SLOTS, "America/Chicago").astimezone(cp.ZoneInfo("America/Chicago")) for t in runs]
        assert [x.strftime("%H:%M") for x in local] == SLOTS          # CDT and CST alike
    # a run 40 minutes late still makes its own slot
    late = cp.next_slot(utc(2026, 10, 9, 16, 55), SLOTS, "America/Chicago")
    assert late.astimezone(cp.ZoneInfo("America/Chicago")).strftime("%H:%M") == "12:00"
    # after the last slot: tomorrow noon
    assert cp.next_slot(utc(2026, 12, 10, 6, 0), SLOTS, "America/Chicago") == utc(2026, 12, 10, 18, 0)


def _webb(**kw):
    base = dict(asset_id="esa:w1", title="Cosmic Cliffs (NIRCam Image)", url="u", source="ESA/Webb",
                credit="NASA, ESA, CSA, STScI", description="d")
    base.update(kw)
    return ImageAsset(**base)


def test_social_script_keeps_the_facts_and_points_at_youtube():
    yt = ("This is a star nursery. It spans light years. This image comes from Webb. "
          "The full 8-hour version is on the channel. Subscribe to travel through time.")
    s = cp.social_script(yt, full_label="8-hour")
    assert s.startswith("This is a star nursery. It spans light years. This image comes from Webb.")
    assert "on YouTube, at Space Screens" in s and "Subscribe" not in s and "on the channel" not in s
    assert cp.end_card("8-hour", "@SpaceScreens") == ("Full 8-hour version on YouTube", "@SpaceScreens")


def test_caption_names_image_telescope_pointer_credit_and_tags():
    text = cp.caption(_webb(), script="Stars are born in these cliffs. More text. The full 8-hour version is on YouTube.",
                      full_label="8-hour", handle="@SpaceScreens", hashtags=["#space", "#sleepmusic", "#ambient", "#relaxing"])
    parts = text.split("\n\n")
    assert parts[0] == "Cosmic Cliffs · Imaged by the James Webb Space Telescope"
    assert parts[1] == "Stars are born in these cliffs."
    assert parts[2] == "Full 8-hour sleep video on YouTube: @SpaceScreens"
    assert parts[3] == "Image: NASA, ESA, CSA, STScI"
    tags = parts[4].split()
    assert "#jwst" in tags and len(tags) <= 5


def test_body_test_mode_is_private_tiktok_only(monkeypatch):
    cfg = load_config(CFG)
    when = utc(2026, 10, 9, 17, 0)
    cfg.crosspost.mode = "on"
    on = cp.post_body(cfg, video_url="https://x/clip.mp4", text="hi", when=when)
    assert [p["platform"] for p in on["platforms"]] == ["tiktok", "instagram"]
    assert on["tiktokSettings"]["privacyLevel"] == "PUBLIC_TO_EVERYONE" and on["tiktokSettings"]["videoMadeWithAi"]
    assert on["scheduledFor"] == "2026-10-09T17:00:00Z" and on["mediaItems"][0]["type"] == "video"
    cfg.crosspost.mode = "test"
    t = cp.post_body(cfg, video_url="https://x/clip.mp4", text="hi", when=when)
    assert [p["platform"] for p in t["platforms"]] == ["tiktok"] and t["tiktokSettings"]["privacyLevel"] == "SELF_ONLY"


def test_a_manual_run_can_force_test_but_never_on(monkeypatch):
    cfg = load_config(CFG)
    cfg.crosspost.mode = "off"
    monkeypatch.setenv(cp.MODE_ENV, "test")
    assert cp.effective_mode(cfg) == "test"
    monkeypatch.setenv(cp.MODE_ENV, "on")
    assert cp.effective_mode(cfg) == "off"


class FakeRelease:
    def __init__(self, fail=False):
        self.fail, self.uploaded = fail, []

    def upload(self, path, name=None, **kw):
        if self.fail:
            raise RuntimeError("github down")
        self.uploaded.append(name)
        return f"https://github.com/o/r/releases/download/social-clips/{name}"

    def prune(self, days):
        return []


def test_crosspost_posts_and_never_raises(monkeypatch, tmp_path):
    cfg = load_config(CFG)
    clip = tmp_path / "a.social.mp4"
    clip.write_bytes(b"x")
    assert cp.crosspost(cfg, clip, _webb(), script=None, full_label="8-hour") is None  # mode off
    cfg.crosspost.mode = "on"
    monkeypatch.delenv(cp.RELAY_ENV, raising=False)
    assert cp.crosspost(cfg, clip, _webb(), script=None, full_label="8-hour", release=FakeRelease()) is None

    sent = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "post_id": "p1", "post_status": "scheduled", "scheduled_for": "2026-10-09T17:00:00Z",
                    "platforms": ["tiktok", "instagram"]}

    def fake_post(url, json, timeout):
        sent.update(json)
        return Resp()

    monkeypatch.setattr(cp.requests, "post", fake_post)
    rel = FakeRelease()
    got = cp.crosspost(cfg, clip, _webb(), script=None, full_label="8-hour", release=rel, relay="https://relay",
                       now=utc(2026, 10, 9, 14, 10))
    assert got.post_id == "p1" and rel.uploaded == ["a.social.mp4"]
    assert sent["method"] == "POST" and sent["path"] == "/posts"
    assert sent["body"]["mediaItems"][0]["url"].endswith("/a.social.mp4")
    # a failure is reported, not raised
    assert cp.crosspost(cfg, clip, _webb(), script=None, full_label="8-hour", release=FakeRelease(fail=True),
                        relay="https://relay") is None


def test_send_rejects_a_relay_that_did_not_create_a_post(monkeypatch):
    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d, self.text = code, data, "Accepted"

        def json(self):
            if self._d is None:
                raise ValueError
            return self._d

    for resp in (Resp(200, None), Resp(502, {"ok": False, "error": "Zernio rejected"}), Resp(200, {"ok": True})):
        monkeypatch.setattr(cp.requests, "post", lambda url, json, timeout, r=resp: r)
        with pytest.raises(RuntimeError):
            cp.send("https://relay", {})


def test_config_rejects_bad_mode_and_slot(tmp_path):
    text = Path(CFG).read_text()
    for old, new, msg in [('mode: "off"', 'mode: "sometimes"', "crosspost.mode"),
                          ('"23:30"]', '"25:30"]', "crosspost.slots")]:
        bad = tmp_path / "c.yaml"
        bad.write_text(text.replace(old, new))
        with pytest.raises(ValueError, match=msg):
            load_config(bad)
