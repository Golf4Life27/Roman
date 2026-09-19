"""Repairing the descriptions of videos that are already on the channel.

Runs #16-#19 went out with every chapter, credit and music line wrapped in a
Python bytes repr, because ESA's export serialises text as bytes. The source
was fixed (PR #12) but those uploads still read `0:00 b'Carina Nebula'`, so
the repair has to work on the published text, not on the assets.
"""
import pytest

from romanfeed.publish.fixup import (
    DEFAULT_PUBLISH_AT,
    fix_descriptions,
    publish_times,
    repair_text,
    rfc3339_utc,
    schedule_body,
    snippet_update_body,
)

BROKEN = """8 hours of drifting space imagery.

IN THIS VIDEO
0:00 b'NGC 3324 (NIRCam Image)'
45:00 b'Westerlund 2 \\xe2\\x80\\x93 Hubble'
1:30:00 b"Hubble's view of Jupiter"

IMAGE CREDITS
- b'ESA/Hubble & NASA, A. Riess'
- NASA/ESA/CSA/STScI

MUSIC
- Drift
"""

CLEAN = """8 hours of drifting space imagery.

IN THIS VIDEO
0:00 NGC 3324 (NIRCam Image)
45:00 Westerlund 2 – Hubble
1:30:00 Hubble's view of Jupiter

IMAGE CREDITS
- ESA/Hubble & NASA, A. Riess
- NASA/ESA/CSA/STScI

MUSIC
- Drift
"""


def test_repair_decodes_every_chapter_and_credit_line():
    assert repair_text(BROKEN) == CLEAN


def test_repair_decodes_escaped_utf8_inside_a_chapter_line():
    """The clipped titles carry raw \\xNN escapes, not the real characters."""
    line = r"45:00 b'Westerlund 2 \xe2\x80\x93 Hubble'"
    assert repair_text(line) == "45:00 Westerlund 2 – Hubble"
    assert repair_text(r"12:00 b'Abell\xe2\x80\x99s richest cluster'") == (
        "12:00 Abell’s richest cluster")


def test_repair_leaves_a_clean_description_alone():
    """A description with no reprs must come back byte-identical."""
    assert repair_text(CLEAN) == CLEAN
    # A word ending in b before an apostrophe is not the start of a repr.
    assert repair_text("0:00 Webb's first deep field, and Hubble's too") == (
        "0:00 Webb's first deep field, and Hubble's too")
    assert repair_text("") == ""


def test_repair_handles_a_repr_the_80_character_clip_truncated():
    """_clip_title cut the repr, not the title, so the closing quote can be gone."""
    assert repair_text("0:00 b'Westerlund 2 anniversary image of the cluster…") == (
        "0:00 Westerlund 2 anniversary image of the cluster…")
    # A clip landing mid-escape must not leave a replacement character behind.
    assert "�" not in repair_text(r"0:00 b'Abell\xe2\x80")


def test_snippet_body_resends_title_category_and_tags():
    """videos.update replaces the snippet; anything left out is wiped."""
    video = {
        "id": "6IKq7GaguBo",
        "snippet": {
            "title": "Carina Nebula | 8 Hours",
            "description": "0:00 b'NGC 3324 (NIRCam Image)'",
            "categoryId": "28",
            "tags": ["space", "sleep screen"],
            "defaultLanguage": "en",
            "thumbnails": {"default": {"url": "x"}},
        },
    }
    body = snippet_update_body(video)
    assert body["id"] == "6IKq7GaguBo"
    assert body["snippet"]["description"] == "0:00 NGC 3324 (NIRCam Image)"
    assert body["snippet"]["title"] == "Carina Nebula | 8 Hours"
    assert body["snippet"]["categoryId"] == "28"
    assert body["snippet"]["tags"] == ["space", "sleep screen"]
    assert body["snippet"]["defaultLanguage"] == "en"
    # Read-only fields must not be echoed back into the write.
    assert "thumbnails" not in body["snippet"]


