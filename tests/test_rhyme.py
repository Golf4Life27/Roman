"""Space rhyme Shorts for kids (test 23, romanfeed/rhyme.py)."""
from datetime import datetime, timezone
from pathlib import Path

from romanfeed import fly_into, reach, rhyme, scale, sky_events
from romanfeed.config import load_config
from romanfeed.publish import shorts_styles as st
from romanfeed.publish.shorts_styles import ShortNumbers
from romanfeed.shorts import end_card_due, is_hook
from romanfeed.sources.base import ImageAsset
from romanfeed.workshop import music

ROOT = Path(__file__).resolve().parents[1]
NOV26 = datetime(2026, 11, 26, 18, tzinfo=timezone.utc)
NOV29 = datetime(2026, 11, 29, 18, tzinfo=timezone.utc)


def _cfg():
    return load_config(ROOT / "config/channels/deep-space-ambient.yaml")


def _due(s, n, when):
    """What run_shorts asks: a rhyme slot that fly-into and sky-event aren't using."""
    return (rhyme.slot_due(s, n, 0, when) and not fly_into.slot_due(s, n, when)
            and not sky_events.slot_due(s, n, when))


def test_rhymes_take_calm_slots_only():
    s = _cfg().shorts
    assert [n for n in range(32) if _due(s, n, NOV26)] == [0, 16]          # fly-into keeps 4 to Nov 27
    assert [n for n in range(32) if _due(s, n, NOV29)] == [0, 4, 16, 20]
    for when in (NOV26, NOV29):
        for n in range(32):
            if _due(s, n, when):
                assert not is_hook(s, n)                                     # a calm Short is swapped
                assert not end_card_due(s, n, when)
                assert not scale.slot_due(s, n, 0, when) and not reach.slot_due(s, n, 0, when)


def test_three_in_all_and_only_inside_the_window():
    s = _cfg().shorts
    assert rhyme.slot_due(s, 0, 2, NOV26)
    assert not rhyme.slot_due(s, 0, 3, NOV26)                                # three posted: done
    assert not rhyme.slot_due(s, 0, 0, datetime(2026, 11, 25, 18, tzinfo=timezone.utc))  # sky-event's day
    assert not rhyme.slot_due(s, 0, 0, datetime(2026, 12, 11, 18, tzinfo=timezone.utc))
    assert s.rhyme_total == 3


def test_rhymes_go_out_in_order_once_each():
    assert rhyme.next_rhyme([]).id == "moon"
    assert rhyme.next_rhyme(["rhyme:moon"]).id == "nebula"
    assert rhyme.next_rhyme([rhyme.asset_id(r) for r in rhyme.RHYMES]) is None


def test_every_rhyme_is_four_lines_and_every_fact_has_a_nasa_page():
    assert len(rhyme.RHYMES) == 3
    for r in rhyme.RHYMES:
        assert len(r.lines) == 4 and all(r.lines)
        assert r.facts, r.id
        for f in r.facts:
            assert f.org in ("NASA", "ESA")
            assert f.url.startswith("https://") and (".nasa.gov/" in f.url or "esa.int/" in f.url)
            assert f.read.startswith("read Nov 25, 2026") and f.quote
    # the one number in any rhyme is the one the page gives
    moon = rhyme.RHYMES[0]
    assert "Twelve" in moon.text and "12 human beings" in rhyme.MOON_WALKERS.quote


def test_length_stays_between_12_and_15_seconds():
    assert rhyme.length(None) == rhyme.SECONDS
    assert rhyme.length(3.0) == 12.0
    assert rhyme.length(20.0) == 15.0
    assert rhyme.MIN_SECONDS <= rhyme.length(10.0) <= rhyme.MAX_SECONDS
    assert rhyme.voice_fits(11.0) and not rhyme.voice_fits(14.0)


def test_each_line_is_on_screen_while_it_is_read():
    r = rhyme.RHYMES[2]
    lines = rhyme.line_times(r, start=0.6, spoken=10.0)
    assert [t for _, _, t in lines] == list(r.lines)
    assert lines[0][0] == 0.6 and abs(lines[-1][1] - 10.6) < 0.01
    assert all(a[1] == b[0] for a, b in zip(lines, lines[1:]))


def _asset(title, *, source="NASA Image Library", credit="NASA, ESA", aid="nasa:x", description=""):
    return ImageAsset(asset_id=aid, title=title, url="", source=source, credit=credit, description=description)


def test_pictures_are_nasa_or_esa_and_show_what_the_rhyme_says():
    nebula, galaxy = rhyme.RHYMES[1], rhyme.RHYMES[2]
    assert rhyme.fits(nebula, _asset("Pillars of Creation, a star-forming region in the Eagle Nebula"))
    assert not rhyme.fits(nebula, _asset("Ring Nebula"))                     # no stars forming in it
    assert rhyme.fits(galaxy, _asset("Spiral galaxy NGC 1300"))
    assert not rhyme.fits(galaxy, _asset("Spiral galaxy NGC 1300", aid="commons:1", source="Wikimedia Commons",
                                         credit="NASA"))                     # never a Commons file
    assert not rhyme.fits(galaxy, _asset("Spiral galaxy", source="ESO", credit="ESO"))
    assert not rhyme.fits(nebula, _asset("Spiral galaxy NGC 1300"))


def test_metadata_is_made_for_kids_and_names_every_source():
    cfg = _cfg()
    r = rhyme.RHYMES[0]
    md = rhyme.metadata(cfg, r, _asset("Full Moon", credit="NASA/Bill Ingalls"))
    assert md.made_for_kids is True
    assert md.title == "Hello, Moon! A Space Rhyme for Kids"
    assert rhyme.RHYME_TAG in md.tags
    for f in r.facts:
        assert f.url in md.description
    assert "our own" in md.description
    assert cfg.publish.made_for_kids is False                                # only these three


def test_the_tune_has_no_sleigh_bells_and_the_workshop_tune_is_unchanged():
    with_bells, _ = music.render_pcm("space-rhyme-moon", target_s=10)
    without, _ = music.render_pcm("space-rhyme-moon", target_s=10, sleigh_bells=False)
    assert with_bells.shape == without.shape and not (with_bells == without).all()
    again, _ = music.render_pcm("space-rhyme-moon", target_s=10)
    assert (with_bells == again).all()


def _row(vid, posted, *, rhyme_=False, views=1000, subs=2, live=500, fly=False):
    return ShortNumbers(video_id=vid, posted=posted, title=vid, style="calm", earth=False, public_views=live,
                        views=views, subs=subs, rhyme=rhyme_, fly=fly)


def test_report_compares_against_the_usual_shorts_of_the_same_days():
    rows = [_row("a", "2026-11-25T10:00"), _row("r1", "2026-11-26T10:00", rhyme_=True, subs=1, live=300),
            _row("b", "2026-11-26T12:00"), _row("f", "2026-11-26T13:00", fly=True)]
    lines = st.rhyme_lines(rows)
    assert lines[0].startswith("Space rhymes (made for kids) vs the usual Shorts")
    assert "rhyme: 1 Shorts, 300 live views each" in lines[1] and "1.0 per 1,000" in lines[1]
    assert "usual: 1 Shorts" in lines[2]
    assert st.rhyme_lines([_row("a", "2026-11-25T10:00")]) == []
    assert any("Space rhymes" in ln for ln in st.report_lines(rows, NOV26.date(), NOV26.date()))
