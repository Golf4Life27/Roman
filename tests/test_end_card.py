"""Subscribe end-card test for Shorts (Nov 21-27, tests.md test 20)."""
from datetime import datetime, timezone
from pathlib import Path

from romanfeed.config import load_config
from romanfeed.publish import shorts_styles as st
from romanfeed.publish.shorts_styles import ShortNumbers
from romanfeed.render.cards import short_end_card
from romanfeed.shorts import END_CARD_TAG, end_card_due

ROOT = Path(__file__).resolve().parents[1]
DURING = datetime(2026, 11, 22, 18, tzinfo=timezone.utc)


def _s():
    return load_config(ROOT / "config/channels/deep-space-ambient.yaml").shorts


def test_half_of_each_run_of_four_and_balanced_across_tests():
    s = _s()
    on = [n for n in range(16) if end_card_due(s, n, DURING)]
    assert len(on) == 8
    assert sum(n % 2 for n in on) == 4            # hook and calm split evenly
    assert sum((n // 2) % 2 for n in on) == 4     # both openings split evenly


def test_only_inside_the_window():
    s = _s()
    assert not end_card_due(s, 1, datetime(2026, 11, 20, 18, tzinfo=timezone.utc))
    assert end_card_due(s, 1, datetime(2026, 11, 27, 18, tzinfo=timezone.utc))
    assert not end_card_due(s, 1, datetime(2026, 11, 28, 18, tzinfo=timezone.utc))
    assert s.end_card_seconds == 3.0


def test_the_card_dims_the_frame_and_draws_the_button():
    img = short_end_card(1080, 1920, channel_name="Space Screens")
    assert img.size == (1080, 1920)
    assert img.getpixel((5, 1900))[3] == 150                 # dimmed everywhere, bottom included
    reds = sum(1 for x in range(0, 1080, 10) for y in range(600, 1000, 10)
               if img.getpixel((x, y))[:3] == (204, 0, 0))
    assert reds > 200                                         # the button sits in the upper half


def _row(vid, end, views, subs, **kw):
    return ShortNumbers(video_id=vid, posted=f"2026-11-2{vid[-1]}T12:00", title=vid, style="calm", earth=False,
                        public_views=views, views=views, subs=subs, end_card=end, **kw)


def test_report_section_compares_card_with_none():
    rows = [_row("a1", False, 100, 0), _row("a2", True, 200, 2), _row("a3", False, 300, 1),
            _row("a4", True, 50, 0, sky=True)]
    lines = st.end_card_lines(rows)
    assert lines[0].startswith("Subscribe end card vs none")
    assert "end card: 1 Shorts" in lines[1] and "10.0 per 1,000" in lines[1]
    assert "no card: 1 Shorts" in lines[2]      # a1 posted before the first card; sky Short sits out
    assert st.end_card_lines([_row("b1", False, 10, 0)]) == []
    assert END_CARD_TAG == "end-card"


def test_a_short_with_the_end_card_ends_on_it(tmp_path):
    import subprocess

    from PIL import Image

    from romanfeed import shorts
    from romanfeed.render.ffmpeg import ffmpeg_path, probe_duration
    from tests.test_shorts import _asset, _cfg

    src = tmp_path / "pic.png"
    Image.new("RGB", (900, 1600), (40, 60, 120)).save(src)
    a = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", local_path=src)
    audio = tmp_path / "a.m4a"
    subprocess.run([ffmpeg_path(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=8",
                    "-c:a", "aac", str(audio)], check=True)
    cfg = _cfg(seconds=6, width=270, height=480, fps=12)
    out = shorts.render_short(a, tmp_path / "x.mp4", cfg=cfg, audio=audio, link_label="8-hour", work_dir=tmp_path,
                              end_card=True)
    assert abs(probe_duration(str(out)) - 6) < 0.5

    def frame(t):
        png = tmp_path / f"f{t}.png"
        subprocess.run([ffmpeg_path(), "-loglevel", "error", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                        str(png)], check=True)
        return Image.open(png).convert("RGB")
    before, during = frame(1.0), frame(4.0)
    # The card's button: bright red in the upper half only once the card is up.
    red = lambda im: sum(1 for x in range(0, 270, 3) for y in range(150, 260, 3)
                         if im.getpixel((x, y))[0] > 150 and im.getpixel((x, y))[1] < 60)
    assert red(during) > 40 and red(before) < red(during) // 4
