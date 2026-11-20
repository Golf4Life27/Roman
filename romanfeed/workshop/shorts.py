"""Kids Shorts from Santa's workshop: one elf room in 30 seconds a day.

Alex's ask on Nov 20 was the workshop stream "can probably capture a lot of
kids on YouTube and shorts". Each day from the workshop's first day to
`shorts_until`, one vertical Short goes up: a room rendered fresh from the same
drawings the stream uses (romanfeed/workshop/art.py), with a big caption on
top, the sleeps-until-Christmas count, our own music, made for kids. The first
one is the launch Short: the assembly line, "Santa's Workshop is LIVE".

The regular Shorts give up the same number of slots over the same days
(reserved(), used by romanfeed/shorts.py daily_target), so the channel's day
stays at its cap. A day already posted is told by the title, which carries the
day's count, in the channel's public upload feed; so a re-run posts nothing.

    python -m romanfeed.workshop.shorts CONFIG [--day YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from romanfeed.config import ChannelConfig, load_config
from romanfeed.live.workshop import sleeps

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
SECONDS = 30
LAUNCH = "assembly-line"
CATEGORY = "1"   # Film & Animation

# What the caption says on top, and the title it leads, per room.
LINES = {
    "assembly-line": ("ELVES BUILD TOYS!", "Elves Build Toys in 30 Seconds"),
    "gift-wrapping": ("ELVES WRAP PRESENTS!", "Elves Wrap Presents in 30 Seconds"),
    "train-track": ("ELVES TEST A TOY TRAIN!", "Elves Test a Toy Train"),
    "teddy-bears": ("AN ELF SEWS A TEDDY!", "An Elf Sews a Teddy Bear in 30 Seconds"),
    "sleigh-bay": ("LOADING SANTA'S SLEIGH!", "Elves Load Santa's Sleigh"),
    "cocoa-break": ("ELF COCOA BREAK!", "The Elves' Cozy Cocoa Break"),
    "mail-room": ("LETTERS TO SANTA!", "Elves Sort Letters to Santa"),
    "north-pole": ("THE NORTH POLE TONIGHT!", "Santa's Workshop Under the Northern Lights"),
}
ORDER = ["assembly-line", "teddy-bears", "train-track", "gift-wrapping", "mail-room", "sleigh-bay",
         "cocoa-break", "north-pole"]

TAGS = ["santa's workshop", "elves", "christmas for kids", "christmas countdown", "santa", "north pole",
        "kids christmas", "christmas animation", "toys", "shorts"]


def _settings(cfg: ChannelConfig):
    ws = cfg.live.workshop
    if not (ws and ws.enabled and ws.first_day and ws.shorts_per_day > 0):
        return None
    return ws


def _from(ws) -> date:
    """The first kids Short's day: the launch Short, once the stream is up."""
    return date.fromisoformat(ws.shorts_from or ws.first_day)


def _until(ws) -> date:
    return date.fromisoformat(ws.shorts_until or ws.last_day)


def season(cfg: ChannelConfig, day: date) -> bool:
    ws = _settings(cfg)
    return bool(ws) and _from(ws) <= day <= _until(ws)


