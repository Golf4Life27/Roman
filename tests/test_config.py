from pathlib import Path

import pytest

from romanfeed.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def test_flagship_config_loads():
    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    assert cfg.channel.slug == "deep-space-ambient"
    assert cfg.video.duration_seconds == 45 * 80
    assert {s.type for s in cfg.enabled_sources} == {"roman", "nasa_images"}
    assert cfg.publish.mode == "dry-run"
    assert cfg.publish.privacy == "private"


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
