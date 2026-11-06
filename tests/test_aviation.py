"""The aviation channel: its photo filter, titles, music, supply and daily job."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from romanfeed.audio.library import Track
from romanfeed.config import SourceSettings, load_config
from romanfeed.curation.selector import aviation_unsuitable, looks_unsuitable
from romanfeed.publish.metadata import _aviation_lead, build_metadata, display_name, pick_aviation_subject
from romanfeed.sources.base import ImageAsset
from romanfeed.sources.commons import WikimediaCommons

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/channels/aviation-ambient.yaml"


def _avi(title, query="Boeing 747 in flight", cats=()):
    return ImageAsset(asset_id=f"commons:{abs(hash(title))}", title=title, url="", source="Wikimedia Commons",
                      credit="x", keywords=["aviation", query, *cats])


def test_config_is_its_own_channel():
    cfg = load_config(CONFIG)
    assert cfg.channel.slug == "aviation-ambient" and cfg.channel.subject == "aviation"
    assert cfg.channel.name == "Night Flight Screens" and cfg.channel.handle == "@NightFlightScreens"
    assert cfg.channel.channel_id == ""                       # the channel does not exist yet
    assert cfg.audio.style == "aviation" and cfg.audio.source == "composed"
    ocean = load_config(ROOT / "config/channels/ocean-ambient.yaml")
    # The same 1 hour + 8 hour pair, going dark after 45 minutes.
    assert cfg.video.duration_seconds == ocean.video.duration_seconds == 3600
    assert cfg.video.extra_lengths_hours == [8] and cfg.video.sleep_dark_after_minutes == 45
    assert cfg.video.thumbnail_line and cfg.video.thumbnail_line != ocean.video.thumbnail_line
    assert cfg.video.thumbnail_plate != ocean.video.thumbnail_plate
    [src] = cfg.enabled_sources
    assert src.type == "commons" and src.options["subject"] == "aviation" and len(src.queries) >= 50


def test_commons_tags_photos_with_the_subject():
    src = WikimediaCommons(SourceSettings(type="commons", min_width=1920, options={"subject": "aviation"}))
    page = {"pageid": 9, "title": "File:Boeing 747 above the clouds.jpg", "imageinfo": [{
        "mime": "image/jpeg", "width": 4000, "height": 2667, "url": "https://upload/full.jpg",
        "extmetadata": {"LicenseShortName": {"value": "CC BY 2.0"}, "Artist": {"value": "Jane Spotter"},
                        "Categories": {"value": "Boeing 747 in flight|Aircraft above clouds"}}}]}
    a = src.to_asset(page, "Boeing 747 in flight")
    assert a.keywords[:3] == ["aviation", "Boeing 747 in flight", "Boeing 747 in flight"]
    assert a.licence == "cc-by" and a.credit == "Jane Spotter, CC BY 2.0, via Wikimedia Commons"


def test_aviation_filter_keeps_calm_flight():
    assert not looks_unsuitable(_avi("Boeing 747 above the clouds"), "aviation")
    assert not looks_unsuitable(_avi("Douglas DC-3 over the Alps", "Douglas DC-3 in flight"), "aviation")
    assert not looks_unsuitable(_avi("Sunset from the cockpit", "cockpit view at sunset"), "aviation")
    assert not looks_unsuitable(_avi("Pilot's view of the approach to Innsbruck", "cockpit view"), "aviation")
    assert not looks_unsuitable(_avi("Runway 27L at night", "runway at night"), "aviation")
    assert not looks_unsuitable(_avi("Contrails over Munich", "contrail"), "aviation")
    assert not looks_unsuitable(_avi("Greenland ice sheet", "Greenland from an airplane",
                                     ["Views from airplanes", "Aerial photographs of Greenland"]), "aviation")
    assert not looks_unsuitable(_avi("Charter flight over the clouds", "airliner above the clouds"), "aviation")


def test_aviation_filter_turns_away_crashes_and_registrations():
    assert looks_unsuitable(_avi("Wreckage of the crashed Boeing 727", "Boeing 727 in flight"), "aviation")
    assert looks_unsuitable(_avi("Plane crash at Tenerife"), "aviation")
    assert looks_unsuitable(_avi("Cessna 172 (N12345) in flight", "aircraft in flight"), "aviation")
    assert looks_unsuitable(_avi("Boeing 747 above the clouds (N12345)"), "aviation")
    assert looks_unsuitable(_avi("Air France Concorde (F-BVFA) landing", "Concorde in flight"), "aviation")
    assert looks_unsuitable(_avi("Boeing 747-400 [G-BNLN] over London"), "aviation")


@pytest.mark.parametrize("title,query", [
    ("F-16 Fighting Falcon over the desert", "aircraft in flight"),          # military combat
    ("B-52 dropping bombs over the clouds", "aircraft in flight"),
    ("Missile launch from aircraft in flight", "aircraft in flight"),
    ("Red Arrows airshow over the crowd", "aircraft in flight"),            # airshow crowds
    ("Pilot portrait in front of a Boeing 737", "Boeing 737 in flight"),    # people
    ("Passengers boarding at sunset", "airliner landing at sunset"),
    ("Boeing 737 cockpit", "cockpit view at sunset"),                       # instrument close-up
    ("Airbus A320 instrument panel at night", "cockpit view at night"),
    ("Route map of Pan Am flights", "airliner in flight"),                  # maps, logos, tickets
    ("Lufthansa logo on the tail", "Boeing 747 in flight"),
    ("Boarding pass for flight BA117", "airliner in flight"),
    ("Terminal 5 departures board at night", "airport at night"),          # terminal interiors
    ("Airbus A380 scale model", "Airbus A380 in flight"),                   # models and toys
    ("Lego Boeing 747 above the clouds", "Boeing 747 in flight"),
    ("Concorde at the Museum of Flight", "Concorde in flight"),             # museum displays
    ("Douglas DC-3 on display in the hangar", "Douglas DC-3 in flight"),
    ("Lufthansa Boeing 747-8 at Frankfurt", "Boeing 747 in flight"),        # a parked plane; no sky shown
])
def test_aviation_filter_turns_away_the_rest(title, query):
    assert aviation_unsuitable(_avi(title, query)), title


def test_a_category_can_show_the_sky():
    parked = _avi("Lufthansa Boeing 747-8 at Frankfurt")
    flying = _avi("Lufthansa Boeing 747-8 at Frankfurt", cats=["Boeing 747-8 in flight"])
    assert looks_unsuitable(parked, "aviation") and not looks_unsuitable(flying, "aviation")


def test_other_channels_keep_their_own_filters():
    # The aviation rules only apply to the aviation subject.
    plane = _avi("Boeing 747 above the clouds")
    assert looks_unsuitable(plane, "ocean")
    assert looks_unsuitable(plane, "space")


@pytest.mark.parametrize("title,lead", [
    ("Boeing 747 above the clouds", "Boeing 747 above the clouds"),
    ("British Airways Boeing 747-436 G-BNLN c/n 24047 2009-06-12", "British Airways Boeing 747-436"),
    ("United Boeing 777 N2749U on approach to SFO", "United Boeing 777 on approach to SFO"),
    ("Lufthansa (DLH) Airbus A380 over Frankfurt · Flickr · someone", "Lufthansa Airbus A380 over Frankfurt"),
    ("Qantas Airbus A380 VH-OQA at sunset", "Qantas Airbus A380 at sunset"),
    ("Lockheed C-130 Hercules over the clouds", "Lockheed C-130 Hercules over the clouds"),   # a type, not a registration
    ("douglas DC-3 at dusk", "Douglas DC-3 at dusk"),
])
def test_aviation_lead_drops_registrations_codes_and_tails(title, lead):
    assert _aviation_lead(title) == lead


def test_intro_card_name_is_the_clean_one():
    assert display_name(_avi("Qantas Airbus A380 VH-OQA at sunset")) == "Qantas Airbus A380 at sunset"
    ocean = ImageAsset(asset_id="o", title="Green sea turtle (Chelonia mydas)", url="", source="s",
                       keywords=["ocean", "sea turtle"])
    assert display_name(ocean) == "Green sea turtle (Chelonia mydas)"   # other channels unchanged


def test_aviation_subject_picks_a_theme():
    assert pick_aviation_subject([_avi("Boeing 747 above the clouds", "airliner above the clouds")]) == "Above the Clouds"
    assert pick_aviation_subject([_avi("Runway lights at night", "runway at night")]) == "Night Flight"
    assert pick_aviation_subject([_avi("Douglas DC-3", "vintage aircraft in flight")]) == "Vintage Aircraft"
    assert pick_aviation_subject([_avi("Contrails", "contrail")]) == "Contrails"
    tie = [_avi("Sunset over the sea", "sunset above the clouds"), _avi("Douglas DC-3", "vintage aircraft in flight")]
    assert pick_aviation_subject(tie) == "Sunset Flight"            # the opening photo's theme wins a tie


def test_aviation_title_and_description():
    cfg = load_config(CONFIG)
    assets = [_avi("Boeing 747 above the clouds", "airliner above the clouds")] \
        + [_avi(f"Airbus A350 in flight {i}", "Airbus A350 in flight") for i in range(59)]
    track = Track("t", Path("t.m4a"), "ambient", "owned", title="Level Cruise", artist="Night Flight Screens")
    meta = build_metadata(cfg, assets, [track], seconds_per_image=60, when=date(2026, 11, 6))
    assert meta.title.startswith("Boeing 747 above the clouds | ")
    assert "| 1 Hour Airplane White Noise for Sleep" in meta.title
    assert len(meta.title) <= 100
    assert "ABOUT NIGHT FLIGHT SCREENS" in meta.description and "@NightFlightScreens" in meta.description
    assert "cabin hum" in meta.description and "#airplanewhitenoise" in meta.description
    assert "Hubble" not in meta.description and "telescope" not in meta.description.lower()
    assert "ocean" not in meta.description.lower()
    assert "airplane white noise for sleep" in meta.tags and "aviation" in meta.tags
    assert meta.category_id == "10" and meta.made_for_kids is False


def test_short_lead_keeps_both_search_phrases():
    cfg = load_config(CONFIG)
    meta = build_metadata(cfg, [_avi("Concorde", "Concorde in flight")] * 60, [], seconds_per_image=60)
    assert meta.title.startswith("Concorde | Airliners | 1 Hour Airplane White Noise for Sleep")
    assert meta.title.endswith("| Relaxing Flight Ambience") and len(meta.title) <= 100


def test_sleep_cut_says_the_screen_goes_dark():
    cfg = load_config(CONFIG)
    assets = [_avi("Douglas DC-3 at dusk", "Douglas DC-3 in flight")] * 60
    meta = build_metadata(cfg, assets, [], seconds_per_image=60, total_seconds=8 * 3600, sleep=True,
                          dark_after=45 * 60)
    assert "8 Hours" in meta.title and len(meta.title) <= 100
    assert "fades to black after 45 minutes" in meta.description
    assert "cabin hum and music play on" in meta.description


def test_aviation_music_is_its_own_style():
    from romanfeed.audio.composer import render_pcm

    aviation = render_pcm(20, "s", 8000, style="aviation")
    ocean = render_pcm(20, "s", 8000, style="ocean")
    space = render_pcm(20, "s", 8000, style="space")
    assert aviation.shape == ocean.shape == space.shape and np.isfinite(aviation).all()
    assert not np.allclose(aviation, ocean) and not np.allclose(aviation, space)
    assert 0 < np.abs(aviation).max() <= 1.0
    assert np.array_equal(aviation, render_pcm(20, "s", 8000, style="aviation"))   # same seed, same piece


def test_aviation_music_has_a_steady_cabin_hum():
    """The hum is a bed under the whole piece: quiet passages still hold low
    noise, where the ocean's surf rises and falls and space has none."""
    import romanfeed.audio.composer as c

    def low_band_floor(style: str) -> float:
        plan = c._make_plan(60, "s", 8000, style)
        x = np.concatenate(list(c._render(plan)), axis=1)[0]
        win = 8000  # one-second windows, past the fades
        levels = []
        for i in range(8, 52):
            seg = x[i * win:(i + 1) * win]
            spec = np.abs(np.fft.rfft(seg)) ** 2
            f = np.fft.rfftfreq(len(seg), 1 / 8000)
            levels.append(spec[(f > 60) & (f < 400)].sum())
        levels = np.array(levels)
        return float(levels.min() / levels.max())

    # Steady: the quietest second keeps a large share of the loudest one's
    # low band, more than the surf or the bare space pads ever do.
    aviation = low_band_floor("aviation")
    assert aviation > 0.45
    assert aviation > low_band_floor("ocean") and aviation > low_band_floor("space")


