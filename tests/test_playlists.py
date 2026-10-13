"""Channel playlists (romanfeed/publish/playlists.py)."""
from __future__ import annotations

import types

from romanfeed.publish import playlists as pl
from romanfeed.publish.playlists import Video

V = [
    Video("w1", "1 Hour Relaxing Space Music for Sleep · Pillars of Creation by Webb · Telescope Screensaver",
          "2026-10-01T03:00:00Z", 3600, False),
    Video("h1", "1 Hour Relaxing Space Music for Sleep · Crab Nebula by Hubble · Telescope Screensaver",
          "2026-10-03T03:00:00Z", 3600, False),
    Video("d1", "8 Hours Deep Sleep Music · Fall Asleep in Space · Crab Nebula · Dark Screen After 60 Min",
          "2026-10-03T04:00:00Z", 8 * 3600, False),
    Video("c1", "Cozy Halloween Cabin · 3 Hours Relaxing Space Music for Sleep · Halloween Ambience",
          "2026-10-10T18:00:00Z", 3 * 3600, False),
    Video("e1", "1 Hour Earth from Space for Sleep · The city lights of Osaka · Space Station Screensaver",
          "2026-10-16T03:00:00Z", 3600, False),
    Video("l1", "Space Screens Live · 10 Hours of Real Telescope Views & Calm Music for Sleep",
          "2026-10-08T02:00:00Z", 36000, True),
    Video("s1", "Crab Nebula #shorts", "2026-10-12T16:00:00Z", 40, False),
]


def members(title):
    rule = next(p.rule for p in pl.PLAYLISTS if p.title == title)
    return {v.id for v in V if rule(v)}


def test_each_video_lands_in_the_right_playlists():
    assert members("Space Videos for Sleep · Full Length") == {"w1", "h1", "d1"}
    assert members("James Webb Telescope Images for Sleep") == {"w1"}
    assert members("Hubble Telescope Images for Sleep") == {"h1"}
    assert members("Deep Sleep · Screen Goes Dark") == {"d1"}
    assert members("Cozy Space Nights") == {"c1"}
    assert members("Earth from Space for Sleep") == {"e1"}
    assert members("Live Stream Replays · 10 Hours for Sleep") == {"l1"}
    assert not any("s1" in members(p.title) for p in pl.PLAYLISTS)       # Shorts have their own shelf


def test_plan_keeps_newest_on_top():
    title = "Space Videos for Sleep · Full Length"
    first = pl.plan(V, {})[title]                                      # empty: append newest first
    assert [(v.id, pos) for v, pos in first] == [("d1", None), ("h1", None), ("w1", None)]
    later = pl.plan(V + [Video("w2", "1 Hour Relaxing Space Music for Sleep · M51 by Webb", "2026-10-20T03:00:00Z",
                               3600, False)], {title: ["d1", "h1"]})[title]
    assert [(v.id, pos) for v, pos in later] == [("w2", 0), ("w1", None)]


class FakeYT:
    def __init__(self, existing=None, sort_refused=False):
        self.existing, self.sort_refused, self.calls = existing or {}, sort_refused, []

    def _call(self, result=None, exc=None):
        return types.SimpleNamespace(execute=lambda: (_ for _ in ()).throw(exc) if exc else result)

    def channels(self):
        return types.SimpleNamespace(list=lambda **k: self._call(
            {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}))

    def videos(self):
        items = [{"id": v.id, "snippet": {"title": v.title, "publishedAt": v.published, "liveBroadcastContent": "none"},
                  "contentDetails": {"duration": f"PT{v.seconds}S"}, "status": {"privacyStatus": "public"},
                  **({"liveStreamingDetails": {}} if v.live else {})} for v in V]
        return types.SimpleNamespace(list=lambda **k: self._call({"items": [i for i in items if i["id"] in k["id"].split(",")]}))

    def playlists(self):
        def insert(**k):
            self.calls.append(("create", k["body"]["snippet"]["title"]))
            return self._call({"id": "PL" + str(len(self.calls))})
        return types.SimpleNamespace(
            list=lambda **k: self._call({"items": [{"id": i, "snippet": {"title": t}} for t, i in self.existing.items()]}),
            insert=insert)

    def playlistItems(self):
        def lst(**k):
            if k["playlistId"] == "UU":
                return self._call({"items": [{"contentDetails": {"videoId": v.id}} for v in V]})
            return self._call({"items": []})

        def insert(**k):
            sn = k["body"]["snippet"]
            if "position" in sn and self.sort_refused:
                return self._call(exc=RuntimeError("HttpError 400 manualSortRequired"))
            self.calls.append(("add", sn["playlistId"], sn["resourceId"]["videoId"], sn.get("position")))
            return self._call({})
        return types.SimpleNamespace(list=lst, insert=insert)


def test_sync_creates_playlists_and_respects_the_cap():
    yt = FakeYT()
    lines = pl.sync(yt, max_inserts=4)
    assert len([c for c in yt.calls if c[0] == "create"]) == 7
    assert len([c for c in yt.calls if c[0] == "add"]) == 4
    assert any("left for next run" in ln for ln in lines)


def test_dry_run_changes_nothing():
    yt = FakeYT()
    lines = pl.sync(yt, dry_run=True)
    assert yt.calls == [] and any(ln.startswith("would create") for ln in lines)


def test_refused_position_falls_back_to_append():
    yt = FakeYT(sort_refused=True)
    pl.add(yt, "PL1", "w2", 0)
    assert yt.calls == [("add", "PL1", "w2", None)]
