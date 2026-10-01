"""On-screen cards burned over the opening clip and over each Short.

Three layers, all transparent RGBA at output size, all Pillow (same reason as
captions.py: no fontconfig surprises in ffmpeg drawtext):

  intro_card      0-12 s of every long video: the object's name and the
                  telescope that imaged it. This is the "what you are about to
                  see" that YouTube's inauthentic-content review looks for, and
                  it is the first thing a sleep-searcher sees.
  subscribe_card  ~15-50 s: channel branding and a subscribe prompt, inside the
                  first minute -- a sleep video's audience is gone (asleep) long
                  before any end screen. Measured: 1 subscriber per 142 views.
  short_overlay   the whole of a vertical Short: object, telescope, and the
                  pointer to the full-length sleep video.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from romanfeed.render.captions import _font
from romanfeed.sources.base import ImageAsset
from romanfeed.telescopes import telescope_line


def _wrap(d: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: float, max_lines: int) -> list[str]:
    """Greedy word wrap; the last line is ellipsised if the text runs over."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if d.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and d.textlength(last + "…", font=font) > max_w:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last.rstrip(",.;:-") + "…"
    return lines


def _shadowed_text(layer: Image.Image, xy, text: str, font, fill, *, blur: int = 6, shadow_alpha: int = 200) -> None:
    """Text with a soft dark halo, so it reads on a bright nebula without a box."""
    halo = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).text(xy, text, font=font, fill=(0, 0, 0, shadow_alpha))
    layer.alpha_composite(halo.filter(ImageFilter.GaussianBlur(blur)))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=fill)


