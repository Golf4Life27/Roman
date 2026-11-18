"""Split one telescope image into the layers the camera flies through.

  stars       small, compact bright points (and their diffraction spikes),
              lifted off the picture with a grey top-hat. Foreground stars sit
              in front of the gas in most nebula pictures, so this layer moves
              as the nearest one and streams past as the camera pushes in.
  background  everything else: the gas, dust and galaxies, which moves at
              per-pixel rates from the depth map (near parts faster).

The split is exact: background + stars == the original image, so the first
frame of every pass is the real picture, untouched."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter


def _slide(a: np.ndarray, k: int, axis: int, op) -> np.ndarray:
    """Running min/max of width k (odd) along one axis, edges replicated."""
    r = k // 2
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    p = np.pad(a, pad, mode="edge")
    n = a.shape[axis]
    out = np.take(p, range(0, n), axis=axis).copy()
    for i in range(1, k):
        op(out, np.take(p, range(i, i + n), axis=axis), out=out)
    return out


def grey_open(a: np.ndarray, k: int) -> np.ndarray:
    """Grey-scale opening with a k x k square: removes bright features
    narrower than k pixels and keeps everything wider exactly."""
    m = _slide(_slide(a, k, 0, np.minimum), k, 1, np.minimum)
    return _slide(_slide(m, k, 0, np.maximum), k, 1, np.maximum)


def grey_dilate(mask: np.ndarray, k: int) -> np.ndarray:
    """Binary dilation of a 2-D bool mask with a k x k square."""
    m = mask.astype(np.uint8)
    return _slide(_slide(m, k, 0, np.maximum), k, 1, np.maximum).astype(bool)


def star_kernel(width: int) -> int:
    """Top-hat width in pixels: wide enough to lift a bright star's core
    (about one 170th of the image width), odd, >= 5."""
    k = max(5, int(round(width / 170)))
    return k if k % 2 else k + 1


def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    try:
        import cv2  # type: ignore

        return cv2.GaussianBlur(a, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
    except ImportError:
        from romanfeed.journey.depth import gaussian_blur

        return gaussian_blur(a, sigma)


def find_stars(lum: np.ndarray, *, scales: tuple[float, ...] | None = None, threshold: float = 12.0) -> list[tuple[int, int, float, float]]:
    """Compact bright points: (x, y, detection scale, blob strength).

    A star is a blob, bright in every direction from its centre; a filament
    or a cliff's lit rim is a ridge, bright along its length. The Hessian
    tells them apart: at a blob both curvatures are strongly negative, along
    a ridge one of them is near zero. Scale-normalised over a few sizes so
    faint pin-pricks and fat bright cores both count."""
    h, w = lum.shape
    base = max(1.0, w / 3000)
    scales = scales or (1.2 * base, 2.5 * base, 5.0 * base)
    # One entry per star: detections at several scales land within a pixel or
    # two of each other, so they are merged on a grid of the largest scale.
    cell = max(2, int(round(scales[-1])))
    found: dict[tuple[int, int], tuple[float, int, int, float]] = {}
    for sig in scales:
        g = _blur(lum, sig)
        gxx = np.zeros_like(g)
        gyy = np.zeros_like(g)
        gxy = np.zeros_like(g)
        gxx[:, 1:-1] = g[:, 2:] - 2 * g[:, 1:-1] + g[:, :-2]
        gyy[1:-1, :] = g[2:, :] - 2 * g[1:-1, :] + g[:-2, :]
        gxy[1:-1, 1:-1] = (g[2:, 2:] - g[2:, :-2] - g[:-2, 2:] + g[:-2, :-2]) / 4
        half = (gxx + gyy) / 2
        root = np.sqrt(np.maximum(half * half - (gxx * gyy - gxy * gxy), 0))
        weak = -(half + root) * sig * sig      # the less-curved direction, sign flipped: big = blob
        peak = (weak > threshold) & (weak >= _slide(_slide(weak, 3, 0, np.maximum), 3, 1, np.maximum))
        for y, x in zip(*np.nonzero(peak)):
            key = (int(x) // cell, int(y) // cell)
            r = float(weak[y, x])
            if key not in found or found[key][0] < r:
                found[key] = (r, int(x), int(y), float(sig))
    return [(x, y, sig, r) for (r, x, y, sig) in found.values()]


def split_stars(rgb: np.ndarray, *, kernel: int | None = None, threshold: float = 12.0,
                spiked: int = 40) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(background, stars, star mask) with background + stars == rgb exactly.

    The star layer is the top-hat (what stands above its opened neighbourhood)
    inside a soft disc around each detected star. The `spiked` biggest,
    brightest stars are left whole in the background instead, inside a wide
    clear disc: their cores and diffraction spikes must move as one, and the
    top-hat cannot lift a fat saturated core cleanly. Outside the discs
    nothing moves to the star layer, so lit filament edges stay with the gas."""
    from PIL import ImageDraw

    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = rgb.shape[:2]
    lum = (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114).astype(np.float32)
    stars = find_stars(lum, threshold=threshold)
    mask_im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask_im)
    big = sorted(stars, key=lambda s: -(s[2] * s[3]))[:spiked]
    for x, y, sig, _r in stars:
        r = 2.5 * sig + 1.5
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    for rank, (x, y, _s, _r) in enumerate(big):
        r = w * 0.025 * (1 - rank / max(1, len(big))) ** 0.5 + 6
        d.ellipse((x - r, y - r, x + r, y + r), fill=0)
    mask = np.asarray(mask_im.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32) / 255.0
    k = kernel or star_kernel(w)
    tophat = rgb.astype(np.int16) - grey_open(rgb, k).astype(np.int16)    # >= 0 by construction
    star = np.round(tophat * mask[..., None]).astype(np.uint8)
    return (rgb - star).astype(np.uint8), star, mask


