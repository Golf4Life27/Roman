"""Fly-into journeys: layers, depth, camera, render, Short, titles, gate, run."""
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from romanfeed.config import load_config
from romanfeed.journey import depth as depth_mod
from romanfeed.journey.camera import Frame, Pose, energy_at, magnification, plan_journey
from romanfeed.journey.layers import Target, fill_holes, find_targets, grey_open, otsu, split_stars
from romanfeed.journey.metadata import (build_journey_metadata, journey_subject, journey_title, leg_chapters,
                                        strip_3d, with_article)
from romanfeed.journey.render import Scene, bilinear, render_black, render_leg
from romanfeed.journey.run import long_cut_sequence, publish_gate
from romanfeed.journey.short import PAYOFF_BY, plan_short, render_journey_short, short_pose
from romanfeed.render.ffmpeg import probe_duration
from romanfeed.sources.base import ImageAsset

CONFIG = Path(__file__).resolve().parents[1] / "config" / "channels" / "deep-space-ambient.yaml"


# --- a small synthetic "nebula": bright cliff below, dark starry sky above ---

def cliff_image(w: int = 480, h: int = 270, seed: int = 0) -> tuple[Image.Image, np.ndarray]:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    edge = h * 0.45 + 12 * np.sin(xx / 25.0)
    cliff = yy > edge
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    rgb[...] = (10, 20, 60)                                        # sky
    rgb[cliff] = (180, 110, 60)                                   # gas
    rgb += rng.normal(0, 6, rgb.shape)                            # texture
    for _ in range(60):                                           # stars: small blobs
        x, y = rng.integers(4, w - 4), rng.integers(4, h - 4)
        rgb[y - 1:y + 2, x - 1:x + 2] += 120
        rgb[y, x] += 100
    depth = np.where(cliff, 0.85, 0.1).astype(np.float32)        # 1 = near
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)), depth


@pytest.fixture(scope="module")
def scene() -> Scene:
    im, d = cliff_image()
    small = np.asarray(Image.fromarray(d).resize((240, 135), Image.NEAREST), dtype=np.float32)
    return Scene.from_image(im, small, "model")


# --- layers ------------------------------------------------------------------

def test_grey_open_removes_points_keeps_wide_shapes():
    a = np.zeros((40, 40), dtype=np.uint8)
    a[5, 5] = 200                    # a point
    a[20:30, 20:30] = 150            # a block wider than the kernel
    o = grey_open(a, 5)
    assert o[5, 5] == 0
    assert (o[20:30, 20:30] == 150).all()


def test_split_stars_is_exact_and_lifts_stars_not_ridges():
    w, h = 300, 200
    im = Image.new("RGB", (w, h), (20, 20, 40))
    d = ImageDraw.Draw(im)
    d.line((20, 150, 280, 150), fill=(220, 160, 90), width=2)    # a lit filament edge (a ridge)
    d.ellipse((98, 48, 102, 52), fill=(255, 255, 255))           # a small star (a blob)
    rgb = np.asarray(im)
    bg, stars, mask = split_stars(rgb, spiked=0)
    assert (bg.astype(int) + stars == rgb).all()                 # nothing lost or added
    assert stars[50, 100].sum() > 300                            # the star moved to the star layer
    assert stars[150, 60:240].sum() < 0.1 * rgb[150, 60:240].sum()  # the ridge mostly stayed with the gas
    assert mask.shape == (h, w)
    _bg, kept, _m = split_stars(rgb, spiked=1)                   # the biggest star stays whole with the gas
    assert kept[50, 100].sum() == 0


def test_find_targets_bright_spread_and_inside():
    bg = np.zeros((300, 600, 3), dtype=np.uint8)
    bg[60:120, 90:150] = 220          # bright region top-left
    bg[180:240, 420:480] = 180        # dimmer region bottom-right
    depth = np.full((150, 300), 0.5, dtype=np.float32)
    t = find_targets(bg, depth, count=4)
    assert t[0].score >= t[-1].score
    assert abs(t[0].x - 120) < 30 and abs(t[0].y - 90) < 30
    assert any(abs(x.x - 450) < 40 and abs(x.y - 210) < 40 for x in t[:3])
    for x in t:
        assert 0.1 * 600 <= x.x <= 0.9 * 600 and 0.1 * 300 <= x.y <= 0.9 * 300


