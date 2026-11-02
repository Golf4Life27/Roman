"""The ocean channel: its photo source, filter, titles, music and upload access."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from romanfeed.audio.library import Track
from romanfeed.config import SourceSettings, load_config
from romanfeed.curation.selector import looks_unsuitable
from romanfeed.publish import channel_token as ct
from romanfeed.publish.metadata import build_metadata, pick_ocean_subject
from romanfeed.sources.base import ImageAsset
from romanfeed.sources.commons import WikimediaCommons, credit_line, licence_class, title_from_file

ROOT = Path(__file__).resolve().parents[1]


def _page(title="File:Green sea turtle (Chelonia mydas) at Kona.jpg", licence="Public domain", w=4000, h=2667,
          artist="<a href='x'>NOAA Photo Library</a>"):
    return {"pageid": 7, "title": title, "imageinfo": [{
        "mime": "image/jpeg", "width": w, "height": h, "url": "https://upload/full.jpg",
        "thumburl": "https://upload/3840px.jpg", "thumbwidth": 3840,
        "extmetadata": {"LicenseShortName": {"value": licence}, "Artist": {"value": artist},
                        "Categories": {"value": "Chelonia mydas|Sea turtles of Hawaii"}},
    }]}


def _source():
    return WikimediaCommons(SourceSettings(type="commons", min_width=1920, options={"subject": "ocean"}))


@pytest.mark.parametrize("short,klass", [
    ("Public domain", "pd"), ("CC0", "pd"), ("PD-USGov-NOAA", "pd"),
    ("CC BY 4.0", "by"), ("CC BY 2.0", "by"),
    ("CC BY-SA 4.0", "by-sa"), ("CC BY-NC 2.0", "nc-nd"), ("CC BY-ND 3.0", "nc-nd"), ("GFDL", "other"),
])
def test_licence_class(short, klass):
    assert licence_class(short) == klass


def test_only_free_landscape_photos_are_kept():
    src = _source()
    a = src.to_asset(_page(), "sea turtle")
    assert a and a.asset_id == "commons:7" and a.url == "https://upload/3840px.jpg"
    assert a.title == "Green sea turtle (Chelonia mydas) at Kona"
    assert a.credit == "NOAA Photo Library (public domain), via Wikimedia Commons"
    assert a.keywords[:2] == ["ocean", "sea turtle"]
    assert src.to_asset(_page(licence="CC BY-SA 4.0"), "q") is None   # share-alike would bind the video
    assert src.to_asset(_page(w=1080, h=1920), "q") is None          # portrait
    assert src.to_asset(_page(w=1600, h=900), "q") is None           # too small for 1080p


def test_names_and_credits():
    assert title_from_file("File:DSC_0042 Manta ray, Kona.JPG") == "Manta ray, Kona"
    assert credit_line("Jane Diver", "CC BY 4.0", "by") == "Jane Diver, CC BY 4.0, via Wikimedia Commons"


def _ocean(title, query="sea turtle", cats=()):
    return ImageAsset(asset_id=f"commons:{abs(hash(title))}", title=title, url="", source="Wikimedia Commons",
                      credit="x", keywords=["ocean", query, *cats])


def test_ocean_filter():
    assert not looks_unsuitable(_ocean("Chelonia mydas, Kona"), "ocean")              # Latin name, sea-life search
    assert looks_unsuitable(_ocean("Fishing boat in the harbor"), "ocean")
    assert looks_unsuitable(_ocean("Scientists on deck", "NOAA Okeanos Explorer"), "ocean")
    assert looks_unsuitable(_ocean("Expedition 2017", "NOAA Okeanos Explorer"), "ocean")   # no sea word anywhere
    assert not looks_unsuitable(_ocean("Coral garden at 40 m", "NOAA Okeanos Explorer"), "ocean")


def test_ocean_title_and_description():
    cfg = load_config(ROOT / "config/channels/ocean-ambient.yaml")
    assets = [_ocean("Green sea turtle (Chelonia mydas) at Kona")] + [_ocean(f"Humpback whale {i}", "humpback whale") for i in range(2)] \
        + [_ocean(f"Sea turtle {i}") for i in range(57)]
    track = Track("t", Path("t.m4a"), "ambient", "owned", title="Still Shallows", artist="Deep Blue Screens")
    meta = build_metadata(cfg, assets, [track], seconds_per_image=60, when=date(2026, 10, 29))
    assert meta.title.startswith("Green sea turtle at Kona | Sea Turtles | 1 Hour Relaxing Ocean Music for Sleep")
    assert len(meta.title) <= 100
    assert "ABOUT DEEP BLUE SCREENS" in meta.description and "@DeepBlueScreens" in meta.description
    assert "Hubble" not in meta.description and "telescope" not in meta.description.lower()
    assert meta.category_id == "10" and meta.made_for_kids is False


def test_ocean_subject_tie_goes_to_the_lead():
    assets = [_ocean("Green sea turtle"), _ocean("Humpback whale", "humpback whale")]
    assert pick_ocean_subject(assets) == "Sea Turtles"
    assert pick_ocean_subject([_ocean("Crystal jelly, Monterey", "comb jelly")]) == "Jellyfish"


def test_ocean_music_is_its_own_style():
    from romanfeed.audio.composer import render_pcm

    ocean = render_pcm(20, "s", 8000, style="ocean")
    space = render_pcm(20, "s", 8000, style="space")
    assert ocean.shape == space.shape and np.isfinite(ocean).all()
    assert not np.allclose(ocean, space)
    assert 0 < np.abs(ocean).max() <= 1.0


def test_supply_for_a_one_subject_channel(tmp_path):
    from romanfeed.state import Ledger
    from romanfeed.supply import single_subject_lines

    cfg = load_config(ROOT / "config/channels/ocean-ambient.yaml")
    cands = [_ocean(f"Reef {i}", "coral reef") for i in range(130)] + [_ocean("Boat at the pier")]
    with Ledger(tmp_path / "state.db") as ledger:
        lines = single_subject_lines(cfg, cands, ledger, date(2026, 10, 29))
    assert lines[0].startswith("Deep Blue Screens: 130 ocean photos never shown, of 130 suitable (1 turned away")
    assert "Daily long videos covered: 2 (through Oct 30)" in lines[1]


def _main_token(tmp_path):
    p = tmp_path / "main.json"
    p.write_text(json.dumps({"client_id": "cid.apps.googleusercontent.com", "client_secret": "s3cret",
                             "refresh_token": "r", "token_uri": ct.TOKEN_URL}))
    return p


def test_consent_link_and_code(tmp_path):
    url = ct.consent_url(_main_token(tmp_path), "ocean-ambient", ["https://www.googleapis.com/auth/youtube.upload"])
    assert url.startswith(ct.AUTH_URL) and "client_id=cid.apps" in url and "s3cret" not in url
    assert "code_challenge_method=S256" in url and "state=ocean-ambient" in url
    assert ct.code_from("http://localhost:8765/?state=ocean-ambient&code=4/abc&scope=x") == "4/abc"
    assert ct.code_from(" 4/abc ") == "4/abc"
    with pytest.raises(SystemExit):
        ct.code_from("http://localhost:8765/?error=access_denied&code=")


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")
def test_token_round_trip_needs_the_client_secret(tmp_path):
    main = _main_token(tmp_path)
    token = {"refresh_token": "abc", "client_id": "cid", "client_secret": "s3cret"}
    blob = ct.encrypt(token, main, "ocean-ambient")
    assert b"abc" not in blob
    assert ct.decrypt(blob, main, "ocean-ambient") == token
    with pytest.raises(SystemExit):
        ct.decrypt(blob, main, "other-channel")      # another channel's key does not open it


def test_upload_to_the_wrong_channel_stops(monkeypatch, tmp_path):
    from romanfeed.publish import youtube

    class Req:
        def next_chunk(self):
            return None, {"id": "vid1", "snippet": {"channelId": "UCspace"}}

    class Videos:
        def insert(self, **kw):
            return Req()

    monkeypatch.setattr(youtube, "client", lambda: type("Y", (), {"videos": lambda self: Videos()})())
    import sys
    import types

    fake = types.ModuleType("googleapiclient.http")
    fake.MediaFileUpload = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "googleapiclient", types.ModuleType("googleapiclient"))
    monkeypatch.setitem(sys.modules, "googleapiclient.http", fake)
    monkeypatch.setenv("ROMANFEED_EXPECT_CHANNEL", "UCocean")
    with pytest.raises(SystemExit, match="expected UCocean"):
        youtube._upload(tmp_path / "v.mp4", {})


def test_sibling_report_lines(tmp_path):
    from datetime import datetime, timezone

    from romanfeed.publish.stats import sibling_lines
    from romanfeed.state import Ledger, VideoRecord

    cfg = load_config(ROOT / "config/channels/ocean-ambient.yaml")

    class Pub:
        def channels(self):
            return self

        def list(self, **kw):
            assert kw["forHandle"] == "@DeepBlueScreens"
            return self

        def execute(self):
            return {"items": [{"statistics": {"subscriberCount": "12", "viewCount": "3400", "videoCount": "7"}}]}

    with Ledger(tmp_path / "state.db") as ledger:
        ledger.record_video(VideoRecord(video_slug="ocean-ambient-2026-10-28", channel="ocean-ambient", path="x",
                                        duration_s=3600, rendered_at="2026-10-28T05:00:00+00:00",
                                        youtube_id="v1", published_at=None, title="t"))
        lines = sibling_lines(cfg, ledger=ledger, today=date(2026, 11, 2), yt_public=Pub())
    assert lines == ["Deep Blue Screens (@DeepBlueScreens)", "  Subscribers 12 · views 3,400 · videos 7",
                     "  Uploaded in the last 7 days: 1"]


def test_code_sign_in_waits_then_returns_the_token(tmp_path, monkeypatch):
    client = tmp_path / "tv.json"
    client.write_text(json.dumps({"installed": {"client_id": "tv-id", "client_secret": "tv-secret"}}))
    replies = iter([{"error": "authorization_pending"}, {"error": "slow_down"},
                    {"access_token": "a", "refresh_token": "r", "scope": "https://www.googleapis.com/auth/youtube"}])
    sent = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def post(url, data, timeout):
        sent.append((url, data))
        return Resp(next(replies))

    monkeypatch.setattr(ct.requests, "post", post)
    waits = []
    tok = ct.device_finish(client, {"device_code": "dc", "interval": 5, "expires_in": 1800},
                           sleep=waits.append, clock=lambda: 0)
    assert tok["refresh_token"] == "r" and tok["client_id"] == "tv-id" and tok["client_secret"] == "tv-secret"
    assert waits == [5, 5, 10]                               # slowed down when Google asked
    assert all(d["device_code"] == "dc" for _, d in sent)


def test_code_sign_in_refusal_stops(tmp_path, monkeypatch):
    client = tmp_path / "tv.json"
    client.write_text(json.dumps({"client_id": "tv-id", "client_secret": "s"}))
    monkeypatch.setattr(ct.requests, "post", lambda *a, **kw: type("R", (), {"json": lambda self: {"error": "access_denied"}})())
    with pytest.raises(SystemExit, match="access_denied"):
        ct.device_finish(client, {"device_code": "dc", "interval": 1}, sleep=lambda s: None, clock=lambda: 0)


def test_wrong_channel_name_is_refused(tmp_path):
    import types

    from romanfeed.cli import _check_and_store

    args = types.SimpleNamespace(expect="", not_channel="UCspace", expect_title="Deep Blue Screens",
                                 out=str(tmp_path / "t.enc"), channel_out=str(tmp_path / "c"), main_token="x", slug="s")
    assert _check_and_store(ct, {}, "UCspace", "Space Screens", args) == 2
    assert _check_and_store(ct, {}, "UCother", "Someone Else", args) == 2
    assert not (tmp_path / "t.enc").exists()


@pytest.mark.parametrize("name,title", [
    ("File:Great Barrier Reef 024.jpg", "Great Barrier Reef"),
    ("File:Tamanend's-Bottlenose-Dolphins-Tursiops-erebennus)-Assateague.jpg",
     "Tamanend's Bottlenose Dolphins Tursiops erebennus Assateague"),
    ("File:Humpback whale (Megaptera novaeangliae.jpg", "Humpback whale Megaptera novaeangliae"),
    ("File:Green sea turtle (Chelonia mydas) at Kona.jpg", "Green sea turtle (Chelonia mydas) at Kona"),
    ("File:Apollo 11 launch.jpg", "Apollo 11 launch"),
    ("File:Phoque gris (Halichoerus grypus) en rade de Brest (Ifremer 00557-66878 - 20145).jpg",
     "Phoque gris (Halichoerus grypus) en rade de Brest"),
    ("File:Episode 9- Underwater Conversations in Glacier Bay.jpg", "Underwater Conversations in Glacier Bay"),
    ("File:Chimney hires.jpg", "Chimney"),
])
def test_titles_lose_numbers_hyphens_and_stray_brackets(name, title):
    assert title_from_file(name) == title


def test_museum_specimens_are_turned_away():
    assert looks_unsuitable(_ocean("Asteroidea (USNM 1450538)", "sea star"), "ocean")
    assert not looks_unsuitable(_ocean("Sea star on the reef", "sea star"), "ocean")
    assert looks_unsuitable(_ocean("Catoosa OK Blue Whale", "blue whale"), "ocean")   # a roadside attraction
    assert looks_unsuitable(_ocean("Dolphin statue at the waterfront", "bottlenose dolphin"), "ocean")


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._data


def test_busy_search_is_retried(monkeypatch):
    src = _source()
    src.retry_waits = (0, 0)
    replies = [_Resp({}, 503), _Resp({"error": {"code": "internal", "info": "Search is currently too busy."}}),
               _Resp({"query": {"pages": [_page()]}})]
    monkeypatch.setattr(src.session, "get", lambda *a, **k: replies.pop(0))
    assert len(src._search("sea turtle")) == 1 and not replies


def test_busy_search_gives_up_after_the_waits(monkeypatch):
    src = _source()
    src.retry_waits = (0,)
    busy = {"error": {"code": "maxlag", "info": "Waiting for a database server"}}
    monkeypatch.setattr(src.session, "get", lambda *a, **k: _Resp(busy))
    assert src._search("sea turtle") == []


def test_cut_short_downloads_are_refused(tmp_path):
    from PIL import Image

    from romanfeed.sources.base import check_image

    good = tmp_path / "good.jpg"
    Image.new("RGB", (64, 48), (10, 60, 90)).save(good, quality=90)
    check_image(good, str(good.stat().st_size))
    with pytest.raises(ValueError):
        check_image(good, str(good.stat().st_size + 40))
    cut = tmp_path / "cut.jpg"
    cut.write_bytes(good.read_bytes()[:-40])
    with pytest.raises(OSError):
        check_image(cut)


def test_title_drops_flickr_site_and_uploader():
    from romanfeed.sources.commons import title_from_file
    assert title_from_file("File:Spinner Dolphins - Flickr - benmacaskill.jpg") == "Spinner Dolphins"
    assert title_from_file("File:Humpback whale (Flickr).jpg") == "Humpback whale"
    assert title_from_file("File:Sea turtle - Flickr - NOAA Photo Library (1).jpg") == "Sea turtle"


def test_named_ships_are_turned_away():
    from romanfeed.curation.selector import ocean_unsuitable
    from romanfeed.sources.base import ImageAsset
    for title in ("Leonardo (A 5301) 13", "Research vessel at sea", "Frigate Carlo Bergamini (F 590)"):
        a = ImageAsset(asset_id="x", title=title, url="u", source="commons", keywords=["ocean", "sea"])
        assert ocean_unsuitable(a), title
    keep = ImageAsset(asset_id="y", title="Rhincodon typus", url="u", source="commons", keywords=["ocean", "whale shark"])
    assert not ocean_unsuitable(keep)
