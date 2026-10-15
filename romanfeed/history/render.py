"""One vertical history video: 1080x1920, the length of its narration.

Its own look, so it never reads as a Space Screens template: a warm sepia
blur behind, the photo in a frame that slowly zooms, a gold serif date
header, the headline under the photo, captions in the band below, and the
credit and handle at the foot. Music is composed for the clip and sits well
under the voice.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from romanfeed.render import ffmpeg
from romanfeed.render.captions import _font
from romanfeed.render.cards import _wrap

log = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
GOLD = (236, 196, 118, 255)
CREAM = (250, 244, 232, 255)
PHOTO_TOP, PHOTO_MAX_W, PHOTO_MAX_H = 470, 1000, 660
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"


def serif(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(SERIF, size)
    except OSError:
        return _font(size, bold=True)


def _centered(d: ImageDraw.ImageDraw, y: int, text: str, font, fill, shadow: bool = True) -> int:
    w = d.textlength(text, font=font)
    x = (W - w) / 2
    if shadow:
        d.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 170))
    d.text((x, y), text, font=font, fill=fill)
    return y + font.size + 16


def photo_box(src: Path) -> tuple[int, int]:
    """(width, height) of the photo inside its frame."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        s = min(PHOTO_MAX_W / im.width, PHOTO_MAX_H / im.height)
        return int(im.width * s) // 2 * 2, int(im.height * s) // 2 * 2


def background(src: Path, out: Path) -> Path:
    """The photo blurred and warmed into sepia, filling the frame."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        s = max(W / im.width, H / im.height)
        bg = im.resize((int(im.width * s) + 2, int(im.height * s) + 2)).crop((0, 0, W, H))
    bg = bg.filter(ImageFilter.GaussianBlur(36))
    sepia = ImageOps.colorize(ImageOps.grayscale(bg), (18, 12, 6), (150, 118, 84))
    Image.blend(sepia, Image.new("RGB", (W, H), (14, 10, 6)), 0.45).save(out)
    return out


def overlay(out: Path, *, date_text: str, year: int, headline: str, subtitle: str, credit: str, handle: str,
            photo_h: int) -> Path:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = _centered(d, 120, "ON THIS DAY", _font(46, bold=True), GOLD)
    y = _centered(d, y, date_text.upper(), _font(46, bold=True), GOLD)
    _centered(d, y + 4, str(year), serif(150), CREAM)
    y = PHOTO_TOP + photo_h + 40
    size = 70   # one line: shrink to fit rather than wrap into the caption band
    while size > 44 and d.textlength(headline, font=serif(size)) > W * 0.92:
        size -= 2
    y = _centered(d, y, headline, serif(size), CREAM)
    if subtitle:
        _centered(d, y, subtitle, _font(36), (232, 224, 210, 235))
    _centered(d, H - 230, credit, _font(28), (200, 190, 175, 220))
    _centered(d, H - 182, handle, _font(38, bold=True), GOLD)
    layer.save(out)
    return out


def caption(out: Path, text: str, *, top: int) -> Path:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _font(52, bold=True)
    lines = _wrap(d, text, font, W * 0.86, 2)
    y = top
    for ln in lines:
        w = d.textlength(ln, font=font)
        d.rounded_rectangle(((W - w) / 2 - 18, y - 8, (W + w) / 2 + 18, y + font.size + 12), radius=14,
                            fill=(10, 8, 6, 170))
        d.text(((W - w) / 2, y), ln, font=font, fill=(255, 255, 255, 255))
        y += font.size + 26
    layer.save(out)
    return out


def render(photo: Path, out: Path, *, audio: Path, seconds: float, work: Path, date_text: str, year: int,
           headline: str, subtitle: str, credit: str, handle: str,
           captions: list[tuple[float, float, str]] | None = None) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    pw, ph = photo_box(photo)
    bg = background(photo, work / "bg.png")
    top = overlay(work / "overlay.png", date_text=date_text, year=year, headline=headline, subtitle=subtitle,
                  credit=credit, handle=handle, photo_h=ph)
    cap_top = PHOTO_TOP + ph + 40 + 86 + 60 + 40   # under the headline and subtitle
    layers = [(top, None)]
    for i, (t0, t1, text) in enumerate(captions or []):
        layers.append((caption(work / f"cap{i:02d}.png", text, top=cap_top), f"between(t,{t0:.3f},{t1:.3f})"))
    # The photo zooms in 8% over the clip, centred: scaled up a little more
    # each frame and cropped back to its box. (zoompan was far slower, and on
    # GitHub's small runners the still inputs queued frames faster than it
    # drew them until the machine ran out of memory.)
    # Both sides grow in even steps from exactly the box size, so the crop
    # never asks for more than the frame has (h=-2 rounded tall photos short).
    grow = f"2*ceil(%d*0.04*t/{seconds:.3f})"
    zoom = (f"[1:v]scale=w='{pw}+{grow % pw}':h='{ph}+{grow % ph}':eval=frame:flags=bicubic,"
            f"crop={pw}:{ph},pad={pw + 12}:{ph + 12}:6:6:color=0xE9D9B8[ph]")
    parts = [zoom, f"[0:v][ph]overlay={(W - pw - 12) // 2}:{PHOTO_TOP - 6}:shortest=1[b0]"]
    # Every still input is exactly as long as the clip, so nothing can queue up.
    still = ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}"]
    inputs = [*still, "-i", str(bg), *still, "-i", str(photo)]
    prev = "[b0]"
    for k, (png, enable) in enumerate(layers, start=2):
        inputs += [*still, "-i", str(png)]
        en = f":enable='{enable}'" if enable else ""
        parts.append(f"{prev}[{k}:v]overlay=0:0{en}[b{k}]")
        prev = f"[b{k}]"
    parts.append(f"{prev}fade=t=in:st=0:d=0.5,fade=t=out:st={max(seconds - 0.8, 0):.3f}:d=0.8,format=yuv420p[v]")
    ffmpeg.run([
        *inputs, "-i", str(audio),
        "-filter_complex", ";".join(parts), "-map", "[v]", "-map", f"{len(layers) + 2}:a:0",
        "-t", f"{seconds:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", str(out),
    ])
    return out
