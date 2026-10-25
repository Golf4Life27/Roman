"""The live network: extra streams ("lanes") beside the nightly one.

Big cozy channels run many streams at once, each a different scene, each
ending before 12 hours so it is saved as a replay. A lane here is one more
stream with its own Studio stream key, running a session from each of its
daily start times (live.lanes in the channel config):

  night  the existing 21:00 stream (romanfeed/live/run.py), sleep titles
  lanes  e.g. 07:30 + 19:30 and 01:30 + 13:30, 11.5 h each: two lanes that
         together keep two more streams up around the clock

Everything here is deterministic, so the GitHub job that creates each
session's broadcast ahead of time (live/schedule.py) and the server that
streams it (`romanfeed live lane ID`) agree on the scene without talking:
the scene comes from the date, the lane and the session, never from state
on the server. Concurrent streams show different scenes: lane sessions step
through the season's scenes in start order (see tick), skipping the night's
scene and the scene of the session running when they start.

A lane streams a scene loop (cozy/seam.py) under freshly composed music,
stream-copied like a scene night. The server makes no YouTube API calls:
the broadcast is already waiting, auto-start and auto-stop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from romanfeed.config import ChannelConfig, LaneSettings
from romanfeed.cozy import scenes as sc
from romanfeed.cozy import titles
from romanfeed.cozy.calendar import Theme, themes_for

log = logging.getLogger(__name__)

NIGHT = "night"
MIN_CHOICES = 5     # fewer in-season scenes than this: add the season-neutral ones (3 streams + spares)
NEUTRAL = ("evergreen", "launch", "meteors")
SEASONS = ("winter", "autumn", "spring", "summer")


@dataclass(frozen=True)
class Session:
    lane: str
    start: datetime          # aware, live.timezone
    hours: float
    index: int               # which of the lane's daily starts

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.hours)

    @property
    def use(self) -> str:
        return titles.SLEEP if self.lane == NIGHT else titles.use_for(self.start.hour)


def _at(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hh, mm), tzinfo=tz)


def lane(cfg: ChannelConfig, lane_id: str) -> LaneSettings:
    for x in cfg.live.lanes:
        if x.id == lane_id:
            return x
    raise KeyError(f"no live lane {lane_id!r} (lanes: {[x.id for x in cfg.live.lanes]})")


def night_session(cfg: ChannelConfig, day: date) -> Session:
    tz = ZoneInfo(cfg.live.timezone)
    return Session(NIGHT, _at(day, cfg.live.start_local, tz), cfg.live.hours, 0)


def sessions(cfg: ChannelConfig, frm: datetime, to: datetime, *, lanes=None) -> list[Session]:
    """Every lane session starting in [frm, to), by start time."""
    tz = ZoneInfo(cfg.live.timezone)
    frm, to = frm.astimezone(tz), to.astimezone(tz)
    out = []
    for x in cfg.live.lanes if lanes is None else lanes:
        day = frm.date() - timedelta(days=1)
        while day <= to.date():
            for i, hhmm in enumerate(x.starts):
                s = Session(x.id, _at(day, hhmm, tz), x.hours, i)
                if frm <= s.start < to:
                    out.append(s)
            day += timedelta(days=1)
    return sorted(out, key=lambda s: (s.start, s.lane))


def current(cfg: ChannelConfig, lane_id: str, now: datetime) -> Session | None:
    """The session `now` belongs to: the timer fires a few minutes before
    the start, a late start still counts until 2 hours in."""
    x = lane(cfg, lane_id)
    near = sessions(cfg, now - timedelta(hours=2), now + timedelta(minutes=30), lanes=[x])
    return min(near, key=lambda s: abs((s.start - now).total_seconds()), default=None)


def season_scenes(library: sc.Library, themes: list[Theme]) -> list[sc.Scene]:
    """The scenes that fit the day, most specific theme first; topped up with
    season-neutral ones, then season ones, then any, so the lanes always have
    enough to keep three streams apart."""
    out: list[sc.Scene] = []

    def add(found):
        for s in sorted(found, key=lambda s: s.id):
            if s not in out:
                out.append(s)

    for t in themes:
        add(library.tagged(t.key))
    for group in (NEUTRAL, SEASONS):     # a Christmas cabin in July only as a last resort
        if len(out) < MIN_CHOICES:
            for key in group:
                add(library.tagged(key))
    if len(out) < MIN_CHOICES:
        add(library.scenes)
    return out


def theme_of(scene: sc.Scene, themes: list[Theme]) -> Theme:
    return next((t for t in themes if t.key in scene.themes), themes[-1])


def night_pick(library: sc.Library, day: date, themes: list[Theme]) -> sc.Scene | None:
    picked = sc.pick(library, day, themes)
    return picked[0] if picked else None


def tick(cfg: ChannelConfig, s: Session) -> int:
    """The session's place in one running count of every lane session, in
    start order: day * (sessions a day) + its rank within the day. With the
    lanes' starts interleaved (01:30, 07:30, 13:30, 19:30), streams that run
    at the same time are always neighbours in this count."""
    per_day = sorted((hhmm, x.id) for x in cfg.live.lanes for hhmm in x.starts)
    rank = per_day.index((f"{s.start:%H:%M}", s.lane))
    return s.start.date().toordinal() * len(per_day) + rank


def night_scenes(cfg: ChannelConfig, library: sc.Library, s: Session, themes_fn) -> set[str]:
    """The night stream's scene on every night that overlaps session `s`."""
    out = set()
    for d in (s.start.date() - timedelta(days=1), s.start.date(), s.start.date() + timedelta(days=1)):
        n = night_session(cfg, d)
        if n.start < s.end and s.start < n.end and (p := night_pick(library, d, themes_fn(d))):
            out.add(p.id)
    return out