def test_fill_holes_and_otsu():
    img = np.zeros((40, 40), dtype=np.float32)
    img[:, :20] = 10.0
    img[:, 20:] = 30.0
    known = np.ones_like(img, dtype=bool)
    known[15:25, 15:25] = False
    out = fill_holes(img, known)
    assert np.all((out >= 10 - 1e-3) & (out <= 30 + 1e-3))
    assert out[20, 16] < out[20, 24]                  # filled from the nearer side
    vals = np.concatenate([np.full(100, 0.1), np.full(100, 0.8)])
    assert 0.1 < otsu(vals) <= 0.8


# --- depth -------------------------------------------------------------------

def test_brightness_depth_is_normalised_and_bright_is_near():
    im, _ = cliff_image()
    d, how = depth_mod.estimate_depth(im, method="brightness")
    assert how == "brightness"
    assert d.dtype == np.float32 and d.min() >= 0 and d.max() <= 1
    h = d.shape[0]
    assert d[int(h * 0.85)].mean() > d[int(h * 0.15)].mean()    # gas nearer than sky


def test_auto_depth_falls_back_without_model(monkeypatch, tmp_path):
    im, _ = cliff_image()
    monkeypatch.setattr(depth_mod, "ensure_model", lambda cache_dir: None)
    _d, how = depth_mod.estimate_depth(im, method="auto", cache_dir=tmp_path)
    assert how == "brightness"
    with pytest.raises(ValueError):
        depth_mod.estimate_depth(im, method="guess")


def test_model_download_rejects_wrong_hash(monkeypatch, tmp_path):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield b"not a model"

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    assert depth_mod.ensure_model(tmp_path) is None
    assert not (tmp_path / depth_mod.MODEL_NAME).exists()


def test_model_pin_is_the_apache_small_model():
    assert "vits" in depth_mod.MODEL_URL           # ViT-S: the Apache-2.0 one, not Base/Large (CC-BY-NC)
    assert len(depth_mod.MODEL_SHA256) == 64


# --- camera ------------------------------------------------------------------

def _targets(w=3600, h=2000):
    return [Target(w * 0.3, h * 0.4, 1.0), Target(w * 0.7, h * 0.6, 0.8), Target(w * 0.5, h * 0.3, 0.6)]


def test_journey_plan_covers_the_hour_with_reveals_every_few_minutes():
    frame = Frame(3600, 2000, 1920, 1080)
    legs = plan_journey(3600, frame, _targets(), seed=3, calm_at=1200)
    assert legs[0].start == 0
    for a, b in zip(legs, legs[1:]):
        assert b.start == pytest.approx(a.start + a.duration)
    assert legs[-1].start + legs[-1].duration == pytest.approx(3600)
    reveals = [lg.reveal_at for lg in legs]
    gaps = np.diff(reveals)
    assert gaps.max() < 6.5 * 60 and gaps.min() > 1.5 * 60       # "every few minutes"
    lively = [lg for lg in legs if lg.start < 600]
    calm = [lg for lg in legs if lg.start >= 1200]
    assert lively and calm
    assert np.mean([lg.duration for lg in lively]) < np.mean([lg.duration for lg in calm])
    assert all(lg.energy == 0 for lg in calm)
    assert np.mean([lg.travel1 for lg in lively]) > np.mean([lg.travel1 for lg in calm])


def test_first_frame_is_the_picture_and_detail_cap_holds():
    frame = Frame(3600, 2000, 1920, 1080)
    legs = plan_journey(600, frame, _targets(), seed=1, calm_at=1200)
    p0 = legs[0].pose(0.0)
    assert p0.travel == 0 and p0.zoom == 1.0 and p0.fade == 0.0   # fades up from black on the real picture
    cap = frame.max_zoom(0.6)
    for lg in legs:
        for s in np.linspace(0, lg.duration, 25):
            assert magnification(lg.pose(s)) <= cap + 1e-6
    assert energy_at(0, 1200) == 1 and energy_at(1200, 1200) == 0


def test_clamp_keeps_view_inside_picture():
    f = Frame(3600, 2000, 1920, 1080)
    p = f.clamp(Pose(-500, 99999, 0.2, 1.3))
    k = f.cover * 1.3
    assert p.cx - 960 / k >= -1e-6 and p.cy + 540 / k <= 2000 + 1e-6


# --- render ------------------------------------------------------------------

