"""Narrated Shorts: scripts only say what the archive caption says."""
from __future__ import annotations

import base64
import types

import pytest

from romanfeed import narration
from romanfeed.sources.base import ImageAsset

CLIFFS = ("What looks much like craggy mountains on a moonlit evening is actually the edge of a nearby, young, "
          "star-forming region NGC 3324 in the Carina Nebula. Captured in infrared light by the Near-Infrared Camera "
          "(NIRCam) on NASA's James Webb Space Telescope, this image reveals previously obscured areas of star birth. "
          "Called the Cosmic Cliffs, the region is actually the edge of a gigantic, gaseous cavity within NGC 3324, "
          "roughly 7,600 light-years away. Credit: NASA, ESA, CSA, STScI <a href='https://x'>Read more</a>")


def _a(desc, source="ESA/Webb", date="2022-07-12", title="Cosmic Cliffs (NIRCam Image)"):
    return ImageAsset(asset_id="esawebb:x", title=title, url="", source=source, description=desc, date=date)


def test_script_is_caption_sentences_plus_known_facts():
    s = narration.fact_script(_a(CLIFFS), full_label="8-hour")
    assert s.startswith("What looks much like craggy mountains")
    assert "roughly 7,600 light-years away." in s                 # the size/distance sentence is chosen
    assert "This image from the James Webb Space Telescope was released in 2022." in s
    assert "The full 8-hour version is on the channel." in s
    assert any(s.endswith(spoken) for spoken, _ in narration.SUBSCRIBE_CALLS)  # ends on a subscribe call
    assert "Credit" not in s and "http" not in s and "(NIRCam)" not in s


def test_every_number_spoken_comes_from_the_caption_or_the_metadata():
    import re
    s = narration.fact_script(_a(CLIFFS), full_label="8-hour")
    for n in re.findall(r"\d[\d,]*", s):
        assert n in CLIFFS or n in ("2022", "8")


def test_nasa_library_dates_are_not_read_aloud():
    s = narration.fact_script(_a(CLIFFS, source="NASA Image Library", date="2017-12-08"), full_label="8-hour")
    assert "2017" not in s and "This image comes from the James Webb Space Telescope." in s


def test_thin_or_boilerplate_captions_give_no_script():
    junk = ("Barred Spiral Galaxy NGC 1300 Credit: NASA, ESA. The Hubble Space Telescope is a project of "
            "international cooperation between NASA and the European Space Agency.")
    assert narration.fact_script(_a(junk), full_label="8-hour") is None
    dangling = "It is based on new observations of the object using a camera in Chile with a long name here."
    assert narration.fact_script(_a(dangling), full_label="8-hour") is None


def test_agency_slashes_and_datelines_are_cleaned():
    t = narration.clean_caption("NASA image release January 13, 2011 These images by the NASA/ESA/CSA James Webb telescope.")
    assert t == "These images by the James Webb telescope."


def test_caption_chunks_cover_the_voice_and_never_cross_sentences():
    chunks = narration.caption_chunks("One two three four five six seven. Eight nine ten.", start=1.0, duration=10.0)
    assert chunks[0][0] == 1.0 and abs(chunks[-1][1] - 11.0) < 0.01
    assert all(not (c[2].endswith(".") is False and "." in c[2]) for c in chunks)
    assert [c[2] for c in chunks][-1] == "Eight nine ten."


def test_synthesize_calls_google_with_the_key(monkeypatch, tmp_path):
    seen = {}

    def fake_post(url, params, json, timeout):
        seen.update(url=url, params=params, json=json)
        return types.SimpleNamespace(status_code=200, json=lambda: {"audioContent": base64.b64encode(b"RIFFwav").decode()})

    monkeypatch.setattr(narration.requests, "post", fake_post)
    out = narration.synthesize("Hello there.", tmp_path / "v.wav", api_key="K", voice="en-US-Chirp3-HD-Charon")
    assert out.read_bytes() == b"RIFFwav"
    assert seen["params"] == {"key": "K"} and seen["json"]["voice"]["languageCode"] == "en-US"


def test_synthesize_reports_http_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(narration.requests, "post",
                        lambda *a, **kw: types.SimpleNamespace(status_code=403, text="API not enabled"))
    with pytest.raises(RuntimeError, match="403"):
        narration.synthesize("Hi.", tmp_path / "v.wav", api_key="K")
