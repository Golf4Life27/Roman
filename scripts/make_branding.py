"""Generate channel branding from a public-domain NASA/STScI image.

  python scripts/make_branding.py [nasa_id]

Outputs (branding/):
  banner.jpg   2560x1440; all text inside YouTube's 1546x423 safe area
  avatar.png   800x800 circular nebula crop with a thin ring
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from romanfeed.config import SourceSettings
from romanfeed.render.captions import _font, fit_canvas
from romanfeed.sources.nasa_images import NasaImageLibrary

OUT = Path("branding")
NAME = "Space Screens"
TAGLINE = "Relaxing space videos for sleep and focus. Real telescope imagery, every day."
CREDIT = "Image: NASA, ESA, CSA, STScI"


def fetch(nasa_id: str) -> Path:
    lib = NasaImageLibrary(SourceSettings(type="nasa_images", queries=[nasa_id]))
    assets = [a for a in lib.fetch() if a.asset_id == f"nasa:{nasa_id}"]
    if not assets:
        raise SystemExit(f"no asset {nasa_id}")
    return assets[0].download("data/cache/images")


def banner(src: Path) -> Path:
    W, H = 2560, 1440
    SAFE_W, SAFE_H = 1546, 423
    with Image.open(src) as im:
        im.load()
        bg = fit_canvas(im, W, H)
    # Darken the safe area gently so the wordmark reads on bright nebulae.
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    cx, cy = W // 2, H // 2
    for r in range(0, 520, 4):
        a = int(150 * (1 - r / 520) ** 1.6)
        d.ellipse([cx - r * 2.2, cy - r, cx + r * 2.2, cy + r], outline=(0, 0, 0, a), width=4)
    overlay = overlay.filter(ImageFilter.GaussianBlur(24))
    out = Image.alpha_composite(bg.convert("RGBA"), overlay)
    d = ImageDraw.Draw(out)

    title_font = _font(150, bold=True)
    tag_font = _font(34)
    credit_font = _font(24)
    tw = d.textlength(NAME, font=title_font)
    ty = cy - 120
    # Soft shadow then text.
    d.text((cx - tw / 2 + 4, ty + 4), NAME, font=title_font, fill=(0, 0, 0, 160))
    d.text((cx - tw / 2, ty), NAME, font=title_font, fill=(255, 255, 255, 255))
    gw = d.textlength(TAGLINE, font=tag_font)
    d.text((cx - gw / 2, ty + 175), TAGLINE, font=tag_font, fill=(225, 228, 240, 235))
    # Thin rule under the tagline, still inside the safe area.
    d.line([(cx - 260, ty + 250), (cx + 260, ty + 250)], fill=(255, 255, 255, 110), width=2)
    # Credit in the TV-only region (bottom right, outside the safe area is fine here).
    cw = d.textlength(CREDIT, font=credit_font)
    d.text((W - cw - 40, H - 60), CREDIT, font=credit_font, fill=(200, 200, 210, 200))

    # Sanity: everything we drew sits inside the safe box.
    assert ty >= cy - SAFE_H // 2 and ty + 260 <= cy + SAFE_H // 2
    assert tw <= SAFE_W and gw <= SAFE_W
    path = OUT / "banner.jpg"
    out.convert("RGB").save(path, "JPEG", quality=90, optimize=True)
    return path


def avatar(src: Path) -> Path:
    S = 800
    with Image.open(src) as im:
        im.load()
        # Pull from the brightest, most textured part: centre-right of the Cosmic Cliffs.
        w, h = im.size
        box = (int(w * 0.30), int(h * 0.15), int(w * 0.30) + int(h * 0.7), int(h * 0.85))
        crop = im.crop(box).convert("RGB").resize((S, S), Image.LANCZOS)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, S - 1, S - 1], fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(crop, (0, 0), mask)
    d = ImageDraw.Draw(out)
    d.ellipse([6, 6, S - 7, S - 7], outline=(255, 255, 255, 230), width=10)
    path = OUT / "avatar.png"
    out.save(path, "PNG", optimize=True)
    return path


if __name__ == "__main__":
    nasa_id = sys.argv[1] if len(sys.argv) > 1 else "carina_nebula"
    OUT.mkdir(exist_ok=True)
    src = fetch(nasa_id)
    print("source:", src)
    print("banner:", banner(src))
    print("avatar:", avatar(src))
