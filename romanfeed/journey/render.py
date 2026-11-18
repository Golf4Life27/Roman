"""Render a journey: one still image, flown into with real parallax.

Per frame, every output pixel looks back into the picture through the
depth map: a part at depth Z is magnified Z / (Z - travel), so near gas
grows and slides past faster than far gas (layers.py and camera.py say how
the scene is built). The warp is solved on a coarse grid (one point every 4
output pixels, where the depth is read and refined twice) and spread to full
size bilinearly; the displacement field is smooth, so nothing is lost and the
cost is one full-size resample per layer.

OpenCV does the resampling when it is installed (the `journey` extra; about
25 ms a 1080p frame on one core). Without it a numpy path does the same maths
far more slowly, which is fine for tests and tiny previews only.

Frames go straight into ffmpeg (libx264, yuv420p) through a pipe, one clip
per leg, so the long cuts are a stream-copy concat of the same clips."""
from __future__ import annotations

import logging
import math
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from romanfeed.journey.camera import Frame, Leg, Pose
from romanfeed.journey.depth import gaussian_blur
from romanfeed.journey.layers import fill_holes, grey_dilate, otsu, split_stars
from romanfeed.render import ffmpeg

log = logging.getLogger(__name__)

try:  # optional accelerator
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - depends on the environment
    cv2 = None

GRID = 4                 # output pixels between warp-solve points
STAR_FLOOR = 0.55        # small stars are never farther than this: they stream past the far sky
MAX_SOURCE_SIDE = 6000   # bigger originals are scaled down once: detail beyond this is never shown


def have_opencv() -> bool:
    return cv2 is not None


