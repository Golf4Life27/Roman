"""The September winner format for long videos (romanfeed/winner.py)."""
from datetime import date
from pathlib import Path

from romanfeed import winner
from romanfeed.audio.library import Track
from romanfeed.config import load_config
from romanfeed.publish.metadata import build_metadata
from romanfeed.sources.base import ImageAsset

ROOT = Path(__file__).resolve().parents[1]
TRACK = [Track("t", Path("t.m4a"), "ambient", "owned", title="Drift")]


def _cfg():
    return load_config(ROOT / "config/channels/deep-space-ambient.yaml")


def _a(i, title, kw=()):
    return ImageAsset(asset_id=f"nasa:{i}", title=title, url="", source="NASA Image Library",
                      credit="NASA/ESA/CSA/STScI", keywords=list(kw))


def test_days_take_the_themes_in_turn_then_stop():
    cfg = _cfg()
    days = {d: winner.plan_for(cfg, date(2026, 11, d)) for d in (18, 19, 20, 21, 22, 24)}
    assert days[18] is None and days[24] is None
    assert [days[d].theme for d in (19, 20, 21, 22)] == ["nebula", "galaxy", "nebula", "galaxy"]
    assert days[19].subject == "Nebulae" and days[20].subject == "Galaxies"


def test_the_remake_day_names_the_old_winner():
    plan = winner.plan_for(_cfg(), date(2026, 11, 23))
    assert plan.remake and plan.theme == "nebula" and plan.lead == "Cosmic Cloudscapes"


def test_only_pictures_of_one_theme():
    neb, gal = _a(1, "Carina Nebula", ["nebula"]), _a(2, "Sombrero Galaxy", ["galaxy"])
    both = _a(3, "A nebula in a nearby galaxy")
    assert winner.matches(neb, "nebula") and not winner.matches(gal, "nebula")
    assert not winner.matches(both, "nebula") and not winner.matches(both, "galaxy")


def test_too_few_fresh_pictures_falls_back():
    pics = [_a(i, f"Nebula {i}", ["nebula"]) for i in range(10)]
    assert winner.narrow(pics, "nebula", set(), 6) is not None
    assert winner.narrow(pics, "nebula", {p.asset_id for p in pics[:5]}, 6) is None


def test_titles_keep_the_september_tail_and_shorten_the_name():
    cfg = _cfg()
    plan = winner.plan_for(cfg, date(2026, 11, 19))
    long_name = "Cosmic Cliffs in the Carina Nebula seen in near infrared light by the James Webb Space Telescope"
    assets = [_a(0, long_name, ["nebula"])] + [_a(i, f"Nebula {i}", ["nebula"]) for i in range(1, 80)]
    meta = build_metadata(cfg, assets, TRACK, seconds_per_image=45, when=date(2026, 11, 19), winner=plan)
    assert meta.title.endswith(" | Nebulae | 1 Hour Relaxing Space Video for Sleep | Telescope Screensaver")
    assert meta.title.startswith("Cosmic Cliffs") and len(meta.title) <= 100 and " by " not in meta.title
    cut = build_metadata(cfg, assets, TRACK, seconds_per_image=45, total_seconds=8 * 3600, sleep=True,
                         dark_after=2700, when=date(2026, 11, 19), winner=plan)
    assert "| Nebulae | 8 Hours Relaxing Space Video for Sleep | Telescope Screensaver" in cut.title


def test_the_remake_title():
    cfg = _cfg()
    plan = winner.plan_for(cfg, date(2026, 11, 23))
    assets = [_a(i, f"Lagoon Nebula detail {i}", ["nebula"]) for i in range(80)]
    meta = build_metadata(cfg, assets, TRACK, seconds_per_image=45, total_seconds=8 * 3600, sleep=True,
                          dark_after=2700, when=date(2026, 11, 23), winner=plan)
    assert meta.title == "Cosmic Cloudscapes | Nebulae | 8 Hours Relaxing Space Video for Sleep | Telescope Screensaver"


def test_a_galaxy_set_with_one_stray_word_is_still_titled_galaxies():
    cfg = _cfg()
    plan = winner.plan_for(cfg, date(2026, 11, 20))
    assets = [_a(i, f"Spiral galaxy NGC {i}", ["galaxy", "cluster"]) for i in range(80)]
    meta = build_metadata(cfg, assets, TRACK, seconds_per_image=45, when=date(2026, 11, 20), winner=plan)
    assert "| Galaxies |" in meta.title
