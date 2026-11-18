"""How far away each part of the picture looks, as a map.

Two ways, best first:

  model       Depth Anything V2 Small (Apache-2.0), run once per image on the
              CPU with onnxruntime. A real per-pixel estimate: cliffs and
              pillars come out near, empty sky far. It is a learned guess from
              how the picture looks, not a measurement of real distances.
  brightness  No model available (no onnxruntime, no network): bright, dense
              gas reads as near and dark sky as far, from a heavily blurred
              brightness map. Coarse layering, not a per-pixel estimate.

Either way the map is float32 in [0, 1] with 1 = near, at a modest size
(the renderer reads it through a coarse grid, so it never needs full size).

The model runs on one 518x518 input, which takes about 1.3 s on one CPU, once
per video; the frames never touch it."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

# fabio-sim/Depth-Anything-ONNX (Apache-2.0) export of Depth-Anything-V2-Small,
# whose weights are Apache-2.0 (Base/Large/Giant are CC-BY-NC and are not used).
MODEL_URL = "https://github.com/fabio-sim/Depth-Anything-ONNX/releases/download/v2.0.0/depth_anything_v2_vits.onnx"
MODEL_SHA256 = "d2b11a11c1d4a12b47608fa65a17ee9a4c605b55ee1730c8e3b526304f2562be"
MODEL_NAME = "depth_anything_v2_vits.onnx"
MODEL_SIDE = 518
DEPTH_LONG_SIDE = 1024

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(cache_dir: str | Path, *, url: str = MODEL_URL, sha256: str = MODEL_SHA256,
                 timeout: int = 120) -> Path | None:
    """The model file, downloaded once into `cache_dir` and checked against
    its pinned hash. None when it cannot be had (offline, blocked, bad hash),
    and the caller falls back to brightness layering."""
    cache_dir = Path(cache_dir)
    dest = cache_dir / MODEL_NAME
    if dest.exists() and _sha256(dest) == sha256:
        return dest
    try:
        import requests

        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        got = _sha256(tmp)
        if got != sha256:
            tmp.unlink(missing_ok=True)
            log.warning("depth model hash mismatch (%s); using brightness layering", got[:12])
            return None
        tmp.replace(dest)
        return dest
    except Exception as exc:  # noqa: BLE001 - any failure means "no model"
        log.warning("depth model unavailable (%s); using brightness layering", exc)
        return None


def _small_size(w: int, h: int, long_side: int = DEPTH_LONG_SIDE) -> tuple[int, int]:
    s = long_side / max(w, h)
    return max(2, round(w * s)), max(2, round(h * s))


def _normalise(d: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(d, 1.0), np.percentile(d, 99.0)
    if hi - lo < 1e-6:
        return np.full(d.shape, 0.5, dtype=np.float32)
    return np.clip((d - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def gaussian_blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur of a 2-D float array, edges replicated."""
    r = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k = (k / k.sum()).astype(np.float32)
    p = np.pad(a.astype(np.float32), r, mode="edge")
    p = np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 0, p)
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 1, p).astype(np.float32)


def model_depth(image: Image.Image, model_path: Path) -> np.ndarray:
    """Per-pixel relative depth from the ONNX model (1 = near)."""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, os.cpu_count() or 1)
    sess = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
    x = np.asarray(image.convert("RGB").resize((MODEL_SIDE, MODEL_SIDE), Image.BICUBIC), dtype=np.float32) / 255.0
    x = ((x - _MEAN) / _STD).transpose(2, 0, 1)[None].astype(np.float32)
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    disp = np.asarray(out, dtype=np.float32).reshape(MODEL_SIDE, MODEL_SIDE)  # relative inverse depth: big = near
    d = np.asarray(Image.fromarray(_normalise(disp), mode="F").resize(_small_size(*image.size), Image.BICUBIC),
                   dtype=np.float32)
    # A light blur: a hard depth edge would tear the warp along the cliff line.
    return np.clip(gaussian_blur(d, 2.0), 0.0, 1.0)


def brightness_depth(image: Image.Image) -> np.ndarray:
    """Fallback layering: bright, dense structure near, dark sky far.

    Built from a blurred luminance map so single stars do not punch holes in
    it (stars get their own near layer in layers.split_stars)."""
    size = _small_size(*image.size)
    lum = image.convert("L").resize(size, Image.BILINEAR)
    # Grey opening first (min then max) drops the stars before blurring.
    lum = lum.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    lum = lum.filter(ImageFilter.GaussianBlur(max(2, size[0] // 60)))
    d = _normalise(np.asarray(lum, dtype=np.float32))
    return np.sqrt(d).astype(np.float32)   # lift the faint gas off the far plane a little


def estimate_depth(image: Image.Image, *, method: str = "auto", cache_dir: str | Path = "data/cache/models") -> tuple[np.ndarray, str]:
    """(depth map, how it was made: "model" or "brightness").

    method "auto" tries the model and falls back; "model" raises when the
    model cannot run; "brightness" never tries it."""
    if method not in {"auto", "model", "brightness"}:
        raise ValueError(f"unknown depth method {method!r}")
    if method != "brightness":
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            if method == "model":
                raise
            log.warning("onnxruntime not installed; using brightness layering")
        else:
            path = ensure_model(cache_dir)
            if path is not None:
                try:
                    return model_depth(image, path), "model"
                except Exception as exc:  # noqa: BLE001
                    if method == "model":
                        raise
                    log.warning("depth model failed (%s); using brightness layering", exc)
            elif method == "model":
                raise RuntimeError("depth model could not be downloaded")
    return brightness_depth(image), "brightness"
