"""Earth-from-orbit videos inside Space Screens (config `earth:`)."""
from __future__ import annotations

from datetime import date

from romanfeed.audio.library import Track
from romanfeed.config import load_config
from romanfeed.curation.selector import is_earth, looks_unsuitable
from romanfeed.narration import fact_script, subscribe_call
from romanfeed.pipeline import todays_subject
from romanfeed.publish.metadata import build_metadata
from romanfeed.sources.base import ImageAsset
from romanfeed.sources.nasa_images import EARTH_TAG
from romanfeed.telescopes import telescope_line, telescope_short

CFG = "config/channels/deep-space-ambient.yaml"
ISS_DESC = ("The city lights of Osaka, Japan, are pictured from the International Space Station as it orbited "
            "260 miles above the Pacific Ocean. Osaka is Japan's third largest city and a major port on Osaka Bay.")


def earth(i=0, title="The city lights of Osaka, Japan", desc=ISS_DESC):
    return ImageAsset(asset_id=f"nasa:e{i}", title=title, url="u", source="NASA Image Library", credit="NASA",
                      description=desc, keywords=["Earth Observation", EARTH_TAG, "International Space Station"])


def nebula(i=0):
    return ImageAsset(asset_id=f"nasa:n{i}", title="Carina Nebula (NIRCam Image)", url="u", source="NASA Image Library",
                      credit="NASA, ESA, CSA, STScI", description="Stars are born in the Carina Nebula.",
                      keywords=["nebula", "webb"])


def test_earth_photos_stay_out_of_space_videos_and_vice_versa():
    assert is_earth(earth()) and not is_earth(nebula())
    assert looks_unsuitable(earth(), "space") and not looks_unsuitable(earth(), "earth")
    assert looks_unsuitable(nebula(), "earth") and not looks_unsuitable(nebula(), "space")


def test_earth_filter_wants_a_captioned_photo_of_earth_from_orbit():
    keep = ["The aurora australis glows above the Indian Ocean", "An orbital sunrise breaks above a cloudy Atlantic Ocean",
            "Mount Fuji is surrounded by the city lights of the Fuji Five Lakes region"]
    drop = ["iss070e091714", "Earth Observation", "Earth Observations taken by the Expedition 17 Crew",
            "The International Space Station begins to shine brightly as it orbits into a sunrise",
            "Orbital Boom Sensor System with a cloudy Earth limb", "Astronaut portrait in the cupola"]
    for t in keep:
        assert not looks_unsuitable(earth(title=t), "earth"), t
    for t in drop:
        assert looks_unsuitable(earth(title=t), "earth"), t
    # The record must say it was shot from Earth orbit (a "nile river" search finds Titan).
    assert looks_unsuitable(earth(title="Titan Nile-Like River Valley", desc="Cassini radar image of Titan."), "earth")
    assert looks_unsuitable(earth(desc="A pretty river delta."), "earth")


def test_attribution_and_narration_say_space_station_not_telescope():
    a = earth()
    assert telescope_line(a) == "Photographed from the International Space Station"
    assert telescope_short(a) is None and telescope_line(nebula()).startswith("Imaged by the James Webb")
    script = fact_script(a, full_label="1-hour")
    assert "taken by astronauts on the International Space Station" in script
    assert "light from the past" not in script and "travel through time" not in script
    assert all("astronaut" in c[0] or "orbit" in c[0] or "clouds" in c[0] for c in [subscribe_call(k, earth=True) for k in "abcdef"])


def test_earth_videos_get_earth_titles_descriptions_and_tags():
    cfg = load_config(CFG)
    tracks = [Track(id="t", genre="ambient", path="x", licence="owned", title="Piece")]
    meta = build_metadata(cfg, [earth(i) for i in range(4)], tracks, seconds_per_image=45, when=date(2026, 10, 16))
    assert "Earth from Space for Sleep" in meta.title and "Osaka" in meta.title and "Telescope" not in meta.title
    assert "International Space Station" in meta.description and "space telescope images" not in meta.description
    assert "earth from space" in meta.tags
    space = build_metadata(cfg, [nebula(i) for i in range(4)], tracks, seconds_per_image=45, when=date(2026, 10, 16))
    assert "Earth" not in space.title and "earth from space" not in space.tags


class _Ledger:
    def __init__(self, used=()):
        self.used = set(used)

    def used_asset_ids(self, channel):
        return self.used