_MEMO: dict[tuple, str] = {}   # (library, lane, start) -> scene id, for this process


def pick(cfg: ChannelConfig, library: sc.Library, s: Session, *, themes_fn=None) -> tuple[sc.Scene, Theme]:
    """The scene for lane session `s`.

    Every lane session of the year is worked out in start order from
    January 1: each steps through its day's scenes by its tick, skipping the
    night stream's scene and the scene of the session running when it starts.
    A pure function of the date, the library and the calendar, so the GitHub
    job and the server always agree (the year's first session may repeat
    the last one of December). The walk ignores rocket launches, whose list
    changes daily; a launch that changes tonight's night scene is applied at
    the end, to this session only."""
    themes_fn = themes_fn or (lambda d: themes_for(d, launches=[]))
    by_id = {x.id: x for x in library.scenes}
    lib_key = tuple(sorted((x.id, tuple(x.themes)) for x in library.scenes))
    if not by_id:
        raise ValueError("the scene library is empty")
    cands_of: dict[date, list[sc.Scene]] = {}
    tz = s.start.tzinfo
    prev, prev_id, chosen = None, None, None
    for x in sessions(cfg, datetime(s.start.year, 1, 1, tzinfo=tz), s.start + timedelta(minutes=1)):
        key = (lib_key, x.lane, x.start.isoformat())
        chosen = _MEMO.get(key)
        if chosen is None:
            d = x.start.date()
            if d not in cands_of:
                cands_of[d] = season_scenes(library, themes_for(d, launches=[]))
            cands = cands_of[d]
            taken = night_scenes(cfg, library, x, _no_launches)   # stable: the launch list changes daily
            if prev is not None and prev.end > x.start:
                taken.add(prev_id)
            k = tick(cfg, x)
            chosen = next((c.id for b in range(len(cands)) if (c := cands[(k + b) % len(cands)]).id not in taken),
                          cands[k % len(cands)].id)   # more streams than scenes: share rather than go dark
            _MEMO[key] = chosen
        prev, prev_id = x, chosen
    themes = themes_for(s.start.date(), launches=[])
    real_night = night_scenes(cfg, library, s, themes_fn)   # a launch can change tonight's scene
    if chosen in real_night:
        cands = season_scenes(library, themes)
        avoid = real_night | night_scenes(cfg, library, s, _no_launches)
        before = previous(cfg, s)
        if before is not None and before.end > s.start:
            avoid.add(pick(cfg, library, before)[0].id)
        chosen = next((c.id for c in cands if c.id not in avoid), chosen)
    scene = by_id[chosen]
    return scene, theme_of(scene, themes)


def _no_launches(d: date) -> list[Theme]:
    return themes_for(d, launches=[])


def previous(cfg: ChannelConfig, s: Session) -> Session | None:
    """The lane session that starts just before `s`."""
    before = [o for o in sessions(cfg, s.start - timedelta(days=1), s.start) if o != s]
    return before[-1] if before else None


def hours_label(hours: float) -> str:
    """Whole hours, rounded down: an 11.5 h session is "11 Hours", never 12."""
    return f"{int(hours)}"


def title(scene: sc.Scene, theme: Theme, s: Session) -> str:
    return titles.seasonal(scene.title, when=s.start, theme_key=theme.key, hours_label=hours_label(s.hours),
                           use=s.use, part=titles.part_of_day(s.start.hour), live=True)


DESCRIPTION = """{tagline}

Live now: {hours} hours in a cozy {scene} with the stars outside and calm original music, {purpose}. Put it on, settle in, and stay as long as you like. The stream ends on its own and stays on the channel to replay any night.

{uses}

Subscribe for a new space video every few days and cozy live streams around the clock:
{subscribe}

{keywords}

AI-animated scene, original music.

{hashtags}
"""
PURPOSE = {titles.SLEEP: "made for falling asleep", titles.WORK: "made for focus, work and study"}


def body(cfg: ChannelConfig, scene: sc.Scene, theme: Theme, s: Session) -> dict:
    from romanfeed.live import broadcast as bc

    desc = DESCRIPTION.format(tagline=cfg.channel.tagline, hours=hours_label(s.hours), scene=scene.title.lower(),
                              purpose=PURPOSE[s.use], subscribe=bc.SUBSCRIBE_URL,
                              keywords=" · ".join(cfg.publish.tags), uses=bc.USES, hashtags=bc.LIVE_HASHTAGS)
    return bc.broadcast_body(title=title(scene, theme, s), description=desc.strip() + "\n", start=s.start)


def library_for(cfg: ChannelConfig, work: Path) -> sc.Library:
    return sc.fetch_manifest(cfg.cozy.repo, cfg.cozy.release_tag, work / sc.MANIFEST)
