import pytest

from romanfeed.audio.library import MusicLibrary
from romanfeed.audio.mix import build_soundtrack
from romanfeed.render.ffmpeg import probe_duration


def test_manifest_requires_licence(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text("tracks:\n  - id: x\n    path: x.m4a\n")
    with pytest.raises(ValueError, match="licence"):
        MusicLibrary(m)


def test_placeholder_not_publishable(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text("tracks: []\n")
    lib = MusicLibrary(m)
    with pytest.raises(RuntimeError, match="no publishable"):
        build_soundtrack(lib, genre="ambient", duration=3, out_path=tmp_path / "s.m4a")
    out, tracks = build_soundtrack(lib, genre="ambient", duration=3, out_path=tmp_path / "s.m4a", fade=0.5, allow_placeholder=True)
    assert out.exists()
    assert tracks and not tracks[0].publishable
    assert abs(probe_duration(str(out)) - 3.0) < 0.3


def test_licensed_track_loops_to_duration(tmp_path):
    from romanfeed.audio.mix import synth_placeholder

    synth_placeholder(tmp_path / "loop.m4a", 2)
    m = tmp_path / "manifest.yaml"
    m.write_text("tracks:\n  - id: loop\n    path: loop.m4a\n    genre: ambient\n    licence: owned\n")
    lib = MusicLibrary(m)
    out, tracks = build_soundtrack(lib, genre="ambient", duration=5, out_path=tmp_path / "s.m4a", fade=0.5)
    assert len(tracks) == 3  # 2s track looped to cover 5s
    assert all(t.publishable for t in tracks)
    assert abs(probe_duration(str(out)) - 5.0) < 0.3