def fill_holes(img: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Fill the unknown pixels of a small float image (2-D or H x W x C)
    from their known surroundings: normalised convolution at growing
    blur widths, nearest structure first. Used for what lies behind the
    near layer, which the camera only ever glimpses past its edge."""
    img = img.astype(np.float32)
    chans = img[..., None] if img.ndim == 2 else img
    have = known.astype(np.float32)
    out = chans * have[..., None]
    filled = have.copy()
    sigma = 1.5
    big = max(img.shape[:2])
    while filled.min() < 0.5 and sigma < big:
        den = _blur(filled, sigma)
        ok = (den > 0.15) & (filled < 0.5)
        if ok.any():
            for c in range(chans.shape[2]):
                num = _blur(out[..., c] * filled, sigma)
                out[..., c] = np.where(ok, num / np.maximum(den, 1e-6), out[..., c])
            filled = np.where(ok, 1.0, filled).astype(np.float32)
        sigma *= 1.6
    if filled.min() < 0.5:   # nothing known anywhere near: the mean of what is
        mean = (chans * have[..., None]).sum((0, 1)) / max(1.0, have.sum())
        out = np.where(filled[..., None] < 0.5, mean, out)
    return out[..., 0] if img.ndim == 2 else out


def otsu(values: np.ndarray, bins: int = 64) -> float:
    """The threshold that best splits `values` (in [0, 1]) into two groups."""
    hist, edges = np.histogram(values, bins=bins, range=(0.0, 1.0))
    p = hist.astype(np.float64) / max(1, hist.sum())
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(p)
    m0 = np.cumsum(p * centres)
    mt = m0[-1]
    between = (mt * w0 - m0) ** 2 / np.maximum(w0 * (1 - w0), 1e-12)
    between = between[:-1]
    # Where the best split is a plateau (empty bins between two groups),
    # take its middle rather than its first edge.
    best = np.flatnonzero(between >= between.max() * (1 - 1e-9))
    return float(edges[int(round((best[0] + best[-1]) / 2)) + 1])


@dataclass
class Target:
    """A place in the picture worth flying toward, in source pixels."""
    x: float
    y: float
    score: float


def find_targets(background: np.ndarray, depth: np.ndarray, *, count: int = 8, seed: int = 0) -> list[Target]:
    """Bright regions of the gas (stars removed), nearer ones preferred,
    spread out by non-maximum suppression, best first."""
    h, w = background.shape[:2]
    small_w = 256
    small_h = max(8, int(round(h * small_w / w)))
    lum = Image.fromarray(background).convert("L").resize((small_w, small_h), Image.BILINEAR)
    lum = np.asarray(lum.filter(ImageFilter.GaussianBlur(small_w / 40)), dtype=np.float32) / 255.0
    dep = np.asarray(Image.fromarray(depth).resize((small_w, small_h), Image.BILINEAR), dtype=np.float32)
    score = lum * (0.6 + 0.4 * dep)
    # Keep away from the frame edge: a target there cannot be centred.
    my, mx = int(small_h * 0.12), int(small_w * 0.12)
    mask = np.zeros_like(score)
    mask[my:small_h - my, mx:small_w - mx] = 1.0
    rng = np.random.default_rng(seed)
    score = (score + rng.uniform(0, 1e-4, score.shape)) * mask   # deterministic tie-break
    radius = small_w * 0.14
    yy, xx = np.mgrid[0:small_h, 0:small_w]
    out: list[Target] = []
    s = score.copy()
    for _ in range(count):
        i = int(np.argmax(s))
        cy, cx = divmod(i, small_w)
        if s[cy, cx] <= 0:
            break
        out.append(Target(x=(cx + 0.5) * w / small_w, y=(cy + 0.5) * h / small_h, score=float(score[cy, cx])))
        s[(yy - cy) ** 2 + (xx - cx) ** 2 < radius ** 2] = 0
    if not out:
        out.append(Target(x=w / 2, y=h / 2, score=0.0))
    return out
