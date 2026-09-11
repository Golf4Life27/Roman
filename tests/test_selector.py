from PIL import Image, ImageDraw

from romanfeed.curation.selector import flat_black_fraction, looks_unsuitable, select_assets
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
    assert looks_unsuitable(_asset(11, title="GSFC_20171208_Archive_e001465", kw=["nebula"]))
    # Keywords alone can qualify an image; catalog names are not photo IDs.
    assert not looks_unsuitable(_asset(7, title="NGC 6302", kw=["Planetary Nebula"]))
    assert not looks_unsuitable(_asset(12, title="IC 1396", kw=["nebula"]))
    assert not looks_unsuitable(_asset(13, title="Nebula 99"))


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


def _write(path, black_box=None):
    """A noisy sky frame, optionally with a solid black rectangle cut out of it."""
    im = Image.effect_noise((2400, 1350), 40).convert("RGB")
    if black_box:
        ImageDraw.Draw(im).rectangle(black_box, fill=(0, 0, 0))
    im.save(path)
    return path


def test_flat_black_fraction_measures_detector_gap(tmp_path):
    clean = _write(tmp_path / "clean.png")
    # Bottom-left quadrant blacked out: the shape a WFPC2 mosaic leaves behind.
    notched = _write(tmp_path / "notched.png", black_box=[0, 675, 1200, 1350])
    assert flat_black_fraction(_local(clean)) < 0.01
    assert flat_black_fraction(_local(notched)) > 0.2


def _local(path):
    return ImageAsset(asset_id="nasa:X", title="Nebula X", url="", source="s", local_path=path)


def test_select_skips_frames_with_black_blocks(tmp_path, monkeypatch):
    notched = _write(tmp_path / "notched.png", black_box=[0, 675, 1200, 1350])
    monkeypatch.setattr(ImageAsset, "download",
                        lambda self, cache_dir, timeout=60: setattr(self, "local_path", notched) or notched)
    cands = [_asset(i) for i in range(4)]
    with Ledger(tmp_path / "s.db") as l:
        chosen = select_assets(cands, channel="c", ledger=l, count=2, min_width=1000,
                               cache_dir=str(tmp_path), seed="x")
        assert chosen == []          # every candidate has the gap
        # ...and the check can be switched off per channel
        allowed = select_assets(cands, channel="c2", ledger=l, count=2, min_width=1000,
                                cache_dir=str(tmp_path), seed="x", max_flat_black=0)
        assert len(allowed) == 2


def test_recycles_when_download_checks_empty_the_pool(tmp_path, monkeypatch):
    """The exact production failure: the pool passes the size check, then the
    black-block check rejects every fresh asset and the run has nothing left.
    Repeating an image beats shipping no video."""
    clean = _write(tmp_path / "clean.png")
    notched = _write(tmp_path / "notched.png", black_box=[0, 675, 1200, 1350])

    # Fresh assets are all defective; the ones the ledger already used are fine.
    def fake_download(self, cache_dir, timeout=60):
        self.local_path = notched if self.asset_id in {f"nasa:{i}" for i in range(6, 10)} else clean
        return self.local_path

    monkeypatch.setattr(ImageAsset, "download", fake_download)
    cands = [_asset(i) for i in range(10)]
    with Ledger(tmp_path / "s.db") as l:
        l.mark_assets_used("c", [f"nasa:{i}" for i in range(6)], "prev")
        chosen = select_assets(cands, channel="c", ledger=l, count=3, min_width=1000,
                               cache_dir=str(tmp_path), seed="x")
        # Four fresh assets cleared the size check, all four were rejected on
        # download, so the used ones get recycled rather than failing the run.
        assert len(chosen) == 3
        assert all(a.asset_id in {f"nasa:{i}" for i in range(6)} for a in chosen)


