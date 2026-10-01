"""The weekly stats email: pace math, the live-stream trigger, and delivery.

Everything runs on fake API objects shaped like googleapiclient's
(`.reports().query(...).execute()`), so no Google library is imported --
CI installs only the dev extra, and google.auth cannot load here anyway.
"""
from datetime import date, timedelta

import pytest

from romanfeed import cli
from romanfeed.publish import stats as st
from romanfeed.publish.stats import ChannelStats, Pace, Window

TODAY = date(2026, 10, 1)


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeYT:
    """Data API v3: channels.list (statistics) and videos.list (titles)."""

    def __init__(self, subs=3, videos=24, views=150):
        self.stats = {"subscriberCount": str(subs), "videoCount": str(videos), "viewCount": str(views)}

    def channels(self):
        return self

    def videos(self):
        return self

    def list(self, part, mine=None, id=None):
        if mine:
            return _Exec({"items": [{"statistics": self.stats}]})
        return _Exec({"items": [{"id": v, "snippet": {"title": f"Title {v}"}} for v in id.split(",")]})


class FakeYTA:
    """Analytics v2: answers by query shape, and records every query."""

    def __init__(self, last7_minutes=600, prev7_minutes=300, year_minutes=3000):
        self.queries = []
        self.last7_minutes, self.prev7_minutes, self.year_minutes = last7_minutes, prev7_minutes, year_minutes

    def reports(self):
        return self

    def query(self, **kw):
        self.queries.append(kw)
        dims = kw.get("dimensions")
        if dims == "day":
            start = date.fromisoformat(kw["startDate"])
            rows = [[(start + timedelta(days=i)).isoformat(), self.last7_minutes / 7] for i in range(7)]
            return _Exec({"columnHeaders": [{"name": "day"}, {"name": "estimatedMinutesWatched"}], "rows": rows})
        if dims == "video":
            return _Exec({"columnHeaders": [{"name": "video"}, {"name": "estimatedMinutesWatched"}, {"name": "views"}],
                          "rows": [["vidA", 300, 20], ["vidB", 120, 9]]})
        if kw["metrics"] == "estimatedMinutesWatched":
            return _Exec({"columnHeaders": [{"name": "estimatedMinutesWatched"}], "rows": [[self.year_minutes]]})
        is_last = kw["endDate"] == (TODAY - timedelta(days=2)).isoformat()
        minutes = self.last7_minutes if is_last else self.prev7_minutes
        names = ["views", "estimatedMinutesWatched", "subscribersGained", "subscribersLost", "averageViewDuration"]
        row = [40 if is_last else 20, minutes, 2 if is_last else 1, 1 if is_last else 0, 252]
        return _Exec({"columnHeaders": [{"name": n} for n in names], "rows": [row]})


def _stats(subs=3, hours_per_day=1.0, hours_12m=50.0) -> ChannelStats:
    end = TODAY - timedelta(days=2)
    last7 = Window(end - timedelta(days=6), end, views=40, minutes=hours_per_day * 7 * 60,
                   subs_gained=2, subs_lost=1, avg_view_duration_s=252)
    prev7 = Window(end - timedelta(days=13), end - timedelta(days=7), views=20, minutes=300, subs_gained=1)
    return ChannelStats(subscribers=subs, video_count=24, view_count=150, last7=last7, prev7=prev7,
                        watch_hours_12m=hours_12m, daily_hours=[(last7.start, hours_per_day)],
                        top_videos=[st.TopVideo("vidA", "8 Hours of Carina Nebula", 300, 20)])


# --- pace math ---------------------------------------------------------------

def test_days_left_to_goal_date():
    assert st.days_left(TODAY) == 122  # Oct 30 + Nov 30 + Dec 31 + Jan 31
    assert st.days_left(date(2027, 1, 31)) == 0
    assert st.days_left(date(2027, 3, 1)) == 0


def test_required_per_day():
    assert st.required_per_day(3, 1000, TODAY) == pytest.approx(997 / 122)
    assert st.required_per_day(50, 4000, TODAY) == pytest.approx(3950 / 122)
    # Past the deadline the whole remainder is due, never a ZeroDivisionError.
    assert st.required_per_day(3, 1000, date(2027, 2, 1)) == 997


