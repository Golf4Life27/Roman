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
    assert meta.title == "Carina Nebula 0 | Nebulae | 1 Hour Relaxing Space Video for Sleep | Telescope Screensaver"
    assert "ABOUT SPACE SCREENS" in meta.description
    assert "Not affiliated" in meta.description
    assert "0:00 Carina Nebula 0" in meta.description
    assert "45:00 Carina Nebula 60" in meta.description
    assert "NASA/ESA/CSA/STScI" in meta.description
    assert "Drift" in meta.description
    assert meta.privacy == "public" and meta.made_for_kids is False
    body = request_body(meta)
    assert body["status"]["privacyStatus"] == "public"
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


def test_lead_name_strips_archive_furniture():
    """Archive titles carry instrument tags and subtitles that do not belong in
    a video title."""
    from romanfeed.publish.metadata import lead_name

    assert lead_name("NGC 3324 (NIRCam Image)") == "NGC 3324"
    assert lead_name("The Pillars of Creation (MIRI Image)") == "The Pillars of Creation"
    assert lead_name("Sombrero Galaxy (Wide Field Camera 3 Image)") == "Sombrero Galaxy"
    # A dash subtitle goes, including when a tag hides behind it.
    assert lead_name("Westerlund 2 - Hubble's 25th anniversary image") == "Westerlund 2"
    assert lead_name("NGC 3132 – the Southern Ring Nebula (NIRCam Image)") == "NGC 3132"
    # A hyphen inside a name is not a subtitle separator.
    assert lead_name("Herbig-Haro 46/47") == "Herbig-Haro 46/47"
    # Trailing ellipsis (an already-clipped title) and stray whitespace.
    assert lead_name("A galaxy far away…") == "A galaxy far away"
    assert lead_name("  Ring   Nebula  ") == "Ring Nebula"
    assert lead_name("") == ""


def test_lead_name_cuts_long_titles_at_a_word_boundary():
    from romanfeed.publish.metadata import lead_name

    lead = lead_name("Hubble Sees a Horsehead of a Different Color in Infrared Light")
    assert len(lead) <= 40
    assert lead == "Hubble Sees a Horsehead of a Different"
    assert not lead.endswith(" ")
    # A single unbroken word still gets cut to the limit.
    assert len(lead_name("N" * 60)) == 40


def test_title_stays_within_100_chars_with_a_very_long_lead():
    """Overflow comes out of the lead, not off the tail: the search phrases
    ("space video for sleep", "screensaver") have to survive."""
    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    long_title = "Westerlund Two Bright Young Stars Flare in an Enormous Stellar Nursery"
    assets = [_asset(0, long_title, ["roman"])] + [_asset(i, f"Roman field {i}", ["roman"]) for i in range(1, 80)]
    meta = build_metadata(cfg, assets, [], seconds_per_image=45, when=date(2026, 9, 5))
    assert len(meta.title) <= 100
    assert meta.title.endswith("Relaxing Space Video for Sleep | Telescope Screensaver")
    assert "Roman Telescope Images" in meta.title
    lead = meta.title.split(" | ")[0]
    assert lead and long_title.startswith(lead)  # shortened at a word boundary, not mid-word
    assert not lead.endswith(" ")


def test_different_first_assets_give_different_titles():
    """Identical titles run after run are what read as bulk uploads."""
    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    rest = [_asset(i, f"Carina Nebula {i}", ["nebula"]) for i in range(1, 80)]
    first = build_metadata(cfg, [_asset(0, "Ring Nebula (NIRCam Image)", ["nebula"])] + rest, [], seconds_per_image=45, when=date(2026, 9, 5))
    second = build_metadata(cfg, [_asset(0, "Helix Nebula (MIRI Image)", ["nebula"])] + rest, [], seconds_per_image=45, when=date(2026, 9, 5))
    assert first.title != second.title
    assert first.title.startswith("Ring Nebula | ") and second.title.startswith("Helix Nebula | ")
    # Same subject and length -- only the lead differs.
    assert first.title.split(" | ", 1)[1] == second.title.split(" | ", 1)[1]