def test_snippet_body_survives_a_video_with_no_tags():
    body = snippet_update_body({"id": "x", "snippet": {"title": "T", "description": "d", "categoryId": "28"}})
    assert body["snippet"]["tags"] == []
    assert "defaultLanguage" not in body["snippet"]


def test_schedule_body_keeps_it_private_until_publish_at():
    """private + publishAt is what makes YouTube flip it public on its own."""
    body = schedule_body("EckN-EHQ4iU", "2026-09-20T02:00:00Z")
    assert body == {
        "id": "EckN-EHQ4iU",
        "status": {
            "privacyStatus": "private",
            "publishAt": "2026-09-20T02:00:00Z",
            "selfDeclaredMadeForKids": False,
        },
    }


def test_schedule_body_normalises_an_offset_time_to_utc():
    body = schedule_body("x", "2026-09-19T21:00:00-05:00")
    assert body["status"]["publishAt"] == "2026-09-20T02:00:00Z"


def test_a_naive_publish_time_is_refused():
    """21:00 local and 21:00 UTC are five hours apart; guessing is not allowed."""
    with pytest.raises(ValueError):
        rfc3339_utc("2026-09-19T21:00:00")
    with pytest.raises(ValueError):
        rfc3339_utc("saturday evening")


def test_publish_times_default_to_the_two_recorded_slots():
    times = publish_times(["EckN-EHQ4iU", "BjYotE1uGog"], None)
    assert times == ["2026-09-20T02:00:00Z", "2026-09-20T02:05:00Z"]
    assert len(DEFAULT_PUBLISH_AT) == 2
    # One --at covers every video; a mismatched count is an error, not a guess.
    assert publish_times(["a", "b"], ["2026-09-20T03:00:00Z"]) == ["2026-09-20T03:00:00Z"] * 2
    with pytest.raises(ValueError):
        publish_times(["a", "b", "c"], None)
    with pytest.raises(ValueError):
        publish_times(["a", "b"], ["2026-09-20T03:00:00Z", "2026-09-20T04:00:00Z", "2026-09-20T05:00:00Z"])


class _FakeVideos:
    """Just enough of yt.videos() to prove what does and does not get sent."""

    def __init__(self, items):
        self.items = items
        self.updates = []

    def list(self, **kw):
        return _Call({"items": self.items})

    def update(self, **kw):
        self.updates.append(kw)
        return _Call({})


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _FakeYouTube:
    def __init__(self, items):
        self._videos = _FakeVideos(items)

    def videos(self):
        return self._videos


def _video():
    return {"id": "6IKq7GaguBo", "snippet": {
        "title": "Carina Nebula | 8 Hours", "description": "0:00 b'NGC 3324'",
        "categoryId": "28", "tags": ["space"]}}


def test_dry_run_writes_nothing(capsys):
    yt = _FakeYouTube([_video()])
    assert fix_descriptions(["6IKq7GaguBo"], dry_run=True, yt=yt) == 0
    assert yt.videos().updates == []
    out = capsys.readouterr().out
    assert "0:00 b'NGC 3324'" in out and "0:00 NGC 3324" in out


def test_a_real_run_sends_the_repaired_snippet(capsys):
    yt = _FakeYouTube([_video()])
    assert fix_descriptions(["6IKq7GaguBo"], dry_run=False, yt=yt) == 0
    (call,) = yt.videos().updates
    assert call["part"] == "snippet"
    assert call["body"]["snippet"]["description"] == "0:00 NGC 3324"
    assert call["body"]["snippet"]["title"] == "Carina Nebula | 8 Hours"


def test_an_already_clean_video_is_not_rewritten():
    video = _video()
    video["snippet"]["description"] = "0:00 NGC 3324"
    yt = _FakeYouTube([video])
    assert fix_descriptions(["6IKq7GaguBo"], dry_run=False, yt=yt) == 0
    assert yt.videos().updates == []


def test_an_unknown_id_is_reported_and_fails():
    yt = _FakeYouTube([])
    assert fix_descriptions(["nosuchid"], dry_run=False, yt=yt) == 1
    assert yt.videos().updates == []
