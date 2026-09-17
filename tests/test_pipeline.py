"""The disk-shape of a run: what the extra-length cuts leave behind.

An 8h cut is ~13 GB of stream-copied clips and its silent intermediate is
another ~13 GB. The run has to hand that space back before the next cut starts,
so these tests drive `run()` with ffmpeg and YouTube stubbed out and assert on
which files survive each stage."""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from romanfeed import pipeline
from romanfeed.config import AudioSettings, ChannelConfig, ChannelInfo, PublishSettings, SourceSettings, VideoSettings


def _cfg(extra_hours: list[float]) -> ChannelConfig:
    return ChannelConfig(
        channel=ChannelInfo(slug="testchan", name="Test Channel"),
        video=VideoSettings(images_per_video=2, seconds_per_image=1, extra_lengths_hours=extra_hours),
        sources=[SourceSettings(type="nasa_images")],
        audio=AudioSettings(allow_placeholder=True),
        publish=PublishSettings(mode="dry-run"),
    )


@pytest.fixture
def stub_run(monkeypatch, sample_asset, tmp_path):
    """Everything expensive replaced by a file-touch, so the only real thing
    under test is which paths the pipeline creates and removes."""
    track = types.SimpleNamespace(publishable=True, title="t", id="t")

    def fake_soundtrack(_library, **kw):
        out = Path(kw["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"audio")
        return out, [track]

    def fake_render(assets, cfg, *, work_dir, audio_path, out_path, seconds_per_image=None):
        clips_dir = Path(work_dir) / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        clips = []
        for i in range(len(assets)):
            c = clips_dir / f"{i:04d}.mp4"
            c.write_bytes(b"clip")
            clips.append(c)
        # what the real function does: stitch, mux, then drop the intermediate
        (Path(work_dir) / "silent.mp4").write_bytes(b"silent")
        Path(out_path).write_bytes(b"video")
        (Path(work_dir) / "silent.mp4").unlink()
        return Path(out_path), clips

    def fake_concat(clips, out_path):
        Path(out_path).write_bytes(b"silent" * len(clips))
        return Path(out_path)

    muxed: list[bool] = []

    def fake_mux(video, audio, out_path, *, faststart=True):
        muxed.append(faststart)
        assert Path(video).exists(), "silent intermediate must still exist at mux time"
        Path(out_path).write_bytes(b"cut")
        return Path(out_path)

    monkeypatch.setattr(pipeline, "build_source", lambda s: types.SimpleNamespace(name="stub", fetch=lambda: [sample_asset]))
    monkeypatch.setattr(pipeline, "select_assets", lambda *a, **kw: [sample_asset, sample_asset])
    monkeypatch.setattr(pipeline, "MusicLibrary", lambda path: object())
    monkeypatch.setattr(pipeline, "build_soundtrack", fake_soundtrack)
    monkeypatch.setattr(pipeline, "render_video_with_clips", fake_render)
    monkeypatch.setattr(pipeline, "concat_clips", fake_concat)
    monkeypatch.setattr(pipeline, "mux_audio", fake_mux)
    monkeypatch.setattr(pipeline, "probe_duration", lambda p: 60.0)
    monkeypatch.setattr(pipeline, "build_metadata", lambda *a, **kw: types.SimpleNamespace(title="T", privacy="private"))
    monkeypatch.setattr(pipeline, "publish", lambda path, meta, mode: None)
    return muxed


def test_extra_cut_intermediate_is_deleted_before_the_next_one(stub_run, tmp_path):
    opts = pipeline.RunOptions(data_dir=tmp_path / "data", output_dir=tmp_path / "out", keep_work=True, seed="s")
    res = pipeline.run(_cfg([3, 8]), opts)

    work = tmp_path / "data" / "work" / Path(res.video_path).stem
    assert res.video_path.exists()
    assert [p.name for p in sorted(work.glob("silent*.mp4"))] == []   # no 13 GB leftovers
    assert (work / "clips").exists() and list((work / "clips").glob("*.mp4"))  # clips survive
    assert len(res.extra_cuts) == 2
    assert all(p.exists() for _, p, _ in res.extra_cuts)


def test_long_cuts_skip_faststart(stub_run, tmp_path):
    opts = pipeline.RunOptions(data_dir=tmp_path / "data", output_dir=tmp_path / "out", seed="s")
    pipeline.run(_cfg([8]), opts)
    # one mux per extra cut; the primary one happens inside the stubbed render
    assert stub_run == [False]


def test_disk_free_is_logged_around_each_cut(stub_run, tmp_path, caplog):
    caplog.set_level("INFO", logger="romanfeed.pipeline")
    opts = pipeline.RunOptions(data_dir=tmp_path / "data", output_dir=tmp_path / "out", seed="s")
    pipeline.run(_cfg([8]), opts)

    disk = [r.getMessage() for r in caplog.records if r.getMessage().startswith("disk free:")]
    assert any("before render" in m for m in disk)
    assert any("before 8h cut" in m for m in disk)
    assert any("after 8h upload" in m for m in disk)
