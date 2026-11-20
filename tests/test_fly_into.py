"""Fly-into Shorts: balanced slots, no test treatment, a logged fallback, their own report line."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from romanfeed import fly_into as fly
from romanfeed import shorts
from romanfeed import sky_events as sky
from romanfeed.audio.library import Track
from romanfeed.config import ShortsSettings, load_config
from romanfeed.publish import shorts_styles as ss
from romanfeed.state import Ledger

from test_shorts import _asset, _cfg, _quiet_run, _video, ledger  # noqa: F401  (fixture)

UTC = timezone.utc
NOV22 = datetime(2026, 11, 22, 18, tzinfo=UTC)    # noon Central, both tests running
NOV27 = datetime(2026, 11, 27, 18, tzinfo=UTC)    # fly-into only
ON = dict(fly_into_from="2026-11-21", fly_into_until="2026-11-27", fly_into_per_day=2)
SKY = dict(sky_event_from="2026-11-18", sky_event_until="2026-11-25", sky_event_per_day=2)
BITS = {"hook": lambda n: n % 2, "opening": lambda n: (n // 2) % 2, "title": lambda n: (n // 4) % 2}


# -- slots: two a day, and the other tests stay even ------------------------------

def _balanced(rest: list[int]) -> None:
    for x, y in (("hook", "opening"), ("opening", "title"), ("hook", "title")):
        cells = [sum(1 for n in rest if (BITS[x](n), BITS[y](n)) == c) for c in product((0, 1), repeat=2)]
        assert len(set(cells)) == 1, (x, y, cells)


def test_the_fly_into_slots_leave_every_other_test_balanced_with_and_without_sky_events():
    s = ShortsSettings(hook_every=2, **ON, **SKY)
    flies = [n for n in range(160) if fly.slot_due(s, n, NOV22)]
    skies = [n for n in range(160) if sky.slot_due(s, n, NOV22)]
    assert len(flies) == 40 and not set(flies) & set(skies)          # 4 in 16: 2 a day at 8 a day
    rest = [n for n in range(16) if n not in flies and n not in skies]
    # both tests on: the other 8 hold every hook x opening x title combination once
    assert sorted((BITS["hook"](n), BITS["opening"](n), BITS["title"](n)) for n in rest) == \
        sorted(product((0, 1), repeat=3))
    _balanced([n for n in range(160) if n not in flies and n not in skies])
    # fly-into alone (Nov 26-27): every pair of tests still even
    alone = [n for n in range(160) if fly.slot_due(s, n, NOV27)]
    assert len(alone) == 40 and not any(sky.slot_due(s, n, NOV27) for n in range(16))
    _balanced([n for n in range(160) if n not in alone])
    # each test loses the same number from both of its sides
    for bit in BITS.values():
        assert sum(bit(n) for n in flies) * 2 == len(flies)


def test_the_window_is_central_time_and_off_without_dates():
    s = ShortsSettings(**ON)
    assert not fly.in_window(s, datetime(2026, 11, 21, 5, tzinfo=UTC))   # still Nov 20 in Central
    assert fly.in_window(s, datetime(2026, 11, 21, 7, tzinfo=UTC))
    assert fly.in_window(s, datetime(2026, 11, 28, 5, tzinfo=UTC))       # Nov 27, 11 pm Central
    assert not fly.in_window(s, datetime(2026, 11, 28, 7, tzinfo=UTC))
    assert not any(fly.slot_due(ShortsSettings(), n, NOV22) for n in range(16))
    assert not any(fly.slot_due(ShortsSettings(fly_into_per_day=0, **{k: v for k, v in ON.items()
                                                                       if k != "fly_into_per_day"}), n, NOV22)
                   for n in range(16))


def test_the_channel_config_runs_the_test_nov_21_to_27_in_slots_the_sky_events_leave(tmp_path):
    s = load_config("config/channels/deep-space-ambient.yaml").shorts
    assert (s.fly_into_from, s.fly_into_until, s.fly_into_per_day) == ("2026-11-21", "2026-11-27", 2)
    assert sorted(s.fly_into_slots) == [2, 4, 9, 15]
    assert not set(s.fly_into_slots) & set(s.sky_event_slots)
    assert (s.test_per_day, s.sky_event_slots) == (8, [0, 6, 11, 13])
    bad = Path("config/channels/deep-space-ambient.yaml").read_text().replace(
        "fly_into_slots: [2, 4, 9, 15]", "fly_into_slots: [0, 4, 9, 15]")
    (tmp_path / "c.yaml").write_text(bad)
    with pytest.raises(ValueError, match="sky_event_slots"):
        load_config(tmp_path / "c.yaml")


# -- the picture, the title and the description -----------------------------------

def _galaxy(i: int = 1, **kw):
    base = dict(asset_id=f"nasa:G{i}", title="Whirlpool Galaxy (M51)", credit="NASA, ESA, S. Beckwith (STScI)",
                keywords=["galaxy", "Hubble"], width=4000, height=3000)
    base.update(kw)
    return _asset(100 + i, **base)


def test_metadata_is_the_plain_name_with_the_motion_and_no_test_treatment():
    md = fly.metadata(_cfg(), _galaxy(), link_id="VID8h", link_label="8-hour", tracks=[], depth_source="brightness")
    assert md.title == "Flying into the Whirlpool Galaxy"
    assert md.tags[0] == fly.FLY_TAG
    for t in (shorts.HOOK_TAG, shorts.PUNCH_TAG, shorts.ANTIC_TAG, shorts.PLAIN_TITLE_TAG, sky.SKY_TAG, "why",
              "explainer"):
        assert t not in md.tags
    assert md.description.startswith("Full 8-hour sleep video: https://youtu.be/VID8h")
    assert "one real Hubble image" in md.description and "not a measurement of real distances" in md.description
    assert "3D" not in md.title + md.description            # no per-pixel depth claimed for brightness layering
    assert "Image: NASA, ESA, S. Beckwith (STScI)" in md.description and "sub_confirmation=1" in md.description
    assert not md.synthetic
    assert fly.title(_galaxy(title="NGC 602")) == "Flying into NGC 602"


def test_the_pool_takes_unused_nebulae_and_galaxies_from_a_known_telescope_biggest_first(ledger):
    ledger.record_video_assets("chan-2026-09-30", [
        _galaxy(1, width=4000, height=3000),
        _galaxy(2, title="Carina Nebula", keywords=["nebula", "Webb"], credit="NASA, ESA, CSA, STScI",
                width=6000, height=4000),
        _galaxy(3, title="Hubble Captures a Spiral Galaxy", width=8000, height=6000),     # a headline
        _galaxy(4, title="Otherwise unnamed galaxy", credit="NASA/JPL", keywords=["galaxy"]),  # no telescope
        _galaxy(5, title="Westerlund 2", keywords=["star cluster", "Hubble"]),            # not a journey subject
        _galaxy(6, title="Sombrero Galaxy", width=1200, height=800),                     # too small
        _galaxy(7, title="Andromeda Galaxy"),
        _galaxy(8, title="Lagoon Nebula", keywords=["nebula", "Hubble"]),
        _galaxy(9, title="The night sky around the Twin Jet Nebula (ground-based image)", keywords=["nebula"],
                width=14000, height=11000),                                        # not the object itself
    ])
    ledger.record_short("chan", "nasa:G7", "chan-2026-09-30", "S1")                      # used by a Short
    ledger.mark_assets_used("chan-journey", ["nasa:G8"], "chan-journey-2026-11-20")      # used by a journey
    pool = shorts.fly_into_pool(ledger, _cfg(), public=lambda v: True)
    assert [a.asset_id for _, a in pool] == ["nasa:G2", "nasa:G1"]
    assert pool[0][0].link_id == "NEW8h"
    skip = {shorts.object_key(_galaxy(2, title="Carina Nebula"))}
    assert [a.asset_id for _, a in shorts.fly_into_pool(ledger, _cfg(), public=lambda v: True, skip_objects=skip)] \
        == ["nasa:G1"]


def test_pictures_of_the_surroundings_or_from_the_ground_are_left_out_and_size_stops_counting_past_a_cap():
    # The first dry run (Nov 20) picked a 157 MP ground-based survey plate of the sky around the Twin Jet Nebula.
    twin = _galaxy(title="The night sky around the Twin Jet Nebula (ground-based image)", keywords=["nebula"],
                   credit="NASA, ESA, Digitized Sky Survey 2", width=14000, height=11000)
    assert not fly.is_the_object(twin)
    for t in ("Wide-field view of the Lagoon Nebula", "Location of the Crab Nebula in Taurus",
              "Annotated image of the Orion Nebula", "Artist's impression of a galaxy"):
        assert not fly.is_the_object(_galaxy(title=t))
    assert fly.is_the_object(_galaxy()) and fly.is_the_object(_galaxy(title="Pillars of Creation (NIRCam Image)"))
    huge, big = _galaxy(2, width=14000, height=11000), _galaxy(3, width=6000, height=5000)
    assert fly.rank_key(huge) == fly.rank_key(big)
    neb = _galaxy(4, title="Lagoon Nebula", keywords=["nebula"], width=6000, height=5000)
    assert fly.rank_key(big, {"galaxy": 0.1}) > fly.rank_key(neb, {"galaxy": 0.1})


@pytest.mark.parametrize("archive_title, title", [
    ("Whirlpool Galaxy (M51)", "Flying into the Whirlpool Galaxy"),
    ("Hubble mosaic of the majestic Sombrero Galaxy", "Flying into the Sombrero Galaxy"),
    ("The Cat's Paw Nebula", "Flying into the Cat's Paw Nebula"),
    ("Stephan's Quintet", "Flying into Stephan's Quintet"),
    ("Pillars of Creation (NIRCam Image)", "Flying into the Pillars of Creation"),
    ("Cosmic Cliffs in the Carina Nebula (NIRCam Image)", "Flying into the Cosmic Cliffs"),
    ("Spiral galaxy NGC 1300", "Flying into NGC 1300"),
    ("Large Magellanic Cloud", "Flying into the Large Magellanic Cloud"),
])
def test_the_title_is_the_objects_own_name(archive_title, title):
    assert fly.title(_galaxy(title=archive_title)) == title


@pytest.mark.parametrize("archive_title", [
    "Stars in the Andromeda Galaxy’s giant stellar stream",   # the second dry run's pick (Nov 20)
    "Word Bank: Dwarf Galaxy",                                     # the third's
    "Barred Spiral Galaxy", "Galaxies in Hiding", "Hubble probes ‘ghost’ galaxy", "A galaxy pair",
])
def test_a_title_that_names_no_one_object_is_left_out(archive_title, ledger):
    assert fly.object_name(_galaxy(title=archive_title)) is None
    ledger.record_video_assets("chan-2026-09-30", [_galaxy(1, title=archive_title)])
    assert shorts.fly_into_pool(ledger, _cfg(), public=lambda v: True) == []


def test_fits_needs_the_picture_to_cover_a_vertical_screen():
    assert fly.fits(4000, 3000, 1080, 1920)
    assert fly.fits(1000, 1750, 1080, 1920)
    assert not fly.fits(3000, 1200, 1080, 1920) and not fly.fits(0, 0, 1080, 1920)


# -- in the run -------------------------------------------------------------------

LIVE = dict(fly_into_from="2026-01-01", fly_into_until="2099-12-31", fly_into_per_day=2,
            anticipation_from="2026-01-01", anticipation_until="2099-12-31",
            plain_title_from="2026-01-01", plain_title_until="2099-12-31", hook_every=2)


def _fly_run(monkeypatch, tmp_path, ledger, *, render=None, posted_before=7, slots=(7,), opencv=True, **kw):
    """A run whose first Short is number `posted_before`, a fly-into slot. Short 7
    would otherwise be hook style, with the anticipation opening and the plain title."""
    _video(ledger, "chan-2026-10-02", "GAL1h", 3600, "2026-10-02T10:00:00")
    _video(ledger, "chan-2026-10-02-8h", "GAL8h", 28800, "2026-10-02T11:00:00")
    ledger.record_video_assets("chan-2026-10-02", [_galaxy(1)])
    for i in range(posted_before):
        ledger.record_short("chan", f"nasa:P{i}", "chan-old", f"P{i}")
    led_path = ledger.db_path
    ledger.close()
    _quiet_run(monkeypatch)
    img = tmp_path / "galaxy.png"
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:1800, 0:1000]
    glow = 220 * np.exp(-(((xx - 600) / 220) ** 2 + ((yy - 700) / 260) ** 2))
    Image.fromarray(np.clip(np.dstack([glow, glow * 0.6, glow * 0.9]) + rng.normal(12, 4, (1800, 1000, 3)), 0, 255)
                    .astype(np.uint8)).save(img)

    def ranked(cands, cache, *w):
        for c in cands:
            c.local_path = img
        return cands

    monkeypatch.setattr(shorts, "ranked_candidates", ranked)
    monkeypatch.setattr(shorts, "recent_shorts", lambda *a, **k: [])
    published = []
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: published.append(md) or f"YT{len(published)}")
    monkeypatch.setattr(shorts, "_render_cut", lambda *a, **kw: Path(kw.get("work", tmp_path)) / "x.mp4")
    import romanfeed.audio.composer as composer
    import romanfeed.journey.render as jrender
    import romanfeed.journey.short as jshort

    monkeypatch.setattr(jrender, "have_opencv", lambda: opencv)
    monkeypatch.setattr(composer, "compose_piece", lambda out, **k: Track("composed-x", Path(out), "ambient", "owned",
                                                                          title="Piece"))
    calls = []

    def fake_render(scene, targets, out, **k):
        calls.append(k)
        if render:
            render()
        Path(out).write_bytes(b"mp4")
        return Path(out)

    monkeypatch.setattr(jshort, "render_journey_short", fake_render)
    cfg = _cfg(enabled=True, per_day=40, fly_into_slots=list(slots), **{**LIVE, **kw})
    cfg.journey.depth = "brightness"
    res = shorts.run_shorts(cfg, count=2, data_dir=led_path.parent, output_dir=tmp_path / "out",
                            public=lambda v: True)
    return res, published, calls, led_path


def test_a_fly_into_slot_posts_a_fly_into_short_without_any_test_treatment(ledger, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    res, published, calls, led_path = _fly_run(monkeypatch, tmp_path, ledger)
    first, second = published
    assert first.title == "Flying into the Whirlpool Galaxy"
    assert first.tags[0] == fly.FLY_TAG
    for t in (shorts.HOOK_TAG, shorts.PUNCH_TAG, shorts.ANTIC_TAG, shorts.PLAIN_TITLE_TAG, sky.SKY_TAG):
        assert t not in first.tags
    assert fly.FLY_TAG not in second.tags                       # Short 8 is a normal one
    assert res[0].parent_slug == fly.FLY_PARENT and res[0].asset_id == "nasa:G1"
    assert "https://youtu.be/GAL8h" in first.description          # the picture's own long video
    assert res[1].asset_id != "nasa:G1"
    assert len(calls) == 1 and 20 <= calls[0]["seconds"] <= 30 and (calls[0]["width"], calls[0]["height"]) == (1080, 1920)
    assert calls[0]["overlay"] is not None and calls[0]["audio"] is not None
    assert "fly-into Short rendered in" in caplog.text
    with Ledger(led_path) as led:
        assert led.shorts_per_parent("chan").get(fly.FLY_PARENT) == 1
        assert shorts.fly_into_recent(led, "chan") == 1


def test_a_fly_into_slot_falls_back_to_a_normal_short_when_the_render_fails(ledger, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)

    def boom():
        raise RuntimeError("encoder died")

    res, published, calls, led_path = _fly_run(monkeypatch, tmp_path, ledger, render=boom)
    assert len(published) == 2 and fly.FLY_TAG not in published[0].tags
    assert "shorts: fly-into slot (Short 7) falls back to a normal Short: the render failed for nasa:G1 " \
           "(encoder died)" in caplog.text
    # the fallback is a normal Short 7, so it takes Short 7's treatments as usual
    # (the hook style needs a voice, and this run has none)
    assert {shorts.ANTIC_TAG, shorts.PLAIN_TITLE_TAG} <= set(published[0].tags)
    with Ledger(led_path) as led:
        assert fly.FLY_PARENT not in led.shorts_per_parent("chan")


def test_a_fly_into_slot_with_no_picture_falls_back_and_says_why(ledger, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    ledger.record_short("chan", "nasa:G1", "chan-2026-09-30", "used")     # the only galaxy is taken
    res, published, calls, _ = _fly_run(monkeypatch, tmp_path, ledger, posted_before=6)
    assert calls == [] and fly.FLY_TAG not in published[0].tags
    assert "fly-into slot (Short 7) falls back to a normal Short: no unused nebula or galaxy picture" in caplog.text


def test_a_fly_into_slot_falls_back_when_the_depth_step_fails(ledger, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    import romanfeed.journey.depth as depth

    def bad(*a, **k):
        raise ValueError("no depth")

    monkeypatch.setattr(depth, "estimate_depth", bad)
    res, published, calls, _ = _fly_run(monkeypatch, tmp_path, ledger)
    assert calls == [] and fly.FLY_TAG not in published[0].tags
    assert "falls back to a normal Short: the depth/scene step failed for nasa:G1 (no depth)" in caplog.text


def test_the_daily_cap_counts_fly_into_shorts_over_24_hours(ledger, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    for i in range(2):
        ledger.record_short("chan", f"nasa:F{i}", fly.FLY_PARENT, f"F{i}")
    res, published, calls, _ = _fly_run(monkeypatch, tmp_path, ledger, posted_before=5)
    assert calls == [] and fly.FLY_TAG not in published[0].tags
    assert "falls back to a normal Short: 2 fly-into Shorts in the last 24 hours, the cap of 2" in caplog.text


def test_without_opencv_the_slot_falls_back_rather_than_render_for_half_an_hour(ledger, monkeypatch, tmp_path,
                                                                                 caplog):
    caplog.set_level(logging.INFO)
    res, published, calls, _ = _fly_run(monkeypatch, tmp_path, ledger, opencv=False)
    assert calls == [] and len(published) == 2
    assert "falls back to a normal Short: OpenCV is not installed" in caplog.text


def test_outside_its_slots_and_dates_a_short_is_never_a_fly_into(ledger, monkeypatch, tmp_path):
    res, published, calls, _ = _fly_run(monkeypatch, tmp_path, ledger, slots=(3,))
    assert calls == [] and not any(fly.FLY_TAG in md.tags for md in published)


def test_the_preview_style_makes_one_whatever_the_slot(ledger, monkeypatch, tmp_path):
    led_path = ledger.db_path
    ledger.record_video_assets("chan-2026-09-30", [_galaxy(1)])
    ledger.close()
    _quiet_run(monkeypatch)
    seen = []
    monkeypatch.setattr(shorts, "_fly_into_short", lambda *a, **k: (seen.append(k["mode"]), (None, "skipped"))[1])
    monkeypatch.setattr(shorts, "recent_shorts", lambda *a, **k: [])
    shorts.run_shorts(_cfg(enabled=True, per_day=40), count=2, dry_run=True, style="fly-into",
                      data_dir=led_path.parent, output_dir=tmp_path / "out", public=lambda v: True)
    assert seen == ["dry-run"]          # the first Short only, and never an upload


# -- the weekly report ------------------------------------------------------------

def _rows():
    N = ss.ShortNumbers
    return [
        N("c0", "2026-11-20T10:00", "C0", "calm", False, 50, views=50),
        N("f1", "2026-11-21T18:00", "Flying into the Whirlpool Galaxy", "calm", False, 600, views=500, subs=5,
          fly=True, avg_view_pct=90),
        N("c1", "2026-11-21T20:00", "C1", "calm", False, 100, views=100, subs=1, avg_view_pct=60),
        N("h1", "2026-11-21T17:30", "H1", "hook", False, 300, views=300, subs=0, avg_view_pct=50, antic=True),
        N("s1", "2026-11-21T19:00", "Full supermoon", "calm", False, 400, views=400, subs=4, sky=True),
    ]


def test_the_report_compares_fly_into_with_calm_shorts():
    lines = ss.fly_lines(_rows())
    assert lines[0] == "Fly-into vs calm (Shorts since the first fly-into):"
    assert lines[1].startswith("  fly-into: 1 Shorts, 600 live views each, 90% watched, 5 subs (10.0 per 1,000 views)")
    assert lines[2].startswith("  calm: 1 Shorts, 100 live views each, 60% watched, 1 subs (10.0 per 1,000 views)")
    assert ss.fly_lines(_rows()[:1]) == []


def test_fly_into_shorts_stay_out_of_every_other_section():
    rows = _rows()
    report = ss.report_lines(rows, date(2026, 11, 20), date(2026, 11, 24))
    calm = next(ln for ln in report if ln.strip().startswith("calm ") and "Shorts" not in ln)
    assert calm.split()[1] == "2"                       # c0, c1: not the fly-into or sky Short
    assert any(ln.startswith("Fly-into vs calm") for ln in report)
    assert any(" fly " in ln and "Whirlpool" in ln for ln in report)
    assert ss.opening_lines(rows)[2].startswith("  plain: 1 Shorts, 100 live views each")   # c1 only
    assert ss.sky_lines(rows)[2].startswith("  calm: 1 Shorts")                             # c1, not f1
    with_title = rows + [ss.ShortNumbers("p1", "2026-11-21T17:00", "P1", "calm", False, 10, plain_title=True)]
    assert ss.title_lines(with_title)[2].startswith("  usual title: 2 Shorts")              # c1, h1
