"""The record shapes here are copied from live ESA/Hubble archive responses."""
from __future__ import annotations

import pytest
import requests

from romanfeed.config import SourceSettings
from romanfeed.curation.selector import looks_unsuitable
from romanfeed.sources.esa_archive import EsaArchive, _best_resource, _keywords


def _record(image_id="heic2017a", title="Hubble's View of Jupiter", **over):
    """A trimmed copy of a real record, keeping the fields the source reads."""
    rec = {
        "ID": image_id,
        "Title": title,
        "Credit": "ESA/Hubble & NASA, A. Simon",
        "Description": "This latest image of the Tarantula Nebula was taken in 2020.",
        "Date": "2020-09-17T19:00:00",
        "Type": "Collection",
        "Subject.Category": ["Nebulae > Star Formation"],
        "Subject.Name": ["Tarantula Nebula"],
        "formats_url": {
            "large": "https://cdn.esahubble.org/archives/images/large/heic2017a.jpg",
            "original": "https://esahubble.org/media/archives/images/original/heic2017a.tif",
        },
        "Resources": [
            {"ResourceType": "Original", "MediaType": "Image",
             "URL": "https://esahubble.org/media/archives/images/original/heic2017a.tif",
             "Dimensions": [4000, 3000]},
            {"ResourceType": "Large", "MediaType": "Image",
             "URL": "https://cdn.esahubble.org/archives/images/large/heic2017a.jpg",
             "Dimensions": [4000, 3000]},
            {"ResourceType": "Small", "MediaType": "Image",
             "URL": "https://cdn.esahubble.org/archives/images/screen/heic2017a.jpg",
             "Dimensions": [1280.0, 1059]},
        ],
    }
    rec.update(over)
    return rec


def _settings(**opts):
    return SourceSettings(type="esa_archive", min_width=1600, options=opts)


def _source(monkeypatch, pages, **opts):
    """Build a source whose category fetches return canned records."""
    src = EsaArchive(_settings(**opts))
    monkeypatch.setattr(EsaArchive, "_category", lambda self, cat: pages.get(cat, []))
    return src


def test_prefers_the_large_jpeg_over_the_tiff_original():
    """Same pixels either way; the TIFF is several times the bytes."""
    url, w, h = _best_resource(_record())
    assert url.endswith("large/heic2017a.jpg")
    assert (w, h) == (4000, 3000)


def test_takes_the_tiff_when_it_is_the_only_image():
    rec = _record()
    rec["Resources"] = [r for r in rec["Resources"] if r["ResourceType"] == "Original"]
    url, w, _ = _best_resource(rec)
    assert url.endswith(".tif")
    assert w == 4000


def test_falls_back_to_formats_url_without_a_resources_array():
    rec = _record()
    del rec["Resources"]
    url, w, h = _best_resource(rec)
    assert url.endswith("large/heic2017a.jpg")
    assert (w, h) == (0, 0)  # unknown, so the download-time check decides


def test_string_dimensions_are_handled():
    """Live records mix ints and strings like "1663.0"."""
    rec = _record()
    rec["Resources"] = [{"ResourceType": "Large", "MediaType": "Image",
                         "URL": "https://x/large.jpg", "Dimensions": ["1663.0", "1375"]}]
    _, w, h = _best_resource(rec)
    assert (w, h) == (1663, 1375)


def test_small_images_are_dropped_without_downloading(monkeypatch):
    """Dimensions arrive with the listing, so this costs no bandwidth."""
    small = _record("potw1234a", "A Small Nebula")
    small["Resources"] = [{"ResourceType": "Large", "MediaType": "Image",
                           "URL": "https://x/small.jpg", "Dimensions": [800, 600]}]
    src = _source(monkeypatch, {"nebulae": [_record(), small]}, categories=["nebulae"])
    assets = src.fetch()
    assert [a.asset_id for a in assets] == ["esa:heic2017a"]


def test_one_image_in_two_categories_is_returned_once(monkeypatch):
    src = _source(monkeypatch, {"nebulae": [_record()], "galaxies": [_record()]},
                  categories=["nebulae", "galaxies"])
    assert len(src.fetch()) == 1


def test_a_failing_category_does_not_lose_the_others(monkeypatch):
    src = EsaArchive(_settings(categories=["nebulae", "galaxies"]))

    def flaky(self, category):
        if category == "nebulae":
            raise requests.RequestException("archive down")
        return [_record("potw9999a", "A Spiral Galaxy")]

    monkeypatch.setattr(EsaArchive, "_category", flaky)
    assets = src.fetch()
    assert [a.asset_id for a in assets] == ["esa:potw9999a"]


def test_credit_and_licence_come_straight_from_the_record(monkeypatch):
    """No scraping prose for a credit, unlike the NASA path."""
    src = _source(monkeypatch, {"nebulae": [_record()]}, categories=["nebulae"])
    a = src.fetch()[0]
    assert a.credit == "ESA/Hubble & NASA, A. Simon"
    assert a.licence == "cc-by"
    assert a.source == "ESA/Hubble"


def test_id_prefix_and_label_keep_two_archives_apart(monkeypatch):
    """Hubble and Webb are two source blocks; their ids must not collide."""
    src = _source(monkeypatch, {"nebulae": [_record()]}, categories=["nebulae"],
                  base_url="https://esawebb.org", id_prefix="esawebb", label="ESA/Webb")
    a = src.fetch()[0]
    assert a.asset_id == "esawebb:heic2017a"
    assert a.source == "ESA/Webb"


def test_category_keywords_survive_the_curation_filter(monkeypatch):
    """The filter needs a sky word or it rejects everything these return."""
    src = _source(monkeypatch, {"nebulae": [_record()]}, categories=["nebulae"])
    a = src.fetch()[0]
    assert "Nebulae" in a.keywords  # "Nebulae > Star Formation" split on ">"
    assert not looks_unsuitable(a)


def test_hardware_titles_are_still_rejected(monkeypatch):
    """Sky-only categories are requested, but the filter stays the backstop."""
    src = _source(monkeypatch, {"nebulae": [_record("opo0001a", "Hubble Servicing Mission 4")]},
                  categories=["nebulae"])
    assert looks_unsuitable(src.fetch()[0])


@pytest.mark.parametrize("field", ["ID", "Resources"])
def test_records_missing_essentials_are_skipped(monkeypatch, field):
    rec = _record()
    del rec[field]
    if field == "Resources":
        del rec["formats_url"]
    src = _source(monkeypatch, {"nebulae": [rec]}, categories=["nebulae"])
    assert src.fetch() == []
