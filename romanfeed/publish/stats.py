"""Weekly channel stats, measured against the YouTube Partner Program goal.

The owner should never need to open YouTube Studio to know whether the channel
is on track, so once a week this pulls the handful of numbers that decide it
and mails them as plain sentences:

* subscribers -- live, from the Data API (`channels.list part=statistics`);
* watch time, views, subscriber churn and the top videos -- from the YouTube
  Analytics API v2, which needs the yt-analytics.readonly scope (added to
  `romanfeed auth --scope manage`; see docs/STATS_EMAIL.md).

Analytics data lags two to three days, so every Analytics window ends at
``today - 2`` and the email says which dates it covers. The 12-month watch-hour
figure is *all* watch time; YPP counts only public long-form hours (Shorts
views do not count), so it can read slightly higher than Studio's YPP number.

Google libraries are imported inside functions only: CI installs the dev
extra alone, and the tests drive everything through fake service objects.
"""
from __future__ import annotations

import html as _html
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage

log = logging.getLogger(__name__)

CHANNEL_NAME = "Space Screens"

# YouTube Partner Program thresholds and the date we want to clear them by.
GOAL_DATE = date(2027, 1, 31)
GOAL_SUBSCRIBERS = 1_000
GOAL_WATCH_HOURS = 4_000

# The nightly live stream is switched on by hand once either of these is met.
LIVE_TRIGGER_SUBSCRIBERS = 100
LIVE_TRIGGER_WATCH_HOURS_PER_DAY = 15

# Analytics trails real time by 2-3 days; ending the window earlier than that
# would report the last days as zero and make every week look like a collapse.
ANALYTICS_LAG_DAYS = 2

SCOPE_HELP = (
    "the saved YouTube token cannot read channel statistics or analytics: re-mint it "
    "with `romanfeed auth --scope manage` (which now includes yt-analytics.readonly) "
    "and paste the new token into the YOUTUBE_TOKEN_JSON secret -- see "
    "docs/STATS_EMAIL.md and docs/YOUTUBE_API_SETUP.md section 8"
)

API_DISABLED_HELP = (
    "the YouTube Analytics API is not enabled for the Google Cloud project: "
    "APIs & Services -> Library -> \"YouTube Analytics API\" -> Enable, then re-run"
)


@dataclass
class Window:
    """One Analytics date range, summed over the channel."""

    start: date
    end: date
    views: int = 0
    minutes: float = 0.0
    subs_gained: int = 0
    subs_lost: int = 0
    avg_view_duration_s: float = 0.0

    @property
    def watch_hours(self) -> float:
        return self.minutes / 60

    @property
    def net_subs(self) -> int:
        return self.subs_gained - self.subs_lost

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass
class TopVideo:
    video_id: str
    title: str
    minutes: float
    views: int

    @property
    def watch_hours(self) -> float:
        return self.minutes / 60


@dataclass
class ChannelStats:
    subscribers: int
    video_count: int
    view_count: int
    last7: Window
    prev7: Window
    watch_hours_12m: float
    daily_hours: list[tuple[date, float]] = field(default_factory=list)
    top_videos: list[TopVideo] = field(default_factory=list)
    # False when only public numbers were available (API key, no Analytics
    # permission): then the week's change comes from ledger snapshots.
    has_analytics: bool = True
    week_ago: tuple[str, int, int, int] | None = None   # (date, subscribers, views, videos)


@dataclass
class Pace:
    """Where one goal stands: what is left, what it takes per day, what we do."""

    current: float
    goal: float
    days_left: int
    actual_per_day: float

    @property
    def remaining(self) -> float:
        return max(self.goal - self.current, 0.0)

    @property
    def required_per_day(self) -> float:
        # Past the deadline the whole remainder is due now, not divided by zero.
        return self.remaining / self.days_left if self.days_left > 0 else self.remaining

    @property
    def reached(self) -> bool:
        return self.remaining == 0

    @property
    def on_pace(self) -> bool:
        return self.reached or self.actual_per_day >= self.required_per_day

    def verdict(self, unit: str) -> str:
        if self.reached:
            return "GOAL REACHED"
        gap = self.required_per_day - self.actual_per_day
        if gap <= 0:
            return f"ON PACE (ahead by {-gap:,.1f} {unit}/day)"
        return f"BEHIND by {gap:,.1f} {unit}/day"


