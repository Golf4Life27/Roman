"""The history channel (romanfeed/history): picking, licences, scripts, captions."""
from __future__ import annotations

import json
import types
from datetime import date

from romanfeed.config import load_config
from romanfeed.history import events as ev
from romanfeed.history import script as sc
from romanfeed.history.run import post_text

DAY = date(2026, 10, 14)
YEAGER_SRC = ("Bell X-1: The X-1 aircraft #46-062, nicknamed Glamorous Glennis and flown by Chuck Yeager, was the first "
              "piloted airplane to exceed the speed of sound in level flight. The airplane was drop launched from the "
              "bomb bay of a B-29 and reached Mach 1.06 (700 miles per hour).")


def event(text="American test pilot Chuck Yeager became the first person to break the sound barrier.", year=1947):
    return ev.Event(year, text, True, [{"title": "Bell X-1", "extract": YEAGER_SRC.split(": ", 1)[1], "image": ""}])


def test_grim_and_divisive_events_are_skipped():
    assert ev.safe(event())
    for text in ["The Battle of Hastings is fought.", "A bomb kills 50 people.", "The ship sank off Cornwall.",
                 "The Smith Act trials convict eleven defendants.", "The 6th Congress of the Workers' Party concluded.",
                 "The From Hell letter, allegedly from Jack the Ripper, is sent.", "The Valencia flood kills 81."]:
        assert not ev.safe(event(text)), text
    # background in a linked article (Apollo 7 mentions the Apollo 1 fire) does not sink the event
    e = event("The first live television broadcast by astronauts in orbit is performed by the Apollo 7 crew.")
    e.pages[0]["extract"] = "after the fire that killed the three Apollo 1 astronauts"
    assert ev.safe(e)


def test_commons_file_names_and_main_image_choice():
    assert ev.commons_file("https://upload.wikimedia.org/wikipedia/commons/5/50/Bell_X-1_46-062_%28in_flight%29.jpg") \
        == "File:Bell X-1 46-062 (in flight).jpg"
    assert ev.commons_file("https://upload.wikimedia.org/wikipedia/commons/thumb/1/15/Roundhay_Garden_Scene_%281888%29"
                           "_7fps.webm/960px--Roundhay.webm.jpg") == "File:Roundhay Garden Scene (1888) 7fps.webm"
    assert ev.commons_file("https://upload.wikimedia.org/wikipedia/en/a/ab/Poster.jpg") is None   # en-wiki: non-free
    e = ev.Event(1888, "Le Prince filmed Roundhay Garden Scene (pictured), the earliest surviving film.", True, [
        {"title": "Louis Le Prince", "extract": "", "image": "https://upload.wikimedia.org/wikipedia/commons/b/ba/LP.png"},
        {"title": "Film", "extract": "", "image": "https://upload.wikimedia.org/wikipedia/commons/6/6d/Moon.jpg"},
        {"title": "Roundhay Garden Scene", "extract": "",
         "image": "https://upload.wikimedia.org/wikipedia/commons/1/15/Roundhay.jpg"}])
    assert ev.image_files(e) == ["File:Roundhay.jpg", "File:LP.png"]                 # pictured first, never "Film"


class FakeCommons:
    """Commons API answers for the licence check."""
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, params=None, timeout=None):
        return types.SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                     json=lambda: {"query": {"pages": self.pages}})


def page(title, lic, cats=(), width=2000, artist="NASA"):
    return {"title": title, "categories": [{"title": f"Category:{c}"} for c in cats],
            "imageinfo": [{"width": width, "url": "u", "thumburl": "t", "extmetadata": {
                "LicenseShortName": {"value": lic}, "Artist": {"value": f"<a>{artist}</a>"}}}]}


def test_only_worldwide_free_photos_pass():
    got = ev.licences(["a"], FakeCommons([
        page("File:X1.jpg", "Public domain", ["PD US Government"]),
        page("File:Pooh.jpg", "Public domain", ["PD US expired"]),            # still protected in the UK
        page("File:Ship.jpg", "CC BY-SA 4.0"),
        page("File:Flag of Utah.svg", "Public domain"),
        page("File:Tiny.jpg", "Public domain", width=300),
        page("File:Moon.jpg", "CC0"),
    ]))
    assert sorted(got) == ["File:Moon.jpg", "File:X1.jpg"] and got["File:X1.jpg"].credit == "NASA"


def test_the_fact_check_catches_what_the_source_never_says():
    src = event().source_text()
    good = ("October 14th, 1947. Chuck Yeager flew the Bell X-1, nicknamed Glamorous Glennis, to Mach 1.06, "
            "about 700 miles per hour, after a drop from a B-29. Follow for one day in history, every day.")
    assert sc.unsupported(good, src, day=DAY, year=1947) == []
    bad = good.replace("Follow", "Today it hangs in the Smithsonian. Follow").replace("700", "750")
    assert sc.unsupported(bad, src, day=DAY, year=1947) == ["750", "Smithsonian"]
    assert sc.date_line(date(2026, 10, 1), 1957) == "October 1st, 1957." and sc.ordinal(13) == "13th"


class FakeClaude:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = json.dumps(self.replies.pop(0))
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=text)])


def test_writer_keeps_a_checked_script_retries_once_then_falls_back():
    ok = {"script": "October 14th, 1947. Chuck Yeager flew the Bell X-1 to Mach 1.06, faster than sound in level "
                    "flight, after a drop from the bomb bay of a B-29. It was nicknamed Glamorous Glennis. "
                    "Follow for one day in history, every day.", "headline": "Faster than sound",
          "subtitle": "Chuck Yeager · Bell X-1"}
    s = sc.write(event(), DAY, client=FakeClaude([ok]))
    assert s.writer == "claude" and s.headline == "Faster than sound"
    invented = dict(ok, script=ok["script"].replace("It was", "In 1950 it was"))
    claude = FakeClaude([invented, ok])
    assert sc.write(event(), DAY, client=claude).writer == "claude"
    assert "1950" in claude.calls[1]["messages"][0]["content"]                      # told what was wrong
    assert claude.calls[0]["model"] == "claude-opus-5-5" and claude.calls[0]["fallbacks"] == "default"
    assert sc.write(event(), DAY, client=FakeClaude([invented, invented])).writer == "template"
    t = sc.write(event(), DAY, client=None)
    assert t.writer == "template" and t.text.startswith("October 14th, 1947.") and t.text.endswith(sc.FOLLOW)


def test_post_caption_credits_the_source_and_the_image():
    cfg = load_config("config/channels/this-day-in-history.yaml")
    e = event()
    e.image = ev.Image("File:X1.jpg", "u", "NASA", "Public domain")
    s = sc.Script("October 14th, 1947. Chuck Yeager broke the sound barrier. " + sc.FOLLOW, "Faster than sound",
                  "Chuck Yeager", "claude")
    text = post_text(cfg, e, s, DAY)
    assert text.startswith("October 14, 1947: Faster than sound")
    assert "Source: Wikipedia. Image: NASA (Public domain)." in text and "#onthisday" in text
    assert cfg.crosspost.mode == "off"                                               # the owner's switch
