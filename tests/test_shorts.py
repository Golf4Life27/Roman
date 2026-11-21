"""Shorts: which video each one comes from, what it says, and the publish switch."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from romanfeed import shorts
from romanfeed.config import AudioSettings, ChannelConfig, ChannelInfo, PublishSettings, ShortsSettings, VideoSettings
from romanfeed.render.ffmpeg import ffmpeg_path, probe_duration
from romanfeed.sources.base import ImageAsset
from romanfeed.state import Ledger, VideoRecord
from romanfeed.telescopes import prefer_known_lead, telescope_line, telescope_short


def _asset(i: int, **kw) -> ImageAsset:
    base = dict(asset_id=f"nasa:A{i}", title=f"Object {i}", url=f"https://example/{i}.jpg", source="NASA Image Library",
                credit="NASA/ESA", description="d", keywords=[])
    base.update(kw)
    return ImageAsset(**base)


def _video(ledger: Ledger, slug: str, yt: str | None, seconds: float, when: str) -> None:
    ledger.record_video(VideoRecord(slug, "chan", f"/x/{slug}.mp4", seconds, when, youtube_id=yt))


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path / "state.db") as led:
        # two rendered sets, each with a 1h primary and an 8h cut
        _video(led, "chan-2026-09-28", "OLD1h", 3600, "2026-09-28T10:00:00")
        _video(led, "chan-2026-09-28-8h", "OLD8h", 28800, "2026-09-28T11:00:00")
        _video(led, "chan-2026-09-30", "NEW1h", 3600, "2026-09-30T10:00:00")
        _video(led, "chan-2026-09-30-8h", "NEW8h", 28800, "2026-09-30T11:00:00")
        led.record_video_assets("chan-2026-09-28", [_asset(i) for i in range(0, 10)])
        led.record_video_assets("chan-2026-09-30", [_asset(i) for i in range(10, 20)])
        yield led


def test_ledger_keeps_running_order_and_metadata(ledger):
    assert ledger.assets_of_video("chan-2026-09-30") == [f"nasa:A{i}" for i in range(10, 20)]
    meta = ledger.asset_meta(["nasa:A12"])
    assert meta["nasa:A12"]["title"] == "Object 12" and meta["nasa:A12"]["url"].endswith("12.jpg")
    assert shorts.asset_from_meta(meta["nasa:A12"]).asset_id == "nasa:A12"


def test_old_videos_fall_back_to_assets_used(ledger):
    ledger.mark_assets_used("chan", ["nasa:Z1", "nasa:Z2"], "chan-2026-09-01")
    assert ledger.assets_of_video("chan-2026-09-01") == ["nasa:Z1", "nasa:Z2"]


def test_shorts_link_to_the_longest_public_cut(ledger):
    ps = shorts.parents(ledger, "chan", public=lambda vid: True)
    assert [(p.slug, p.link_id) for p in ps] == [("chan-2026-09-30", "NEW8h"), ("chan-2026-09-28", "OLD8h")]
    # 8h still private -> fall back to the public 1h; nothing public -> skipped
    ps = shorts.parents(ledger, "chan", public=lambda vid: vid in {"NEW1h"})
    assert [(p.slug, p.link_id, p.link_seconds) for p in ps] == [("chan-2026-09-30", "NEW1h", 3600)]


def test_plan_rotates_parents_and_never_shares_candidates(ledger):
    picks = shorts.plan(ledger, "chan", 3, per_short=3, public=lambda vid: True)
    assert [p.slug for p, _ in picks] == ["chan-2026-09-30", "chan-2026-09-28", "chan-2026-09-30"]
    flat = [a for _, ids in picks for a in ids]
    assert len(flat) == len(set(flat)) == 9
    # spread through the running order, not the first three images
    assert picks[0][1] == ["nasa:A10", "nasa:A13", "nasa:A16"]


def test_plan_prefers_parents_with_fewer_shorts_and_skips_used_images(ledger):
    ledger.record_short("chan", "nasa:A10", "chan-2026-09-30", "S1")
    picks = shorts.plan(ledger, "chan", 1, per_short=20, public=lambda vid: True)
    assert picks[0][0].slug == "chan-2026-09-28"
    picks = shorts.plan(ledger, "chan", 2, per_short=20, public=lambda vid: True)
    assert "nasa:A10" not in picks[1][1]


def test_telescope_detection():
    webb = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", credit="NASA, ESA, CSA, STScI")
    hubble = _asset(2, title="Westerlund 2", source="ESA/Hubble", credit="NASA, ESA, the Hubble Heritage Team")
    both = _asset(3, title="Pillars of Creation: Hubble and Webb compared", credit="NASA, ESA, CSA")
    unknown = _asset(4, title="Otherwise unnamed nebula", credit="NASA/JPL")
    assert telescope_line(webb) == "Imaged by the James Webb Space Telescope"
    assert telescope_short(hubble) == "Hubble"
    assert telescope_line(both) == "Imaged by the James Webb Space Telescope and Hubble Space Telescope"
    assert telescope_line(unknown) is None and telescope_short(unknown) is None
    assert prefer_known_lead([unknown, hubble, webb])[0] is hubble


def _cfg(**shorts_kw) -> ChannelConfig:
    return ChannelConfig(
        channel=ChannelInfo(slug="chan", name="Space Screens", handle="@SpaceScreens"),
        video=VideoSettings(), sources=[], audio=AudioSettings(allow_placeholder=True),
        publish=PublishSettings(mode="upload", privacy="public"), shorts=ShortsSettings(**shorts_kw),
    )


def test_short_metadata_points_at_the_full_video():
    a = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", description="Star birth in Carina.")
    md = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), [])
    assert md.title == "Cosmic Cliffs by Webb | Calm Space for Sleep"
    assert md.description.startswith("Full 8-hour sleep video: https://youtu.be/VID8h")
    assert "sub_confirmation=1" in md.description and "#shorts" in md.description
    assert md.privacy == "public"
    unknown = shorts.short_metadata(_cfg(), _asset(2, title="Faint Smudge"), shorts.Parent("p", "V", 3600, ""), [])
    assert unknown.title == "Faint Smudge | Calm Space for Sleep"  # no dangling "by"


def test_image_score_prefers_colour_and_detail(tmp_path):
    flat = tmp_path / "flat.png"
    Image.new("RGB", (400, 300), (20, 20, 22)).save(flat)
    rich = tmp_path / "rich.png"
    Image.merge("RGB", [Image.effect_noise((400, 300), 80).point(lambda v: min(255, v + k)) for k in (60, 0, 30)]).save(rich)
    assert shorts.image_score(rich) > shorts.image_score(flat)


def test_switch_off_renders_but_never_uploads(ledger, monkeypatch, tmp_path):
    led_path = ledger.db_path
    ledger.close()
    sent = []
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: sent.append(mode) or ("YT" if mode == "upload" else None))
    monkeypatch.setattr(shorts, "is_public", lambda vid: True)
    monkeypatch.setattr(shorts, "ranked_candidates", lambda cands, cache, *w: cands)
    monkeypatch.setattr(shorts, "build_soundtrack", lambda lib, **kw: (Path(kw["out_path"]), []))
    monkeypatch.setattr(shorts, "render_short", lambda a, out, **kw: out)
    monkeypatch.delenv("GOOGLE_TTS_API_KEY", raising=False)
    data = led_path.parent

    res = shorts.run_shorts(_cfg(enabled=False), count=2, data_dir=data, output_dir=tmp_path / "out", public=lambda v: True)
    assert len(res) == 2 and sent == ["dry-run", "dry-run"]
    with Ledger(led_path) as led:
        assert led.shorts_asset_ids("chan") == set()  # dry runs burn nothing

    sent.clear()
    res = shorts.run_shorts(_cfg(enabled=True, per_day=3), count=5, data_dir=data, output_dir=tmp_path / "out", public=lambda v: True)
    assert sent == ["upload"] * 3  # capped at per_day
    again = shorts.run_shorts(_cfg(enabled=True, per_day=3), data_dir=data, output_dir=tmp_path / "out", public=lambda v: True)
    assert again == []  # the cap holds across runs on the same day


def test_render_short_is_vertical_and_timed(tmp_path):
    src = tmp_path / "wide.png"
    Image.effect_noise((1600, 900), 50).convert("RGB").save(src)
    a = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", local_path=src)
    audio = tmp_path / "a.m4a"
    subprocess.run([ffmpeg_path(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=20",
                    "-c:a", "aac", str(audio)], check=True)
    cfg = _cfg(seconds=15, width=270, height=480, fps=12)
    out = shorts.render_short(a, tmp_path / "s.mp4", cfg=cfg, audio=audio, link_label="8-hour", work_dir=tmp_path)
    assert abs(probe_duration(str(out)) - 15) < 0.5
    probe = subprocess.run([ffmpeg_path(), "-i", str(out)], capture_output=True, text=True).stderr
    assert "270x480" in probe


def test_ledger_merge_keeps_both_jobs_rows(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    with Ledger(a) as la:
        la.record_short("chan", "nasa:X1", "p", "S1")            # a Shorts run
    with Ledger(b) as lb:
        lb.record_video(VideoRecord("chan-2026-10-07", "chan", "/v.mp4", 3600, "2026-10-07T12:00:00"))
        lb.mark_published("chan-2026-10-07", "V1", "t")          # the render, same time
        added = lb.merge_from(a)
        assert lb.shorts_asset_ids("chan") == {"nasa:X1"} and added["shorts"] == 1
        assert lb.videos("chan")[0].youtube_id == "V1"


def test_feed_titles_block_a_second_short_of_the_same_image():
    a = _asset(1, title="Extra X-rays at the Hub of Our Milky Way Galaxy")
    assert shorts.already_posted(a, ["Extra X-rays at the Hub of Our Milky Way Galaxy | Calm Space for Sleep"])
    assert not shorts.already_posted(a, ["Cosmic fairy lights by Hubble | Calm Space for Sleep"])


def test_no_by_when_the_name_already_names_a_telescope():
    a = _asset(1, title="Spitzer Space Telescope View of Galaxy Messier 101", credit="NASA/JPL-Caltech", keywords=["hubble"])
    md = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "V", 3600, ""), [])
    assert " by " not in md.title


def test_a_video_with_nothing_usable_hands_its_turn_on(ledger, monkeypatch, tmp_path):
    led_path = ledger.db_path
    ledger.close()
    sent = []
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: sent.append(md.title) or "YT")
    monkeypatch.setattr(shorts, "is_public", lambda vid: True)
    # The newest video's images are all unusable (as an Earth video's were).
    monkeypatch.setattr(shorts, "ranked_candidates",
                        lambda cands, cache, *w: [a for a in cands if int(a.asset_id.split("A")[1]) < 10])
    monkeypatch.setattr(shorts, "build_soundtrack", lambda lib, **kw: (Path(kw["out_path"]), []))
    monkeypatch.setattr(shorts, "render_short", lambda a, out, **kw: out)
    monkeypatch.delenv("GOOGLE_TTS_API_KEY", raising=False)
    res = shorts.run_shorts(_cfg(enabled=True, per_day=4), count=1, data_dir=led_path.parent,
                            output_dir=tmp_path / "out", public=lambda v: True)
    assert len(res) == 1 and res[0].parent_slug == "chan-2026-09-28" and len(sent) == 1


def _at(stamp: str):
    from datetime import datetime
    return datetime.fromisoformat(stamp)


def test_the_cap_counts_the_last_24_hours_so_late_runs_keep_their_slot(tmp_path):
    # GitHub started some runs 3 to 6 hours late. Counted by calendar day, the
    # late 23:10 run fell into the next day and that day lost a Short.
    with Ledger(tmp_path / "state.db") as led:
        def short_at(stamp, n):
            led.record_short("chan", f"nasa:L{n}", "p", f"S{n}")
            led.conn.execute("UPDATE shorts SET created_at = ? WHERE asset_id = ?", (stamp, f"nasa:L{n}"))
        short_at("2026-10-30T16:40:00+00:00", 1)
        short_at("2026-10-31T02:18:00+00:00", 2)
        short_at("2026-10-31T06:50:00+00:00", 3)
        assert shorts.shorts_today(led, "chan", _at("2026-10-31T09:40:00+00:00")) == 3
        assert shorts.shorts_today(led, "chan", _at("2026-10-31T17:00:00+00:00")) == 2
        assert sorted(shorts.recent_shorts(led, "chan", _at("2026-11-01T03:00:00+00:00"))) == ["nasa:L3"]


def test_the_test_runs_through_its_last_day_in_central_time_then_falls_back():
    s = ShortsSettings(per_day=4, test_per_day=8, test_until="2026-11-03")
    assert shorts.daily_target(s, _at("2026-10-31T12:00:00+00:00")) == 8
    assert shorts.daily_target(s, _at("2026-11-04T04:30:00+00:00")) == 8   # 23:30 Central on Nov 3
    assert shorts.daily_target(s, _at("2026-11-04T06:00:00+00:00")) == 4
    assert shorts.daily_target(ShortsSettings(per_day=4)) == 4


def test_a_test_only_slot_posts_nothing_once_the_test_is_over(tmp_path, monkeypatch):
    monkeypatch.setattr(shorts, "_snapshot", lambda *a, **k: None)
    cfg = _cfg(enabled=True, per_day=4, test_per_day=8, test_until="2020-01-01")
    assert shorts.run_shorts(cfg, extra=True, data_dir=tmp_path, output_dir=tmp_path / "out", public=lambda v: True) == []


def test_the_channel_config_runs_the_shorts_test_on_the_test_slots():
    import re
    from romanfeed.config import load_config
    cfg = load_config(Path("config/channels/deep-space-ambient.yaml"))
    assert cfg.shorts.per_day == 4 and cfg.shorts.test_per_day == 8
    wf = Path(".github/workflows/shorts.yml").read_text()
    crons = re.findall(r'- cron: "([^"]+)"', wf)
    extra = re.search(r"EXTRA: .*fromJSON\('(\[.*?\])'\)", wf).group(1)
    import json
    extra = json.loads(extra)
    backup = json.loads(re.search(r"BACKUP: .*fromJSON\('(\[.*?\])'\)", wf).group(1))
    slots = [c for c in crons if c not in backup]  # backups post only after a skipped slot
    assert set(backup) <= set(crons) and not set(backup) & set(extra)
    assert len(slots) == cfg.shorts.test_per_day
    assert set(extra) <= set(slots) and len(slots) - len(extra) == cfg.shorts.per_day


def test_other_scheduled_workflows_start_a_backup_shorts_run():
    # GitHub dropped every Shorts cron for 8 hours on Nov 5 while other
    # scheduled workflows ran; their completion starts a backup run.
    import yaml
    wf = yaml.safe_load(Path(".github/workflows/shorts.yml").read_text())
    on = wf.get("on", wf.get(True))
    names = on["workflow_run"]["workflows"]
    existing = {yaml.safe_load(p.read_text()).get("name") for p in Path(".github/workflows").glob("*.yml")}
    assert names and set(names) <= existing and "Shorts" not in names
    assert on["workflow_run"]["types"] == ["completed"]
    text = Path(".github/workflows/shorts.yml").read_text()
    assert "BACKUP: ${{ github.event_name == 'workflow_run' ||" in text


def test_a_hook_opens_on_the_captions_own_distance():
    from romanfeed import narration
    a = _asset(1, title="Messier 13", source="ESA/Hubble",
               description="Messier 13 is a globular cluster packed with several hundred thousand stars. "
                           "It lies about 22,000 light-years away in the constellation of Hercules.")
    hook = narration.hook_line(a, a.description)
    assert hook == "The light in this picture has been traveling for about 22,000 years to reach you."
    script = narration.hook_script(a, full_label="8-hour")
    assert script.startswith(hook + " Messier 13 is a globular cluster")
    assert " The full 8-hour version is on the channel." in script


def test_a_hook_without_numbers_says_only_what_is_true_of_every_image():
    from romanfeed import narration
    a = _asset(2, title="A spiral", source="ESA/Hubble",
               description="This spiral galaxy shows a bright core surrounded by dusty winding arms of young stars.")
    assert narration.hook_line(a, a.description) in narration.SPACE_HOOKS
    assert narration.hook_line(a, "It is 120,000 light-years across.") == \
        "What you're looking at is 120,000 light-years across."


def test_a_hook_style_short_is_tagged_for_the_report():
    a = _asset(3, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", description="Star birth in Carina.")
    md = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), [], hook=True)
    assert md.tags[0] == shorts.HOOK_TAG
    assert shorts.HOOK_TAG not in shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), []).tags


def test_earth_shorts_stop_at_their_cap(ledger, monkeypatch, tmp_path):
    led_path = ledger.db_path
    ledger.close()
    sent = []
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: sent.append(md.tags[0]) or "YT")
    monkeypatch.setattr(shorts, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(shorts, "ranked_candidates", lambda cands, cache, *w: cands)
    monkeypatch.setattr(shorts, "build_soundtrack", lambda lib, **kw: (Path(kw["out_path"]), []))
    monkeypatch.setattr(shorts, "render_short", lambda a, out, **kw: out)
    monkeypatch.setattr(shorts, "is_earth", lambda a: True)
    monkeypatch.delenv("GOOGLE_TTS_API_KEY", raising=False)
    res = shorts.run_shorts(_cfg(enabled=True, per_day=4, earth_per_day=1), count=3, data_dir=led_path.parent,
                            output_dir=tmp_path / "out", public=lambda v: True)
    assert len(res) == 1


def test_capped_earth_sets_do_not_crowd_out_deep_sky(ledger, monkeypatch, tmp_path):
    """Nov 1-2: Earth sets had the fewest Shorts, so they took every pick while
    Earth was at its cap, and runs posted nothing for 18 hours."""
    led_path = ledger.db_path
    earth = [_asset(i, keywords=["earth"]) for i in range(30, 50)]
    for n, day in enumerate(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]):
        _video(ledger, f"chan-{day}", f"E{n}", 28800, f"{day}T10:00:00")
        ledger.record_video_assets(f"chan-{day}", earth[n * 5:(n + 1) * 5])
    # the two deep-sky sets each already have a Short; the Earth sets have none
    ledger.record_short("chan", "nasa:A0", "chan-2026-09-28", "S0")
    ledger.record_short("chan", "nasa:A10", "chan-2026-09-30", "S1")
    ledger.record_short("chan", "nasa:A30", "chan-2026-10-01", "S2")
    ledger.close()
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: "YT")
    monkeypatch.setattr(shorts, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(shorts, "ranked_candidates", lambda cands, cache, *w: cands)
    monkeypatch.setattr(shorts, "already_posted", lambda a, posted: False)
    monkeypatch.setattr(shorts, "recent_upload_titles", lambda channel_id: [])
    monkeypatch.setattr(shorts, "build_soundtrack", lambda lib, **kw: (Path(kw["out_path"]), []))
    monkeypatch.setattr(shorts, "render_short", lambda a, out, **kw: out)
    monkeypatch.setattr(shorts, "is_earth", lambda a: "earth" in a.keywords)
    monkeypatch.delenv("GOOGLE_TTS_API_KEY", raising=False)
    res = shorts.run_shorts(_cfg(enabled=True, per_day=4, earth_per_day=1), count=1, data_dir=led_path.parent,
                            output_dir=tmp_path / "out", public=lambda v: True)
    assert [r.parent_slug for r in res] in (["chan-2026-09-28"], ["chan-2026-09-30"])


def _quiet_run(monkeypatch):
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: "YT")
    monkeypatch.setattr(shorts, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(shorts, "ranked_candidates", lambda cands, cache, *w: cands)
    monkeypatch.setattr(shorts, "already_posted", lambda a, posted: False)
    monkeypatch.setattr(shorts, "recent_upload_titles", lambda channel_id: [])
    monkeypatch.setattr(shorts, "build_soundtrack", lambda lib, **kw: (Path(kw["out_path"]), []))
    monkeypatch.setattr(shorts, "render_short", lambda a, out, **kw: out)
    monkeypatch.delenv("GOOGLE_TTS_API_KEY", raising=False)


@pytest.mark.parametrize("posted_today, catch_up, made", [
    (1, True, 2),    # two of four slots never ran: the scheduled run makes one up
    (1, False, 1),   # a manual one-Short run stays at one
    (3, True, 1),    # on pace: no catch-up
])
def test_a_scheduled_run_makes_up_a_skipped_slot(ledger, monkeypatch, tmp_path, posted_today, catch_up, made):
    """Nov 2-4: GitHub skipped the morning slots day after day."""
    led_path = ledger.db_path
    for i in range(posted_today):
        ledger.record_short("chan", f"nasa:A{i}", "chan-2026-09-28", f"S{i}")
    ledger.close()
    _quiet_run(monkeypatch)
    res = shorts.run_shorts(_cfg(enabled=True, per_day=4), count=1, catch_up=catch_up, data_dir=led_path.parent,
                            output_dir=tmp_path / "out", public=lambda v: True)
    assert len(res) == made


@pytest.mark.parametrize("posted_today, made", [(1, 2), (3, 0)])
def test_a_backup_slot_posts_only_after_a_skipped_slot(ledger, monkeypatch, tmp_path, posted_today, made):
    led_path = ledger.db_path
    for i in range(posted_today):
        ledger.record_short("chan", f"nasa:A{i}", "chan-2026-09-28", f"S{i}")
    ledger.close()
    _quiet_run(monkeypatch)
    res = shorts.run_shorts(_cfg(enabled=True, per_day=4), count=1, backup=True, data_dir=led_path.parent,
                            output_dir=tmp_path / "out", public=lambda v: True)
    assert len(res) == made


def test_no_earth_shorts_at_all_when_the_cap_is_minus_one(ledger, monkeypatch, tmp_path):
    led_path = ledger.db_path
    ledger.close()
    _quiet_run(monkeypatch)
    monkeypatch.setattr(shorts, "is_earth", lambda a: int(a.asset_id.split("A")[1]) >= 10)  # the newer set is Earth
    res = shorts.run_shorts(_cfg(enabled=True, per_day=4, earth_per_day=-1), count=3, data_dir=led_path.parent,
                            output_dir=tmp_path / "out", public=lambda v: True)
    assert res and all(r.parent_slug == "chan-2026-09-28" for r in res)


def test_styles_alternate_across_runs_at_the_cap(ledger, monkeypatch, tmp_path):
    """Nov 4-5: at the cap the 24-hour count never changed, so every run was calm."""
    led_path = ledger.db_path
    ledger.close()
    _quiet_run(monkeypatch)
    hooks = []
    # at the cap the 24-hour window holds the same number every run
    monkeypatch.setattr(shorts, "recent_shorts", lambda *a, **k: ["x1", "x2", "x3"])
    monkeypatch.setattr(shorts.narration, "api_key", lambda: "k")
    monkeypatch.setattr(shorts, "narrate", lambda cand, **kw: hooks.append(kw["hook"]) or None)
    for _ in range(4):
        shorts.run_shorts(_cfg(enabled=True, per_day=40, narration=True, hook_every=2), count=1, data_dir=led_path.parent,
                          output_dir=tmp_path / "out", public=lambda v: True)
    assert hooks[:1] and len(set(hooks)) == 2


def test_a_why_short_is_cut_when_due_and_falls_back_when_no_caption_answers(ledger, monkeypatch, tmp_path):
    from romanfeed import why as why_mod
    led_path = ledger.db_path
    for i in range(2):   # two Shorts posted before: the next is the first of a why pair
        ledger.record_short("chan", f"nasa:P{i}", "chan-2026-09-28", f"P{i}")
    ledger.close()
    _quiet_run(monkeypatch)
    monkeypatch.setattr(shorts, "recent_shorts", lambda *a, **k: [])
    monkeypatch.setattr(shorts.narration, "api_key", lambda: "k")
    calls, published = [], []
    monkeypatch.setattr(shorts, "narrate", lambda cand, **kw: calls.append(kw.get("why")) or ("Q? A.", Path("v.wav"), 10.0))
    monkeypatch.setattr(shorts, "_render_cut", lambda *a, **kw: Path(kw.get("work", tmp_path)) / "x.mp4")
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: published.append(md) or "YT")
    w = why_mod.Why("Why does Object glow red?", "Its red glow comes from hydrogen.", "NASA", "https://images.nasa.gov/details/A1")
    monkeypatch.setattr(why_mod, "find_why", lambda a, lead="": w)
    cfg = _cfg(enabled=True, per_day=40, narration=True, hook_every=2, why_every=2, why_until="2099-01-01")
    shorts.run_shorts(cfg, count=1, data_dir=led_path.parent, output_dir=tmp_path / "out", public=lambda v: True)
    assert calls[0] is w and published[0].tags[0] == why_mod.WHY_TAG
    # no caption answers a why: the slot still gets a standard Short
    calls.clear(); published.clear()
    monkeypatch.setattr(why_mod, "find_why", lambda a, lead="": None)
    shorts.run_shorts(cfg, count=1, data_dir=led_path.parent, output_dir=tmp_path / "out", public=lambda v: True)
    assert published and calls and calls[-1] is None and why_mod.WHY_TAG not in published[-1].tags


def test_a_why_slot_looks_across_the_whole_library(ledger, monkeypatch, tmp_path):
    """Nov 6-7: two why slots found no answer in four videos' picks; answers are rare."""
    from romanfeed import why as why_mod
    led_path = ledger.db_path
    for i in range(2):   # the next Short is the first of a why pair
        ledger.record_short("chan", f"nasa:P{i}", "chan-2026-09-28", f"P{i}")
    ledger.close()
    _quiet_run(monkeypatch)
    monkeypatch.setattr(shorts, "recent_shorts", lambda *a, **k: [])
    monkeypatch.setattr(shorts.narration, "api_key", lambda: "k")
    calls, published = [], []
    monkeypatch.setattr(shorts, "narrate", lambda cand, **kw: calls.append((cand.asset_id, kw.get("why"))) or ("Q? A.", Path("v.wav"), 10.0))
    monkeypatch.setattr(shorts, "_render_cut", lambda *a, **kw: Path(kw.get("work", tmp_path)) / "x.mp4")
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: published.append(md) or "YT")
    w = why_mod.Why("Why does Object 7 glow?", "It glows because a hot star lights it.", "NASA", "https://images.nasa.gov/details/A7")
    # one image in the whole library answers a why, and it is not among the planned picks
    monkeypatch.setattr(why_mod, "find_why", lambda a, lead="": w if a.asset_id == "nasa:A7" else None)
    monkeypatch.setattr(shorts, "plan", lambda *a, **k: [(shorts.parents(shorts.Ledger(led_path), "chan", public=lambda v: True)[0], ["nasa:A15", "nasa:A16"])])
    cfg = _cfg(enabled=True, per_day=40, narration=True, hook_every=2, why_every=2, why_until="2099-01-01")
    res = shorts.run_shorts(cfg, count=1, data_dir=led_path.parent, output_dir=tmp_path / "out", public=lambda v: True)
    assert len(published) == 1 and calls == [("nasa:A7", w)] and published[0].tags[0] == why_mod.WHY_TAG
    assert res[0].asset_id == "nasa:A7" and res[0].parent_slug == "chan-2026-09-28"


