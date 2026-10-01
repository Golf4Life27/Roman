import numpy as np
import pytest

from romanfeed.audio.composer import ComposedLibrary, compose_piece, measure_loudness, open_library, render_pcm
from romanfeed.audio.library import PUBLISHABLE, TARGET_LUFS
from romanfeed.audio.mix import build_soundtrack
from romanfeed.render.ffmpeg import probe_duration


@pytest.fixture(scope="module")
def piece(tmp_path_factory):
    """One encoded piece shared by the encode-side checks (encoding is the slow part)."""
    out = tmp_path_factory.mktemp("composed") / "p.m4a"
    return compose_piece(out, seconds=20, seed="test-piece")


def test_same_seed_renders_identical_pcm():
    a = render_pcm(6, "night-1")
    b = render_pcm(6, "night-1")
    assert a.dtype == np.float32
    assert a.tobytes() == b.tobytes()


def test_different_seeds_make_different_music():
    a = render_pcm(6, "night-1")
    b = render_pcm(6, "night-2")
    assert a.shape == b.shape
    # Not just a different phase: the signals are essentially uncorrelated.
    corr = np.corrcoef(a[:, 0], b[:, 0])[0, 1]
    assert abs(corr) < 0.5


def test_pcm_is_stereo_quiet_and_never_clips():
    x = render_pcm(10, "headroom", sample_rate=44100)
    assert x.shape == (441000, 2)
    peak = float(np.max(np.abs(x)))
    assert 0.05 < peak < 1.0
    assert not np.allclose(x[:, 0], x[:, 1])  # actually spread, not dual mono
    assert np.all(np.isfinite(x))


def test_composed_piece_is_owned_stereo_and_on_target(piece):
    assert piece.licence == "owned" and piece.licence in PUBLISHABLE and piece.publishable
    assert piece.genre == "ambient" and piece.artist == "Space Screens"
    assert piece.id == "composed-test-piece" and " in " in piece.title
    assert "seed=test-piece" in piece.notes
    assert abs(probe_duration(str(piece.path)) - 20.0) < 0.5
    assert abs(measure_loudness(piece.path) - TARGET_LUFS) < 1.5


def test_composed_piece_is_aac_stereo(piece):
    import subprocess
    from romanfeed.render.ffmpeg import ffmpeg_path

    info = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(piece.path)], capture_output=True, text=True).stderr
    assert "Audio: aac" in info and "stereo" in info and "44100 Hz" in info


def test_composed_library_feeds_build_soundtrack(tmp_path):
    lib = ComposedLibrary(tmp_path / "composed", count=2, seconds_each=18, seed="run-1")
    assert lib.manifest_path == tmp_path / "composed"
    assert not (tmp_path / "composed").exists()  # nothing composed until asked for
    out, order = build_soundtrack(lib, genre="ambient", duration=30, out_path=tmp_path / "s.m4a",
                                  fade=2.0, crossfade=4.0, seed="x")
    assert abs(probe_duration(str(out)) - 30.0) < 0.5
    assert len(order) >= 2 and all(t.publishable and t.licence == "owned" for t in order)
    assert {t.id for t in lib.for_genre("ambient")} == {"composed-run-1-0", "composed-run-1-1"}
    assert sorted(p.name for p in (tmp_path / "composed").glob("*.m4a")) == ["composed-run-1-0.m4a", "composed-run-1-1.m4a"]
    assert not list((tmp_path / "composed").glob("*.wav"))  # intermediate PCM cleaned up


def test_open_library_follows_audio_source(tmp_path):
    from romanfeed.audio.library import MusicLibrary
    from romanfeed.config import AudioSettings

    assert isinstance(open_library(AudioSettings(), work_dir=tmp_path, seed="s"), MusicLibrary)
    lib = open_library(AudioSettings(source="composed", composed_pieces=3, composed_seconds=60),
                       work_dir=tmp_path, seed="s")
    assert isinstance(lib, ComposedLibrary) and lib.count == 3 and lib.seconds_each == 60


def test_config_loads_audio_source(tmp_path):
    from romanfeed.config import load_config

    base = "channel: {slug: t, name: T}\nvideo: {}\nsources: []\npublish: {}\n"
    p = tmp_path / "c.yaml"
    p.write_text(base + "audio: {source: composed, composed_pieces: 4, composed_seconds: 300}\n")
    a = load_config(p).audio
    assert (a.source, a.composed_pieces, a.composed_seconds) == ("composed", 4, 300)
    p.write_text(base + "audio: {}\n")
    assert load_config(p).audio.source == "library"
    p.write_text(base + "audio: {source: spotify}\n")
    with pytest.raises(ValueError, match="audio.source"):
        load_config(p)


def test_compose_cli(tmp_path, capsys):
    from romanfeed.cli import main

    assert main(["compose", str(tmp_path / "c.m4a"), "--seconds", "8", "--seed", "cli"]) == 0
    out = capsys.readouterr().out
    assert "title:" in out and "duration: 8.0s" in out and "LUFS" in out
