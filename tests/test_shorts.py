"""Shorts: which video each one comes from, what it says, and the publish switch."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from romanfeed import shorts
from romanfeed.config import AudioSettings, ChannelConfig, ChannelInfo, PublishSettings, ShortsSettings, VideoSettings
from romanfeed.render.ffmpeg import ffmpeg_path, probe_duration
from romanfeed.sources.base import ImageAsset
from romanfeed.state import Ledger, VideoRecord
from romanfeed.telescopes import prefer_known_lead, telescope_line, telescope_short


def _asset(i: int, **kw) -> ImageAsset:
    base = dict(asset_id=f"nasa:A{i}", title=f"Object {i}", url=f"https://example/{i}.jpg", source="NASA Image Library",
                credit="NASA/ESA", description="d", keywords=[])
    base.update(kw)
    return ImageAsset(**base)


def _video(ledger: Ledger, slug: str, yt: str | None, seconds: float, when: str) -> None:
    ledger.record_video(VideoRecord(slug, "chan", f"/x/{slug}.mp4", seconds, when, youtube_id=yt))


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path / "state.db") as led:
        # two rendered sets, each with a 1h primary and an 8h cut
        _video(led, "chan-2026-09-28", "OLD1h", 3600, "2026-09-28T10:00:00")
        _video(led, "chan-2026-09-28-8h", "OLD8h", 28800, "2026-09-28T11:00:00")
        _video(led, "chan-2026-09-30", "NEW1h", 3600, "2026-09-30T10:00:00")
        _video(led, "chan-2026-09-30-8h", "NEW8h", 28800, "2026-09-30T11:00:00")
        led.record_video_assets("chan-2026-09-28", [_asset(i) for i in range(0, 10)])
        led.record_video_assets("chan-2026-09-30", [_asset(i) for i in range(10, 20)])
        yield led


def test_ledger_keeps_running_order_and_metadata(ledger):
    assert ledger.assets_of_video("chan-2026-09-30") == [f"nasa:A{i}" for i in range(10, 20)]
    meta = ledger.asset_meta(["nasa:A12"])
    assert meta["nasa:A12"]["title"] == "Object 12" and meta["nasa:A12"]["url"].endswith("12.jpg")
    assert shorts.asset_from_meta(meta["nasa:A12"]).asset_id == "nasa:A12"


def test_old_videos_fall_back_to_assets_used(ledger):
    ledger.mark_assets_used("chan", ["nasa:Z1", "nasa:Z2"], "chan-2026-09-01")
    assert ledger.assets_of_video("chan-2026-09-01") == ["nasa:Z1", "nasa:Z2"]


def test_shorts_link_to_the_longest_public_cut(ledger):
    ps = shorts.parents(ledger, "chan", public=lambda vid: True)
    assert [(p.slug, p.link_id) for p in ps] == [("chan-2026-09-30", "NEW8h"), ("chan-2026-09-28", "OLD8h")]
    # 8h still private -> fall back to the public 1h; nothing public -> skipped
    ps = shorts.parents(ledger, "chan", public=lambda vid: vid in {"NEW1h"})
    assert [(p.slug, p.link_id, p.link_seconds) for p in ps] == [("chan-2026-09-30", "NEW1h", 3600)]


def test_plan_rotates_parents_and_never_shares_candidates(ledger):
    picks = shorts.plan(ledger, "chan", 3, per_short=3, public=lambda vid: True)
    assert [p.slug for p, _ in picks] == ["chan-2026-09-30", "chan-2026-09-28", "chan-2026-09-30"]
    flat = [a for _, ids in picks for a in ids]
    assert len(flat) == len(set(flat)) == 9
    # spread through the running order, not the first three images
    assert picks[0][1] == ["nasa:A10", "nasa:A13", "nasa:A16"]


def test_plan_prefers_parents_with_fewer_shorts_and_skips_used_images(ledger):
    ledger.record_short("chan", "nasa:A10", "chan-2026-09-30", "S1")
    picks = shorts.plan(ledger, "chan", 1, per_short=20, public=lambda vid: True)
    assert picks[0][0].slug == "chan-2026-09-28"
    picks = shorts.plan(ledger, "chan", 2, per_short=20, public=lambda vid: True)
    assert "nasa:A10" not in picks[1][1]


def test_telescope_detection():
    webb = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", credit="NASA, ESA, CSA, STScI")
    hubble = _asset(2, title="Westerlund 2", source="ESA/Hubble", credit="NASA, ESA, the Hubble Heritage Team")
    both = _asset(3, title="Pillars of Creation: Hubble and Webb compared", credit="NASA, ESA, CSA")
    unknown = _asset(4, title="Otherwise unnamed nebula", credit="NASA/JPL")
    assert telescope_line(webb) == "Imaged by the James Webb Space Telescope"
    assert telescope_short(hubble) == "Hubble"
    assert telescope_line(both) == "Imaged by the James Webb Space Telescope and Hubble Space Telescope"
    assert telescope_line(unknown) is None and telescope_short(unknown) is None
    assert prefer_known_lead([unknown, hubble, webb])[0] is hubble


def _cfg(**shorts_kw) -> ChannelConfig:
    return ChannelConfig(
        channel=ChannelInfo(slug="chan", name="Space Screens", handle="@SpaceScreens"),
        video=VideoSettings(), sources=[], audio=AudioSettings(allow_placeholder=True),
        publish=PublishSettings(mode="upload", privacy="public"), shorts=ShortsSettings(**shorts_kw),
    )


def test_short_metadata_points_at_the_full_video():
    a = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", description="Star birth in Carina.")
    md = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), [])
    assert md.title == "Cosmic Cliffs by Webb | Calm Space for Sleep"
    assert md.description.startswith("Full 8-hour sleep video: https://youtu.be/VID8h")
    assert "sub_confirmation=1" in md.description and "#shorts" in md.description
    assert md.privacy == "public"
    unknown = shorts.short_metadata(_cfg(), _asset(2, title="Faint Smudge"), shorts.Parent("p", "V", 3600, ""), [])
    assert unknown.title == "Faint Smudge | Calm Space for Sleep"  # no dangling "by"


def test_image_score_prefers_colour_and_detail(tmp_path):
    flat = tmp_path / "flat.png"
    Image.new("RGB", (400, 300), (20, 20, 22)).save(flat)
    rich = tmp_path / "rich.png"
    Image.merge("RGB", [Image.effect_noise((400, 300), 80).point(lambda v: min(255, v + k)) for k in (60, 0, 30)]).save(rich)
    assert shorts.image_score(rich) > shorts.image_score(flat)


def test_switch_off_renders_but_never_uploads(ledger, monkeypatch, tmp_path):
    led_path = ledger.db_path
    ledger.close()
    sent = []
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: sent.append(mode) or ("YT" if mode == "upload" else None))
    monkeypatch.setattr(shorts, "is_public", lambda vid: True)
    monkeypatch.setattr(shorts, "ranked_candidates", lambda cands, cache: cands)
    monkeypatch.setattr(shorts, "build_soundtrack", lambda lib, **kw: (Path(kw["out_path"]), []))
    monkeypatch.setattr(shorts, "render_short", lambda a, out, **kw: out)
    monkeypatch.delenv("GOOGLE_TTS_API_KEY", raising=False)
    data = led_path.parent

    res = shorts.run_shorts(_cfg(enabled=False), count=2, data_dir=data, output_dir=tmp_path / "out", public=lambda v: True)
    assert len(res) == 2 and sent == ["dry-run", "dry-run"]
    with Ledger(led_path) as led:
        assert led.shorts_asset_ids("chan") == set()  # dry runs burn nothing

    sent.clear()
    res = shorts.run_shorts(_cfg(enabled=True, per_day=3), count=5, data_dir=data, output_dir=tmp_path / "out", public=lambda v: True)
    assert sent == ["upload"] * 3  # capped at per_day
    again = shorts.run_shorts(_cfg(enabled=True, per_day=3), data_dir=data, output_dir=tmp_path / "out", public=lambda v: True)
    assert again == []  # the cap holds across runs on the same day


def test_render_short_is_vertical_and_timed(tmp_path):
    src = tmp_path / "wide.png"
    Image.effect_noise((1600, 900), 50).convert("RGB").save(src)
    a = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", local_path=src)
    audio = tmp_path / "a.m4a"
    subprocess.run([ffmpeg_path(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=20",
                    "-c:a", "aac", str(audio)], check=True)
    cfg = _cfg(seconds=15, width=270, height=480, fps=12)
    out = shorts.render_short(a, tmp_path / "s.mp4", cfg=cfg, audio=audio, link_label="8-hour", work_dir=tmp_path)
    assert abs(probe_duration(str(out)) - 15) < 0.5
    probe = subprocess.run([ffmpeg_path(), "-i", str(out)], capture_output=True, text=True).stderr
    assert "270x480" in probe