def test_pace_verdicts():
    behind = Pace(current=3, goal=1000, days_left=122, actual_per_day=0.1)
    assert not behind.on_pace
    assert behind.verdict("subscribers") == f"BEHIND by {997 / 122 - 0.1:,.1f} subscribers/day"
    ahead = Pace(current=900, goal=1000, days_left=10, actual_per_day=12)
    assert ahead.on_pace and ahead.verdict("subscribers").startswith("ON PACE (ahead by 2.0")
    assert Pace(1200, 1000, 10, 0).verdict("subscribers") == "GOAL REACHED"


# --- live-stream trigger ----------------------------------------------------

def test_trigger_met_by_subscribers():
    met, line = st.live_trigger(_stats(subs=100, hours_per_day=1))
    assert met and line.startswith("TRIGGER MET: ask Claude to switch on the nightly stream")


def test_trigger_met_by_watch_hours():
    met, line = st.live_trigger(_stats(subs=3, hours_per_day=15))
    assert met and "15.0/15 watch hours/day" in line
    subject, text, html = st.report(_stats(subs=3, hours_per_day=15), TODAY)
    assert subject.startswith("LIVE STREAM TRIGGER MET")
    assert "TRIGGER MET: ask Claude to switch on the nightly stream" in text
    assert "TRIGGER MET" in html


def test_trigger_not_met():
    met, line = st.live_trigger(_stats(subs=99, hours_per_day=14.9))
    assert not met and line.startswith("Not met yet: 99/100 subscribers, 14.9/15")
    subject, text, _ = st.report(_stats(subs=99, hours_per_day=14.9), TODAY)
    assert "TRIGGER" not in subject and "TRIGGER MET" not in text


# --- fetching and the report -------------------------------------------------

def test_fetch_stats_reads_both_apis_with_lagged_windows():
    yta = FakeYTA()
    s = st.fetch_stats(TODAY, yt=FakeYT(subs=3), yta=yta)
    assert (s.subscribers, s.video_count, s.view_count) == (3, 24, 150)
    assert s.last7.end == date(2026, 9, 29) and s.last7.start == date(2026, 9, 23)
    assert s.prev7.end == date(2026, 9, 22) and s.prev7.start == date(2026, 9, 16)
    assert s.last7.watch_hours == pytest.approx(10) and s.prev7.watch_hours == pytest.approx(5)
    assert s.last7.net_subs == 1
    assert s.watch_hours_12m == pytest.approx(50)
    assert len(s.daily_hours) == 7 and s.daily_hours[0] == (date(2026, 9, 23), pytest.approx(10 / 7))
    assert [(v.video_id, v.title) for v in s.top_videos] == [("vidA", "Title vidA"), ("vidB", "Title vidB")]
    assert all(q["ids"] == "channel==MINE" for q in yta.queries)
    year = next(q for q in yta.queries if q["metrics"] == "estimatedMinutesWatched" and "dimensions" not in q)
    assert year["startDate"] == "2025-10-01"
    top = next(q for q in yta.queries if q.get("dimensions") == "video")
    assert top["sort"] == "-estimatedMinutesWatched" and top["maxResults"] == 5


def test_report_contains_key_numbers():
    subject, text, html = st.report(_stats(subs=3, hours_per_day=1.0, hours_12m=50.0), TODAY)
    assert subject == "Space Screens weekly: 3 subs (+1), 50.0 watch hrs"
    for needle in [
        "3 of 1,000",
        "50.0 of 4,000",
        "Days left: 122",
        f"Needed: {997 / 122:,.1f}/day",       # subscribers
        f"Needed: {3950 / 122:,.1f}/day",      # watch hours
        f"BEHIND by {997 / 122 - 1 / 7:,.1f} subscribers/day",
        f"BEHIND by {3950 / 122 - 1:,.1f} hours/day",
        "Views per subscriber: 50.0",
        "Not met yet: 3/100 subscribers",
        "1. 8 Hours of Carina Nebula: 5.0 h, 20 views",
        "lags 2-3 days",
        "Shorts",
    ]:
        assert needle in text, needle
    assert "<table" in html and "3 of 1,000" in html and "8 Hours of Carina Nebula" in html


def test_report_survives_zero_subscribers():
    _, text, _ = st.report(_stats(subs=0), TODAY)
    assert "Views per subscriber: n/a" in text


# --- email -------------------------------------------------------------------

class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, **kw):
        self.host, self.port, self.sent, self.logged_in = host, port, [], None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