def test_frame_at_rest_is_the_picture(scene):
    im, _ = cliff_image()
    f = Frame(scene.width, scene.height, 480, 270)
    out = scene.frame(Pose(240, 135, 0.0, 1.0), 480, 270)
    assert out.shape == (270, 480, 3) and out.dtype == np.uint8
    assert f.cover == 1.0
    diff = np.abs(out.astype(int) - np.asarray(im).astype(int))
    assert diff.mean() < 3.0


def test_near_layer_moves_faster_than_far(scene):
    # A vertical red line 160 px right of centre, crossing sky (far) and cliff
    # (near). Flying in pushes the cliff part outward further than the sky part.
    im, d = cliff_image()
    a = np.asarray(im).copy()
    a[20:255, 399:402] = (255, 0, 0)
    sc = Scene.from_image(Image.fromarray(a), np.asarray(Image.fromarray(d).resize((240, 135), Image.NEAREST)), "model")
    out = sc.frame(Pose(240, 135, 0.25, 1.0), 480, 270)
    red = (out[..., 0].astype(int) - out[..., 1]) > 120
    ys, xs = np.nonzero(red)
    far_x = xs[ys < 90].mean()
    near_x = xs[ys > 190].mean()
    assert 405 < far_x < near_x - 12


def test_numpy_and_opencv_paths_agree(scene):
    cv2 = pytest.importorskip("cv2")  # noqa: F841
    p = Pose(250, 140, 0.3, 1.1)
    a = dataclasses.replace(scene, use_cv2=True).frame(p, 240, 136).astype(int)
    b = dataclasses.replace(scene, use_cv2=False).frame(p, 240, 136).astype(int)
    assert np.abs(a - b).mean() < 4.0


def test_bilinear_samples_between_pixels():
    img = np.array([[0, 100], [100, 200]], dtype=np.uint8)
    v = bilinear(img, np.array([0.5]), np.array([0.5]))
    assert v[0] == 100


def test_render_leg_and_black_clip(tmp_path, scene):
    legs = plan_journey(4, Frame(scene.width, scene.height, 64, 36), _targets(480, 270), seed=0, calm_at=1200)
    out = render_leg(scene, legs[0], tmp_path / "leg.mp4", w=64, h=36, fps=8)
    assert probe_duration(str(out)) == pytest.approx(4.0, abs=0.3)
    blk = render_black(tmp_path / "black.mp4", 2, w=64, h=36, fps=8)
    assert probe_duration(str(blk)) == pytest.approx(2.0, abs=0.3)


# --- the Short ---------------------------------------------------------------

def test_short_opens_moving_pays_off_by_three_seconds_and_reveals_at_the_end(scene):
    targets = find_targets(scene.background, scene.depth, count=4)
    legs = plan_short(scene, targets, seconds=24, width=90, height=160)    # a small scene: a small screen
    p0, p1 = short_pose(legs, 0.0), short_pose(legs, 0.25)
    assert p0.fade == 1.0 and p0.travel > 0                      # mid-motion on frame one, no fade
    assert p1.travel - p0.travel > 0.02                          # ...and moving fast
    assert legs[0].duration == PAYOFF_BY                         # landed by second 3
    payoff = short_pose(legs, PAYOFF_BY)
    assert (payoff.cx, payoff.cy) == pytest.approx(legs[0].c1, abs=1.0)
    mags = [magnification(short_pose(legs, t)) for t in np.linspace(0, 24, 97)]
    assert int(np.argmax(mags)) >= 90                            # the closest view is the ending
    assert legs[-1].start + legs[-1].duration == pytest.approx(24)
    with pytest.raises(ValueError):
        plan_short(scene, targets, seconds=40)


def test_render_journey_short_vertical(tmp_path, scene):
    targets = find_targets(scene.background, scene.depth, count=4)
    out = render_journey_short(scene, targets, tmp_path / "s.mp4", seconds=20, width=54, height=96, fps=5)
    assert probe_duration(str(out)) == pytest.approx(20.0, abs=0.5)


# --- titles and descriptions -------------------------------------------------

def _asset(title="Pillars of Creation (NIRCam Image)", credit="NASA, ESA, CSA, STScI", source="ESA/Webb"):
    return ImageAsset(asset_id="esawebb:x", title=title, url="https://example/x.jpg", source=source, credit=credit,
                      keywords=["nebula"])


