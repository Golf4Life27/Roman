from romanfeed.state import Ledger, VideoRecord


def test_ledger_roundtrip(tmp_path):
    with Ledger(tmp_path / "s.db") as l:
        assert l.used_asset_ids("c") == set()
        l.mark_assets_used("c", ["a", "b"], "c-2026-09-05")
        assert l.used_asset_ids("c") == {"a", "b"}
        assert l.used_asset_ids("other") == set()
        l.record_video(VideoRecord("c-2026-09-05", "c", "out.mp4", 60.0, "2026-09-05T00:00:00+00:00"))
        l.mark_published("c-2026-09-05", "yt123", "Title")
        v = l.videos("c")[0]
        assert v.youtube_id == "yt123" and v.published_at and v.title == "Title"
        assert l.reset_assets("c") == 2
        assert l.used_asset_ids("c") == set()
