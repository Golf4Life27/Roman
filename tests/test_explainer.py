from datetime import datetime, timezone

from romanfeed import explainer
from romanfeed.config import ShortsSettings
from romanfeed.shorts import Parent, _length, short_metadata
from romanfeed.sources.base import ImageAsset

# Built like the archives' captions: an answer sentence, then more about the object.
LONG = ("This image of the Horsehead Nebula was taken with Webb's NIRCam. The nebula formed from a collapsing "
        "interstellar cloud of material, and glows because it is illuminated by a nearby hot star. "
        "The gas clouds surrounding the Horsehead have already dissipated, but the jutting pillar is made of "
        "thicker clumps of material that are harder to erode. Astronomers estimate that the Horsehead has about "
        "five million years left before it too disintegrates. Webb's new view focuses on the illuminated edge "
        "of the top of the nebula's distinctive dust and gas structure. Credit: ESA/Webb, NASA, CSA.")


def _horse(description=LONG):
    return ImageAsset(asset_id="esawebb:weic2411a", title="Horsehead Nebula (NIRCam image)", url="u",
                      source="ESA/Webb", description=description, keywords=[])


def test_an_explainer_reads_the_answer_and_the_captions_next_sentences_word_for_word():
    w, body = explainer.find_explainer(_horse())
    assert w.question == "Why does the Horsehead Nebula glow?"
    assert body[0] == w.answer
    assert body[1].startswith("The gas clouds surrounding the Horsehead")
    assert all(s in LONG for s in body)
    words = len(" ".join([w.question, *body]).split())
    assert explainer.WORDS[0] <= words <= explainer.WORDS[1]
    script = explainer.explainer_script(_horse(), w, body, full_label="8-hour")
    assert script.startswith(w.question) and "The full 8-hour version is on the channel." in script


def test_a_caption_with_one_answer_sentence_is_no_explainer():
    short = ("The nebula formed from a collapsing interstellar cloud of material, and glows because it is "
             "illuminated by a nearby hot star.")
    assert explainer.find_explainer(_horse(short)) is None


def test_one_explainer_on_each_date_and_none_on_other_days():
    s = ShortsSettings(explainer_dates=["2026-11-21", "2026-11-22", "2026-11-23"])
    on = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
    assert explainer.explainer_due(s, set(), on)
    assert not explainer.explainer_due(s, {"2026-11-21"}, on)
    assert not explainer.explainer_due(s, set(), datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc))
    # 03:40 UTC on Nov 22 is still Nov 21 in Central time
    assert explainer._central_day(datetime(2026, 11, 22, 3, 40, tzinfo=timezone.utc)) == "2026-11-21"
    assert not explainer.explainer_due(ShortsSettings(), set(), on)


def test_explainer_upload_is_tagged_apart_and_carries_the_synthetic_disclosure():
    from tests.test_shorts import _cfg

    sample_cfg, sample_parent = _cfg(), Parent("p", "VID8h", 28800, "")
    w, body = explainer.find_explainer(_horse())
    md = short_metadata(sample_cfg, _horse(), sample_parent, [], script="x", explainer=w)
    assert md.synthetic is True
    assert md.tags[0] == explainer.EXPLAINER_TAG and "why-caption" not in md.tags
    assert md.title.startswith("Why does the Horsehead Nebula glow?")
    plain = short_metadata(sample_cfg, _horse(), sample_parent, [], script="x")
    assert plain.synthetic is False and explainer.EXPLAINER_TAG not in plain.tags


def test_an_explainer_runs_under_a_minute():
    assert _length(70.0, explainer.MAX_SECONDS) == explainer.MAX_SECONDS < 60
    assert _length(70.0) == 45.0
