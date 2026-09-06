from romanfeed.curation.selector import looks_unsuitable, select_assets
from romanfeed.sources.base import ImageAsset
from romanfeed.state import Ledger


def _asset(i, kw=(), title="Nebula"):
    """Default title 'Nebula N' keeps these assets sky-positive."""
    return ImageAsset(asset_id=f"nasa:{i}", title=f"{title} {i}", url="", source="s", keywords=list(kw))


def test_unsuitable_filter():
    assert looks_unsuitable(_asset(1, title="Team portrait"))
    assert not looks_unsuitable(_asset(2, title="Carina Nebula"))
    # Mission-ops photos that mention the sky only in the description.
    bad = _asset(3, title="James Webb Space Telescope Mirror Reveal")
    bad.description = "The telescope will observe distant galaxies."
    assert looks_unsuitable(bad)
    assert looks_unsuitable(_asset(4, title="KSC-2012-3155"))
    assert looks_unsuitable(_asset(5, title="Earth observations taken from shuttle orbiter Columbia"))
    assert looks_unsuitable(_asset(6, title="Dr. Nancy Grace Roman visits JWST"))
    assert looks_unsuitable(_asset(8, title="ARC-2010-ACD10-0054-002", kw=["galaxy"]))
    assert looks_unsuitable(_asset(9, title="Beyond the Deep Field: Hubble's Legacy and the Future"))
    assert looks_unsuitable(_asset(10, title="NASA Galaxy Mission Celebrates Sixth Anniversary"))
    # Keywords alone can qualify an image.
    assert not looks_unsuitable(_asset(7, title="NGC 6302", kw=["Planetary Nebula"]))


def test_select_prefers_roman_and_skips_used(tmp_path, sample_asset, monkeypatch):
    # Every asset "downloads" to the same sample file.
    monkeypatch.setattr(ImageAsset, "download", lambda self, cache_dir, timeout=60: setattr(self, "local_path", sample_asset.local_path) or sample_asset.local_path)
    cands = [_asset(i) for i in range(6)] + [_asset(99, kw=["roman"])]
    with Ledger(tmp_path / "s.db") as l:
        l.mark_assets_used("c", ["nasa:0", "nasa:1"], "prev")
        chosen = select_assets(cands, channel="c", ledger=l, count=3, min_width=1000, cache_dir=str(tmp_path), seed="x")
        ids = [a.asset_id for a in chosen]
        assert ids[0] == "nasa:99"
        assert "nasa:0" not in ids and "nasa:1" not in ids
        assert len(ids) == 3


def test_select_resets_when_exhausted(tmp_path, sample_asset, monkeypatch):
    monkeypatch.setattr(ImageAsset, "download", lambda self, cache_dir, timeout=60: setattr(self, "local_path", sample_asset.local_path) or sample_asset.local_path)
    cands = [_asset(i) for i in range(3)]
    with Ledger(tmp_path / "s.db") as l:
        l.mark_assets_used("c", ["nasa:0", "nasa:1", "nasa:2"], "prev")
        chosen = select_assets(cands, channel="c", ledger=l, count=2, min_width=1000, cache_dir=str(tmp_path), seed="x")
        assert len(chosen) == 2
        assert l.used_asset_ids("c") == set()


def test_min_width_enforced(tmp_path, sample_asset, monkeypatch):
    monkeypatch.setattr(ImageAsset, "download", lambda self, cache_dir, timeout=60: setattr(self, "local_path", sample_asset.local_path) or sample_asset.local_path)
    with Ledger(tmp_path / "s.db") as l:
        assert select_assets([_asset(1)], channel="c", ledger=l, count=1, min_width=5000, cache_dir=str(tmp_path)) == []