def days_left(today: date, goal_date: date = GOAL_DATE) -> int:
    """Whole days from today to the goal date (0 once it has passed)."""
    return max((goal_date - today).days, 0)


def required_per_day(current: float, goal: float, today: date, goal_date: date = GOAL_DATE) -> float:
    return Pace(current, goal, days_left(today, goal_date), 0.0).required_per_day


def analytics_client(creds=None):
    """YouTube Analytics API v2 on the saved token (lazy import, see module doc)."""
    from googleapiclient.discovery import build

    from romanfeed.publish.youtube import stored_credentials

    return build("youtubeAnalytics", "v2", credentials=creds or stored_credentials(), cache_discovery=False)


def _rows(resp: dict) -> list[dict]:
    """Analytics answers in columns + rows; key each row by column name."""
    names = [h["name"] for h in resp.get("columnHeaders", [])]
    return [dict(zip(names, row)) for row in resp.get("rows") or []]


def _query(yta, start: date, end: date, metrics: str, **extra) -> list[dict]:
    resp = yta.reports().query(
        ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(), metrics=metrics, **extra,
    ).execute()
    return _rows(resp)


_WINDOW_METRICS = "views,estimatedMinutesWatched,subscribersGained,subscribersLost,averageViewDuration"


def _window(yta, start: date, end: date) -> Window:
    rows = _query(yta, start, end, _WINDOW_METRICS)
    r = rows[0] if rows else {}
    return Window(
        start=start, end=end,
        views=int(r.get("views", 0) or 0),
        minutes=float(r.get("estimatedMinutesWatched", 0) or 0),
        subs_gained=int(r.get("subscribersGained", 0) or 0),
        subs_lost=int(r.get("subscribersLost", 0) or 0),
        avg_view_duration_s=float(r.get("averageViewDuration", 0) or 0),
    )


def fetch_stats(today: date, *, yt=None, yta=None) -> ChannelStats:
    """Pull everything the weekly report needs. Raises the API's own errors."""
    if yt is None:
        from romanfeed.publish.youtube import client

        yt = client()
    yta = yta or analytics_client()

    channels = yt.channels().list(part="statistics", mine=True).execute().get("items") or []
    st = (channels[0] if channels else {}).get("statistics", {})

    end = today - timedelta(days=ANALYTICS_LAG_DAYS)
    start7 = end - timedelta(days=6)
    last7 = _window(yta, start7, end)
    prev7 = _window(yta, start7 - timedelta(days=7), start7 - timedelta(days=1))

    year = _query(yta, today - timedelta(days=365), end, "estimatedMinutesWatched")
    hours_12m = float((year[0] if year else {}).get("estimatedMinutesWatched", 0) or 0) / 60

    by_day = {r["day"]: float(r.get("estimatedMinutesWatched", 0) or 0) / 60
              for r in _query(yta, start7, end, "estimatedMinutesWatched", dimensions="day", sort="day")}
    daily = [(d, by_day.get(d.isoformat(), 0.0)) for d in (start7 + timedelta(days=i) for i in range(7))]

    top_rows = _query(yta, start7, end, "estimatedMinutesWatched,views",
                      dimensions="video", sort="-estimatedMinutesWatched", maxResults=5)
    titles: dict[str, str] = {}
    if top_rows:
        ids = ",".join(r["video"] for r in top_rows)
        for item in yt.videos().list(part="snippet", id=ids).execute().get("items") or []:
            titles[item["id"]] = (item.get("snippet") or {}).get("title", "")
    top = [TopVideo(r["video"], titles.get(r["video"], r["video"]),
                    float(r.get("estimatedMinutesWatched", 0) or 0), int(r.get("views", 0) or 0))
           for r in top_rows]

    return ChannelStats(
        subscribers=int(st.get("subscriberCount", 0) or 0),
        video_count=int(st.get("videoCount", 0) or 0),
        view_count=int(st.get("viewCount", 0) or 0),
        last7=last7, prev7=prev7, watch_hours_12m=hours_12m,
        daily_hours=daily, top_videos=top,
    )


