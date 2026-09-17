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


def test_repeat_order_keeps_first_pass_and_breaks_seams():
    from romanfeed.render.compose import repeat_order

    o = repeat_order(12, 4, seed="s")
    assert len(o) == 48
    assert o[:12] == list(range(12))          # curated order is what a viewer sees first
    assert sorted(o) == sorted(list(range(12)) * 4)   # every image used equally
    assert all(o[i] != o[i + 1] for i in range(len(o) - 1))  # no image twice in a row
    assert o[12:24] != list(range(12))        # later passes are reshuffled


def test_repeat_order_edges():
    from romanfeed.render.compose import repeat_order

    assert repeat_order(0, 3) == []
    assert repeat_order(5, 0) == []
    assert repeat_order(1, 3) == [0, 0, 0]    # single clip: nothing to shuffle


def test_mux_audio_faststart_is_optional(monkeypatch, tmp_path):
    """+faststart makes ffmpeg rewrite the whole file through a temp copy, so
    the long cuts must be able to turn it off."""
    from romanfeed.render import compose

    calls: list[list[str]] = []
    monkeypatch.setattr(compose.ffmpeg, "run", lambda args, **kw: calls.append(args))

    compose.mux_audio(tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "out.mp4")
    assert "+faststart" in calls[0]
    compose.mux_audio(tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "long.mp4", faststart=False)
    assert "+faststart" not in calls[1]
    assert "-movflags" not in calls[1]
    assert calls[1][-1].endswith("long.mp4")  # output still the last argument


def test_concat_clips_removes_its_list_file(tmp_path):
    from romanfeed.render.compose import concat_clips

    out = concat_clips(_tiny_clips(tmp_path, 2), tmp_path / "silent.mp4")
    assert out.exists()
    assert not out.with_suffix(".txt").exists()


def _tiny_clips(dir_: Path, n: int) -> list[Path]:
    """A couple of one-second colour clips, cheap enough to stitch for real."""
    from romanfeed.render import ffmpeg

    out = []
    for i in range(n):
        p = dir_ / f"clip{i}.mp4"
        ffmpeg.run([
            "-f", "lavfi", "-i", f"color=c=0x{i:02x}2040:s=160x90:r=10", "-t", "1",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(p),
        ])
        out.append(p)
    return out


def test_render_video_with_clips_deletes_the_silent_intermediate(sample_asset, tmp_path):
    """The silent cut is a full-size copy of the finished video. Keeping it
    doubles peak disk for no reason once the audio is muxed on."""
    from romanfeed.audio.mix import synth_placeholder
    from romanfeed.config import AudioSettings, ChannelConfig, ChannelInfo, PublishSettings, VideoSettings
    from romanfeed.render.compose import render_video_with_clips

    cfg = ChannelConfig(
        channel=ChannelInfo(slug="t", name="T"),
        video=VideoSettings(width=160, height=90, fps=10, transition_seconds=0.2, captions=False, preset="ultrafast"),
        sources=[], audio=AudioSettings(), publish=PublishSettings(),
    )
    work = tmp_path / "work"
    work.mkdir()
    audio = synth_placeholder(work / "a.m4a", 3).path
    out = tmp_path / "final.mp4"

    path, clips = render_video_with_clips(
        [sample_asset, sample_asset], cfg, work_dir=work, audio_path=audio,
        out_path=out, seconds_per_image=1,
    )
    assert path.exists() and path == out
    assert not (work / "silent.mp4").exists()       # intermediate is gone
    assert all(c.exists() for c in clips)           # clips stay: long cuts reuse them


def test_render_video_with_clips_keeps_output_when_there_is_no_audio(sample_asset, tmp_path):
    from romanfeed.config import AudioSettings, ChannelConfig, ChannelInfo, PublishSettings, VideoSettings
    from romanfeed.render.compose import render_video_with_clips

    cfg = ChannelConfig(
        channel=ChannelInfo(slug="t", name="T"),
        video=VideoSettings(width=160, height=90, fps=10, transition_seconds=0.2, captions=False, preset="ultrafast"),
        sources=[], audio=AudioSettings(), publish=PublishSettings(),
    )
    work = tmp_path / "work"
    work.mkdir()
    out = tmp_path / "silent-final.mp4"
    path, _ = render_video_with_clips([sample_asset], cfg, work_dir=work, audio_path=None, out_path=out, seconds_per_image=1)
    assert path.exists()
    assert not (work / "silent.mp4").exists()       # moved, not copied
