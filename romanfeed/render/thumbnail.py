"""Custom YouTube thumbnail: the lead image, full bleed, with a duration badge.

In the long-form sleep/ambient niche the viewer is choosing *how long* before
*what*, so the length is the headline: "8 HOURS" in big white type on a dark
rounded plate, bottom-left, with a small "SPACE FOR SLEEP" line under it. No
logo, no faces, nothing else -- the image does the rest.

Pillow only, same as the burned-in captions, and the same font lookup: DejaVu
Sans Bold where it is installed (CI installs fonts-dejavu-core), PIL's built-in
font otherwise. A missing font makes an uglier thumbnail, never a crash."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont, ImageOps

from romanfeed.render.captions import _font

MAX_BYTES = 2 * 1024 * 1024  # YouTube rejects thumbnails over 2 MB
DEFAULT_TAGLINE = "SPACE FOR SLEEP"


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        return _font(size, bold=True)
    except Exception:  # broken font file, Pillow built without FreeType
        return ImageFont.load_default()


def _glyph_box(font: ImageFont.ImageFont, text: str) -> tuple[int, int, int, int]:
    """Bounding box of the ink, relative to the draw origin."""
    return font.getbbox(text)


def _sized_font(text: str, cap_px: int, max_w: int) -> ImageFont.ImageFont:
    """A bold font whose ink for `text` is about cap_px tall and at most max_w wide."""
    size = max(int(cap_px / 0.73), 8)  # DejaVu Bold cap height is ~0.73 em
    font = _load_font(size)
    for _ in range(3):  # one correction usually lands it; bitmap fallback never moves
        x0, y0, x1, y1 = _glyph_box(font, text)
        h, w = max(y1 - y0, 1), max(x1 - x0, 1)
        scale = min(cap_px / h, max_w / w)
        new = max(int(size * scale), 8)
        if abs(new - size) <= 1:
            break
        size = new
        font = _load_font(size)
    return font


def _tracked_width(font, text: str, tracking: float) -> float:
    return sum(font.getlength(c) for c in text) + tracking * max(len(text) - 1, 0)


def _draw_tracked(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, tracking: float, fill) -> None:
    x, y = xy
    for c in text:
        d.text((x, y), c, font=font, fill=fill)
        x += font.getlength(c) + tracking


def _shade(w: int, h: int) -> Image.Image:
    """RGBA darkening layer: a gradient over the bottom half plus a soft vignette."""
    band = int(h * 0.55)
    col = Image.new("L", (1, band))
    col.putdata([int(200 * (i / band) ** 1.6) for i in range(band)])
    gradient = Image.new("L", (w, h), 0)
    gradient.paste(col.resize((w, band)), (0, h - band))
    # radial_gradient is 0 at the centre and 255 at the edge of its 256px square.
    vignette = Image.radial_gradient("L").resize((w, h), Image.BILINEAR)
    vignette = vignette.point(lambda v: int(max(0, v - 110) * 0.75))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.putalpha(ImageChops.lighter(gradient, vignette))
    return layer


def _encode(im: Image.Image, limit: int = MAX_BYTES) -> bytes:
    """JPEG at quality 90, stepping down until it fits YouTube's size limit."""
    data = b""
    for quality in (90, 85, 80, 75, 70, 60, 50, 40, 30):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        data = buf.getvalue()
        if len(data) < limit:
            return data
    return data  # a 1280x720 frame at q30 is far under 2 MB; kept for completeness


def make_thumbnail(image_path, out_path, *, length_label: str, subject: str | None = None,
                   size: tuple[int, int] = (1280, 720)) -> Path:
    """Render a thumbnail JPEG for one video and return its path.

    `length_label` is the headline ("8 Hours" -> "8 HOURS"). `subject`, when
    given, replaces the default "SPACE FOR SLEEP" line under it."""
    w, h = size
    with Image.open(image_path) as src:
        src.load()
        im = ImageOps.fit(src.convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.5))
    im = ImageEnhance.Contrast(im).enhance(1.15)
    im = ImageEnhance.Color(im).enhance(1.15)

    im = im.convert("RGBA")
    im.alpha_composite(_shade(w, h))

    headline = " ".join(str(length_label).split()).upper() or "SPACE"
    tagline = " ".join(str(subject or DEFAULT_TAGLINE).split()).upper()

    # Tight plate: the headline has to read at phone size, but every pixel of
    # plate is a pixel of nebula the viewer does not see.
    margin = int(h * 0.045)
    pad_x, pad_y = int(h * 0.036), int(h * 0.032)
    gap = int(h * 0.03)
    max_text_w = int(w * 0.72) - 2 * pad_x

    big = _sized_font(headline, int(h * 0.19), max_text_w)
    small = _sized_font(tagline, int(h * 0.055), max_text_w)
    bx0, by0, bx1, by1 = _glyph_box(big, headline)
    sx0, sy0, sx1, sy1 = _glyph_box(small, tagline)
    tracking = (sy1 - sy0) * 0.28
    small_w = _tracked_width(small, tagline, tracking)

    content_w = max(bx1 - bx0, small_w)
    content_h = (by1 - by0) + gap + (sy1 - sy0)
    plate = (margin, h - margin - content_h - 2 * pad_y, margin + content_w + 2 * pad_x, h - margin)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    radius = int(h * 0.03)
    d.rounded_rectangle(plate, radius=radius, fill=(6, 8, 18, 150), outline=(255, 255, 255, 40), width=max(1, h // 360))

    tx, ty = plate[0] + pad_x, plate[1] + pad_y
    shadow = max(2, h // 240)
    d.text((tx - bx0 + shadow, ty - by0 + shadow), headline, font=big, fill=(0, 0, 0, 150))
    d.text((tx - bx0, ty - by0), headline, font=big, fill=(255, 255, 255, 255))
    sy = ty + (by1 - by0) + gap
    _draw_tracked(d, (tx - sx0, sy - sy0), tagline, small, tracking, (235, 238, 250, 235))
    im.alpha_composite(overlay)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_encode(im.convert("RGB")))
    return out
