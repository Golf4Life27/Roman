from pathlib import Path

import pytest

from romanfeed.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def test_flagship_config_loads():
    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    assert cfg.channel.slug == "deep-space-ambient"
    assert cfg.video.duration_seconds == 45 * 80
    assert {s.type for s in cfg.enabled_sources} == {"roman", "nasa_images", "esa_archive"}
    assert cfg.publish.privacy == "private"


def test_long_cut_is_enabled_and_sized_for_the_watch_hour_gate():
    """The 8-hour cut is what banks watch hours, and it is built from the same
    rendered clips as the base video -- a concat, not a re-render."""
    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    assert cfg.video.extra_lengths_hours == [8]
    # A cut shorter than the base video would be pointless.
    base_hours = cfg.video.duration_seconds / 3600
    assert all(h > base_hours for h in cfg.video.extra_lengths_hours)


def test_the_two_esa_archives_cannot_collide():
    """Hubble and Webb are two blocks of the same source type. If they shared
    an id prefix, one archive's images would silently mask the other's."""
    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    esa = [s for s in cfg.enabled_sources if s.type == "esa_archive"]
    assert len(esa) == 2
    prefixes = [s.options["id_prefix"] for s in esa]
    bases = [s.options["base_url"] for s in esa]
    assert len(set(prefixes)) == 2, prefixes
    assert len(set(bases)) == 2, bases
    # Sky only: hardware, artwork and event categories are never requested.
    for s in esa:
        assert not ({"spacecraft", "mission", "illustrations", "anniversary", "misc"}
                    & set(s.options["categories"]))


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("channel: {slug: x, name: X}\nvideo: {fpz: 30}\n")
    with pytest.raises(ValueError, match="fpz"):
        load_config(p)


def test_bad_publish_mode(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("channel: {slug: x, name: X}\npublish: {mode: yolo}\n")
    with pytest.raises(ValueError, match="publish.mode"):
        load_config(p)
