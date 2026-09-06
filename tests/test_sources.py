from romanfeed.config import SourceSettings
from romanfeed.sources import build_source
from romanfeed.sources.base import ImageAsset
from romanfeed.sources.nasa_images import _extract_credit
from romanfeed.sources.roman import RomanTelescopeSource


def test_extract_credit():
    assert _extract_credit("Lovely. Image credit: NASA, ESA, CSA. More text", "X") == "NASA, ESA, CSA"
    assert _extract_credit("no credit here", "JPL") == "JPL"
    assert _extract_credit("Credit: NASA, ESA, and P. Kalas (UC Berkeley). Next sentence", "X") == "NASA, ESA, and P. Kalas (UC Berkeley)"
    assert _extract_credit("Credit: ESA/Garrelt Mellema (Leiden)  <b><a href=\"http://x\">link</a></b>\nmore", "X") == "ESA/Garrelt Mellema (Leiden)  link"
    long = "Credit: " + "NASA " * 40
    assert len(_extract_credit(long, "X")) <= 92 and _extract_credit(long, "X").endswith("…")


def test_short_description():
    a = ImageAsset(asset_id="x", title="t", url="", source="s", description="word " * 100)
    assert len(a.short_description) <= 161 and a.short_description.endswith("…")


def test_roman_filters_pre_launch(monkeypatch):
    src = build_source(SourceSettings(type="roman"))
    assert isinstance(src, RomanTelescopeSource)
    fake = [
        ImageAsset(asset_id="nasa:1", title="Artist concept of Roman", url="", source="s", date="2027-02-01"),
        ImageAsset(asset_id="nasa:2", title="Roman first light: Andromeda", url="", source="s", date="2027-02-01"),
        ImageAsset(asset_id="nasa:3", title="Nancy Grace Roman Space Telescope Launch", url="", source="s", date="2026-08-30"),
        ImageAsset(asset_id="nasa:4", title="Roman Space Telescope Arrival", url="", source="s", date="2026-05-01"),
        ImageAsset(asset_id="nasa:5", title="Roman: Galactic Bulge", url="", source="s", date="2026-09-15"),
    ]
    monkeypatch.setattr(src._lib, "fetch", lambda: fake)
    got = src.fetch()
    assert [a.asset_id for a in got] == ["nasa:2"]
    assert "roman" in got[0].keywords


def test_resolver_runs_once_at_download(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, stream=True, timeout=60, headers=None):
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def raise_for_status(self): pass
            def iter_content(self, n): yield b"data"
        calls.append(url)
        return R()

    monkeypatch.setattr("romanfeed.sources.base.requests.get", fake_get)
    a = ImageAsset(asset_id="nasa:1", title="t", url="https://x/preview.jpg", source="s", resolver=lambda: "https://x/orig.jpg")
    a.download(tmp_path)
    a.download(tmp_path)  # cached; resolver not called again
    assert a.url == "https://x/orig.jpg" and a.resolver is None
    assert calls == ["https://x/orig.jpg"]