def channel_counts(yt_public, handle: str) -> tuple[int, int, int]:
    """(subscribers, views, videos) from public data, by handle."""
    items = yt_public.channels().list(part="statistics", forHandle=handle).execute().get("items") or []
    st = (items[0] if items else {}).get("statistics", {})
    return int(st.get("subscriberCount", 0) or 0), int(st.get("viewCount", 0) or 0), int(st.get("videoCount", 0) or 0)


def fetch_public_stats(today: date, *, yt_public, ledger, handle: str) -> ChannelStats:
    """What the week looks like from public numbers alone.

    Subscribers, views and video count are public; watch time is not (it needs
    the Analytics permission, which Google now gates behind an app review).
    Today's counts are saved to the ledger, and the report compares them with
    the latest snapshot from at least seven days ago."""
    subs, views, videos = channel_counts(yt_public, handle)
    ledger.record_snapshot(today.isoformat(), subs, views, videos)
    week_ago = ledger.snapshot_on_or_before((today - timedelta(days=7)).isoformat())
    empty = Window(today - timedelta(days=6), today)
    return ChannelStats(subscribers=subs, video_count=videos, view_count=views, last7=empty, prev7=empty,
                        watch_hours_12m=0.0, has_analytics=False, week_ago=week_ago)


def live_trigger(stats: ChannelStats) -> tuple[bool, str]:
    """Is it time to switch on the nightly live stream? And the measured why."""
    if not stats.has_analytics:
        by_subs = stats.subscribers >= LIVE_TRIGGER_SUBSCRIBERS
        detail = f"{stats.subscribers:,}/{LIVE_TRIGGER_SUBSCRIBERS} subscribers (watch hours/day not readable without Analytics)"
        if by_subs:
            return True, f"TRIGGER MET: ask Claude to switch on the nightly stream. ({detail})"
        return False, f"Not met yet: {detail}."
    hours_per_day = stats.last7.watch_hours / stats.last7.days
    by_subs = stats.subscribers >= LIVE_TRIGGER_SUBSCRIBERS
    by_hours = hours_per_day >= LIVE_TRIGGER_WATCH_HOURS_PER_DAY
    detail = (f"{stats.subscribers:,}/{LIVE_TRIGGER_SUBSCRIBERS} subscribers, "
              f"{hours_per_day:,.1f}/{LIVE_TRIGGER_WATCH_HOURS_PER_DAY} watch hours/day (7-day average)")
    if by_subs or by_hours:
        return True, f"TRIGGER MET: ask Claude to switch on the nightly stream. ({detail})"
    return False, f"Not met yet: {detail}."


def _signed(n: float, fmt: str = ",.0f") -> str:
    return f"{n:+{fmt}}"


def _change(now: float, before: float) -> str:
    if before == 0:
        return "n/a" if now == 0 else "new"
    return f"{(now - before) / before * 100:+.0f}%"


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _public_sections(stats: ChannelStats, today: date) -> list[tuple[str, list[tuple[str, str]]]]:
    left = days_left(today)
    _, trigger = live_trigger(stats)
    rows = [("Now", f"{stats.subscribers:,} of {GOAL_SUBSCRIBERS:,} ({stats.subscribers / GOAL_SUBSCRIBERS:.1%})"),
            ("Needed", f"{required_per_day(stats.subscribers, GOAL_SUBSCRIBERS, today):,.1f}/day")]
    views_rows = [("Lifetime", f"{stats.view_count:,} views across {stats.video_count:,} videos")]
    if stats.week_ago:
        then, subs0, views0, _ = stats.week_ago
        days = max((today - date.fromisoformat(then)).days, 1)
        pace = Pace(stats.subscribers, GOAL_SUBSCRIBERS, left, (stats.subscribers - subs0) / days)
        rows += [(f"Since {then}", f"{_signed(stats.subscribers - subs0)} ({pace.actual_per_day:,.1f}/day)"),
                 ("Pace", pace.verdict("subscribers"))]
        new_views = stats.view_count - views0
        views_rows.append((f"Since {then}", f"{_signed(new_views)} views"))
        if stats.subscribers - subs0 > 0:
            views_rows.append(("Views per new subscriber", f"{new_views / (stats.subscribers - subs0):,.0f}"))
    else:
        rows.append(("Pace", "first snapshot taken today; next week's report shows the change"))
    return [
        (f"Goal: YouTube Partner Program by {GOAL_DATE:%Y-%m-%d}", [("Days left", f"{left:,}")]),
        ("Subscribers", rows),
        ("Views", views_rows),
        ("Watch hours", [("Now", "not readable yet: YouTube's watch-time data needs the Analytics permission, "
                                 "which Google gates behind an app review. Studio > Analytics shows it.")]),
        ("Live stream trigger (100 subscribers OR 15 watch hours/day)", [("Status", trigger)]),
    ]


