from datetime import datetime, timezone

from romanfeed import shorts, why
from romanfeed.config import ShortsSettings
from romanfeed.sources.base import ImageAsset


def _a(asset_id="esa:heic0910h", title="The Cat's Head Nebula", description="", source="ESA/Hubble", keywords=()):
    return ImageAsset(asset_id=asset_id, title=title, url="u", source=source, description=description,
                      keywords=list(keywords))


RED = ("The Cat's Head Nebula is a vast cloud of gas and dust in the southern sky. "
       "Its red glow comes from hydrogen gas energised by the intense light of young stars nearby.")


def test_a_why_is_asked_only_when_the_caption_says_the_cause():
    w = why.find_why(_a(description=RED), "the Cat's Head Nebula")
    assert w.question == "Why does the Cat's Head Nebula glow red?"
    assert w.answer.startswith("Its red glow comes from hydrogen")
    assert w.url == "https://esahubble.org/images/heic0910h/" and w.source == "ESA/Hubble"
    # red and a glow in one sentence, but no cause given: no question
    bare = "The nebula has a striking red glow and sits about 7,000 light-years away in Carina."
    assert why.find_why(_a(description=bare), "the Cat's Head Nebula") is None
    # a red star elsewhere in a causal sentence does not make the nebula glow red
    s = "The nebula glows faintly because a red giant star at its edge heats the surrounding gas slowly."
    assert why.question_for(s, "NGC 1234") == "Why does NGC 1234 glow?"


def test_other_why_questions_come_from_the_captions_own_words():
    dust = "Dark lanes of cosmic dust block the light from the stars behind them in this galaxy's disc."
    assert why.question_for(dust, "Messier 64") == "What makes the dark patches in Messier 64?"
    shaped = "The pillars were sculpted by powerful winds and radiation from massive stars in the region."
    assert why.question_for(shaped, "the Pillars of Creation") == "What shaped the Pillars of Creation?"
    birth = "Shock waves from the collision compressed the gas and triggered a burst of star formation here."
    assert why.question_for(birth, "the Antennae Galaxies") == "Why are new stars forming in the Antennae Galaxies?"
    assert why.question_for("It glows red due to hydrogen gas lit by stars.", "the Pillars of Creation") == \
        "Why do the Pillars of Creation glow red?"
    assert why.question_for("This spiral galaxy lies about 30 million light-years away.", "Messier 81") is None


def test_only_primary_archives_are_cited():
    assert why.source_page(_a(asset_id="esawebb:weic2205a")) == "https://esawebb.org/images/weic2205a/"
    assert why.source_page(_a(asset_id="nasa:PIA12175")) == "https://images.nasa.gov/details/PIA12175"
    assert why.source_page(_a(asset_id="commons:123")) is None
    assert why.find_why(_a(asset_id="commons:123", description=RED), "the Cat's Head Nebula") is None


def test_why_shorts_split_evenly_across_hook_and_calm_inside_the_window():
    s = ShortsSettings(hook_every=2, why_every=2, why_until="2026-11-13")
    now = datetime(2026, 11, 8, 18, tzinfo=timezone.utc)
    due = [why.why_due(s, n, now) for n in range(8)]
    assert due == [False, False, True, True, False, False, True, True]
    hook = [n % 2 == 1 for n in range(8)]
    assert sum(1 for d, h in zip(due, hook) if d and h) == sum(1 for d, h in zip(due, hook) if d and not h) == 2
    after = datetime(2026, 11, 14, 18, tzinfo=timezone.utc)
    assert not any(why.why_due(s, n, after) for n in range(8))
    assert not any(why.why_due(ShortsSettings(hook_every=2), n, now) for n in range(8))


def test_subjects():
    assert why.subject(_a(title="The Cat's Head Nebula")) == "nebula"
    assert why.subject(_a(title="Messier 81", description="This spiral galaxy is bright.")) == "galaxy"
    assert why.subject(_a(title="Messier 15", description="A globular cluster of old stars.")) == "cluster"
    assert why.subject(_a(title="Jupiter's aurora", description="Light show.")) == "other"


def test_a_why_short_titles_on_its_question_and_cites_its_source():
    from romanfeed.config import AudioSettings, ChannelConfig, ChannelInfo, PublishSettings, VideoSettings

    def _cfg():
        return ChannelConfig(channel=ChannelInfo(slug="chan", name="Space Screens", handle="@SpaceScreens"),
                             video=VideoSettings(), sources=[], audio=AudioSettings(allow_placeholder=True),
                             publish=PublishSettings(mode="upload", privacy="public"), shorts=ShortsSettings())
    a = _a(description=RED)
    w = why.find_why(a, shorts.short_lead(a))
    script = why.why_script(a, w, full_label="8-hour")
    md = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), [], script=script, why=w)
    assert md.title.startswith("Why does the Cat's Head Nebula glow red?")
    assert "Why, in ESA/Hubble's own words: https://esahubble.org/images/heic0910h/" in md.description
    assert md.tags[0] == why.WHY_TAG
    plain = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), [])
    assert not plain.title.startswith("Why") and why.WHY_TAG not in plain.tags
