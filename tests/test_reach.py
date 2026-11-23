""""How long would it take to reach it?" Shorts (test 22, romanfeed/reach.py)."""
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from romanfeed import fly_into, reach, scale, sky_events
from romanfeed.config import load_config
from romanfeed.publish import shorts_styles as st
from romanfeed.publish.shorts_styles import ShortNumbers
from romanfeed.shorts import end_card_due, is_hook

ROOT = Path(__file__).resolve().parents[1]
DURING = datetime(2026, 11, 25, 18, tzinfo=timezone.utc)


def _cfg():
    return load_config(ROOT / "config/channels/deep-space-ambient.yaml")


def test_slots_are_calm_and_no_other_test_uses_them():
    s = _cfg().shorts
    on = [n for n in range(32) if reach.slot_due(s, n, 0, DURING)]
    assert on == [8, 12, 24, 28]
    for n in on:
        assert not is_hook(s, n)
        assert not end_card_due(s, n, DURING)
        assert not sky_events.slot_due(s, n, DURING)
        assert not fly_into.slot_due(s, n, DURING)


def test_you_are_here_goes_first_while_it_has_one_left():
    s = _cfg().shorts
    # run_shorts checks scale first; with 2 of 3 posted on Nov 24 it still owns slot 8
    nov24 = datetime(2026, 11, 24, 18, tzinfo=timezone.utc)
    assert scale.slot_due(s, 8, 2, nov24) and reach.slot_due(s, 8, 0, nov24)
    assert not scale.slot_due(s, 8, 3, nov24)                      # then reach has it


def test_all_targets_once_and_only_inside_the_window():
    s = _cfg().shorts
    assert not reach.slot_due(s, 8, len(reach.TARGETS), DURING)
    assert not reach.slot_due(s, 8, 0, datetime(2026, 11, 22, 18, tzinfo=timezone.utc))
    assert not reach.slot_due(s, 8, 0, datetime(2026, 12, 7, 18, tzinfo=timezone.utc))
    assert reach.next_target([]).id == "moon"
    assert reach.next_target([reach.asset_id(t) for t in reach.TARGETS]) is None
    assert len({t.id for t in reach.TARGETS}) == len(reach.TARGETS)


def test_every_distance_comes_from_a_nasa_page_read_on_nov_23():
    for src in [t.source for t in reach.TARGETS] + [reach.VOYAGER]:
        assert src.org == "NASA" and src.url.startswith("https://science.nasa.gov/")
        assert src.read.startswith("read Nov 23, 2026") and src.quote
    assert "17.0 km/second" in reach.VOYAGER.quote and reach.VOYAGER_KM_S == 17.0
    for t in reach.TARGETS:
        figure = re.match(r"[\d.,]+( million)?", t.shown).group(0)
        assert figure in t.source.quote, t.id                      # what's shown is what NASA says
        n = float(figure.split()[0].replace(",", "")) * (1e6 if "million" in figure else 1)
        km = n * reach.KM_PER_LY if "light-year" in t.shown else n
        assert abs(km - t.km) / t.km < 1e-9, t.id


def test_the_answers_are_worked_out_right():
    want = {"moon": ("6.3 hours", "1.3 seconds"), "sun": ("100 days", "8.3 minutes"),
            "proxima": ("74,000 years", "4.2 years"), "orion": ("26 million years", "1,500 years"),
            "andromeda": ("44 billion years", "2.5 million years"), "crab": ("110 million years", "6,500 years"),
            "center": ("460 million years", "26,000 years"), "pillars": ("110 million years", "6,500 years"),
            "whirlpool": ("550 billion years", "31 million years")}
    for t in reach.TARGETS:
        assert (reach.fmt(*reach.probe_time(t)), reach.fmt(*reach.light_time(t))) == want[t.id]
        # and independently: km / 17 km/s, in the unit shown, within rounding to 2 figures
        secs = t.km / 17.0
        v, _, unit = reach.probe_time(t)
        size = {"hours": 3600, "days": 86400, "years": reach.YEAR_S, "million years": reach.YEAR_S * 1e6,
                "billion years": reach.YEAR_S * 1e9}[unit]
        assert abs(secs / size - v) / v < 0.05, t.id


def test_the_frame_draws_the_trip():
    t = reach.TARGETS[2]
    w, h = 270, 480
    start, end = reach.frame(t, 0.0, w, h), reach.frame(t, reach.TRAVEL_TO, w, h)
    assert start.shape == (h, w, 3) and start.dtype == np.uint8
    assert reach.progress(0.0) == 0.0 and reach.progress(reach.TRAVEL_TO) == 1.0
    assert reach.progress(2.0) > (2.0 - reach.TRAVEL_FROM) / (reach.TRAVEL_TO - reach.TRAVEL_FROM)  # quick start
    assert not np.array_equal(start, end)


def test_cards_render(tmp_path):
    t = reach.TARGETS[0]
    for card in (reach.question_card(t, 270, 480), reach.counter_card(t, 3.0, 270, 480),
                 reach.light_card(t, 270, 480), reach.source_card(t, 270, 480)):
        assert card.size == (270, 480) and card.getbbox() is not None


def test_metadata_names_the_sources_and_the_question():
    md = reach.metadata(_cfg(), reach.TARGETS[2], link_id="VID8h", link_label="8-hour", tracks=[])
    assert md.title == "How Long Would It Take to Reach the Orion Nebula? 🚀"
    assert md.description.startswith("Full 8-hour sleep video: https://youtu.be/VID8h")
    assert reach.TARGETS[2].source.url in md.description and reach.VOYAGER.url in md.description
    assert "26 million years" in md.description and "1,500 years" in md.description
    assert reach.REACH_TAG in md.tags and len(md.title) <= 100


def _row(vid, posted, *, reach_=False, views=1000, subs=2, live=500, scale_=False):
    return ShortNumbers(video_id=vid, posted=posted, title=vid, style="calm", earth=False, public_views=live,
                        views=views, subs=subs, reach=reach_, scale=scale_)


def test_report_compares_against_the_usual_shorts_of_the_same_days():
    rows = [_row("a", "2026-11-23T10:00"), _row("r1", "2026-11-24T10:00", reach_=True, subs=6, live=3000),
            _row("b", "2026-11-24T12:00"), _row("s", "2026-11-24T13:00", scale_=True)]
    lines = st.reach_lines(rows)
    assert lines[0].startswith("How long would it take vs the usual Shorts")
    assert "how long: 1 Shorts, 3,000 live views each" in lines[1] and "6.0 per 1,000" in lines[1]
    assert "usual: 1 Shorts" in lines[2]
    assert st.reach_lines([_row("a", "2026-11-23T10:00")]) == []
    assert not any(ln.startswith("You are here") and "r1" in ln for ln in st.scale_lines(rows))
