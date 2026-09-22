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


# --- upload ------------------------------------------------------------------

class _Call:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.result


class _FakeYouTube:
    """Just enough of the googleapiclient service for _upload."""

    def __init__(self, thumb_exc=None):
        self.thumb_calls: list[dict] = []
        self.thumb_exc = thumb_exc

    def videos(self):
        insert = types.SimpleNamespace(next_chunk=lambda: (None, {"id": "vid123"}))
        return types.SimpleNamespace(insert=lambda **kw: insert)

    def thumbnails(self):
        def set_(**kw):
            self.thumb_calls.append(kw)
            return _Call({}, self.thumb_exc)
        return types.SimpleNamespace(set=set_)


@pytest.fixture
def fake_api(monkeypatch):
    """Stand-in googleapiclient.http so this runs on CI's dev-only install."""
    http = types.ModuleType("googleapiclient.http")

    class MediaFileUpload:
        def __init__(self, filename, **kw):
            self.filename, self.kw = filename, kw

    http.MediaFileUpload = MediaFileUpload
    pkg = types.ModuleType("googleapiclient")
    pkg.http = http
    monkeypatch.setitem(sys.modules, "googleapiclient", pkg)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http)

    def install(yt):
        monkeypatch.setattr(youtube, "client", lambda: yt)
        return yt
    return install


def _meta():
    return VideoMetadata(title="T", description="D")


def test_publish_sets_the_thumbnail_after_upload(tmp_path, fake_api):
    yt = fake_api(_FakeYouTube())
    video, thumb = tmp_path / "v.mp4", tmp_path / "v.thumb.jpg"
    video.write_bytes(b"v")
    thumb.write_bytes(b"j")
    assert youtube.publish(video, _meta(), mode="upload", thumbnail=thumb) == "vid123"
    assert len(yt.thumb_calls) == 1
    call = yt.thumb_calls[0]
    assert call["videoId"] == "vid123"
    assert call["media_body"].filename == str(thumb)
    assert call["media_body"].kw["mimetype"] == "image/jpeg"


def test_thumbnail_failure_is_a_warning_not_a_failed_run(tmp_path, fake_api, caplog):
    yt = fake_api(_FakeYouTube(thumb_exc=RuntimeError("403 forbidden")))
    video, thumb = tmp_path / "v.mp4", tmp_path / "v.thumb.jpg"
    video.write_bytes(b"v")
    thumb.write_bytes(b"j")
    with caplog.at_level(logging.WARNING, logger="romanfeed.publish.youtube"):
        assert youtube.publish(video, _meta(), mode="upload", thumbnail=thumb) == "vid123"
    assert yt.thumb_calls
    assert any(r.levelno == logging.WARNING and "thumbnail" in r.getMessage() for r in caplog.records)


def test_publish_without_thumbnail_is_unchanged(tmp_path, fake_api):
    yt = fake_api(_FakeYouTube())
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    assert youtube.publish(video, _meta(), mode="upload") == "vid123"
    assert yt.thumb_calls == []


def test_dry_run_never_touches_the_api(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "client", lambda: pytest.fail("dry run built a client"))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    assert youtube.publish(video, _meta(), mode="dry-run", thumbnail=tmp_path / "x.jpg") is None


# --- retrofit CLI ---------------------------------------------------------------

def test_thumbnails_command_builds_and_sets(tmp_path, fake_api, capsys):
    from romanfeed import cli

    yt = fake_api(_FakeYouTube())
    src = _source(tmp_path, (1600, 1600))
    rc = cli.main(["thumbnails", "abcDEF12345", str(src), "--length", "8 HOURS", "--out-dir", str(tmp_path / "th")])
    assert rc == 0
    thumb = tmp_path / "th" / "abcDEF12345.thumb.jpg"
    assert thumb.exists()
    assert yt.thumb_calls[0]["videoId"] == "abcDEF12345"
    assert yt.thumb_calls[0]["media_body"].filename == str(thumb)


def test_thumbnails_command_reports_api_failure(tmp_path, fake_api, capsys):
    from romanfeed import cli

    fake_api(_FakeYouTube(thumb_exc=RuntimeError("The user is forbidden")))
    src = _source(tmp_path, (800, 450))
    rc = cli.main(["thumbnails", "vid", str(src), "--length", "1 HOUR", "--out-dir", str(tmp_path / "th")])
    assert rc == 1
    assert "phone-verified" in capsys.readouterr().out
