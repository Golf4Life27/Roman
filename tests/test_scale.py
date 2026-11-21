""""You are here" scale Shorts (test 21, romanfeed/scale.py)."""
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from romanfeed import fly_into, scale, sky_events
from romanfeed.config import load_config
from romanfeed.publish import shorts_styles as st
from romanfeed.publish.shorts_styles import ShortNumbers
from romanfeed.shorts import end_card_due, is_hook

ROOT = Path(__file__).resolve().parents[1]
DURING = datetime(2026, 11, 22, 18, tzinfo=timezone.utc)


def _s():
    return load_config(ROOT / "config/channels/deep-space-ambient.yaml").shorts


def test_slots_take_calm_shorts_no_other_kind_uses():
    s = _s()
    on = [n for n in range(32) if scale.slot_due(s, n, 0, DURING)]
    assert on == [8, 12, 24, 28]
    for n in on:
        assert not is_hook(s, n)                                   # a calm Short is swapped out
        assert not end_card_due(s, n, DURING)                      # the end-card test keeps its sides
        assert not sky_events.slot_due(s, n, DURING)
        assert not fly_into.slot_due(s, n, DURING)


def test_three_in_all_and_only_inside_the_window():
    s = _s()
    assert scale.slot_due(s, 8, 2, DURING)
    assert not scale.slot_due(s, 8, 3, DURING)                     # three posted: done
    assert not scale.slot_due(s, 8, 0, datetime(2026, 11, 21, 18, tzinfo=timezone.utc))
    assert not scale.slot_due(s, 8, 0, datetime(2026, 12, 1, 18, tzinfo=timezone.utc))


def test_scenes_go_out_in_order_once_each():
    assert scale.next_scene([]).id == "stars"
    assert scale.next_scene(["scale:stars"]).id == "galaxies"
    assert scale.next_scene(["scale:stars", "scale:galaxies"]).id == "planets"
    assert scale.next_scene([scale.asset_id(sc) for sc in scale.SCENES]) is None


def test_every_number_shown_or_said_has_an_official_source():
    for sc in scale.SCENES:
        assert sc.sources, sc.id
        for src in sc.sources:
            assert src.org in ("NASA", "ESA")
            assert src.url.startswith("https://") and (".nasa.gov/" in src.url or "esa.int/" in src.url)
            assert src.read.startswith("read Nov 21, 2026") and src.quote
        words = " ".join([sc.every, sc.fact, sc.turn, sc.here, sc.say, sc.title])
        found = set(re.findall(r"\d[\d,]*", words))
        listed = {re.match(r"\d[\d,]*", n).group(0) for n in sc.numbers}
        assert found <= listed, (sc.id, found - listed)            # nothing unchecked on screen or said
        for n in sc.numbers:
            assert re.match(r"\d[\d,]*", n).group(0) in words      # nothing listed that isn't used
            # and the number is what the page says
            digits = re.match(r"\d[\d,]*", n).group(0)
            assert any(digits in src.quote or digits.replace(",", "") in src.quote.replace(",", "")
                       or (digits == "100" and "10^11" in src.quote) for src in sc.sources), (sc.id, n)


def test_the_field_starts_crowded_and_ends_on_one_dot():
    sc = scale.SCENES[0]
    w, h = 270, 480
    first = scale.dots(sc, scale.zoom_at(0.0), w, h, scale.salt_of(sc), scale.min_cell(0.0))[0]
    last = scale.dots(sc, scale.zoom_at(20.0), w, h, scale.salt_of(sc), scale.min_cell(20.0))[0]
    assert len(first) > 10 * max(len(last), 1)
    fr = scale.field_frame(sc, 20.0, w, h)
    assert fr.shape == (h, w, 3) and fr.dtype == np.uint8
    cy, cx = int(h * scale.TARGET_Y), w // 2
    assert fr[cy, cx].max() > 200                                 # the one dot, lit
    assert scale.zoom_at(0.0) == 1.0 and scale.zoom_at(scale.ZOOM_END) == scale.ZOOM


def test_metadata_names_the_sources_and_says_the_dots_are_drawn():
    cfg = load_config(ROOT / "config/channels/deep-space-ambient.yaml")
    md = scale.metadata(cfg, scale.SCENES[2], link_id="VID8h", link_label="8-hour", tracks=[])
    assert md.title == "You Are Here: One Planet Among 6,000+ Found"
    assert md.description.startswith("Full 8-hour sleep video: https://youtu.be/VID8h")
    assert "https://science.nasa.gov/exoplanets/" in md.description
    assert "drawn to illustrate" in md.description
    assert scale.SCALE_TAG in md.tags


def _row(vid, posted, *, scale_=False, views=1000, subs=2, live=500, fly=False):
    return ShortNumbers(video_id=vid, posted=posted, title=vid, style="calm", earth=False, public_views=live,
                        views=views, subs=subs, scale=scale_, fly=fly)


def test_report_compares_against_the_usual_shorts_of_the_same_days():
    rows = [_row("a", "2026-11-21T10:00"), _row("s1", "2026-11-22T10:00", scale_=True, subs=9, live=2000),
            _row("b", "2026-11-22T12:00"), _row("f", "2026-11-22T13:00", fly=True)]
    lines = st.scale_lines(rows)
    assert lines[0].startswith("You are here vs the usual Shorts")
    assert "you are here: 1 Shorts, 2,000 live views each" in lines[1] and "9.0 per 1,000" in lines[1]
    assert "usual: 1 Shorts" in lines[2]                          # before the first, and fly-into, sit out
    assert st.scale_lines([_row("a", "2026-11-21T10:00")]) == []
    assert any("You are here" in ln for ln in st.report_lines(rows, DURING.date(), DURING.date()))