def test_journey_subjects_are_nebulae_and_galaxies_only():
    assert journey_subject(_asset("Carina Nebula")) == "nebula"
    assert journey_subject(_asset("Pillars of Creation")) == "nebula"
    assert journey_subject(dataclasses.replace(_asset("NGC 1300"), keywords=["barred spiral galaxy"])) == "galaxy"
    assert journey_subject(dataclasses.replace(_asset("Westerlund 1"), keywords=["star cluster"])) is None


def test_with_article():
    assert with_article("Pillars of Creation") == "the Pillars of Creation"
    assert with_article("Orion Nebula") == "the Orion Nebula"
    assert with_article("NGC 602") == "NGC 602"
    assert with_article("Stephan's Quintet") == "Stephan's Quintet"
    assert with_article("The Ring Nebula") == "The Ring Nebula"


def test_title_is_feeling_first_and_never_claims_3d_without_per_pixel_depth():
    cfg = load_config(CONFIG)
    t = journey_title(cfg, _asset(), total_seconds=3600, sleep=False, depth_source="model")
    assert t == "Flying Into the Pillars of Creation | Real Webb Image | Space Ambient"
    assert "3D" not in t
    cfg.journey.title_template = "{verb} Into {lead} in 3D | Real {telescope} Image"
    assert "3D" in journey_title(cfg, _asset(), total_seconds=3600, sleep=False, depth_source="model")
    assert "3D" not in journey_title(cfg, _asset(), total_seconds=3600, sleep=False, depth_source="brightness")
    assert strip_3d("Flying Into X | 3D | Space", "brightness") == "Flying Into X | Space"
    sleep = journey_title(load_config(CONFIG), _asset(), total_seconds=8 * 3600, sleep=True, depth_source="model")
    assert sleep.startswith("Drifting Into the Pillars of Creation") and "8 Hours" in sleep and len(sleep) <= 100


def test_description_is_honest_about_how_it_is_made():
    cfg = load_config(CONFIG)
    frame = Frame(3600, 2000, 1920, 1080)
    legs = plan_journey(3600, frame, _targets(), seed=2, calm_at=1200)
    for how, phrase in (("model", "per-pixel depth estimate"), ("brightness", "split into depth layers by brightness")):
        m = build_journey_metadata(cfg, _asset(), [], legs, total_seconds=3600, depth_source=how)
        assert "one real image" in m.description and phrase in m.description
        assert "not a measurement of real distances" in m.description
        assert "0:00 Approach" in m.description and "Reveal 1" in m.description
        assert m.privacy == cfg.publish.privacy and len(m.description) <= 5000
    s = build_journey_metadata(cfg, _asset(), [], legs, total_seconds=8 * 3600, depth_source="model", sleep=True,
                               dark_after=2700)
    assert "fades to black" in s.description
    ch = leg_chapters(legs, dark_after=2700, total=8 * 3600)
    assert ch[-1][0] == 2700 and all(t < 2700 for t, _ in ch[:-1])


# --- switch, config, long cuts ------------------------------------------------

def test_channel_config_has_the_journey_block():
    cfg = load_config(CONFIG)
    assert cfg.journey.hours == 1 and 8 in cfg.journey.extra_lengths_hours
    assert cfg.journey.calm_minutes == 20


def test_publish_gate():
    cfg = load_config(CONFIG)
    day = date(2026, 12, 5)
    cfg.journey.enabled = False
    assert publish_gate(cfg, day)[0] is False
    cfg.journey.enabled = True
    cfg.journey.dates = ["2026-12-06"]
    assert publish_gate(cfg, day)[0] is False
    cfg.journey.dates = [date(2026, 12, 5)]          # YAML hands dates over as date objects
    cfg.publish.mode = "upload"
    assert publish_gate(cfg, day)[0] is True
    cfg.journey.dates = []
    assert publish_gate(cfg, day)[0] is True
    cfg.publish.mode = "dry-run"
    assert publish_gate(cfg, day)[0] is False


def test_bad_journey_config_is_rejected(tmp_path):
    text = CONFIG.read_text().replace("  depth: auto\n", "  depth: lidar\n", 1)
    p = tmp_path / "c.yaml"
    p.write_text(text)
    with pytest.raises(ValueError, match="journey.depth"):
        load_config(p)
    p.write_text(CONFIG.read_text().replace("  dates: []\n", "  dates: [tomorrow]\n", 1))
    with pytest.raises(ValueError, match="journey.dates"):
        load_config(p)