def central_day(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(CT).date()


def reserved(cfg: ChannelConfig, now: datetime | None = None) -> int:
    """Regular Shorts slots the kids Shorts take today (0 out of season)."""
    return _settings(cfg).shorts_per_day if season(cfg, central_day(now)) else 0


def room(cfg: ChannelConfig, day: date) -> str:
    """The launch room on the first day, then the rooms in turn."""
    first = _from(_settings(cfg))
    n = (day - first).days
    return LAUNCH if n <= 0 else ORDER[n % len(ORDER)]


def count_line(n: int) -> str:
    if n <= 0:
        return "Merry Christmas from Santa's Elves!"
    return f"{n} Sleep{'s' if n != 1 else ''} Until Christmas"


def title(cfg: ChannelConfig, day: date) -> str:
    n = sleeps(day)
    if n <= 0:
        return "Merry Christmas from Santa's Elves! 🎅🎄"
    lead = "Santa's Workshop is LIVE" if day <= _from(_settings(cfg)) \
        else LINES[room(cfg, day)][1]
    return f"{lead}! 🎅 {count_line(n)}"


def description(cfg: ChannelConfig, day: date) -> str:
    n = sleeps(day)
    count = f"Only {n} sleep{'s' if n != 1 else ''} until Christmas!" if n > 0 else "Merry Christmas!"
    return "\n".join([
        f"Peek inside Santa's workshop, where the elves are busy getting ready for Christmas. {count}",
        "",
        "Santa's workshop is live on our channel every day until Christmas: watch the elves build toys, "
        "wrap presents, test the toy train and load the sleigh.",
        "",
        f"Animation and music made by {cfg.channel.name}. All the characters are our own.",
    ])


def posted(cfg: ChannelConfig, day: date, titles: list[str] | None = None) -> bool:
    """True when the channel's latest uploads already hold this day's Short."""
    if titles is None:
        from romanfeed.shorts import recent_upload_titles
        titles = recent_upload_titles(cfg.channel.channel_id)
    import re

    want = re.compile(r"(?<!\d)" + re.escape(count_line(sleeps(day)).lower()))
    return any(want.search(t.lower()) and re.search(r"santa|elf|elves", t.lower()) for t in titles)


# --- render --------------------------------------------------------------------------------------------

def _overlay(cfg: ChannelConfig, day: date):
    from romanfeed.workshop import art

    n = sleeps(day)
    launch = day <= _from(_settings(cfg))
    top = ("SANTA'S WORKSHOP", "IS LIVE NOW!") if launch else (LINES[room(cfg, day)][0], None)
    if n <= 0:
        top = ("MERRY CHRISTMAS!", "from the elves")
    count = f"{n} SLEEP{'S' if n != 1 else ''}" if n > 0 else None

    def draw(cr, t, w, h):
        pop = min(1.0, t / 0.4)                       # caption pops in over the first 0.4 s
        size = 104 * (0.6 + 0.4 * pop)
        art.text(cr, top[0], w / 2, 300, size, "#FFFFFF", outline="#B3121F", ow=16, max_w=w - 120)
        if top[1]:
            art.text(cr, top[1], w / 2, 420, size * 0.8, "#FFE45C", outline="#5A1E12", ow=12, max_w=w - 160)
        if t >= SECONDS - 4:                          # the last 4 s point at the live stream
            art.rrect(cr, w / 2 - 400, 1250, 800, 210, 40)
            art.fill(cr, "#E0182D")
            art.text(cr, "Watch the elves", w / 2, 1318, 78, "#FFFFFF", outline=None, max_w=740)
            art.text(cr, "LIVE on our channel!", w / 2, 1408, 62, "#FFE45C", outline=None, max_w=740)
        elif count and t >= 1.5:
            art.rrect(cr, w / 2 - 330, 1250, 660, 210, 40)
            art.fill(cr, "#1E9E5A")
            art.text(cr, count, w / 2, 1320, 96, "#FFFFFF", outline="#0E4A2A", ow=10, max_w=600)
            art.text(cr, "until Christmas!", w / 2, 1412, 62, "#FFFFFF", outline="#0E4A2A", ow=8, max_w=600)
    return draw


def render(cfg: ChannelConfig, day: date, out: Path) -> Path:
    from romanfeed.workshop import art, music

    out.mkdir(parents=True, exist_ok=True)
    rid = room(cfg, day)
    silent = art.render(art.SCENES[rid].draw, out / f"kids-{day}-video.mp4", seconds=SECONDS, vertical=True,
                        overlay=_overlay(cfg, day))
    pcm, _ = music.render_pcm(f"workshop-short-{day}", target_s=SECONDS + 4)
    wav = out / f"kids-{day}.wav"
    music.write_wav(pcm, wav)
    dst = out / f"kids-{day}.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent), "-i", str(wav),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-af", f"loudnorm=I=-14:TP=-1.5,afade=t=in:d=0.3,afade=t=out:st={SECONDS - 2}:d=2",
                    "-t", str(SECONDS), "-movflags", "+faststart", str(dst)], check=True)
    silent.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    return dst


def metadata(cfg: ChannelConfig, day: date):
    from romanfeed.publish.metadata import VideoMetadata

    ws = _settings(cfg)
    # Animation that no one could take for real footage: YouTube's altered or
    # synthetic disclosure is for realistic content, so it stays off.
    return VideoMetadata(title=title(cfg, day), description=description(cfg, day), tags=TAGS,
                         category_id=CATEGORY, privacy="public", made_for_kids=bool(ws.made_for_kids),
                         synthetic=False)


def run(cfg: ChannelConfig, *, day: date | None = None, out: Path = Path("output/workshop-shorts"),
        dry_run: bool = False) -> str:
    from romanfeed.publish import youtube

    day = day or central_day()
    if not season(cfg, day):
        return f"workshop Short: {day} is outside the kids Shorts dates"
    if not dry_run and not cfg.shorts.enabled:
        return "workshop Short: Shorts are switched off for this channel"
    if not dry_run and posted(cfg, day):
        return f"workshop Short: {day} already posted ({count_line(sleeps(day))})"
    path = render(cfg, day, out)
    vid = youtube.publish(path, metadata(cfg, day), mode="dry-run" if dry_run else "upload")
    return f"workshop Short {day} ({room(cfg, day)}): " + (f"https://youtu.be/{vid}" if vid else f"dry run {path}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="romanfeed.workshop.shorts")
    ap.add_argument("config")
    ap.add_argument("--day")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="output/workshop-shorts")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(Path(a.config))
    print(run(cfg, day=date.fromisoformat(a.day) if a.day else None, out=Path(a.out), dry_run=a.dry_run))


if __name__ == "__main__":
    main()
