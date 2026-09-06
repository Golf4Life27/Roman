from pathlib import Path

from PIL import Image

from romanfeed.render.captions import fit_canvas, prepare_frame
from romanfeed.render.kenburns import Segment


def test_fit_canvas_wide_and_tall():
    wide = Image.new("RGB", (4000, 1000), "red")
    assert fit_canvas(wide, 1920, 1080).size == (1920, 1080)
    tall = Image.new("RGB", (1000, 3000), "blue")
    out = fit_canvas(tall, 1920, 1080)
    assert out.size == (1920, 1080)
    # blurred backdrop is darkened, so corners must not be pure blue
    assert out.getpixel((5, 5)) != (0, 0, 255)


def test_prepare_frame_supersamples_and_caption_layer(sample_asset, tmp_path):
    out, cap = prepare_frame(sample_asset, tmp_path / "f.png", width=640, height=360, captions=True)
    with Image.open(out) as im:
        assert im.size == (1280, 720)
    with Image.open(cap) as im:
        assert im.size == (640, 360) and im.mode == "RGBA"
        assert im.getpixel((5, 5))[3] == 0  # top-left is transparent
        assert im.getpixel((100, 340))[3] > 0  # lower-third band is drawn
    assert prepare_frame(sample_asset, tmp_path / "g.png", width=640, height=360, captions=False)[1] is None


def test_caption_truncates_to_width(sample_asset, tmp_path):
    from PIL import ImageDraw
    from romanfeed.render.captions import _fit_text, _font

    d = ImageDraw.Draw(Image.new("RGB", (100, 100)))
    f = _font(20)
    out = _fit_text(d, "the quick brown fox jumps over the lazy dog " * 5, f, 300)
    assert out.endswith("…") and d.textlength(out, font=f) <= 300
    assert _fit_text(d, "short", f, 300) == "short"


def test_segment_filter_alternates_direction(tmp_path):
    s0 = Segment(0, Path("a.png"), tmp_path / "0.mp4", duration=8, fps=30, width=640, height=360)
    s1 = Segment(1, Path("a.png"), tmp_path / "1.mp4", duration=8, fps=30, width=640, height=360)
    assert "min(zoom+" in s0.filter_graph()
    assert "max(zoom-" in s1.filter_graph()
    assert "d=240" in s0.filter_graph()
    assert "fade=t=out:st=6.000" in s0.filter_graph()


def test_segment_renders_real_clip(sample_asset, tmp_path):
    from romanfeed.render.ffmpeg import probe_duration

    png, cap = prepare_frame(sample_asset, tmp_path / "f.png", width=320, height=180, captions=True)
    seg = Segment(0, png, tmp_path / "0.mp4", duration=2, fps=15, width=320, height=180, fade=0.5, preset="ultrafast", caption_png=cap)
    assert "overlay" in seg.filter_graph() and "-filter_complex" in seg.ffmpeg_args()
    out = seg.render()
    assert out.exists()
    assert abs(probe_duration(str(out)) - 2.0) < 0.2
    plain = Segment(1, png, tmp_path / "1.mp4", duration=1, fps=15, width=320, height=180, fade=0.2, preset="ultrafast")
    assert "-vf" in plain.ffmpeg_args()
    assert plain.render().exists()