def _centered(layer: Image.Image, y: int, text: str, font, fill, **kw) -> int:
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font)
    _shadowed_text(layer, ((layer.width - (x1 - x0)) // 2 - x0, y - y0), text, font, fill, **kw)
    return y + (y1 - y0)


def lead_name(title: str, limit: int = 60) -> str:
    # Imported late: publish.metadata -> audio.library -> render.ffmpeg would
    # otherwise loop back through render/__init__ -> compose -> cards.
    from romanfeed.publish.metadata import lead_name as _lead_name

    return _lead_name(title, limit)


def _tracked(text: str) -> str:
    return " ".join(text.upper())  # cheap letter-spacing for the small labels


def intro_card(asset: ImageAsset, width: int, height: int, *, channel_name: str) -> Image.Image:
    """Object name, big and centred; the telescope under it; the channel above."""
    s = height / 1080
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # A soft dark wash behind the text block, so it reads on any image.
    wash = Image.new("L", (width, height), 0)
    ImageDraw.Draw(wash).ellipse(
        (width * 0.12, height * 0.28, width * 0.88, height * 0.72), fill=150,
    )
    wash = wash.filter(ImageFilter.GaussianBlur(int(120 * s)))
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shade.putalpha(wash)
    layer.alpha_composite(shade)

    d = ImageDraw.Draw(layer)
    label_font = _font(int(26 * s), bold=True)
    name_font = _font(int(88 * s), bold=True)
    tele_font = _font(int(38 * s))

    name = lead_name(asset.title, limit=60) or asset.title.strip()
    lines = _wrap(d, name, name_font, width * 0.78, 2)
    tele = telescope_line(asset)

    line_h = int(104 * s)
    block = int(46 * s) + line_h * len(lines) + (int(70 * s) if tele else 0)
    y = (height - block) // 2
    _centered(layer, y, _tracked(channel_name), label_font, (215, 220, 240, 230), blur=4)
    y += int(46 * s)
    for ln in lines:
        _centered(layer, y, ln, name_font, (255, 255, 255, 255), blur=10)
        y += line_h
    if tele:
        _centered(layer, y + int(14 * s), tele, tele_font, (225, 230, 245, 240), blur=6)
    return layer


def subscribe_card(width: int, height: int, *, channel_name: str, line: str) -> Image.Image:
    """Top-right: a red SUBSCRIBE pill with the channel name and a one-line why.

    Top-right because the lower third carries the image caption and YouTube's
    own controls cover the bottom on hover."""
    s = height / 1080
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    name_font = _font(int(34 * s), bold=True)
    line_font = _font(int(24 * s))
    btn_font = _font(int(26 * s), bold=True)

    pad = int(22 * s)
    btn_text = "SUBSCRIBE"
    bw = int(d.textlength(btn_text, font=btn_font)) + 2 * pad
    bh = int(52 * s)
    text_w = int(max(d.textlength(channel_name, font=name_font), d.textlength(line, font=line_font)))
    box_w = text_w + bw + 3 * pad
    box_h = int(96 * s)
    x0 = width - int(48 * s) - box_w
    y0 = int(44 * s)
    d.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=int(18 * s), fill=(8, 10, 22, 170),
                        outline=(255, 255, 255, 50), width=max(1, int(2 * s)))
    tx = x0 + pad
    d.text((tx, y0 + int(14 * s)), channel_name, font=name_font, fill=(255, 255, 255, 245))
    d.text((tx, y0 + int(56 * s)), line, font=line_font, fill=(215, 220, 240, 230))
    bx = x0 + box_w - pad - bw
    by = y0 + (box_h - bh) // 2
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=bh // 2, fill=(204, 0, 0, 235))
    tb = d.textbbox((0, 0), btn_text, font=btn_font)
    d.text((bx + (bw - (tb[2] - tb[0])) // 2 - tb[0], by + (bh - (tb[3] - tb[1])) // 2 - tb[1]),
           btn_text, font=btn_font, fill=(255, 255, 255, 255))
    return layer


def short_overlay(asset: ImageAsset, width: int, height: int, *, full_label: str, handle: str, cta: bool = True) -> Image.Image:
    """Overlay for a vertical Short, kept inside YouTube's safe zone.

    The Shorts player covers roughly the bottom fifth (title, channel row) and
    a strip down the right (like/comment/share), so text sits in the upper
    two-thirds and stays left of centre-right."""
    s = width / 1080
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Top fade so white text reads on bright frames.
    band = int(height * 0.34)
    col = Image.new("L", (1, band))
    col.putdata([int(170 * (1 - i / band) ** 1.5) for i in range(band)])
    top = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    mask = Image.new("L", (width, height), 0)
    mask.paste(col.resize((width, band)), (0, 0))
    top.putalpha(mask)
    layer.alpha_composite(top)

    d = ImageDraw.Draw(layer)
    name_font = _font(int(78 * s), bold=True)
    tele_font = _font(int(38 * s))

    max_w = width * 0.84
    y = int(height * 0.12)
    for ln in _wrap(d, lead_name(asset.title, limit=60) or asset.title, name_font, max_w, 2):
        y = _centered(layer, y, ln, name_font, (255, 255, 255, 255), blur=10) + int(18 * s)
    tele = telescope_line(asset)
    if tele:
        for ln in _wrap(d, tele, tele_font, max_w, 2):
            y = _centered(layer, y + int(10 * s), ln, tele_font, (225, 230, 245, 240)) + int(4 * s)

    if cta:
        _cta_plate(layer, full_label=full_label, handle=handle)
    return layer


def short_cta_card(width: int, height: int, *, full_label: str, handle: str) -> Image.Image:
    """The pointer plate alone, for narrated Shorts where it arrives at the end."""
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    _cta_plate(layer, full_label=full_label, handle=handle)
    return layer


def caption_card(text: str, width: int, height: int) -> Image.Image:
    """One burned-in caption chunk for a narrated Short: big, centred, two
    lines at most, in the middle band between the title and the safe-zone floor."""
    s = width / 1080
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _font(int(60 * s), bold=True)
    lines = _wrap(d, text, font, width * 0.82, 3)
    y = int(height * 0.50) - len(lines) * int(36 * s)
    for ln in lines:
        y = _centered(layer, y, ln, font, (255, 255, 255, 255), blur=8, shadow_alpha=240) + int(16 * s)
    return layer


def _cta_plate(layer: Image.Image, *, full_label: str, handle: str) -> None:
    """The pointer to the long video, on a plate, two-thirds of the way down."""
    width, height = layer.size
    s = width / 1080
    d = ImageDraw.Draw(layer)
    cta_font = _font(int(44 * s), bold=True)
    sub_font = _font(int(34 * s))
    max_w = width * 0.84
    cta = f"Full {full_label} sleep video on {handle}"
    lines = _wrap(d, cta, cta_font, max_w - 60 * s, 2)
    plate_h = int(len(lines) * 58 * s + 70 * s + 44 * s)
    py = int(height * 0.60)
    d.rounded_rectangle((int(width * 0.06), py, int(width * 0.94), py + plate_h), radius=int(28 * s),
                        fill=(8, 10, 22, 175), outline=(255, 255, 255, 55), width=max(1, int(2 * s)))
    yy = py + int(26 * s)
    for ln in lines:
        yy = _centered(layer, yy, ln, cta_font, (255, 255, 255, 255), blur=2, shadow_alpha=80) + int(16 * s)
    _centered(layer, yy + int(10 * s), "Subscribe for more space to sleep to", sub_font, (255, 200, 200, 240), blur=2, shadow_alpha=80)
