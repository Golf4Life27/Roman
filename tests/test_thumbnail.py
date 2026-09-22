"""Custom thumbnails: the image itself, and getting it onto YouTube."""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from romanfeed.publish import youtube
from romanfeed.publish.metadata import VideoMetadata
from romanfeed.render import captions, thumbnail
from romanfeed.render.thumbnail import MAX_BYTES, make_thumbnail


def _source(tmp_path: Path, size: tuple[int, int], name: str = "src.png") -> Path:
    p = tmp_path / name
    Image.effect_noise(size, 60).convert("RGB").save(p)
    return p


@pytest.mark.parametrize("size", [(3000, 900), (400, 1200), (97, 61)], ids=["wide", "tall", "tiny"])
def test_output_is_a_1280x720_jpeg_under_2mb(tmp_path, size):
    out = make_thumbnail(_source(tmp_path, size), tmp_path / "t.thumb.jpg", length_label="8 Hours")
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert im.size == (1280, 720)
    assert out.stat().st_size < MAX_BYTES


def test_headline_is_big_and_bottom_left(tmp_path):
    # A flat mid-grey source: anything near-white in the output is the badge text.
    src = tmp_path / "grey.png"
    Image.new("RGB", (1920, 1080), (90, 90, 90)).save(src)
    out = make_thumbnail(src, tmp_path / "t.jpg", length_label="1 hour")
    with Image.open(out) as im:
        white = im.convert("L").point(lambda v: 255 if v > 235 else 0)
    x0, y0, x1, y1 = white.getbbox()
    assert x0 < 1280 * 0.15 and y1 > 720 * 0.8          # anchored bottom-left
    assert (y1 - y0) > 720 * 0.25                          # headline + tagline stack
    assert x1 < 1280 * 0.85                                # not a full-width banner


def test_oversized_encode_steps_quality_down(tmp_path):
    im = Image.effect_noise((1280, 720), 120).convert("RGB")
    big = len(thumbnail._encode(im, limit=10**9))
    small = thumbnail._encode(im, limit=big)  # q90 no longer fits
    assert len(small) < big


def test_missing_bold_font_falls_back_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(captions, "FONT_CANDIDATES", [str(tmp_path / "nope" / "DejaVuSans.ttf")])
    out = make_thumbnail(_source(tmp_path, (1600, 900)), tmp_path / "t.jpg", length_label="8 HOURS")
    with Image.open(out) as im:
        assert im.size == (1280, 720)


def test_broken_font_loader_falls_back_to_default(tmp_path, monkeypatch):
    def boom(size, bold=False):
        raise OSError("cannot open resource")

    monkeypatch.setattr(thumbnail, "_font", boom)
    out = make_thumbnail(_source(tmp_path, (1600, 900)), tmp_path / "t.jpg", length_label="8 HOURS", subject="Carina")
    assert out.exists()