def test_earth_runs_on_its_weekdays_and_falls_back_when_photos_run_low():
    cfg = load_config(CFG)
    assert cfg.earth.weekdays == ["tue", "thu", "fri", "sun"]
    pool = [earth(i, title=f"The city lights of Town {i}") for i in range(200)] + [nebula(i) for i in range(300)]
    fri, wed = date(2026, 10, 16), date(2026, 10, 14)
    assert todays_subject(cfg, pool, _Ledger(), 80, day=fri) == "earth"
    assert todays_subject(cfg, pool, _Ledger(), 80, day=wed) == "space"
    used = {f"nasa:e{i}" for i in range(100)}          # 100 left, under 1.5 videos' worth
    assert todays_subject(cfg, pool, _Ledger(used), 80, day=fri) == "space"


def test_an_earth_photo_can_be_a_short(monkeypatch, tmp_path):
    # Earth photos were judged by the deep-space test and always rejected, so
    # the first Earth video never gave a Short and, first in line, stalled them all.
    from romanfeed import shorts

    monkeypatch.setattr(ImageAsset, "download", lambda self, cache: None)
    monkeypatch.setattr(shorts, "flat_black_fraction", lambda a: 0.0)
    monkeypatch.setattr(shorts, "image_score", lambda p: 0.5)
    assert [a.asset_id for a in shorts.ranked_candidates([earth(1), nebula(2)], tmp_path)] == ["nasa:e1", "nasa:n2"]


def test_earth_short_titles_name_the_place_not_half_the_station():
    import types

    from romanfeed import shorts

    cfg = load_config(CFG)
    parent = types.SimpleNamespace(link_id="X", link_seconds=3600)
    a = earth(3, title="The Grand Canyon in Arizona pictured from the International Space Station")
    assert shorts.short_metadata(cfg, a, parent, []).title == \
        "The Grand Canyon in Arizona from the Space Station | Calm Space for Sleep"
    assert shorts.short_metadata(cfg, nebula(4), parent, []).title.endswith("| Calm Space for Sleep")


def test_earth_uses_only_clearly_nasa_frames():
    # A partner agency's or outside photographer's frame carries its own licence.
    for desc in (ISS_DESC + " Credit: ESA/NASA", ISS_DESC + " ESA astronaut Thomas Pesquet took this photo.",
                 ISS_DESC + " Image courtesy of JAXA.", ISS_DESC + " © Roscosmos"):
        assert looks_unsuitable(earth(desc=desc), "earth"), desc
    assert not looks_unsuitable(earth(), "earth")
    # "mesa" and "Odessa" are not ESA.
    assert not looks_unsuitable(earth(title="The city lights of Odessa beside a mesa"), "earth")


def test_frame_numbered_station_photos_take_their_title_from_the_caption():
    from romanfeed.sources.nasa_images import earth_caption

    cases = {
        ("iss069e032977 (July 18, 2023) --- The city lights of the oasis city of Al Ain in the United Arab "
         "Emirates are pictured from the International Space Station as it orbited 261 miles above."):
            "The city lights of the oasis city of Al Ain in the United Arab Emirates",
        ("iss066e128292 (Jan. 26, 2022) --- The city lights of Tabuk, a Saudi Arabian city near the border with "
         "Jordan, are pictured during an orbital nightime pass from the station."):
            "The city lights of Tabuk",
        ("iss070e105102 (March 1, 2024) --- The city lights of Detroit, Michigan, separated from the city of "
         "Windsor in Ontario, Canada, by the Detroit River, are pictured from the space station."):
            "The city lights of Detroit, Michigan",
        "iss071e000001 (May 2, 2024) --- An aurora streams above the Indian Ocean. The station was orbiting.":
            "An aurora streams above the Indian Ocean",
    }
    for desc, want in cases.items():
        assert earth_caption(desc.split(" ")[0], desc) == want
    # A real title is left alone, and so is a frame number with no caption.
    assert earth_caption("The aurora australis", cases and "x") is None
    assert earth_caption("iss070e091714", "Earth observation taken by the Expedition 70 crew.") is None


def test_a_captioned_frame_passes_the_earth_filter():
    from romanfeed.sources.nasa_images import earth_caption

    desc = ("iss070e105102 (March 1, 2024) --- The city lights of Detroit, Michigan, are pictured from the "
            "International Space Station as it orbited 262 miles above.")
    assert not looks_unsuitable(earth(title=earth_caption("iss070e105102", desc), desc=desc), "earth")
