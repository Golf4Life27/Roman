"""`romanfeed history`: today's videos for the history channel.

pick the date's events -> write and fact-check each script -> download the
public-domain image -> voice (Google TTS) -> compose music -> render ->
post per crosspost.mode at the day's slots (one video per slot).

Never stalls on one bad event: a failure is logged and the next video goes
on. With no TTS key the video is rendered with music and captions only
(a preview); nothing posts without a voice.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from romanfeed.config import ChannelConfig
from romanfeed.history import events as ev
from romanfeed.history import render as rd
from romanfeed.history import script as sc

log = logging.getLogger(__name__)

TAIL_S, LEAD_S = 2.5, 0.8
WORDS_PER_S = 2.6   # caption timing when there is no voice to measure


@dataclass
class Made:
    event: ev.Event
    script: sc.Script
    video: Path
    caption: str
    voiced: bool
    posted: object = None


def post_text(cfg: ChannelConfig, event: ev.Event, script: sc.Script, day: date) -> str:
    """TikTok/Instagram caption: what happened, the source and image credit, tags."""
    body = script.text.split(" ", 3)[-1] if script.text.startswith(sc.MONTHS[day.month - 1]) else script.text
    first = body.split(". ")[0].rstrip(".") + "."
    tags = " ".join(dict.fromkeys(cfg.history.hashtags))
    return "\n\n".join([
        f"{sc.MONTHS[day.month - 1]} {day.day}, {event.year}: {script.headline}",
        first,
        f"Source: Wikipedia. Image: {event.image.credit} ({event.image.licence}).",
        tags,
    ])[:2000]


def download(url: str, dest: Path) -> Path:
    s = requests.Session()
    s.headers["User-Agent"] = ev.USER_AGENT
    for attempt in range(4):
        r = s.get(url, timeout=60)
        if r.ok and r.headers.get("content-type", "").startswith("image"):
            dest.write_bytes(r.content)
            return dest
        if attempt < 3:
            log.warning("image download answered HTTP %s; retrying", r.status_code)
            time.sleep(min(60, 5 * 2 ** attempt))
    raise RuntimeError(f"could not download {url}: HTTP {r.status_code}")


def make_one(cfg: ChannelConfig, event: ev.Event, day: date, work: Path, out_dir: Path, *,
             tts_key: str | None, writer=None) -> Made:
    from romanfeed import narration
    from romanfeed.audio.composer import compose_piece
    from romanfeed.render.ffmpeg import probe_duration

    def step(what: str) -> None:   # progress in the run log
        log.info("history %s: %s", event.year, what)

    step("reading the articles")
    try:
        ev.enrich(event)
    except Exception as exc:   # the summaries are still a fine source
        log.warning("history: no full article text for %s (%s)", event.key, exc)
    step("writing the script")
    script = sc.write(event, day, client=writer)
    stem = f"{day.isoformat()}-{event.year}"
    step(f"downloading {event.image.file}")
    photo = download(event.image.url, work / f"{stem}.img")
    spoken = script.text
    if tts_key:
        step("voicing")
        voice = narration.synthesize(spoken, work / f"{stem}.voice.wav", api_key=tts_key, voice=cfg.history.voice)
        talk = probe_duration(str(voice))
    else:
        voice, talk = None, len(spoken.split()) / WORDS_PER_S
    seconds = min(59.0, LEAD_S + talk + TAIL_S)
    step(f"composing {seconds:.0f} s of music")
    music = compose_piece(work / f"{stem}.music.m4a", seconds=seconds + 2, seed=f"history-{stem}").path
    if voice:
        audio = narration.mix_voice(voice, music, work / f"{stem}.mix.m4a", voice_start=LEAD_S, duration=seconds,
                                    music_gain=cfg.history.music_gain)
    else:
        audio = music
    # Captions stop before the sign-off; the handle on screen says it already.
    said = spoken.removesuffix(sc.FOLLOW).strip()
    caps = narration.caption_chunks(said, start=LEAD_S, duration=talk * len(said) / max(len(spoken), 1))
    step("rendering")
    video = rd.render(photo, out_dir / f"{stem}.mp4", audio=audio, seconds=seconds, work=work / stem,
                      date_text=f"{sc.MONTHS[day.month - 1]} {day.day}", year=event.year,
                      headline=script.headline, subtitle=script.subtitle,
                      credit=f"Image: {event.image.credit} · {event.image.licence}", handle=cfg.channel.handle,
                      captions=caps)
    caption = post_text(cfg, event, script, day)
    (out_dir / f"{stem}.json").write_text(json.dumps({
        "year": event.year, "event": event.text, "script": script.text, "writer": script.writer,
        "headline": script.headline, "subtitle": script.subtitle, "image": event.image.__dict__,
        "caption": caption, "voiced": bool(voice),
    }, indent=2))
    return Made(event, script, video, caption, bool(voice))


def run(cfg: ChannelConfig, *, day: date | None = None, output_dir: Path = Path("output"), post: bool = True,
        writer=None) -> list[Made]:
    from romanfeed import crosspost, narration

    tz = ZoneInfo(cfg.crosspost.timezone)
    day = day or datetime.now(tz).date()
    out_dir = output_dir / "history" / day.isoformat()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    chosen = ev.pick(day, cfg.history.per_day)
    log.info("history %s: %d events: %s", day, len(chosen), ", ".join(str(e.year) for e in chosen))
    print(f"{day}: {len(chosen)} events: {', '.join(str(e.year) for e in chosen)}", flush=True)
    tts_key = narration.api_key()
    made: list[Made] = []
    for i, event in enumerate(chosen):
        try:
            m = make_one(cfg, event, day, work, out_dir, tts_key=tts_key, writer=writer)
        except Exception:
            log.exception("history: %s failed; going on with the rest", event.key)
            continue
        print(f"{event.year}: {m.script.headline} [{m.script.writer}] -> {m.video.name}")
        print(f"  {m.script.text}")
        if post and m.voiced and i < len(cfg.crosspost.slots):
            m.posted = crosspost.post_clip(cfg, m.video, m.caption, label=f"history {day} {event.year}",
                                           slots=[cfg.crosspost.slots[i]],
                                           now=datetime.now(timezone.utc))
        made.append(m)
    return made