def test_the_why_pool_never_asks_the_same_question_twice(ledger):
    from romanfeed import why as why_mod
    ledger.record_short("chan", "nasa:A1", "chan-2026-09-28", "S1")
    same = why_mod.Why("Why does X glow?", "It glows because of a star.", "NASA", "u")
    other = why_mod.Why("Why is it called Y?", "Y is so named because of its shape.", "NASA", "u")
    import romanfeed.why as w
    orig = w.find_why
    try:
        w.find_why = lambda a, lead="": {"nasa:A1": same, "nasa:A2": same, "nasa:A3": other, "nasa:A4": other}.get(a.asset_id)
        pool = shorts.why_pool(ledger, "chan", public=lambda v: True)
    finally:
        w.find_why = orig
    assert [a.asset_id for _, a, _ in pool] == ["nasa:A3"]


def test_punch_in_alternates_by_pair_inside_its_window():
    from datetime import datetime, timezone

    from romanfeed.config import ShortsSettings
    from romanfeed.shorts import punch_due

    s = ShortsSettings(punch_in_until="2026-11-19")
    inside = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    assert [punch_due(s, n, inside) for n in range(8)] == [False, False, True, True] * 2
    assert not punch_due(s, 2, datetime(2026, 11, 20, 12, tzinfo=timezone.utc))
    assert not punch_due(ShortsSettings(), 2, inside)


