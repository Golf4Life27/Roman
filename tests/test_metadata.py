from datetime import date

import pytest
from pathlib import Path

from romanfeed.audio.library import Track
from romanfeed.config import load_config
from romanfeed.publish.metadata import build_metadata, pick_subject
from romanfeed.publish.youtube import request_body
from romanfeed.sources.base import ImageAsset

ROOT = Path(__file__).resolve().parents[1]


def _asset(i, title, kw=()):
    return ImageAsset(asset_id=f"nasa:{i}", title=title, url="", source="NASA Image Library", credit="NASA/ESA/CSA/STScI", keywords=list(kw))


def test_subject_and_title():
    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    assets = [_asset(i, f"Carina Nebula {i}", ["nebula"]) for i in range(80)]
    meta = build_metadata(cfg, assets, [Track("t", Path("t.m4a"), "ambient", "owned", title="Drift")], seconds_per_image=45, when=date(2026, 9, 5))
    assert meta.title == "Nebulae | 1 Hour Relaxing Space Video for Sleep | Real Telescope Screensaver"
    assert "ABOUT SPACE SCREENS" in meta.description
    assert "Not affiliated" in meta.description
    assert "0:00 Carina Nebula 0" in meta.description
    assert "45:00 Carina Nebula 60" in meta.description
    assert "NASA/ESA/CSA/STScI" in meta.description
    assert "Drift" in meta.description
    assert meta.privacy == "private" and meta.made_for_kids is False
    body = request_body(meta)
    assert body["status"]["privacyStatus"] == "private"
    assert body["snippet"]["categoryId"] == "28"


def test_length_text():
    from romanfeed.publish.metadata import length_text

    assert length_text(48) == "1 Minute"
    assert length_text(600) == "10 Minutes"
    assert length_text(3600) == "1 Hour"
    assert length_text(8 * 3600 + 30) == "8 Hours"


def test_pick_subject_roman_wins():
    assets = [_asset(1, "x", ["roman"]), _asset(2, "Whirlpool galaxy")]
    assert pick_subject(assets) == "Roman Telescope Images"
    assert pick_subject([_asset(3, "Something")]) == "Deep Space"


def test_private_test_forces_private_and_permits_placeholder(tmp_path, monkeypatch):
    """The private_test path must never produce a public upload."""
    import romanfeed.pipeline as pl
    from romanfeed.audio.library import Track
    from romanfeed.config import load_config

    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    cfg.publish.privacy = "public"
    captured = {}

    class FakeSrc:
        name = "fake"
        def fetch(self):
            return [_asset(i, f"Nebula {i}", ["nebula"]) for i in range(3)]

    monkeypatch.setattr(pl, "build_source", lambda s: FakeSrc())
    monkeypatch.setattr(pl, "select_assets", lambda cands, **kw: cands[:2])
    monkeypatch.setattr(pl, "build_soundtrack", lambda *a, **kw: (tmp_path / "s.m4a", [Track("p", tmp_path / "p.m4a", "ambient", "placeholder")]))
    monkeypatch.setattr(pl, "render_video_with_clips", lambda *a, **kw: (kw["out_path"].write_bytes(b"x"), []) and (kw["out_path"], []))
    monkeypatch.setattr(pl, "probe_duration", lambda p: 12.0)
    def fake_publish(video_path, meta, *, mode):
        captured["mode"], captured["privacy"], captured["title"] = mode, meta.privacy, meta.title
        return "vid123"
    monkeypatch.setattr(pl, "publish", fake_publish)

    res = pl.run(cfg, pl.RunOptions(images=2, seconds_per_image=6, data_dir=tmp_path / "d", output_dir=tmp_path / "o", private_test=True))
    assert captured == {"mode": "upload", "privacy": "private", "title": captured["title"]}
    assert captured["title"].startswith("[TEST] ")
    assert res.youtube_id == "vid123"

    # Without private_test, placeholder audio must block a real upload.
    cfg.publish.mode = "upload"
    with pytest.raises(RuntimeError, match="non-publishable"):
        pl.run(cfg, pl.RunOptions(images=2, seconds_per_image=6, data_dir=tmp_path / "d2", output_dir=tmp_path / "o2"))


def test_chapter_limit_keeps_description_under_the_cap():
    """An 8-hour cut has hundreds of chapters; unlimited, they'd eat the
    5000-character description and push the credits out of it."""
    from romanfeed.audio.library import Track
    from romanfeed.config import load_config
    from romanfeed.publish.metadata import build_metadata

    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    assets = [_asset(i, f"Nebula number {i}", ["nebula"]) for i in range(80)] * 8
    tracks = [Track("t", ROOT / "x.m4a", "ambient", "generated", title="Drift")]

    capped = build_metadata(cfg, assets, tracks, seconds_per_image=45, chapter_limit=80)
    assert len(capped.description) < 5000
    assert "IMAGE CREDITS" in capped.description      # credits survived
    assert "MUSIC" in capped.description
    assert capped.description.count("\n0:") + capped.description.count("\n1:") > 0
    assert "the sequence continues to" in capped.description
    assert "8 Hour" in capped.title or "Hour" in capped.title


def test_chapter_titles_cut_on_word_boundary():
    """Run #12 published '...interstellar material over 160,000 lig'."""
    from romanfeed.publish.metadata import _clip_title

    long = "Hubble views a spectacular supernova with interstellar material over 160,000 light-years away"
    out = _clip_title(long)
    assert len(out) <= 80
    assert out.endswith("…")
    assert not out.endswith("lig…")
    assert out.startswith("Hubble views a spectacular supernova")
    # Short titles pass through untouched, with whitespace tidied.
    assert _clip_title("  Soul   Nebula ") == "Soul Nebula"