def test_send_email_builds_multipart_alternative(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(st.smtplib, "SMTP_SSL", FakeSMTP)
    st.send_email("Subj", "plain body", "<p>html body</p>", to="owner@example.com",
                  smtp_host="smtp.gmail.com", smtp_port=465, user="bot@example.com", password="app-pw")
    (server,) = FakeSMTP.instances
    assert (server.host, server.port, server.logged_in) == ("smtp.gmail.com", 465, ("bot@example.com", "app-pw"))
    (msg,) = server.sent
    assert msg.get_content_type() == "multipart/alternative"
    assert msg["Subject"] == "Subj" and msg["To"] == "owner@example.com" and msg["From"] == "bot@example.com"
    parts = list(msg.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert "plain body" in parts[0].get_content() and "<p>html body</p>" in parts[1].get_content()


def test_send_email_falls_back_to_starttls_when_ssl_cannot_connect(monkeypatch):
    def refuse(*a, **kw):
        raise ConnectionRefusedError("465 blocked")

    class FakeStartTLS(FakeSMTP):
        def starttls(self, context=None):
            self.tls = True

    FakeSMTP.instances = []
    monkeypatch.setattr(st.smtplib, "SMTP_SSL", refuse)
    monkeypatch.setattr(st.smtplib, "SMTP", FakeStartTLS)
    st.send_email("s", "t", "<p>h</p>", to="a@example.com", smtp_host="smtp.gmail.com", smtp_port=465,
                  user="u@example.com", password="p")
    (server,) = FakeSMTP.instances
    assert server.port == 587 and server.tls and len(server.sent) == 1


# --- CLI ---------------------------------------------------------------------

def test_cli_email_without_smtp_settings_exits_2(monkeypatch, capsys):
    for k in ("STATS_EMAIL_TO", "SMTP_USER", "SMTP_PASSWORD", "SMTP_HOST", "SMTP_PORT"):
        monkeypatch.delenv(k, raising=False)

    def must_not_fetch(*a, **kw):
        raise AssertionError("fetched stats before checking SMTP settings")

    monkeypatch.setattr(st, "fetch_stats", must_not_fetch)
    assert cli.main(["stats", "--email"]) == 2
    out = capsys.readouterr().out
    assert "STATS_EMAIL_TO" in out and "SMTP_USER" in out and "SMTP_PASSWORD" in out


def test_cli_scope_error_exits_2_with_remint_instruction(monkeypatch, capsys):
    class Resp:
        status = 403

    class FakeHttpError(Exception):
        resp = Resp()

    def denied(today):
        raise FakeHttpError("Request had insufficient authentication scopes.")

    monkeypatch.setattr(st, "fetch_stats", denied)
    assert cli.main(["stats"]) == 2
    assert "romanfeed auth --scope manage" in capsys.readouterr().out


def test_cli_prints_report(monkeypatch, capsys):
    monkeypatch.setattr(st, "fetch_stats", lambda today: _stats())
    assert cli.main(["stats", "--email", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "3 of 1,000" in out and "dry run: no email sent" in out


def test_manage_scopes_include_analytics_and_keep_upload():
    from romanfeed.publish.youtube import MANAGE_SCOPES, SCOPES

    assert "https://www.googleapis.com/auth/yt-analytics.readonly" in MANAGE_SCOPES
    assert set(SCOPES) <= set(MANAGE_SCOPES)


def test_public_numbers_report_when_analytics_is_unavailable(tmp_path):
    from datetime import date

    from romanfeed.publish import stats as st
    from romanfeed.state import Ledger

    class Pub:
        def channels(self):
            return self

        def list(self, part, forHandle):
            assert forHandle == "@SpaceScreens"
            return type("R", (), {"execute": lambda self: {"items": [{"statistics": {
                "subscriberCount": "12", "viewCount": "1500", "videoCount": "20"}}]}})()

    with Ledger(tmp_path / "s.db") as led:
        led.record_snapshot("2026-10-01", 3, 425, 14)
        data = st.fetch_public_stats(date(2026, 10, 8), yt_public=Pub(), ledger=led, handle="@SpaceScreens")
        assert led.snapshot_on_or_before("2026-10-08")[1] == 12  # today's counts saved
    subject, text, html = st.report(data, date(2026, 10, 8))
    assert "12 subscribers (+9 this week)" in subject
    assert "Since 2026-10-01: +9 (1.3/day)" in text
    assert "Views per new subscriber: 119" in text
    assert "not readable yet" in text and "<table" in html
