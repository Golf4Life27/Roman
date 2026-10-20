"""The weekly cozy upload: one scene loop, a few hours long, original music.

Built without rendering hours of video: two short encodes and a stream copy.

  loop   the scene's steady middle with a dissolved seam (cozy/seam.py)
  head   about the first 60 s (passes of the loop) with the intro card (what
         this is) and the subscribe card, inside the first minute like every
         other video on the channel
  body   one clean pass of the loop, encoded with the head's exact settings
  video  head + body x N through the concat demuxer (-c copy), then the
         composed soundtrack muxed on

The upload is marked as altered/synthetic content (status.containsSyntheticMedia)
and says so in the description: the scene is AI-animated. YouTube asks for
the label on realistic synthetic scenes, and saying it up front is cheaper
than a strike.

`cozy.enabled` is the publish switch; off, everything is rendered and the
request body written next to the file, and nothing is uploaded.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from romanfeed.config import ChannelConfig
from romanfeed.cozy import scenes as sc
from romanfeed.cozy import seam, titles
from romanfeed.cozy.calendar import Theme, fetch_launches, themes_for
from romanfeed.publish.metadata import HASHTAGS, VideoMetadata, length_text
from romanfeed.render import ffmpeg
from romanfeed.render.cards import intro_card, subscribe_card
from romanfeed.sources.base import ImageAsset

log = logging.getLogger(__name__)

FPS = 24
GOP = 48
HEAD_SECONDS = 60        # the head (intro and subscribe cards) is about the first minute
REUSE_AFTER_DAYS = 56    # a scene can headline a weekly video again after 8 weeks


def ledger_channel(cfg: ChannelConfig) -> str:
    """Cozy uploads sit under their own name in the ledger, so the Shorts
    job (which cuts from the channel's space videos) never picks one up."""
    return f"{cfg.channel.slug}-cozy"


def choose(library: sc.Library, day: date, recent: dict[str, date], *,
           themes: list[Theme] | None = None) -> tuple[sc.Scene, Theme] | None:
    """The week's scene: the most specific theme with a scene not shown in
    the last eight weeks; failing that, the plain calendar pick."""
    themes = themes or themes_for(day)
    for theme in themes:
        fresh = [s for s in sorted(library.tagged(theme.key), key=lambda s: s.id)
                 if s.id not in recent or (day - recent[s.id]).days >= REUSE_AFTER_DAYS]
        if fresh:
            return fresh[day.toordinal() % len(fresh)], theme
    return sc.pick(library, day, themes)


def _x264(cfg: ChannelConfig) -> list[str]:
    """Identical for head and body, so the concat demuxer can copy across."""
    return ["-an", "-r", str(FPS), "-c:v", "libx264", "-preset", cfg.video.preset, "-crf", str(cfg.video.crf),
            "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0", "-pix_fmt", "yuv420p"]


def _frames(src: Path) -> int:
    """One pass of a smooth loop (seam.smooth_loop): already whole GOPs."""
    return seam.count_frames(src)


def head_passes(n: int) -> int:
    return max(1, math.ceil(HEAD_SECONDS * FPS / n))


def render_body(src: Path, out: Path, cfg: ChannelConfig) -> Path:
    n = _frames(src)
    ffmpeg.run(["-i", str(src), "-vf",
                f"fps={FPS},scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                f"trim=end_frame={n},setpts=PTS-STARTPTS"] + _x264(cfg) + [str(out)])
    return out


def render_head(src: Path, out: Path, cfg: ChannelConfig, *, title: str, work: Path) -> Path:
    """About a minute of the loop with the intro card, then the subscribe card."""
    n = _frames(src)
    passes = head_passes(n)
    v = cfg.video
    intro = work / "intro.png"
    intro_card(ImageAsset(asset_id="cozy:x", title=title, url="", source=""), 1920, 1080,
               channel_name=cfg.channel.name).save(intro)
    sub = work / "subscribe.png"
    subscribe_card(1920, 1080, channel_name=cfg.channel.name, line=v.subscribe_line).save(sub)
    head_s = passes * n / FPS
    s0, s1 = v.subscribe_from, min(v.subscribe_until, head_s - 2)
    graph = (
        f"[0:v]fps={FPS},scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
        f"trim=end_frame={n},setpts=PTS-STARTPTS,loop=loop={passes - 1}:size={n}:start=0,setpts=N/{FPS}/TB[b];"
        f"[1:v]format=rgba,fade=in:st=0.6:d=1.2:alpha=1,fade=out:st={v.intro_seconds - 1.2}:d=1.2:alpha=1[i];"
        f"[2:v]format=rgba,fade=in:st={s0}:d=1.0:alpha=1,fade=out:st={s1 - 1.0}:d=1.0:alpha=1[s];"
        f"[b][i]overlay=0:0:enable='lt(t,{v.intro_seconds})'[bi];"
        f"[bi][s]overlay=0:0:enable='between(t,{s0},{s1})'[out]"
    )
    ffmpeg.run(["-i", str(src),
                "-loop", "1", "-framerate", str(FPS), "-t", f"{head_s:.3f}", "-i", str(intro),
                "-loop", "1", "-framerate", str(FPS), "-t", f"{head_s:.3f}", "-i", str(sub),
                "-filter_complex", graph, "-map", "[out]", "-frames:v", str(passes * n)]
               + _x264(cfg) + [str(out)])
    return out


def build_video(src: Path, cfg: ChannelConfig, *, title: str, hours: float, work: Path, out: Path,
                seed: str) -> Path:
    from romanfeed.audio import build_soundtrack
    from romanfeed.audio.composer import open_library
    from romanfeed.render.compose import concat_clips, mux_audio

    work.mkdir(parents=True, exist_ok=True)
    src, _ = seam.smooth_loop(src, work / "smooth.mp4")
    head = render_head(src, work / "head.mp4", cfg, title=title, work=work)
    body = render_body(src, work / "body.mp4", cfg)
    head_s = ffmpeg.probe_duration(str(head))
    body_s = ffmpeg.probe_duration(str(body))
    passes = max(1, math.ceil((hours * 3600 - head_s) / body_s))
    silent = concat_clips([head] + [body] * passes, work / "silent.mp4")
    total = head_s + passes * body_s
    library = open_library(cfg.audio, work_dir=work, seed=seed)
    audio, _ = build_soundtrack(library, genre=cfg.audio.genre, duration=total, out_path=work / "soundtrack.m4a",
                                fade=cfg.audio.fade_seconds, crossfade=cfg.audio.crossfade_seconds,
                                gain_db=cfg.audio.gain_db, allow_placeholder=cfg.audio.allow_placeholder, seed=seed)
    mux_audio(silent, audio, out, faststart=hours <= 1.5)
    silent.unlink(missing_ok=True)
    return out


def metadata(cfg: ChannelConfig, scene: sc.Scene, theme: Theme, hours: float, *,
             day: date | None = None) -> VideoMetadata:
    """Seasonal title (cozy/titles.py): 'Cozy Halloween Observatory on an
    October Night 🎃 3 Hours of Calm Space Music for Sleep'."""
    c = cfg.cozy
    length = length_text(hours * 3600)
    title = titles.seasonal(scene.title, when=day or date.today(), theme_key=theme.key, hours_label=f"{hours:g}")
    handle = cfg.channel.handle.strip()
    lines = [
        f"{length} of cozy ambience for sleep, study and calm: a warm {scene.title.lower()} on a quiet night, "
        "with the stars outside and soft original space music.",
    ]
    if handle:
        lines.append(f"Subscribe for more space to fall asleep to: https://www.youtube.com/{handle}?sub_confirmation=1")
    lines += [
        "ABOUT THIS VIDEO\nThe scene is AI-generated and animated (Runway), made for this channel and looped "
        "seamlessly. The music is original ambient, composed for this video.",
        f"More from {cfg.channel.name}: real Hubble and Webb telescope imagery, slowly, every week.",
        HASHTAGS,
    ]
    tags = ["cozy ambience", "sleep music", "fireplace sounds", "cozy night", "space music for sleep",
            "relaxing music", "study music", "ambient music", theme.label.lower(), scene.title.lower()]
    return VideoMetadata(title=title, description="\n\n".join(lines), tags=tags, category_id=cfg.publish.category_id,
                         privacy=c.privacy, synthetic=True)


@dataclass
class CozyResult:
    path: Path
    meta: VideoMetadata
    scene: sc.Scene
    youtube_id: str | None


def run(cfg: ChannelConfig, *, today: date, data_dir: Path, output_dir: Path, dry_run: bool = False,
        hours: float | None = None) -> CozyResult | None:
    from romanfeed.publish import publish
    from romanfeed.render.thumbnail import make_thumbnail
    from romanfeed.state import Ledger, VideoRecord

    uploading = cfg.cozy.enabled and not dry_run
    preview = not uploading and hours is None
    hours = hours or (0.1 if preview else cfg.cozy.hours)  # a preview is 6 minutes, not 4 GB
    work = output_dir / "cozy-work"
    work.mkdir(parents=True, exist_ok=True)
    library = sc.fetch_manifest(cfg.cozy.repo, cfg.cozy.release_tag, work / sc.MANIFEST)
    chan = ledger_channel(cfg)
    with Ledger(data_dir / "state.db") as ledger:
        recent = {}
        for v in ledger.videos(chan):
            sid = v.video_slug.split("--", 1)[-1]
            recent.setdefault(sid, date.fromisoformat(v.rendered_at[:10]))
    # Theme of the days the video will mostly be found in: the coming week.
    week = today + timedelta(days=3)
    picked = choose(library, week, recent, themes=themes_for(week, launches=fetch_launches(cache=work / "launches.json")))
    if picked is None:
        print("no cozy scene in the library yet; nothing to make")
        return None
    scene, theme = picked
    print(f"scene: {scene.title} ({scene.id}) for {theme.label}")
    src = sc.fetch_scene(cfg.cozy.repo, cfg.cozy.release_tag, scene, work / "scenes")
    slug = f"{today.isoformat()}--{scene.id}"
    out = output_dir / "cozy" / f"cozy-{slug}{'-preview' if preview else ''}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    build_video(src, cfg, title=f"Cozy {scene.title}", hours=hours, work=work, out=out, seed=f"cozy-{slug}")
    meta_hours = cfg.cozy.hours if preview else hours
    still = work / "still.png"
    ffmpeg.run(["-i", str(src), "-frames:v", "1", str(still)])
    thumb = make_thumbnail(still, out.with_suffix(".jpg"), length_label=length_text(meta_hours * 3600),
                           subject=f"Cozy {theme.label}" if theme.key != "evergreen" else "Cozy Space Night")
    meta = metadata(cfg, scene, theme, meta_hours, day=today)
    mode = "upload" if uploading else "dry-run"
    if mode == "dry-run" and not cfg.cozy.enabled:
        print("cozy.enabled is false: rendered, not uploaded (the switch is the owner's call)")
    vid = publish(out, meta, mode=mode, thumbnail=thumb)
    print(f"title: {meta.title}")
    if vid:
        with Ledger(data_dir / "state.db") as ledger:
            ledger.record_video(VideoRecord(slug, chan, str(out), ffmpeg.probe_duration(str(out)),
                                            today.isoformat() + "T00:00:00", youtube_id=vid, title=meta.title))
            ledger.mark_published(slug, vid, meta.title)
        print(f"uploaded https://youtu.be/{vid}")
    return CozyResult(out, meta, scene, vid)