def test_a_punch_in_short_is_tagged_for_the_report():
    from romanfeed.config import load_config
    from romanfeed.shorts import PUNCH_TAG, Parent, short_metadata
    from romanfeed.sources.base import ImageAsset

    cfg = load_config(Path(__file__).resolve().parents[1] / "config/channels/deep-space-ambient.yaml")
    asset = ImageAsset(asset_id="nasa:x", title="Ring Nebula", url="", source="NASA", credit="NASA")
    parent = Parent("p", "vid", 28800, "2026-11-01")
    assert PUNCH_TAG in short_metadata(cfg, asset, parent, [], punch_in=True).tags
    assert PUNCH_TAG not in short_metadata(cfg, asset, parent, []).tags


def test_a_punch_in_short_opens_zoomed_and_settles_on_the_full_frame(tmp_path):
    src = tmp_path / "wide.png"
    Image.effect_noise((1600, 900), 50).convert("RGB").save(src)
    a = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", local_path=src)
    audio = tmp_path / "a.m4a"
    subprocess.run([ffmpeg_path(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=8",
                    "-c:a", "aac", str(audio)], check=True)
    cfg = _cfg(seconds=4, width=270, height=480, fps=12)
    out = shorts.render_short(a, tmp_path / "p.mp4", cfg=cfg, audio=audio, link_label="8-hour", work_dir=tmp_path,
                              punch_in=True)
    assert abs(probe_duration(str(out)) - 4) < 0.5
    probe = subprocess.run([ffmpeg_path(), "-i", str(out)], capture_output=True, text=True).stderr
    assert "270x480" in probe


def test_anticipation_alternates_by_pair_between_its_dates():
    from datetime import datetime, timezone

    from romanfeed.shorts import anticipation_due
    s = ShortsSettings(anticipation_from="2026-11-21", anticipation_until="2026-11-27")
    inside = datetime(2026, 11, 22, 18, tzinfo=timezone.utc)
    assert [anticipation_due(s, n, inside) for n in range(8)] == [False, False, True, True] * 2
    assert not anticipation_due(s, 2, datetime(2026, 11, 20, 18, tzinfo=timezone.utc))
    assert not anticipation_due(s, 2, datetime(2026, 11, 28, 18, tzinfo=timezone.utc))
    assert not anticipation_due(ShortsSettings(), 2, inside)


def test_an_anticipation_short_is_tagged_for_the_report():
    from romanfeed.shorts import ANTIC_TAG, short_metadata
    cfg = _cfg()
    asset = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb")
    parent = shorts.Parent("p", "VID8h", 28800, "")
    assert ANTIC_TAG in short_metadata(cfg, asset, parent, [], anticipation=True).tags
    assert ANTIC_TAG not in short_metadata(cfg, asset, parent, []).tags


def test_subject_point_finds_the_glow_and_falls_back_to_the_middle(tmp_path):
    from PIL import ImageDraw, ImageFilter
    from romanfeed.shorts import subject_point
    im = Image.new("RGB", (360, 640), (5, 5, 10))
    ImageDraw.Draw(im).ellipse((220, 120, 320, 220), fill=(255, 160, 100))
    im.filter(ImageFilter.GaussianBlur(20)).save(tmp_path / "glow.png")
    u, v = subject_point(tmp_path / "glow.png", (0, 0, 360, 640))
    assert 0.6 < u < 0.85 and 0.15 < v < 0.35
    Image.new("RGB", (360, 640), (5, 5, 10)).save(tmp_path / "flat.png")
    assert subject_point(tmp_path / "flat.png", (0, 0, 360, 640)) == (0.5, 0.5)


def test_an_anticipation_short_opens_close_and_lands_on_the_full_frame(tmp_path):
    from PIL import ImageDraw, ImageFilter
    src = tmp_path / "glow.png"
    im = Image.new("RGB", (900, 1600), (5, 5, 10))
    ImageDraw.Draw(im).ellipse((500, 400, 800, 700), fill=(255, 160, 100))
    im.filter(ImageFilter.GaussianBlur(40)).save(src)
    a = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", local_path=src)
    audio = tmp_path / "a.m4a"
    subprocess.run([ffmpeg_path(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=8",
                    "-c:a", "aac", str(audio)], check=True)
    cfg = _cfg(seconds=4, width=270, height=480, fps=12)
    out = shorts.render_short(a, tmp_path / "x.mp4", cfg=cfg, audio=audio, link_label="8-hour", work_dir=tmp_path,
                              anticipation=True)
    assert abs(probe_duration(str(out)) - 4) < 0.5
    probe = subprocess.run([ffmpeg_path(), "-i", str(out)], capture_output=True, text=True).stderr
    assert "270x480" in probe


def test_one_explainer_a_day_on_its_dates_never_in_an_anticipation_slot(ledger, monkeypatch, tmp_path):
    from romanfeed import explainer as ex
    from romanfeed import why as why_mod
    led_path = ledger.db_path
    ledger.close()
    _quiet_run(monkeypatch)
    monkeypatch.setattr(shorts, "recent_shorts", lambda *a, **k: [])
    monkeypatch.setattr(shorts.narration, "api_key", lambda: "k")
    calls, published, cuts = [], [], []
    monkeypatch.setattr(shorts, "narrate", lambda cand, **kw: calls.append(kw.get("explain")) or ("Q? A.", Path("v.wav"), 50.0))
    monkeypatch.setattr(shorts, "_render_cut", lambda *a, **kw: cuts.append(kw) or Path(kw.get("work", tmp_path)) / "x.mp4")
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: published.append(md) or f"YT{len(published)}")
    w = why_mod.Why("Why does Object glow?", "It glows because a hot star lights it.", "NASA", "https://images.nasa.gov/details/A1")
    monkeypatch.setattr(ex, "find_explainer", lambda a, lead="": (w, [w.answer, "More about it."]))
    monkeypatch.setattr(ex, "_central_day", lambda now=None: "2026-11-21")
    cfg = _cfg(enabled=True, per_day=40, narration=True, explainer_dates=["2026-11-21"],
               anticipation_from="2000-01-01", anticipation_until="2099-01-01")
    # n = 0, 1 are a plain pair; 2, 3 an anticipation pair
    shorts.run_shorts(cfg, count=3, data_dir=led_path.parent, output_dir=tmp_path / "out", public=lambda v: True)
    assert [ex.EXPLAINER_TAG in md.tags for md in published] == [True, False, False]
    assert published[0].synthetic and not published[1].synthetic
    assert cuts[0]["duration"] > 45 and not cuts[0]["anticipation"]
    with shorts.Ledger(led_path) as led:
        assert led.explainer_days("chan") == {"2026-11-21"}
    # the day's explainer is up: the next run on the same day posts a regular Short
    published.clear()
    shorts.run_shorts(cfg, count=1, data_dir=led_path.parent, output_dir=tmp_path / "out", public=lambda v: True)
    assert published and ex.EXPLAINER_TAG not in published[0].tags


def test_a_regular_short_skips_an_image_kept_for_an_explainer(ledger, monkeypatch, tmp_path):
    from romanfeed import explainer as ex
    from romanfeed import why as why_mod
    led_path = ledger.db_path
    ledger.close()
    _quiet_run(monkeypatch)
    monkeypatch.setattr(shorts, "recent_shorts", lambda *a, **k: [])
    published = []
    monkeypatch.setattr(shorts, "_render_cut", lambda *a, **kw: Path(kw.get("work", tmp_path)) / "x.mp4")
    monkeypatch.setattr(shorts, "publish", lambda path, md, mode: published.append(md) or "YT")
    w = why_mod.Why("Why?", "Because.", "NASA", "u")
    monkeypatch.setattr(shorts, "plan", lambda *a, **k: [(shorts.parents(shorts.Ledger(led_path), "chan", public=lambda v: True)[0], ["nasa:A15", "nasa:A16"])])
    monkeypatch.setattr(shorts, "ranked_candidates", lambda cands, cache, *w: list(cands))
    monkeypatch.setattr(ex, "find_explainer", lambda a, lead="": (w, ["x"]) if a.asset_id == "nasa:A15" else None)
    cfg = _cfg(enabled=True, per_day=40, explainer_dates=["2099-01-01"])
    res = shorts.run_shorts(cfg, count=1, data_dir=led_path.parent, output_dir=tmp_path / "out", public=lambda v: True)
    assert res[0].asset_id == "nasa:A16"


def test_hook_slots_give_hook_two_of_eight_one_in_each_pair_group():
    s = ShortsSettings(hook_every=2, hook_slots=[1, 3])
    hooks = [n for n in range(16) if shorts.is_hook(s, n)]
    assert hooks == [1, 3, 9, 11]
    # one hook in the plain pairs ((n // 2) % 2 == 0), one in the test pairs
    assert [(n // 2) % 2 for n in hooks[:2]] == [0, 1]
    assert [n for n in range(4) if shorts.is_hook(ShortsSettings(hook_every=2), n)] == [1, 3]


def test_the_subject_weight_lifts_a_galaxy_over_a_slightly_better_nebula(monkeypatch, tmp_path):
    from romanfeed.sources.base import ImageAsset
    gal = ImageAsset(asset_id="esa:g", title="Spiral galaxy NGC 1", url="u", source="ESA", description="", keywords=[])
    neb = ImageAsset(asset_id="esa:n", title="Orion Nebula", url="u", source="ESA", description="", keywords=[])
    for a in (gal, neb):
        a.local_path = tmp_path / a.asset_id
    monkeypatch.setattr(shorts, "looks_unsuitable", lambda a, kind: False)
    monkeypatch.setattr(ImageAsset, "download", lambda self, d: None)
    monkeypatch.setattr(shorts, "flat_black_fraction", lambda a: 0.0)
    monkeypatch.setattr(shorts, "image_score", lambda p: 0.55 if "n" in p.name else 0.50)
    assert shorts.ranked_candidates([gal, neb], tmp_path)[0] is neb
    assert shorts.ranked_candidates([gal, neb], tmp_path, {"galaxy": 0.1})[0] is gal


def test_plain_title_runs_in_fours_inside_its_window():
    from datetime import datetime, timezone

    from romanfeed.shorts import plain_title_due
    s = ShortsSettings(plain_title_from="2026-11-16", plain_title_until="2026-11-22")
    inside = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    assert [plain_title_due(s, n, inside) for n in range(16)] == ([False] * 4 + [True] * 4) * 2
    assert not plain_title_due(s, 5, datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
    assert not plain_title_due(s, 5, datetime(2026, 11, 15, 12, tzinfo=timezone.utc))
    assert not plain_title_due(ShortsSettings(), 5, inside)


def test_plain_title_drops_the_tagline_but_question_titles_stay():
    a = _asset(1, title="Cosmic Cliffs (NIRCam Image)", source="ESA/Webb", description="Star birth in Carina.")
    md = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "VID8h", 28800, ""), [], plain_title=True)
    assert md.title == "Cosmic Cliffs by Webb"
    assert shorts.PLAIN_TITLE_TAG in md.tags
    bare = shorts.short_metadata(_cfg(), _asset(2, title="Faint Smudge"), shorts.Parent("p", "V", 3600, ""), [],
                                 plain_title=True)
    assert bare.title == "Faint Smudge"
    why = shorts.why_mod.Why("Why is it red?", "Dust.", "NASA", "https://example/x")
    asked = shorts.short_metadata(_cfg(), a, shorts.Parent("p", "V", 3600, ""), [], why=why, plain_title=True)
    assert asked.title.startswith("Why is it red?") and shorts.PLAIN_TITLE_TAG not in asked.tags


def test_object_key_matches_two_pictures_of_one_object():
    k = shorts.object_key
    assert k(_asset(1, title="Horsehead Nebula by Webb")) == k(_asset(2, title="The Horsehead Nebula"))
    assert k(_asset(3, title="Hubble's view of the Crab Nebula")) == k(_asset(4, title="Crab Nebula"))
    assert k(_asset(5, title="Horsehead Nebula")) != k(_asset(6, title="Orion Nebula"))


def test_an_object_posted_recently_waits(ledger, monkeypatch, tmp_path):
    led_path = ledger.db_path
    ledger.record_video_assets("chan-2026-09-30", [_asset(i, title="Horsehead Nebula" if i < 15 else f"Object {i}")
                                                    for i in range(10, 20)])
    ledger.record_video_assets("chan-2026-09-28", [_asset(i, title="Horsehead Nebula") for i in range(0, 10)])
    ledger.record_short("chan", "nasa:A0", "chan-2026-09-28", "S0")
    assert shorts.recent_short_objects(ledger, "chan", 30) == {"horseheadnebula"}
    assert shorts.recent_short_objects(ledger, "chan", 0) == set()
    ledger.close()
    _quiet_run(monkeypatch)
    res = shorts.run_shorts(_cfg(enabled=True, per_day=8, repeat_days=30), count=1, data_dir=led_path.parent,
                            output_dir=tmp_path / "out", public=lambda v: True)
    assert [r.asset_id for r in res] and all(int(r.asset_id[6:]) >= 15 for r in res)


def test_a_short_with_the_end_card_ends_on_it(tmp_path):
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
