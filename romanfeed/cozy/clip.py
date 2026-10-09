"""The weekly cozy clip for TikTok and Instagram.

The season's cozy scene as a 20-second vertical clip: the whole 16:9 scene
in the middle of the 9:16 frame over a blurred, darkened copy of itself (a
tight crop would cut the fireplace or the window), the scene's name above,
and the pointer below: "Cozy nights live on YouTube · @SpaceScreens". That
pointer is true on every scene: the live stream plays these scenes.

Twenty seconds is two passes of the 10 s loop (each trimmed of its duplicate
closing frame), so when TikTok replays the clip the picture does not jump.
The music is composed for the clip. The scene is AI-made: the caption says
so, and TikTok's AI label rides on crosspost.made_with_ai.

Posting goes through romanfeed.crosspost (so crosspost.mode switches it), at
crosspost.cozy_slots, a slot of its own, never on top of a Short.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from romanfeed.config import ChannelConfig
from romanfeed.cozy import scenes as sc
from romanfeed.cozy.calendar import Theme, fetch_launches, themes_for
from romanfeed.render import ffmpeg
from romanfeed.render.cards import _centered, _wrap
from romanfeed.render.captions import _font

log = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 24
LOOP_FRAMES = 240     # one 10 s pass at 24 fps, without the duplicate closing frame
POINTER = "Cozy nights live on YouTube"

EMOJI = {"halloween": "🎃", "christmas": "🎄", "new_year": "✨", "thanksgiving": "🍂", "autumn": "🍂",
         "winter": "❄️", "meteors": "🌠", "eclipse": "🌘", "launch": "🚀", "valentines": "🕯️",
         "spring": "🌸", "easter": "🌸", "summer": "🌙", "independence_day": "🎆"}
THEME_TAG = {"halloween": "#halloween", "christmas": "#christmas", "new_year": "#newyear",
             "thanksgiving": "#autumnvibes", "autumn": "#autumnvibes", "winter": "#winter",
             "meteors": "#meteorshower", "launch": "#rocketlaunch", "summer": "#summernight"}


def overlay_png(path: Path, *, title: str, handle: str) -> Path:
    """Scene name above the picture, the pointer plate below it."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    title_font = _font(76, bold=True)
    y = 330
    for ln in _wrap(d, title, title_font, W * 0.86, 2):
        y = _centered(layer, y, ln, title_font, (255, 255, 255, 255), blur=10) + 16
    call_font, sub_font = _font(52, bold=True), _font(40)
    py = 1330
    d.rounded_rectangle((70, py, W - 70, py + 190), radius=30, fill=(18, 14, 30, 200),
                        outline=(255, 214, 160, 120), width=3)
    _centered(layer, py + 34, POINTER, call_font, (255, 236, 210, 255), blur=2, shadow_alpha=90)
    _centered(layer, py + 112, handle or "Space Screens", sub_font, (255, 236, 210, 235), blur=2, shadow_alpha=80)
    layer.save(path)
    return path


def render_clip(loop: Path, out: Path, *, title: str, handle: str, seconds: int, work: Path, seed: str) -> Path:
    from romanfeed.audio.composer import compose_piece

    work.mkdir(parents=True, exist_ok=True)
    passes = max(1, round(seconds * FPS / LOOP_FRAMES))
    frames = passes * LOOP_FRAMES
    dur = frames / FPS
    music = compose_piece(work / "clip-music.m4a", seconds=dur + 3, seed=seed).path
    text = overlay_png(work / "clip-text.png", title=title, handle=handle)
    graph = (
        f"[0:v]fps={FPS},scale=1920:1080,trim=end_frame={LOOP_FRAMES},setpts=PTS-STARTPTS,"
        f"loop=loop={passes - 1}:size={LOOP_FRAMES}:start=0,setpts=N/{FPS}/TB,split[a][b];"
        f"[a]scale=-2:{H},crop={W}:{H},boxblur=28:2,eq=brightness=-0.18:saturation=0.9[bg];"
        f"[b]scale={W}:-2[fg];"
        f"[bg][fg]overlay=0:(H-h)/2[v0];[v0][1:v]overlay=0:0,format=yuv420p[v]"
    )
    ffmpeg.run([
        "-i", str(loop), "-loop", "1", "-framerate", str(FPS), "-i", str(text), "-i", str(music),
        "-filter_complex", graph + f";[2:a]afade=t=in:d=1.0,afade=t=out:st={dur - 1.5:.3f}:d=1.5[aud]",
        "-map", "[v]", "-map", "[aud]", "-frames:v", str(frames), "-t", f"{dur:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", str(out),
    ])
    return out


def caption(cfg: ChannelConfig, scene: sc.Scene, theme: Theme) -> str:
    emoji = EMOJI.get(theme.key, "🌌")
    tags = list(dict.fromkeys(cfg.crosspost.cozy_hashtags + [THEME_TAG.get(theme.key, "#space")]))[:5]
    return "\n\n".join([
        f"Cozy {scene.title} {emoji}",
        "A warm room, a quiet night and the stars outside. Stay as long as you like.",
        f"Cozy nights live on YouTube: {cfg.channel.handle or cfg.channel.name}",
        "AI-animated scene, original music.",
        " ".join(tags),
    ])


def run(cfg: ChannelConfig, *, today: date, output_dir: Path, now: datetime | None = None,
        release=None, relay: str | None = None):
    """Render this week's cozy clip and post it per crosspost.mode. Returns
    (clip path, Posted or None), or None when the library has nothing."""
    from romanfeed import crosspost

    work = output_dir / "cozy-clip-work"
    work.mkdir(parents=True, exist_ok=True)
    library = sc.fetch_manifest(cfg.cozy.repo, cfg.cozy.release_tag, work / sc.MANIFEST)
    picked = sc.pick(library, today, themes_for(today, launches=fetch_launches(cache=work / "launches.json")))
    if picked is None:
        print("no cozy scene in the library yet; no clip this week")
        return None
    scene, theme = picked
    loop = sc.fetch_scene(cfg.cozy.repo, cfg.cozy.release_tag, scene, work / "scenes")
    out = output_dir / "cozy" / f"cozy-clip-{today.isoformat()}-{scene.id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    render_clip(loop, out, title=f"Cozy {scene.title}", handle=cfg.channel.handle,
                seconds=cfg.crosspost.cozy_seconds, work=work, seed=f"cozyclip-{today.isoformat()}-{scene.id}")
    text = caption(cfg, scene, theme)
    print(f"clip: {out.name} ({scene.title} for {theme.label})")
    print(text)
    posted = crosspost.post_clip(cfg, out, text, label=f"cozy clip {scene.id}", slots=cfg.crosspost.cozy_slots,
                                 now=now or datetime.now(timezone.utc), release=release, relay=relay)
    if posted:
        print(f"scheduled: Zernio post {posted.post_id} for {posted.scheduled_for}")
    elif crosspost.effective_mode(cfg) == "off":
        print("crosspost.mode is off: rendered, not posted (the switch is the owner's call)")
    return out, posted
