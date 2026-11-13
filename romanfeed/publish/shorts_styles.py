"""Hook-style against calm Shorts, measured per Short.

Every other Short of the Oct 31 test was read in the hook style and tagged
``hook-style`` (romanfeed/shorts.py). This pulls each Short posted since a
date from the ledger, reads its tags and live view count from the Data API
and its Analytics numbers (views, average view length, subscribers gained),
and sums them per style, so the call between the two comes from the numbers.

Analytics lags two to three days, so a Short posted in the last two days has
public views but little or no Analytics yet; the per-Short lines show both.
Google libraries are imported inside functions only, as in stats.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from romanfeed.publish.stats import _query
from romanfeed.shorts import HOOK_TAG, PUNCH_TAG, asset_from_meta, is_earth
from romanfeed.why import SUBJECTS, WHY_TAG, subject

STYLES = ("hook", "calm")


@dataclass
class ShortNumbers:
    video_id: str
    posted: str            # created_at from the ledger (UTC)
    title: str
    style: str             # "hook" or "calm"
    earth: bool
    public_views: int      # live, from the Data API
    views: int = 0         # Analytics, lagging
    avg_view_s: float = 0.0
    subs: int = 0
    why: bool = False      # opened on a why question (romanfeed/why.py)
    subject: str = "other"  # nebula, galaxy, cluster or other
    punch: bool = False    # opened with the punch-in zoom (Nov 13-19 test)
    avg_view_pct: float = 0.0  # Analytics: average share of the Short watched


def posted_since(ledger, channel: str, since: date) -> list[tuple[str, str, str]]:
    """(youtube_id, created_at, asset_id) for every uploaded Short since `since`, oldest first."""
    rows = ledger.conn.execute(
        "SELECT youtube_id, created_at, asset_id FROM shorts WHERE channel = ? AND youtube_id IS NOT NULL"
        " AND created_at >= ? ORDER BY created_at", (channel, since.isoformat())).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def collect(yt, yta, ledger, channel: str, since: date, end: date) -> list[ShortNumbers]:
    posted = posted_since(ledger, channel, since)
    if not posted:
        return []
    meta = ledger.asset_meta([a for _, _, a in posted])
    info: dict[str, dict] = {}
    ids = [v for v, _, _ in posted]
    for i in range(0, len(ids), 50):
        for item in yt.videos().list(part="snippet,statistics", id=",".join(ids[i:i + 50])).execute().get("items") or []:
            info[item["id"]] = item
    out: list[ShortNumbers] = []
    for vid, created, asset in posted:
        item = info.get(vid)
        if item is None:      # deleted or private: nothing to compare
            continue
        sn, stt = item.get("snippet") or {}, item.get("statistics") or {}
        tags = [t.lower() for t in sn.get("tags") or []]
        out.append(ShortNumbers(
            video_id=vid, posted=created, title=sn.get("title", vid),
            style="hook" if HOOK_TAG in tags else "calm",
            earth=asset in meta and is_earth(asset_from_meta(meta[asset])),
            public_views=int(stt.get("viewCount", 0) or 0),
            why=WHY_TAG in tags,
            punch=PUNCH_TAG in tags,
            subject=subject(asset_from_meta(meta[asset])) if asset in meta else "other"))
    by_id = {s.video_id: s for s in out}
    for i in range(0, len(out), 200):
        chunk = [s.video_id for s in out[i:i + 200]]
        rows = _query(yta, since, end, "views,averageViewDuration,subscribersGained,averageViewPercentage",
                      dimensions="video", filters="video==" + ",".join(chunk))
        for r in rows:
            s = by_id.get(r.get("video", ""))
            if s:
                s.views = int(r.get("views", 0) or 0)
                s.avg_view_s = float(r.get("averageViewDuration", 0) or 0)
                s.subs = int(r.get("subscribersGained", 0) or 0)
                s.avg_view_pct = float(r.get("averageViewPercentage", 0) or 0)
    return out


@dataclass
class StyleTotals:
    style: str
    shorts: int
    public_views: int
    views: int
    subs: int
    avg_view_s: float      # weighted by Analytics views

    @property
    def views_per_short(self) -> float:
        return self.public_views / self.shorts if self.shorts else 0.0

    @property
    def subs_per_1000(self) -> float:
        return 1000 * self.subs / self.views if self.views else 0.0


def totals(rows: list[ShortNumbers], style: str, *, earth: bool | None = None) -> StyleTotals:
    pick = [r for r in rows if r.style == style and (earth is None or r.earth == earth)]
    views = sum(r.views for r in pick)
    avg = sum(r.avg_view_s * r.views for r in pick) / views if views else 0.0
    return StyleTotals(style, len(pick), sum(r.public_views for r in pick), views, sum(r.subs for r in pick), avg)


def verdict(hook: StyleTotals, calm: StyleTotals, *, min_shorts: int = 8, min_subs: int = 10) -> str:
    """Which style the numbers favour, or that the gap is still inside noise.

    Subscribers decide (they are the goal's binding limit). With few
    subscribers in play, a lead of one or two is chance: the call waits for
    at least `min_subs` subscribers and `min_shorts` Shorts per style, then
    needs the leader ahead on subscribers per 1,000 views by a third or more."""
    if min(hook.shorts, calm.shorts) < min_shorts:
        return f"too early: {hook.shorts} hook and {calm.shorts} calm Shorts (need {min_shorts} each)"
    if hook.subs + calm.subs < min_subs:
        return f"inside noise: {hook.subs + calm.subs} subscribers between both styles (need {min_subs})"
    a, b = hook.subs_per_1000, calm.subs_per_1000
    if a >= b * 4 / 3 and a > 0:
        return f"hook wins: {a:.1f} vs {b:.1f} subscribers per 1,000 views"
    if b >= a * 4 / 3 and b > 0:
        return f"calm wins: {b:.1f} vs {a:.1f} subscribers per 1,000 views"
    return f"inside noise: {a:.1f} vs {b:.1f} subscribers per 1,000 views"


def group_line(label: str, pick: list[ShortNumbers]) -> str:
    """One group's Shorts, live views per Short and subscribers per Short."""
    n = len(pick)
    views = sum(r.public_views for r in pick)
    subs = sum(r.subs for r in pick)
    return (f"  {label}: {n} Shorts, {views / n if n else 0:,.0f} live views each, "
            f"{subs} subs ({subs / n if n else 0:.2f} per Short)")


def why_lines(rows: list[ShortNumbers]) -> list[str]:
    """The why test (Nov 6-13): why Shorts against the rest, split by style and subject."""
    if not any(r.why for r in rows):
        return []
    out = ["Why vs standard Shorts (views and subscribers per Short):"]
    for label, w in (("why", True), ("standard", False)):
        out.append(group_line(label, [r for r in rows if r.why == w]))
    for style in STYLES:
        for label, w in (("why", True), ("standard", False)):
            out.append(group_line(f"{style} {label}", [r for r in rows if r.why == w and r.style == style]))
    for subj in SUBJECTS:
        for label, w in (("why", True), ("standard", False)):
            pick = [r for r in rows if r.why == w and r.subject == subj]
            if pick:
                out.append(group_line(f"{subj} {label}", pick))
    return out


def opening_line(label: str, pick: list[ShortNumbers]) -> str:
    """One opening's Shorts: live views each, share watched and subscribers
    per 1,000 views, the last two weighted by Analytics views."""
    n = len(pick)
    views = sum(r.views for r in pick)
    live = sum(r.public_views for r in pick)
    pct = sum(r.avg_view_pct * r.views for r in pick) / views if views else 0.0
    subs = sum(r.subs for r in pick)
    return (f"  {label}: {n} Shorts, {live / n if n else 0:,.0f} live views each, {pct:.0f}% watched, "
            f"{subs} subs ({subs * 1000 / views if views else 0:.1f} per 1,000 views)")


def opening_lines(rows: list[ShortNumbers]) -> list[str]:
    """The punch-in opening test (Nov 13-19): zoom-in opening against the plain
    slow pan, over the Shorts posted while the test ran, split by style."""
    if not any(r.punch for r in rows):
        return []
    start = min(r.posted for r in rows if r.punch)
    during = [r for r in rows if r.posted >= start]
    out = ["Punch-in vs plain opening (Shorts since the first punch-in):"]
    for label, p in (("punch-in", True), ("plain", False)):
        out.append(opening_line(label, [r for r in during if r.punch == p]))
    for style in STYLES:
        for label, p in (("punch-in", True), ("plain", False)):
            out.append(opening_line(f"{style} {label}", [r for r in during if r.punch == p and r.style == style]))
    return out


def _mmss(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def report_lines(rows: list[ShortNumbers], since: date, end: date) -> list[str]:
    if not rows:
        return [f"No Shorts posted since {since.isoformat()}."]
    lines = [f"Shorts posted since {since.isoformat()}: {len(rows)}. Analytics through {end.isoformat()} "
             "(lags 2-3 days); live views are from today."]
    lines.append(f"  {'':6} {'Shorts':>6} {'live views':>10} {'per Short':>9} {'avg view':>8} {'subs':>5} {'per 1,000':>9}")
    for style in STYLES:
        t = totals(rows, style)
        lines.append(f"  {style:6} {t.shorts:>6} {t.public_views:>10,} {t.views_per_short:>9,.0f} "
                     f"{_mmss(t.avg_view_s):>8} {t.subs:>5} {t.subs_per_1000:>9.1f}")
    for label, earth in (("deep sky", False), ("Earth", True)):
        parts = []
        for style in STYLES:
            t = totals(rows, style, earth=earth)
            parts.append(f"{style} {t.shorts} Shorts, {t.views_per_short:,.0f} views each, {t.subs} subs")
        lines.append(f"  {label}: " + "; ".join(parts))
    lines.append("  Reading: " + verdict(totals(rows, "hook"), totals(rows, "calm")))
    lines += why_lines(rows)
    lines += opening_lines(rows)
    lines.append("  Each Short (posted UTC, style, live views, Analytics views, avg view, subs):")
    for r in rows:
        lines.append(f"    {r.posted[:16]} {r.style:4} {r.public_views:>6,} {r.views:>6,} {_mmss(r.avg_view_s):>5} "
                     f"{r.subs:>3}  {r.title[:70]}")
    return lines