def test_no_recycle_when_nothing_was_used(tmp_path, monkeypatch):
    """With an empty ledger there is nothing to recycle; come up short honestly."""
    notched = _write(tmp_path / "notched.png", black_box=[0, 675, 1200, 1350])
    monkeypatch.setattr(ImageAsset, "download",
                        lambda self, cache_dir, timeout=60: setattr(self, "local_path", notched) or notched)
    with Ledger(tmp_path / "s.db") as l:
        assert select_assets([_asset(i) for i in range(5)], channel="c", ledger=l, count=3,
                             min_width=1000, cache_dir=str(tmp_path), seed="x") == []


def test_rejects_filename_and_generic_titles():
    """Both defects were observed in run #12's published chapter list."""
    def exact(title, kw):
        # _asset() appends an index; these checks need the title verbatim.
        return ImageAsset(asset_id=f"nasa:{title}", title=title, url="", source="s", keywords=list(kw))

    assert looks_unsuitable(exact("hs-2007-16-e-full_jpg", ["nebula"]))
    assert looks_unsuitable(exact("opo0501a", ["galaxy"]))
    assert looks_unsuitable(exact("heic1104a.tif", ["nebula"]))
    # Archival retrospectives: hardware and montages, never sky.
    assert looks_unsuitable(exact("History of Hubble Space Telescope (HST)", ["nebula"]))
    # Generic archive category labels that pass the keyword test.
    for junk in ("Space Science", "space science", "Astronomy", "Hubble", "Untitled"):
        a = ImageAsset(asset_id=f"nasa:{junk}", title=junk, url="", source="s", keywords=["nebula"])
        assert looks_unsuitable(a), junk
    # Real captions that superficially resemble the above must survive.
    assert not looks_unsuitable(_asset(23, title="NGC 6302", kw=["nebula"]))
    assert not looks_unsuitable(_asset(24, title="M 31", kw=["galaxy"]))
    assert not looks_unsuitable(_asset(25, title="Space Science Institute Maps the Lagoon Nebula"))


def test_no_two_clips_share_a_title(tmp_path, sample_asset, monkeypatch):
    """Chapter markers come from titles, so a repeat is visible in the
    description. Distinct archive IDs really do share captions."""
    monkeypatch.setattr(ImageAsset, "download", lambda self, cache_dir, timeout=60: setattr(self, "local_path", sample_asset.local_path) or sample_asset.local_path)
    cands = [
        ImageAsset(asset_id=f"nasa:{i}", title="Carina Nebula", url="", source="s", keywords=["nebula"])
        for i in range(5)
    ] + [_asset(50), _asset(51)]
    with Ledger(tmp_path / "s.db") as l:
        chosen = select_assets(cands, channel="c", ledger=l, count=4, min_width=1000, cache_dir=str(tmp_path), seed="x")
    titles = [a.title for a in chosen]
    assert len(titles) == len(set(titles)), titles
    assert titles.count("Carina Nebula") == 1


def test_warns_before_the_pool_runs_dry(tmp_path, sample_asset, monkeypatch, caplog):
    """Run #13 had 2 fresh images left and said nothing until it was already
    recycling. The warning has to arrive while there is still room to act."""
    import logging

    monkeypatch.setattr(ImageAsset, "download", lambda self, cache_dir, timeout=60: setattr(self, "local_path", sample_asset.local_path) or sample_asset.local_path)
    cands = [_asset(i) for i in range(8)]
    with Ledger(tmp_path / "s.db") as l:
        # Plenty of head room: 8 fresh for a 2-image video is 4 videos' worth.
        with caplog.at_level(logging.WARNING):
            select_assets(cands, channel="c", ledger=l, count=2, min_width=1000, cache_dir=str(tmp_path), seed="x")
        assert "LOW SUPPLY" not in caplog.text
        # Now most of the library is spent: 3 fresh for a 2-image video.
        l.mark_assets_used("c", [f"nasa:{i}" for i in range(5)], "prev")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            select_assets(cands, channel="c", ledger=l, count=2, min_width=1000, cache_dir=str(tmp_path), seed="y")
        assert "LOW SUPPLY" in caplog.text
