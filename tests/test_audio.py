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


def test_register_track_copies_and_appends(tmp_path):
    from romanfeed.audio.library import register_track
    from romanfeed.audio.mix import synth_placeholder

    src = synth_placeholder(tmp_path / "in" / "Drift One.m4a", 1).path
    manifest = tmp_path / "music" / "manifest.yaml"
    e1 = register_track(manifest, src, licence="generated", title="Drift One", notes="Suno Pro")
    e2 = register_track(manifest, src, licence="generated", title="Drift One")
    assert e1["id"] == "drift-one" and e2["id"] == "drift-one-2"
    assert e1["path"] == "ambient/drift-one.m4a"
    assert (manifest.parent / e1["path"]).exists()
    assert abs(probe_duration(str(manifest.parent / e1["path"])) - 1.0) < 0.3
    lib = MusicLibrary(manifest)
    assert [t.id for t in lib.for_genre("ambient")] == ["drift-one", "drift-one-2"]
    with pytest.raises(ValueError, match="licence"):
        register_track(manifest, src, licence="placeholder")


def test_music_cli(tmp_path, capsys):
    from romanfeed.audio.mix import synth_placeholder
    from romanfeed.cli import main

    src = synth_placeholder(tmp_path / "t.m4a", 1).path
    manifest = tmp_path / "m" / "manifest.yaml"
    assert main(["music", "add", str(src), "--licence", "owned", "--title", "Calm", "--manifest", str(manifest)]) == 0
    assert main(["music", "list", "--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "registered calm (owned)" in out and "ok " in out


def test_normalise_track_hits_target_loudness(tmp_path):
    import json, re, subprocess
    from romanfeed.audio.library import normalise_track
    from romanfeed.audio.mix import synth_placeholder
    from romanfeed.render.ffmpeg import ffmpeg_path

    src = synth_placeholder(tmp_path / "loud.m4a", 4).path
    out = normalise_track(src, tmp_path / "norm.m4a", target_lufs=-18.0)
    res = subprocess.run([ffmpeg_path(), "-hide_banner", "-nostats", "-i", str(out), "-af", "loudnorm=print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    st = json.loads(re.findall(r"\{[^{}]*\}", res.stderr)[-1])
    assert abs(float(st["input_i"]) - (-18.0)) < 2.0
