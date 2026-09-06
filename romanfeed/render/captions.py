"""Burn a lower-third caption (title, credit, one-line description) into a
frame with Pillow. Doing this in Pillow rather than ffmpeg drawtext keeps the
ffmpeg graph simple and avoids fontconfig differences between machines."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from romanfeed.sources.base import ImageAsset

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for cand in FONT_CANDIDATES:
        p = cand.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf") if bold else cand
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def fit_canvas(im: Image.Image, width: int, height: int) -> Image.Image:
    """Cover-fit the image onto a width x height canvas.

    Wide images are cropped slightly; tall images get a blurred, darkened
    copy of themselves as background so we never show black bars."""
    im = im.convert("RGB")
    scale = max(width / im.width, height / im.height)
    ratio = im.width / im.height
    target_ratio = width / height
    if abs(ratio - target_ratio) / target_ratio < 0.35:
        resized = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        return resized.crop((left, top, left + width, top + height))
    # Portrait / very square: blurred backdrop + contained foreground.
    bg = im.resize((round(im.width * scale), round(im.height * scale)), Image.BILINEAR)
    left = (bg.width - width) // 2
    top = (bg.height - height) // 2
    bg = bg.crop((left, top, left + width, top + height)).filter(ImageFilter.GaussianBlur(40))
    bg = Image.eval(bg, lambda v: int(v * 0.45))
    contain = min(width / im.width, height / im.height)
    fg = im.resize((round(im.width * contain), round(im.height * contain)), Image.LANCZOS)
    bg.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
    return bg


def _fit_text(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: float) -> str:
    """Truncate on a word boundary so the line fits in max_w pixels."""
    if d.textlength(text, font=font) <= max_w:
        return text
    words = text.split()
    while words and d.textlength(" ".join(words) + "…", font=font) > max_w:
        words.pop()
    return (" ".join(words).rstrip(",.;:") + "…") if words else ""


def caption_layer(asset: ImageAsset, width: int, height: int) -> Image.Image:
    """Transparent RGBA layer with the lower-third caption, at output size.

    Overlaid by ffmpeg *after* zoompan so the text never gets cropped by the
    zoom and stays still while the image drifts."""
    w, h = width, height
    scale = h / 1080
    pad = int(48 * scale)
    title_font = _font(int(40 * scale), bold=True)
    body_font = _font(int(26 * scale))
    credit_font = _font(int(22 * scale))

    title = asset.title.strip()[:90]
    body = asset.short_description
    credit = f"Image: {asset.credit or asset.source}"

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    band_h = int(190 * scale)
    # Soft gradient band so text stays legible on bright nebulae.
    for i in range(band_h):
        alpha = int(170 * (i / band_h) ** 1.4)
        d.line([(0, h - band_h + i), (w, h - band_h + i)], fill=(0, 0, 0, alpha))

    max_w = w - 2 * pad
    y = h - band_h + int(30 * scale)
    d.text((pad, y), _fit_text(d, title, title_font, max_w), font=title_font, fill=(255, 255, 255, 235))
    y += int(52 * scale)
    credit = _fit_text(d, credit, credit_font, max_w * 0.5)
    cw = d.textlength(credit, font=credit_font)
    d.text((pad, y), _fit_text(d, body, body_font, max_w - cw - pad), font=body_font, fill=(225, 225, 235, 220))
    d.text((w - pad - cw, h - pad - int(6 * scale)), credit, font=credit_font, fill=(190, 190, 205, 210))
    return overlay


def prepare_frame(asset: ImageAsset, out_path: Path, *, width: int, height: int, captions: bool, supersample: int = 2) -> tuple[Path, Path | None]:
    """Write a cover-fitted PNG at supersample x output size, plus an optional
    caption layer PNG at output size. Returns (frame_png, caption_png|None).

    zoompan looks best when its input is larger than its output, so we render
    frames at 2x and let ffmpeg scale down while zooming."""
    if asset.local_path is None:
        raise ValueError(f"{asset.asset_id} not downloaded")
    with Image.open(asset.local_path) as src:
        src.load()
        frame = fit_canvas(src, width * supersample, height * supersample)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.save(out_path, "PNG", compress_level=1)
    cap_path = None
    if captions:
        cap_path = out_path.with_name(out_path.stem + "_caption.png")
        caption_layer(asset, width, height).save(cap_path, "PNG", compress_level=1)
    return out_path, cap_path