def test_long_cut_goes_dark_after_whole_passes():
    frame = Frame(3600, 2000, 1920, 1080)
    legs = plan_journey(3600, frame, _targets(), seed=2, calm_at=1200)
    clips = [Path(f"leg{i}.mp4") for i in range(len(legs))]
    black = Path("black.mp4")
    seq, total, dark = long_cut_sequence(legs, clips, black, total=8 * 3600, dark_after=45 * 60)
    n = seq.index(black)
    assert seq[:n] == clips[:n]
    assert dark >= 45 * 60 and dark == pytest.approx(sum(lg.duration for lg in legs[:n]))
    assert set(seq[n:]) == {black} and total >= 8 * 3600
    seq2, total2, dark2 = long_cut_sequence(legs, clips, None, total=3 * 3600, dark_after=0)
    assert dark2 is None and total2 >= 3 * 3600 and black not in seq2
    assert seq2[:len(clips)] == clips                         # the full journey first, then calm passes
    calm_clips = {c for lg, c in zip(legs, clips) if lg.energy < 0.2}
    assert set(seq2[len(clips):]) <= calm_clips


# --- the whole job, tiny ------------------------------------------------------

def test_run_journey_dry_run_end_to_end(tmp_path, monkeypatch):
    from romanfeed.journey.run import JourneyOptions, run_journey

    cfg = load_config(CONFIG)
    cfg.video.width, cfg.video.height, cfg.video.fps = 96, 54, 6
    cfg.video.intro_seconds = 3
    cfg.video.subscribe_from, cfg.video.subscribe_until = 3.5, 7
    cfg.audio.composed_pieces, cfg.audio.composed_seconds = 1, 20
    cfg.journey.extra_lengths_hours = [0.01]          # 36 s
    cfg.journey.sleep_dark_after_minutes = 0.1
    cfg.journey.enabled = True                         # the switch on, but --dry-run must still win
    cfg.publish.mode = "upload"

    def no_upload(*a, **k):
        raise AssertionError("a dry run must never upload")

    monkeypatch.setattr("romanfeed.publish.youtube._upload", no_upload)
    im, _ = cliff_image(640, 360)
    img = tmp_path / "pillars.png"
    im.save(img)
    res = run_journey(cfg, JourneyOptions(
        minutes=0.2, image=img, title="Pillars of Creation", credit="NASA, ESA, CSA, STScI", source_label="ESA/Webb",
        dry_run=True, short=False, seed="t", data_dir=tmp_path / "data", output_dir=tmp_path / "out", workers=1,
        depth="brightness"))
    assert res.youtube_id is None and res.depth_source == "brightness"
    assert res.title.startswith("Flying Into the Pillars of Creation")
    assert res.video_path.exists() and res.video_path.with_suffix(".upload.json").exists()
    assert probe_duration(str(res.video_path)) == pytest.approx(12, abs=1.0)
    (hours, cut, cut_id), = res.extra_cuts
    assert cut_id is None and probe_duration(str(cut)) >= 35


# --- music: lively at first, calm by calm_at ------------------------------------

def test_journey_music_starts_lively_and_is_calm_by_calm_at():
    from romanfeed.audio.composer import _make_plan, journey_energy, render_pcm

    assert journey_energy(0, 60) == 1 and journey_energy(60, 60) == 0 and journey_energy(90, 60) == 0
    plan = _make_plan(120, "x", 8000, "journey", calm_at=60)
    space = _make_plan(120, "journey:x", 8000, "space")
    from collections import Counter

    extra = sorted((Counter(v.start for v in plan.voices) - Counter(v.start for v in space.voices)).elements())
    assert len(extra) > 50                                                 # the arpeggio and pulse
    assert max(extra) < 60 * 8000                                          # nothing new starts once calm
    early = sum(1 for s in extra if s < 20 * 8000)
    late = sum(1 for s in extra if 40 * 8000 <= s < 60 * 8000)
    assert early > late                                                    # it thins out on the way
    a = render_pcm(30, "x", 8000, style="journey", calm_at=20)
    b = render_pcm(30, "x", 8000, style="journey", calm_at=20)
    assert np.array_equal(a, b)                                            # deterministic
    assert not np.array_equal(a, render_pcm(30, "x", 8000))                # and not the space style
