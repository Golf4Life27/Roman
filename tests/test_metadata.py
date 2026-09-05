from datetime import date
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
    assert meta.title.startswith("Nebulae | 1 Hour Space Telescope Sleep Screen")
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
