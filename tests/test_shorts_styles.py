from datetime import date

from romanfeed.publish import shorts_styles as ss
from romanfeed.state import Ledger


class _Exec:
    def __init__(self, val):
        self.val = val

    def execute(self):
        return self.val


class _YT:
    def __init__(self, items):
        self.items = items

    def videos(self):
        return self

    def list(self, part, id):
        want = id.split(",")
        return _Exec({"items": [i for i in self.items if i["id"] in want]})


class _YTA:
    def __init__(self, rows):
        self.rows = rows

    def reports(self):
        return self

    def query(self, **kw):
        assert kw["dimensions"] == "video"
        return _Exec({"columnHeaders": [{"name": n} for n in ("video", "views", "averageViewDuration", "subscribersGained")],
                      "rows": self.rows})


def _item(vid, hook, views):
    return {"id": vid, "snippet": {"title": f"T {vid}", "tags": ["space", "Hook-Style"] if hook else ["space"]},
            "statistics": {"viewCount": str(views)}}


def test_collect_splits_by_tag_and_reads_analytics(tmp_path):
    with Ledger(tmp_path / "state.db") as led:
        for n, vid in enumerate(["H1", "C1", "H2", "GONE"]):
            led.record_short("chan", f"nasa:A{n}", "p", vid)
        led.record_short("chan", "nasa:A9", "p", None)  # a dry run: not counted
        yt = _YT([_item("H1", True, 900), _item("C1", False, 300), _item("H2", True, 100)])
        yta = _YTA([["H1", 800, 12, 3], ["C1", 250, 20, 1]])
        rows = ss.collect(yt, yta, led, "chan", date(2026, 1, 1), date(2026, 12, 31))
    by = {r.video_id: r for r in rows}
    assert set(by) == {"H1", "C1", "H2"}  # deleted or private ones drop out
    assert by["H1"].style == "hook" and by["C1"].style == "calm"
    assert (by["H1"].views, by["H1"].subs, by["H2"].views) == (800, 3, 0)
    hook = ss.totals(rows, "hook")
    assert (hook.shorts, hook.public_views, hook.subs) == (2, 1000, 3)
    lines = ss.report_lines(rows, date(2026, 1, 1), date(2026, 12, 31))
    assert any("Reading: too early" in line for line in lines)


def _t(style, shorts, views, subs):
    return ss.StyleTotals(style, shorts, views, views, subs, 10.0)


def test_verdict_waits_for_enough_shorts_and_subscribers():
    assert ss.verdict(_t("hook", 5, 5000, 20), _t("calm", 9, 5000, 2)).startswith("too early")
    assert ss.verdict(_t("hook", 9, 5000, 5), _t("calm", 9, 5000, 3)).startswith("inside noise")
    assert ss.verdict(_t("hook", 9, 5000, 12), _t("calm", 9, 5000, 6)).startswith("hook wins")
    assert ss.verdict(_t("hook", 9, 5000, 6), _t("calm", 9, 5000, 12)).startswith("calm wins")
    assert ss.verdict(_t("hook", 9, 5000, 6), _t("calm", 9, 5000, 7)).startswith("inside noise")


def test_the_why_test_is_split_by_style_and_subject():
    rows = [
        ss.ShortNumbers("a", "2026-11-07", "Why does X glow red?", "calm", False, 300, subs=2, why=True, subject="nebula"),
        ss.ShortNumbers("b", "2026-11-07", "Why do Y glow?", "hook", False, 100, subs=0, why=True, subject="galaxy"),
        ss.ShortNumbers("c", "2026-11-07", "Z | Calm", "calm", False, 200, subs=1, subject="nebula"),
    ]
    lines = ss.why_lines(rows)
    assert lines[0].startswith("Why vs standard")
    assert "  why: 2 Shorts, 200 live views each, 2 subs (1.00 per Short)" in lines
    assert "  standard: 1 Shorts, 200 live views each, 1 subs (1.00 per Short)" in lines
    assert any(l.startswith("  nebula why: 1 Shorts") for l in lines)
    assert ss.why_lines([rows[2]]) == []


def test_the_opening_test_compares_punch_in_with_plain_from_its_start():
    rows = [
        ss.ShortNumbers("old", "2026-11-12T10:00", "Old", "calm", False, 900, views=900, subs=9),
        ss.ShortNumbers("p1", "2026-11-13T20:00", "P1", "hook", False, 200, views=200, subs=2, punch=True,
                        avg_view_pct=80),
        ss.ShortNumbers("q1", "2026-11-13T21:00", "Q1", "calm", False, 100, views=100, subs=0, avg_view_pct=50),
    ]
    lines = ss.opening_lines(rows)
    assert lines[1] == "  punch-in: 1 Shorts, 200 live views each, 80% watched, 2 subs (10.0 per 1,000 views)"
    assert lines[2] == "  plain: 1 Shorts, 100 live views each, 50% watched, 0 subs (0.0 per 1,000 views)"
    assert ss.opening_lines(rows[:1]) == []


def test_collect_reads_the_punch_in_tag(tmp_path):
    with Ledger(tmp_path / "state.db") as led:
        led.record_short("chan", "nasa:A0", "p", "P1")
        item = {"id": "P1", "snippet": {"title": "T", "tags": ["punch-in-open", "space"]},
                "statistics": {"viewCount": "5"}}
        rows = ss.collect(_YT([item]), _YTA([]), led, "chan", date(2026, 1, 1), date(2026, 12, 31))
    assert rows[0].punch is True
