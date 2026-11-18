"""Sky-event Shorts: verified dates only, balanced slots, a logged fallback."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from itertools import product
from pathlib import Path

import pytest

from romanfeed import shorts
from romanfeed import sky_events as sky
from romanfeed.config import ShortsSettings, load_config
from romanfeed.publish import shorts_styles as ss
from romanfeed.state import Ledger

from test_shorts import _cfg, _quiet_run, ledger  # noqa: F401  (fixture)

UTC = timezone.utc
NOV18 = datetime(2026, 11, 18, 18, tzinfo=UTC)   # noon Central

ON = dict(sky_event_from="2026-11-18", sky_event_until="2026-11-25", sky_event_per_day=2)


# -- the data file ------------------------------------------------------------

def test_every_event_in_the_file_names_its_official_page_and_the_day_it_was_read():
    cat = sky.load()
    official = ("https://science.nasa.gov/", "https://www.nasa.gov/", "https://aa.usno.navy.mil/",
                "https://www.imo.net/", "https://www.amsmeteors.org/", "https://www.spacex.com/")
    assert cat.events
    for ev in cat.events:
        assert ev.sources and all(s.url.startswith(official) and s.read for s in ev.sources), ev.id
        assert ev.verified_on <= date(2026, 11, 18)
        assert ev.start.utcoffset() == timedelta(hours=-6), ev.id    # Central (standard time)
        assert ev.kind != "launch" or ev.max_age_days, f"{ev.id}: launches slip, so they go stale"
        assert ev.images and all(cat.pictures.get(k) for k in ev.images), ev.id
        assert date(2026, 11, 18) <= ev.start.date() <= date(2026, 12, 1)
    ids = [p.id for ps in cat.pictures.values() for p in ps]
    assert len(ids) == len(set(ids))


def test_a_file_without_a_source_page_is_refused(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("events:\n  - {id: x, kind: moon, title: t, kicker: k, when: w, look: l, say: s,"
                 " start: '2026-11-24T08:53:00-06:00', verified_on: '2026-11-18', sources: [], images: [moon]}\n")
    with pytest.raises(ValueError):
        sky.load(p)


def test_every_day_of_the_test_has_a_verified_event():
    cat = sky.load()
    for d in range(8):   # Nov 18-25, checked at noon and 5 PM Central
        for hour in (18, 23):
            now = datetime(2026, 11, 18 + d, hour, tzinfo=UTC)
            ok, why = sky.choose(cat, [], now, set())
            assert ok, (now, why)


# -- which events can be shown --------------------------------------------------

def _ev(**kw) -> sky.SkyEvent:
    base = dict(id="e", kind="moon", title="Full supermoon", kicker="LOOK UP", when="Tue, Nov 24 · at sunset",
                look="Look east.", say="Look east at sunset.", start=datetime(2026, 11, 24, 14, 53, tzinfo=UTC),
                end=datetime(2026, 11, 25, 12, tzinfo=UTC), verified_on=date(2026, 11, 18),
                sources=[sky.Source("NASA", "https://science.nasa.gov/x/")], images=["moon"])
    base.update(kw)
    return sky.SkyEvent(**base)


def test_events_more_than_a_week_ahead_or_over_are_not_shown():
    moon = _ev()
    assert moon.usable(NOV18) is None
    assert "7 days" in moon.usable(datetime(2026, 11, 17, 12, tzinfo=UTC))
    assert moon.usable(datetime(2026, 11, 25, 6, tzinfo=UTC)) is None      # the night of the full Moon
    assert "over" in moon.usable(datetime(2026, 11, 25, 13, tzinfo=UTC))


def test_a_launch_stops_before_liftoff_and_goes_stale_when_the_reading_is_old():
    launch = _ev(kind="launch", start=datetime(2026, 11, 23, 16, 4, tzinfo=UTC), end=None, max_age_days=5)
    assert launch.usable(datetime(2026, 11, 23, 14, tzinfo=UTC)) is None
    assert "passed" in launch.usable(datetime(2026, 11, 23, 15, 30, tzinfo=UTC))
    later = _ev(kind="launch", start=datetime(2026, 11, 29, 15, 47, tzinfo=UTC), end=None, max_age_days=5)
    assert later.usable(datetime(2026, 11, 23, 18, tzinfo=UTC)) is None
    assert "read the page again" in later.usable(datetime(2026, 11, 24, 18, tzinfo=UTC))


def test_choice_spreads_events_and_waits_a_day_before_repeating_one():
    pics = {"moon": [sky.Picture(f"M{i}", "Moon", "NASA") for i in range(3)]}
    a = _ev(id="a", start=datetime(2026, 11, 20, 12, tzinfo=UTC), end=datetime(2026, 11, 21, tzinfo=UTC))
    b = _ev(id="b")
    cat = sky.Catalogue([b, a], pics)
    assert [e.id for e in sky.choose(cat, [], NOV18, set())[0]] == ["a", "b"]          # soonest first
    old = (NOV18 - timedelta(hours=30)).isoformat()
    assert [e.id for e in sky.choose(cat, [("a", old)], NOV18, set())[0]] == ["b", "a"]   # fewest first
    fresh = (NOV18 - timedelta(hours=3)).isoformat()
    ok, _ = sky.choose(cat, [("b", old), ("a", fresh)], NOV18, set())
    assert [e.id for e in ok] == ["b"]
    ok, why = sky.choose(cat, [], NOV18, {"nasa:M0", "nasa:M1", "nasa:M2"})
    assert ok == [] and "every picture" in why


# -- slots: two a day, and the other tests stay even ---------------------------

def test_the_sky_slots_leave_every_other_test_balanced():
    s = ShortsSettings(hook_every=2, **ON)
    slots = [n for n in range(160) if sky.slot_due(s, n, NOV18)]
    assert len(slots) == 40                            # 4 in 16: 2 a day at 8 a day
    rest = [n for n in range(160) if n not in slots]
    bits = {"hook": lambda n: n % 2, "opening": lambda n: (n // 2) % 2, "title": lambda n: (n // 4) % 2}
    # each pair of tests: the four combinations stay equal among the normal Shorts
    for x, y in (("hook", "opening"), ("opening", "title"), ("hook", "title")):
        cells = [sum(1 for n in rest if (bits[x](n), bits[y](n)) == c) for c in product((0, 1), repeat=2)]
        assert len(set(cells)) == 1, (x, y, cells)
    assert not any(sky.slot_due(s, n, datetime(2026, 11, 26, 18, tzinfo=UTC)) for n in range(16))
    assert not any(sky.slot_due(ShortsSettings(), n, NOV18) for n in range(16))


def test_the_channel_config_runs_the_test_nov_18_to_25():
    s = load_config("config/channels/deep-space-ambient.yaml").shorts
    assert (s.sky_event_from, s.sky_event_until, s.sky_event_per_day) == ("2026-11-18", "2026-11-25", 2)
    assert sorted(s.sky_event_slots) == [0, 6, 11, 13]


# -- the Short itself -----------------------------------------------------------

def test_metadata_tags_the_short_and_links_the_page_that_confirms_the_date():
    cat = sky.load()
    moon = next(e for e in cat.events if e.id == "full-supermoon-2026-11-24")
    md = sky.metadata(_cfg(), moon, cat.pictures["moon"][0], link_id="VID8h", link_label="8-hour", tracks=[])
    assert md.tags[0] == sky.SKY_TAG
    for t in (shorts.HOOK_TAG, shorts.PUNCH_TAG, shorts.ANTIC_TAG, shorts.PLAIN_TITLE_TAG, "explainer", "why"):
        assert t not in md.tags
    assert md.title == "Full supermoon, Tue, Nov 24 | This Week in the Sky"
    assert "Date confirmed: NASA Science: What's Up, November 2026: https://science.nasa.gov/" in md.description
    assert "(read 2026-11-18)" in md.description
    assert "https://youtu.be/VID8h" in md.description and "sub_confirmation=1" in md.description
    launch = next(e for e in cat.events if e.kind == "launch")
    md = sky.metadata(_cfg(), launch, cat.pictures["launch_night"][0], link_id=None, link_label="8-hour", tracks=[])
    assert "https://www.spacex.com/launches/" in md.description and "Launch times often change" in md.description
    assert "The photo shows a 2021 Falcon 9 launch" in md.description
    for e in cat.events:
        assert len(sky.metadata(_cfg(), e, cat.pictures[e.images[0]][0], link_id="v", link_label="8-hour",
                                tracks=[]).title) <= 100


def test_the_event_card_states_the_event_and_keeps_out_of_the_players_bottom_fifth():
    cat = sky.load()
    ev = cat.events[0]
    for low in (False, True):
        im = sky.event_card(ev, 1080, 1920, low=low)
        assert im.size == (1080, 1920)
        assert im.getchannel("A").getbbox() is not None
        assert im.crop((0, 1536, 1080, 1920)).getchannel("A").getbbox() is None
    # low: nothing over the upper third, where the Moon is
    assert sky.event_card(ev, 1080, 1920, low=True).crop((0, 0, 1080, 640)).getchannel("A").getbbox() is None
    credit = sky.credit_layer(cat.pictures["launch_night"][0], 1080, 1920)
    box = credit.getchannel("A").getbbox()
    assert box is not None and box[3] <= 1536


def test_the_card_moves_down_when_the_bright_subject_is_high(tmp_path):
    from PIL import Image, ImageDraw
    for name, cy, high in (("top.png", 300, True), ("bottom.png", 1500, False)):
        im = Image.new("RGB", (900, 1600), (10, 10, 20))
        ImageDraw.Draw(im).ellipse((350, cy - 100, 550, cy + 100), fill=(250, 245, 230))
        im.save(tmp_path / name)
        assert sky.subject_high(tmp_path / name) is high
    Image.new("RGB", (900, 1600), (40, 40, 40)).save(tmp_path / "flat.png")
    assert sky.subject_high(tmp_path / "flat.png") is False
    assert sky.subject_high(None) is False


# -- in the run -------------------------------------------------------------------

def _sky_run(monkeypatch, tmp_path, ledger, cfg, count, now=NOV18):
    led_path = ledger.db_path
    ledger.close()
    _quiet_run(monkeypatch)
    monkeypatch.setattr(shorts, "recent_shorts", lambda *a, **k: [])
    published = []
    monkeypatch.setattr(shorts, "_render_cut", lambda *a, **kw: Path(kw.get("work", tmp_path)) / "x.mp4")
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: published.append(md) or f"YT{len(published)}")
    monkeypatch.setattr(sky.ImageAsset, "download", lambda self, d: Path(d) / "x.jpg")
    real = sky.slot_due
    monkeypatch.setattr(sky, "slot_due", lambda s, n, now_=None: real(s, n, now))
    real_pick = sky.pick
    monkeypatch.setattr(sky, "pick", lambda cat, posted, _now, used, cache: real_pick(cat, posted, now, used, cache))
    monkeypatch.setattr(sky, "event_card", lambda *a, **k: None)
    monkeypatch.setattr(sky, "credit_layer", lambda *a, **k: None)
    res = shorts.run_shorts(cfg, count=count, data_dir=led_path.parent, output_dir=tmp_path / "out",
                            public=lambda v: True)
    return res, published, led_path


def test_a_sky_slot_posts_a_sky_event_short_and_records_it(ledger, monkeypatch, tmp_path):
    cfg = _cfg(enabled=True, per_day=40, hook_every=2, **ON)
    res, published, led_path = _sky_run(monkeypatch, tmp_path, ledger, cfg, count=2)
    # Short 0 is a sky slot, Short 1 is not
    assert [sky.SKY_TAG in md.tags for md in published] == [True, False]
    assert res[0].parent_slug == sky.SKY_PARENT and res[0].asset_id.startswith("nasa:")
    assert published[0].title.startswith("SpaceX Falcon 9 launch: USSF-317")
    with Ledger(led_path) as led:
        assert [e for e, _ in led.sky_events_since("chan", "")] == ["falcon9-ussf-317"]
        assert res[0].asset_id in led.shorts_asset_ids("chan")
        assert led.shorts_per_parent("chan").get(sky.SKY_PARENT) == 1


def test_a_sky_slot_with_no_event_falls_back_to_a_normal_short_and_says_why(ledger, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    late = datetime(2026, 12, 2, 18, tzinfo=UTC)   # nothing in the file is ahead any more
    res, published, _ = _sky_run(monkeypatch, tmp_path, ledger,
                                 _cfg(enabled=True, per_day=40, hook_every=2, sky_event_from="2026-11-18",
                                      sky_event_until="2026-12-31", sky_event_per_day=2), count=1, now=late)
    assert len(published) == 1 and sky.SKY_TAG not in published[0].tags
    assert "falls back to a normal Short: no verified sky event in the next 7 days" in caplog.text


def test_the_daily_cap_counts_sky_shorts_over_24_hours(ledger, monkeypatch, tmp_path, caplog):
    for i in range(2):
        ledger.record_sky_event("chan", f"nasa:S{i}", "full-supermoon-2026-11-24", f"Y{i}")
    caplog.set_level(logging.INFO)
    cfg = _cfg(enabled=True, per_day=40, hook_every=2, **ON)
    res, published, _ = _sky_run(monkeypatch, tmp_path, ledger, cfg, count=1)
    assert sky.SKY_TAG not in published[0].tags
    assert "2 sky-event Shorts in the last 24 hours" in caplog.text


def test_the_ledger_merge_carries_sky_events(tmp_path):
    with Ledger(tmp_path / "a.db") as a:
        a.record_sky_event("chan", "nasa:M1", "full-supermoon-2026-11-24", "Y1")
    with Ledger(tmp_path / "b.db") as b:
        assert b.merge_from(tmp_path / "a.db").get("sky_events") == 1
        assert b.sky_events_since("chan", "")[0][0] == "full-supermoon-2026-11-24"


# -- the weekly report ------------------------------------------------------------

def _rows():
    N = ss.ShortNumbers
    return [
        N("c0", "2026-11-17T10:00", "C0", "calm", False, 50, views=50),
        N("s1", "2026-11-18T18:00", "Full supermoon", "calm", False, 400, views=400, subs=4, sky=True,
          avg_view_pct=70),
        N("c1", "2026-11-18T20:00", "C1", "calm", False, 100, views=100, subs=1, avg_view_pct=60),
        N("h1", "2026-11-18T17:30", "H1", "hook", False, 300, views=300, subs=0, avg_view_pct=50, punch=True),
        N("e1", "2026-11-21T17:00", "Why?", "calm", False, 900, views=900, subs=9, explainer=True),
    ]


def test_the_report_compares_sky_events_with_calm_shorts():
    lines = ss.sky_lines(_rows())
    assert lines[0] == "Sky-event vs calm (Shorts since the first sky-event):"
    assert lines[1].startswith("  sky-event: 1 Shorts, 400 live views each, 70% watched, 4 subs (10.0 per 1,000 views)")
    assert lines[2].startswith("  calm: 1 Shorts, 100 live views each, 60% watched, 1 subs (10.0 per 1,000 views)")
    assert ss.sky_lines(_rows()[:1]) == []


def test_sky_event_shorts_stay_out_of_every_other_section():
    rows = _rows()
    report = ss.report_lines(rows, date(2026, 11, 17), date(2026, 11, 22))
    calm = next(ln for ln in report if ln.strip().startswith("calm ") and "Shorts" not in ln)
    assert calm.split()[1] == "3"                       # c0, c1, e1: not the sky Short
    assert any(ln.startswith("Sky-event vs calm") for ln in report)
    assert any(" sky " in ln and "Full supermoon" in ln for ln in report)
    plain = ss.opening_lines(rows)
    assert plain[2].startswith("  plain: 1 Shorts, 100 live views each")   # c1 only
    assert ss.explainer_lines(rows)[2].startswith("  regular: 0 Shorts")
    with_title = rows + [ss.ShortNumbers("p1", "2026-11-18T17:00", "P1", "calm", False, 10, plain_title=True)]
    assert ss.title_lines(with_title)[2].startswith("  usual title: 2 Shorts")  # c1, h1