def _resize(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Bilinear resize of a float32 2-D or H x W x C array to (w, h)."""
    if cv2 is not None:
        return cv2.resize(a, size, interpolation=cv2.INTER_LINEAR)
    if a.ndim == 2:
        return np.asarray(Image.fromarray(a.astype(np.float32), mode="F").resize(size, Image.BILINEAR), dtype=np.float32)
    return np.stack([_resize(np.ascontiguousarray(a[..., c]), size) for c in range(a.shape[2])], axis=-1)


def _rgbx(rgb: np.ndarray) -> np.ndarray:
    """RGB -> 4-channel with an opaque spare channel (see Scene)."""
    return np.dstack([rgb, np.full(rgb.shape[:2], 255, dtype=np.uint8)])


@dataclass
class Scene:
    """The prepared picture, as layers the camera flies through:

      far        the gas and sky behind the near layer, with what the near
                 layer hides filled in from its surroundings (seen only in
                 the sliver its edge uncovers as the camera moves)
      far_stars  the small stars in front of the far gas
      near       the nearest part of the picture (stars included), alpha-
                 premultiplied, or None when the depth map has no clear
                 near/far split
      depths     one smooth depth map per layer (float32, 1 = near, small)

    All three images are 4-channel uint8: OpenCV resamples 4-channel pixels
    about twice as fast as 3-channel ones (measured 10.7 ms vs 20.5 ms a
    1080p frame), so far and far_stars carry a spare channel.

    Each layer is warped with its own smooth depth, and the near layer is laid
    over the far one with its alpha: the near edge slides over what is behind
    it instead of stretching the picture between them."""
    far: np.ndarray
    far_stars: np.ndarray
    near: np.ndarray | None
    far_depth: np.ndarray
    star_depth: np.ndarray
    near_depth: np.ndarray | None
    depth_source: str
    depth_range: float = 1.5
    use_cv2: bool = field(default_factory=have_opencv)

    @property
    def width(self) -> int:
        return self.far.shape[1]

    @property
    def height(self) -> int:
        return self.far.shape[0]

    @property
    def depth(self) -> np.ndarray:
        """The far layer's depth (the nearest gas where there is no near layer)."""
        return self.far_depth if self.near_depth is None else np.maximum(self.far_depth, self.near_depth * self.near_alpha_small())

    def near_alpha_small(self) -> np.ndarray:
        if self.near is None:
            return np.zeros_like(self.far_depth)
        return _resize(self.near[..., 3].astype(np.float32) / 255.0, (self.far_depth.shape[1], self.far_depth.shape[0]))

    @property
    def background(self) -> np.ndarray:
        """The picture without the small stars, RGB (for choosing where to fly)."""
        far = self.far[..., :3]
        if self.near is None:
            return np.ascontiguousarray(far)
        a = self.near[..., 3:].astype(np.float32) / 255.0
        return np.clip(far * (1 - a) + self.near[..., :3], 0, 255).astype(np.uint8)

    @classmethod
    def from_image(cls, image: Image.Image, depth: np.ndarray, depth_source: str, *,
                   max_side: int = MAX_SOURCE_SIDE, depth_range: float = 1.5, layered: bool = True) -> "Scene":
        im = image.convert("RGB")
        if max(im.size) > max_side:
            s = max_side / max(im.size)
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        rgb = np.asarray(im, dtype=np.uint8)
        bg, stars, _mask = split_stars(rgb)
        depth = np.ascontiguousarray(depth, dtype=np.float32)
        H, W = rgb.shape[:2]
        dh, dw = depth.shape
        thr = otsu(depth)
        soft = 0.04
        a_small = np.clip((depth - (thr - soft)) / (2 * soft), 0.0, 1.0)
        a_small = a_small * a_small * (3 - 2 * a_small)
        frac = float(a_small.mean())
        if not layered or not 0.03 < frac < 0.97:
            star_depth = np.maximum(gaussian_blur(depth, 3.0), STAR_FLOOR).astype(np.float32)
            return cls(_rgbx(bg), _rgbx(stars), None, gaussian_blur(depth, 3.0), star_depth, None, depth_source, depth_range)
        # Far layer: what the near layer hides, filled in from around it
        # (generously: a few depth-map pixels past the soft edge).
        hidden = grey_dilate(a_small > 0.02, 3)
        small_bg = np.asarray(Image.fromarray(bg).resize((dw, dh), Image.BILINEAR), dtype=np.float32)
        fill_small = fill_holes(small_bg, ~hidden)
        fill = np.clip(_resize(fill_small, (W, H)), 0, 255)
        m = _resize(hidden.astype(np.float32), (W, H))[..., None]
        far = (bg * (1 - m) + fill * m + 0.5).astype(np.uint8)
        a_full = np.clip(_resize(a_small, (W, H)), 0.0, 1.0)
        far_stars = (stars * (1 - a_full[..., None]) + 0.5).astype(np.uint8)
        near = np.dstack([(rgb * a_full[..., None] + 0.5).astype(np.uint8), (a_full * 255 + 0.5).astype(np.uint8)])
        # Each layer's depth, smooth across the other's region, so neither
        # stretches at the split.
        far_depth = np.minimum(fill_holes(depth, a_small < 0.5), thr)
        near_depth = np.maximum(fill_holes(depth, a_small >= 0.5), thr)
        far_depth = gaussian_blur(far_depth, 3.0)
        near_depth = gaussian_blur(near_depth, 3.0)
        star_depth = np.maximum(far_depth, STAR_FLOOR).astype(np.float32)
        log.info("layers: near %.0f%% of the picture (split at depth %.2f)", frac * 100, thr)
        return cls(_rgbx(far), _rgbx(far_stars), near, far_depth.astype(np.float32), star_depth,
                   near_depth.astype(np.float32), depth_source, depth_range)

    def z(self, d: np.ndarray) -> np.ndarray:
        return 1.0 + self.depth_range * (1.0 - d)

    # -- the warp ---------------------------------------------------------
    def _sample_depth(self, depth: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        sx = depth.shape[1] / self.width
        sy = depth.shape[0] / self.height
        dx = (x + 0.5) * sx - 0.5
        dy = (y + 0.5) * sy - 0.5
        if self.use_cv2:
            return cv2.remap(depth, dx.astype(np.float32), dy.astype(np.float32), cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
        return bilinear(depth, dx, dy)

    def source_coords(self, pose: Pose, px: np.ndarray, py: np.ndarray, out_w: int, out_h: int,
                      depth: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Where in the picture output pixels (px, py) look, for a layer
        with the given depth map."""
        k = Frame(self.width, self.height, out_w, out_h).cover * pose.zoom
        ux = pose.cx + (px - out_w / 2) / k
        uy = pose.cy + (py - out_h / 2) / k
        x, y = ux, uy
        for _ in range(2):   # fixed point: depth is read where the ray lands
            z = self.z(self._sample_depth(depth, x, y))
            f = (z - pose.travel) / z
            x = pose.cx + (ux - pose.cx) * f
            y = pose.cy + (uy - pose.cy) * f
        return x.astype(np.float32), y.astype(np.float32)

    def _layers(self):
        """(image, depth, border) per layer, back to front."""
        yield self.far, self.far_depth, "reflect"
        yield self.far_stars, self.star_depth, "zero"
        if self.near is not None:
            yield self.near, self.near_depth, "zero"

    def frame(self, pose: Pose, out_w: int, out_h: int) -> np.ndarray:
        """One (out_h, out_w, 3) uint8 frame."""
        pose = Frame(self.width, self.height, out_w, out_h).clamp(pose)
        warped = []
        if self.use_cv2:
            n_x, n_y = math.ceil(out_w / GRID), math.ceil(out_h / GRID)
            gx = (np.arange(n_x, dtype=np.float32) + 0.5) * GRID - 0.5
            gy = (np.arange(n_y, dtype=np.float32) + 0.5) * GRID - 0.5
            px, py = np.meshgrid(gx, gy)
            size = (n_x * GRID, n_y * GRID)
            for img, depth, border in self._layers():
                sx, sy = self.source_coords(pose, px, py, out_w, out_h, depth)
                mx = cv2.resize(sx, size, interpolation=cv2.INTER_LINEAR)[:out_h, :out_w]
                my = cv2.resize(sy, size, interpolation=cv2.INTER_LINEAR)[:out_h, :out_w]
                warped.append(cv2.remap(img, mx, my, cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_REFLECT if border == "reflect" else cv2.BORDER_CONSTANT))
            out = cv2.add(warped[0], warped[1])
            if self.near is not None:
                # "over" with a premultiplied near layer: near + far * (1 - alpha)
                near = warped[2]
                keep = cv2.merge([255 - near[..., 3]] * 4)
                out = cv2.add(near, cv2.multiply(out, keep, scale=1 / 255.0))
            out = cv2.cvtColor(out, cv2.COLOR_RGBA2RGB)
        else:
            py, px = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
            for img, depth, border in self._layers():
                sx, sy = self.source_coords(pose, px, py, out_w, out_h, depth)
                warped.append(bilinear(img, sx, sy, zero_outside=border == "zero"))
            out = np.minimum(warped[0][..., :3].astype(np.uint16) + warped[1][..., :3], 255).astype(np.float32)
            if self.near is not None:
                a = warped[2][..., 3:].astype(np.float32) / 255.0
                out = out * (1 - a) + warped[2][..., :3]
            out = np.clip(out + 0.5, 0, 255).astype(np.uint8)
        if pose.fade < 1.0:
            out = (out.astype(np.float32) * max(0.0, pose.fade)).astype(np.uint8)
        return out


def bilinear(img: np.ndarray, x: np.ndarray, y: np.ndarray, *, zero_outside: bool = False) -> np.ndarray:
    """Bilinear sample of img (H, W[, C]) at float coords; edges clamped
    (or black outside, for the star layer). The slow, dependency-free path."""
    h, w = img.shape[:2]
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0).astype(np.float32)
    fy = (y - y0).astype(np.float32)
    inside = (x >= -0.5) & (x <= w - 0.5) & (y >= -0.5) & (y <= h - 0.5)
    x0c, x1c = np.clip(x0, 0, w - 1), np.clip(x0 + 1, 0, w - 1)
    y0c, y1c = np.clip(y0, 0, h - 1), np.clip(y0 + 1, 0, h - 1)
    f = img.astype(np.float32)
    if img.ndim == 3:
        fx, fy, inside = fx[..., None], fy[..., None], inside[..., None]
    top = f[y0c, x0c] + (f[y0c, x1c] - f[y0c, x0c]) * fx
    bot = f[y1c, x0c] + (f[y1c, x1c] - f[y1c, x0c]) * fx
    out = top + (bot - top) * fy
    if zero_outside:
        out = out * inside
    return np.clip(np.round(out), 0, 255).astype(img.dtype) if img.dtype == np.uint8 else out.astype(img.dtype)


# --- encoding ----------------------------------------------------------------

Overlay = Callable[[float, np.ndarray], np.ndarray]   # (seconds into the clip, frame) -> frame


def encoder_args(out: Path, w: int, h: int, fps: int, *, crf: int = 20, preset: str = "veryfast") -> list[str]:
    return [ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-r", str(fps), "-an", str(out)]


def encode_frames(frames, out: Path, w: int, h: int, fps: int, *, crf: int = 20, preset: str = "veryfast") -> Path:
    """Pipe an iterable of (h, w, 3) uint8 frames into one H.264 clip."""
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(encoder_args(out, w, h, fps, crf=crf, preset=preset), stdin=subprocess.PIPE)
    try:
        for fr in frames:
            proc.stdin.write(np.ascontiguousarray(fr, dtype=np.uint8).tobytes())
    finally:
        proc.stdin.close()
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg exited {code} encoding {out}")
    return out


def leg_frames(scene: Scene, leg: Leg, w: int, h: int, fps: int, overlay: Overlay | None = None):
    n = max(1, int(round(leg.duration * fps)))
    for i in range(n):
        s = i / fps
        fr = scene.frame(leg.pose(s), w, h)
        if overlay is not None:
            fr = overlay(s, fr)
        yield fr


def render_leg(scene: Scene, leg: Leg, out: Path, *, w: int, h: int, fps: int, crf: int = 20,
               preset: str = "veryfast", overlay: Overlay | None = None) -> Path:
    return encode_frames(leg_frames(scene, leg, w, h, fps, overlay), out, w, h, fps, crf=crf, preset=preset)


def render_black(out: Path, seconds: float, *, w: int, h: int, fps: int, crf: int = 20, preset: str = "veryfast") -> Path:
    """Black through the same encoder, so the concat demuxer sees identical streams."""
    black = np.zeros((h, w, 3), dtype=np.uint8)
    return encode_frames((black for _ in range(max(1, int(round(seconds * fps))))), out, w, h, fps, crf=crf, preset=preset)


# Worker processes get the scene once each, through the pool initializer.
# "spawn", not "fork": a fork after OpenCV has started its thread pool can
# deadlock the child.
_WORKER_SCENE: Scene | None = None


def _init_worker(scene: Scene) -> None:
    global _WORKER_SCENE
    _WORKER_SCENE = scene
    if cv2 is not None:
        cv2.setNumThreads(1)   # parallelism comes from the processes


def _render_leg_worker(args) -> str:
    leg, out, w, h, fps, crf, preset = args
    render_leg(_WORKER_SCENE, leg, Path(out), w=w, h=h, fps=fps, crf=crf, preset=preset)
    return out


def render_legs(scene: Scene, legs: list[Leg], clips_dir: Path, *, w: int, h: int, fps: int, crf: int = 20,
                preset: str = "veryfast", workers: int | None = None,
                overlays: dict[int, Overlay] | None = None) -> list[Path]:
    """One clip per leg, in order. Legs with an overlay (the opener's cards)
    render in this process; the rest are spread over `workers` processes."""
    clips_dir.mkdir(parents=True, exist_ok=True)
    overlays = overlays or {}
    outs = [clips_dir / f"leg{leg.index:03d}.mp4" for leg in legs]
    plain = [(leg, str(o), w, h, fps, crf, preset) for leg, o in zip(legs, outs) if leg.index not in overlays]
    workers = workers if workers is not None else default_workers()
    if workers > 1 and len(plain) > 1:
        with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"),
                                 initializer=_init_worker, initargs=(scene,)) as pool:
            futures = [pool.submit(_render_leg_worker, a) for a in plain]
            for leg, o in zip(legs, outs):
                if leg.index in overlays:
                    render_leg(scene, leg, o, w=w, h=h, fps=fps, crf=crf, preset=preset, overlay=overlays[leg.index])
            for f in futures:
                log.info("rendered %s", Path(f.result()).name)
    else:
        for leg, o in zip(legs, outs):
            render_leg(scene, leg, o, w=w, h=h, fps=fps, crf=crf, preset=preset, overlay=overlays.get(leg.index))
            log.info("rendered %s", o.name)
    return outs


def default_workers() -> int:
    """Leave a core for x264's own threads: 4 cores -> 3 render processes."""
    return max(1, (os.cpu_count() or 1) - 1)


def alpha_overlay(card: Image.Image, start: float, end: float, fade: float = 1.0) -> Overlay:
    """Burn a transparent card over the frames from `start` to `end` seconds,
    faded in and out on its alpha (the same timings the daily opener uses).
    Only the card's visible box is blended, in integer maths."""
    rgba = np.asarray(card.convert("RGBA"), dtype=np.uint8)
    ys, xs = np.nonzero(rgba[..., 3])
    if len(ys) == 0:
        return lambda s, fr: fr
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    rgb = np.ascontiguousarray(rgba[y0:y1, x0:x1, :3])
    alpha = rgba[y0:y1, x0:x1, 3].astype(np.float32) / 255.0

    def apply(s: float, fr: np.ndarray) -> np.ndarray:
        if s < start or s > end:
            return fr
        a = min(1.0, (s - start) / fade if fade > 0 else 1.0, (end - s) / fade if fade > 0 else 1.0)
        if a <= 0:
            return fr
        k = alpha * a
        box = np.ascontiguousarray(fr[y0:y1, x0:x1])
        out = fr.copy()
        if cv2 is not None:
            out[y0:y1, x0:x1] = cv2.blendLinear(box, rgb, 1.0 - k, k)
        else:
            out[y0:y1, x0:x1] = (box * (1.0 - k[..., None]) + rgb * k[..., None] + 0.5).astype(np.uint8)
        return out

    return apply


def chain(*overlays: Overlay) -> Overlay:
    def apply(s: float, fr: np.ndarray) -> np.ndarray:
        for o in overlays:
            fr = o(s, fr)
        return fr
    return apply