def test_open_chords_never_hold_a_semitone():
    from romanfeed.audio.composer import _AVIATION_MODES, _clashes, _open_chord

    for mode in _AVIATION_MODES.values():
        for root in range(12):
            for deg in mode["moves"]:
                pcs = _open_chord(lambda d: (root + mode["scale"][d % 7]) % 12, deg)
                assert not _clashes(pcs), (mode["label"], root, deg, pcs)


def test_composed_track_is_credited_to_the_channel(tmp_path):
    from romanfeed.audio.composer import compose_piece

    t = compose_piece(tmp_path / "a.m4a", seconds=8, seed="x", sample_rate=22050, style="aviation",
                      artist="Night Flight Screens")
    assert t.licence == "owned" and t.publishable and t.artist == "Night Flight Screens"
    assert "aviation" in t.tags and t.path.exists()


def test_supply_counts_aviation_photos(tmp_path):
    from romanfeed.state import Ledger
    from romanfeed.supply import single_subject_lines

    cfg = load_config(CONFIG)
    cands = [_avi(f"Airbus A350 above the clouds {i}", "Airbus A350 in flight") for i in range(130)] \
        + [_avi("Plane crash at the airport")]
    with Ledger(tmp_path / "state.db") as ledger:
        lines = single_subject_lines(cfg, cands, ledger, date(2026, 11, 16))
    assert lines[0].startswith("Night Flight Screens: 130 aviation photos never shown, of 130 suitable (1 turned away")
    assert "Daily long videos covered: 2 (through Nov 17)" in lines[1]


def test_daily_workflow_copies_ocean_and_waits_for_the_channel():
    ocean = yaml.safe_load((ROOT / ".github/workflows/ocean-daily.yml").read_text())
    avi = yaml.safe_load((ROOT / ".github/workflows/aviation-daily.yml").read_text())
    on = avi.get("on") or avi.get(True)                    # PyYAML reads the bare key `on` as True
    assert on["schedule"] == [{"cron": "45 4 * * *"}]
    job, ojob = avi["jobs"]["render"], ocean["jobs"]["render"]
    assert job["env"]["SLUG"] == "aviation-ambient"
    assert job["if"] == ojob["if"]                         # same switch as ocean
    steps = {s.get("name"): s for s in job["steps"]}
    # Not connected yet: the check stops the job before anything renders.
    assert "ready=false" in steps["Channel connected?"]["run"]
    assert steps["Render"]["if"] == "steps.token.outputs.ready == 'true'"
    assert len(job["steps"]) == len(ojob["steps"])
    assert (ROOT / "config/channels" / f"{job['env']['SLUG']}.yaml").exists()