def _sections(stats: ChannelStats, today: date) -> list[tuple[str, list[tuple[str, str]]]]:
    """The report as (heading, [(label, value)]) -- shared by text and HTML."""
    if not stats.has_analytics:
        return _public_sections(stats, today)
    left = days_left(today)
    l7, p7 = stats.last7, stats.prev7
    subs = Pace(stats.subscribers, GOAL_SUBSCRIBERS, left, l7.net_subs / l7.days)
    hours = Pace(stats.watch_hours_12m, GOAL_WATCH_HOURS, left, l7.watch_hours / l7.days)
    _, trigger = live_trigger(stats)
    views_per_sub = f"{stats.view_count / stats.subscribers:,.1f}" if stats.subscribers else "n/a (0 subscribers)"

    return [
        (f"Goal: YouTube Partner Program by {GOAL_DATE:%Y-%m-%d}", [
            ("Days left", f"{left:,}"),
        ]),
        ("Subscribers", [
            ("Now", f"{stats.subscribers:,} of {GOAL_SUBSCRIBERS:,} ({stats.subscribers / GOAL_SUBSCRIBERS:.1%})"),
            ("vs last week", f"{_signed(l7.net_subs)} net ({l7.subs_gained:,} gained, {l7.subs_lost:,} lost); "
                             f"week before {_signed(p7.net_subs)}"),
            ("Needed", f"{subs.required_per_day:,.1f}/day"),
            ("Actual (7 days)", f"{subs.actual_per_day:,.1f}/day"),
            ("Pace", subs.verdict("subscribers")),
        ]),
        ("Watch hours (rolling 12 months)", [
            ("Now", f"{stats.watch_hours_12m:,.1f} of {GOAL_WATCH_HOURS:,} ({stats.watch_hours_12m / GOAL_WATCH_HOURS:.1%})"),
            ("Needed", f"{hours.required_per_day:,.1f}/day"),
            ("Actual (7 days)", f"{hours.actual_per_day:,.1f}/day"),
            ("Pace", hours.verdict("hours")),
            ("Note", "All watch time. YPP counts only public long-form hours (not Shorts), "
                     "so Studio's YPP figure may be slightly lower."),
        ]),
        ("Live stream trigger (100 subscribers OR 15 watch hours/day)", [
            ("Status", trigger),
        ]),
        (f"Last 7 days ({l7.start:%b %d}-{l7.end:%b %d}) vs previous 7", [
            ("Views", f"{l7.views:,} (prev {p7.views:,}, {_change(l7.views, p7.views)})"),
            ("Watch hours", f"{l7.watch_hours:,.1f} (prev {p7.watch_hours:,.1f}, {_change(l7.minutes, p7.minutes)})"),
            ("Avg view duration", f"{_mmss(l7.avg_view_duration_s)} (prev {_mmss(p7.avg_view_duration_s)})"),
            ("Views per subscriber", f"{views_per_sub} (lifetime {stats.view_count:,} views, {stats.video_count:,} videos)"),
        ]),
        ("Daily watch hours", [(f"{d:%a %b %d}", f"{h:,.1f}") for d, h in stats.daily_hours]),
        ("Top 5 videos, last 7 days (by watch time)", [
            (f"{i}. {v.title}", f"{v.watch_hours:,.1f} h, {v.views:,} views")
            for i, v in enumerate(stats.top_videos, 1)
        ] or [("", "no watch time recorded")]),
    ]


