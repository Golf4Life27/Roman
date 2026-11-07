from datetime import datetime, timezone

from romanfeed import shorts, why
from romanfeed.config import ShortsSettings
from romanfeed.sources.base import ImageAsset


def _a(asset_id="esa:heic0910h", title="The Cat's Head Nebula", description="", source="ESA/Hubble", keywords=()):
    return ImageAsset(asset_id=asset_id, title=title, url="u", source=source, description=description,
                      keywords=list(keywords))


# Sentences below are from the archives' own captions (Nov 7 coverage run).
HORSE = ("This image of the Horsehead Nebula was taken with Webb's NIRCam. The nebula formed from a collapsing "
         "interstellar cloud of material, and glows because it is illuminated by a nearby hot star.")


def _horse(**kw):
    return _a(asset_id="esawebb:weic2411a", title="Horsehead Nebula (NIRCam image)", source="ESA/Webb",
              description=HORSE, **kw)


def test_a_why_is_asked_only_when_the_caption_says_the_cause():
    w = why.find_why(_horse())
    assert w.question == "Why does the Horsehead Nebula glow?"
    assert w.answer.startswith("The nebula formed from a collapsing")
    assert w.url == "https://esawebb.org/images/weic2411a/" and w.source == "ESA/Webb"
    # a glow but no cause given: no question
    bare = "The Horsehead Nebula has a striking red glow and sits about 1,300 light-years away in Orion."
    assert why.find_why(_a(asset_id="esawebb:weic2411a", title="Horsehead Nebula", description=bare)) is None
    # a guess is not an answer
    guess = "The interaction may have triggered the galaxy's recent burst of new stars, astronomers think."
    assert why.question_for(guess, "NGC 1234") is None


def test_the_question_names_the_object_not_the_press_headline():
    a = _a(asset_id="esa:heic0715a", title="Extreme star cluster bursts into life in new Hubble image",
           description="The star-forming region NGC 3603 contains one of the most impressive young star clusters. "
                       "The hot blue stars at the core are responsible for carving out a huge cavity in the gas "
                       "seen to the right of the star cluster in NGC 3603's centre.")
    assert why.object_name(a) == "NGC 3603"
    assert why.find_why(a).question == "What shaped NGC 3603?"
    # no name anywhere: the image is passed over rather than asked about badly
    vague = _a(title="Hubble portrays a dusty spiral galaxy", description="A galaxy glows because of its stars.")
    assert why.object_name(vague) is None and why.find_why(vague) is None


def test_other_why_questions_come_from_the_captions_own_words():
    cone = "Called the Cone Nebula - so named because in ground-based images it has a conical shape - this pillar sits in a busy region."
    assert why.question_for(cone, "the Cone Nebula") == "Why is it called the Cone Nebula?"
    # the name explained must be the object's, not its group's
    hickson = "The three galaxies NGC 7173 and friends are part of Hickson Compact Group 90, named after astronomer Paul Hickson."
    assert why.question_for(hickson, "NGC 7173") is None
    red = "The red colour that's most prevalent in this view shows light from hydrogen, while green and blue come from iron."
    assert why.question_for(red, "the Butterfly Nebula") == "Why is the Butterfly Nebula red?"
    dark = "Hubble separates the arms of the galaxy from dark patches of dust in between, which block out visible light."
    assert why.question_for(dark, "IC 5332") == "Why are there dark patches in IC 5332?"
    birth = "Shock waves from the collision compressed the gas and triggered a burst of star formation in the galaxies."
    assert why.question_for(birth, "the Antennae Galaxies") == "Why are new stars forming in the Antennae Galaxies?"
    assert why.question_for("This spiral galaxy lies about 30 million light-years away from us.", "Messier 81") is None


def test_only_primary_archives_are_cited():
    assert why.source_page(_a(asset_id="esawebb:weic2205a")) == "https://esawebb.org/images/weic2205a/"
    assert why.source_page(_a(asset_id="nasa:PIA12175")) == "https://images.nasa.gov/details/PIA12175"
    assert why.source_page(_a(asset_id="commons:123")) is None
    assert why.find_why(_a(asset_id="commons:123", title="Horsehead Nebula", description=HORSE)) is None


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
    a = _horse()
    w = why.find_why(a)
    script = why.why_script(a, w, full_label="8-hour")
    md = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), [], script=script, why=w)
    assert md.title.startswith("Why does the Horsehead Nebula glow?")
    assert "Why, in ESA/Webb's own words: https://esawebb.org/images/weic2411a/" in md.description
    assert md.tags[0] == why.WHY_TAG
    plain = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), [])
    assert not plain.title.startswith("Why") and why.WHY_TAG not in plain.tags
