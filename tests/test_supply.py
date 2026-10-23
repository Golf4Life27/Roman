"""Days of long videos the unused images cover (romanfeed/supply.py)."""
from __future__ import annotations

from datetime import date

from romanfeed.supply import Supply, runway

SAT = date(2026, 10, 24)


def test_daily_space_only_runs_until_the_pool_is_short():
    r = runway(Supply(space_fresh=800, earth_fresh=0, space_suitable=800, earth_suitable=0),
               per_video=80, earth_weekdays=[], start=SAT)
    assert (r.days, r.space_days, r.earth_days) == (10, 10, 0)
    assert r.last_day == date(2026, 11, 2)


def test_earth_days_stretch_the_space_pool():
    s = Supply(space_fresh=400, earth_fresh=400, space_suitable=400, earth_suitable=400)
    r = runway(s, per_video=80, earth_weekdays=["tue", "thu", "sat"], start=SAT)
    # Earth days need 1.5 videos in hand, so the Earth pool gives 4 of its 5;
    # after that Earth days fall back to space, as the render does.
    assert r.earth_days == 4 and r.space_days == 5 and r.days == 9


def test_render_weekdays_skip_days():
    s = Supply(space_fresh=240, earth_fresh=0, space_suitable=240, earth_suitable=0)
    r = runway(s, per_video=80, earth_weekdays=[], start=SAT, render_weekdays=["mon", "wed", "fri"])
    assert r.days == 3 and r.last_day == date(2026, 10, 30)