def report(stats: ChannelStats, today: date) -> tuple[str, str, str]:
    """(subject, plain text, HTML) for one weekly email. Measured numbers only."""
    met, _ = live_trigger(stats)
    l7 = stats.last7
    if stats.has_analytics:
        subject = (f"{CHANNEL_NAME} weekly: {stats.subscribers:,} subs ({_signed(l7.net_subs)}), "
                   f"{stats.watch_hours_12m:,.1f} watch hrs")
    else:
        change = f" ({_signed(stats.subscribers - stats.week_ago[1])} this week)" if stats.week_ago else ""
        subject = f"{CHANNEL_NAME} weekly: {stats.subscribers:,} subscribers{change}, {stats.view_count:,} views"
    if met:
        subject = "LIVE STREAM TRIGGER MET -- " + subject

    sections = _sections(stats, today)
    lag = (f"Analytics covers through {l7.end:%Y-%m-%d} (YouTube data lags 2-3 days); "
           f"subscriber count is live as of {today:%Y-%m-%d}." if stats.has_analytics
           else f"Public channel numbers, live as of {today:%Y-%m-%d}.")

    lines = [f"{CHANNEL_NAME} -- weekly stats, {today:%a %Y-%m-%d}", lag, ""]
    for heading, rows in sections:
        lines.append(heading.upper())
        for label, value in rows:
            lines.append(f"  {label}: {value}" if label else f"  {value}")
        lines.append("")
    text = "\n".join(lines).rstrip() + "\n"

    esc = _html.escape
    cell = "padding:6px 8px;border-bottom:1px solid #e5e5e5;vertical-align:top;"
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;'
        'color:#111;max-width:600px;margin:0 auto;">',
        f'<h2 style="font-size:18px;margin:0 0 4px;">{esc(CHANNEL_NAME)} weekly stats</h2>',
        f'<p style="margin:0 0 12px;color:#555;font-size:13px;">{esc(lag)}</p>',
    ]
    if met:
        parts.append('<p style="background:#fff3cd;border:1px solid #e0b400;padding:10px;font-weight:bold;">'
                     f'{esc(live_trigger(stats)[1])}</p>')
    parts.append('<table style="width:100%;border-collapse:collapse;" cellpadding="0" cellspacing="0">')
    for heading, rows in sections:
        parts.append(f'<tr><th colspan="2" style="text-align:left;padding:14px 8px 6px;font-size:15px;'
                     f'border-bottom:2px solid #111;">{esc(heading)}</th></tr>')
        for label, value in rows:
            style = cell + ("font-weight:bold;" if label in ("Pace", "Status") else "")
            parts.append(f'<tr><td style="{cell}color:#555;">{esc(label)}</td>'
                         f'<td style="{style}">{esc(value)}</td></tr>')
    parts.append("</table></div>")
    return subject, text, "\n".join(parts)


def send_email(subject: str, text: str, html: str, *, to: str, smtp_host: str, smtp_port: int,
               user: str, password: str) -> None:
    """Send a multipart/alternative mail (plain text + HTML) with the stdlib.

    Implicit TLS on 465 is Gmail's default; some networks block it, so a
    failure to *connect* there falls back to STARTTLS on 587. An auth or
    send failure does not fall back -- retrying a rejected password on
    another port only hides the real error, and a retried send could
    deliver twice. Port 587 given explicitly goes straight to STARTTLS.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    server = None
    if smtp_port != 587:
        try:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30, context=ctx)
        except OSError as exc:
            log.warning("SSL connect to %s:%s failed (%s); trying STARTTLS on 587", smtp_host, smtp_port, exc)
    starttls = server is None
    if starttls:
        server = smtplib.SMTP(smtp_host, 587, timeout=30)
    with server:
        if starttls:
            server.starttls(context=ctx)
        server.login(user, password)
        server.send_message(msg)
    log.info("stats email sent (%s)", subject)
