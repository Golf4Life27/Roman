"""Channel configuration.

A channel is fully described by one YAML file under config/channels/. Every
knob that affects the daily output (pacing, sources, music genre, publish
behaviour) lives here so that spinning up a second channel (a different music
genre, a different pacing) is a config change, not a code change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ChannelInfo:
    slug: str
    name: str
    tagline: str = ""
    handle: str = ""  # e.g. "@SpaceScreens"; used in on-screen prompts and links
    channel_id: str = ""  # UC...; the public upload feed is read from it


@dataclass
class VideoSettings:
    width: int = 1920
    height: int = 1080
    fps: int = 24
    seconds_per_image: float = 45.0
    images_per_video: int = 80
    transition_seconds: float = 2.0
    captions: bool = True
    max_zoom: float = 1.18
    max_flat_black: float = 0.02
    # Extra cuts of the same asset set, in hours. Empty = just the base video.
    extra_lengths_hours: list[float] = field(default_factory=list)
    crf: int = 20
    preset: str = "veryfast"
    # Opening of every long video: an intro card naming the object and the
    # telescope for `intro_seconds`, then the channel's subscribe prompt between
    # subscribe_from and subscribe_until (seconds). 0 turns either off.
    intro_seconds: float = 12.0
    subscribe_from: float = 14.0
    subscribe_until: float = 40.0
    subscribe_line: str = "Space to fall asleep to, every week"
    # Sleep cuts (the extra lengths) fade to black after this many minutes and
    # the music plays on, so the screen does not keep a sleeper awake. 0 = off.
    sleep_dark_after_minutes: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return self.seconds_per_image * self.images_per_video


@dataclass
class SourceSettings:
    type: str
    enabled: bool = True
    queries: list[str] = field(default_factory=list)
    min_width: int = 1600
    max_per_query: int = 100
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AudioSettings:
    genre: str = "ambient"
    library: str = "assets/music/manifest.yaml"
    fade_seconds: float = 6.0
    crossfade_seconds: float = 0.0
    gain_db: float = -6.0
    allow_placeholder: bool = False
    # "library": tracks from the licensed manifest above. "composed": original
    # pieces written per run by romanfeed.audio.composer (licence "owned").
    source: str = "library"
    composed_pieces: int = 8
    composed_seconds: float = 450.0


@dataclass
class PublishSettings:
    mode: str = "dry-run"  # dry-run | upload
    privacy: str = "private"  # private | unlisted | public
    category_id: str = "28"  # Science & Technology
    title_template: str = "{lead} | {subject} | {length} Relaxing Space Video for Sleep | Telescope Screensaver"
    # Title for the extra-length sleep cuts; blank = title_template.
    sleep_title_template: str = ""
    description_template: str = ""
    tags: list[str] = field(default_factory=list)
    made_for_kids: bool = False


# Absolute ceiling on one night's stream. YouTube only auto-archives a live
# stream as a VOD when it runs under 12 hours; past that the recording is
# lost. 11.5 h leaves half an hour of slack for a slow start or a late stop,
# and every cap in romanfeed.live is derived from this one number.
LIVE_MAX_HOURS = 11.5


@dataclass
class LiveSettings:
    """The nightly live stream (romanfeed.live). Off unless the owner turns it on.

    Switched on only once the channel has an audience to stream to (100
    subscribers or 15 watch hours/day); until then a live stream nobody joins
    is just a second, worse copy of the uploads. The channel YAML has no
    `live:` block, so these defaults -- enabled false -- are what runs; adding
    `live: {enabled: true}` there is the owner's switch.
    """
    enabled: bool = False
    hours: float = 10.0
    # Documentation of the schedule, and the night the broadcast is dated by.
    # The systemd timer (deploy/live/romanfeed-live.timer) is what actually
    # fires at this time; keep the two in step.
    start_local: str = "21:00"
    timezone: str = "America/Chicago"
    library_dir: str = "data/live"
    keep_files: int = 12
    # Stop pulling new renders when the disk would drop below this much free.
    min_free_gb: float = 5.0
    # Where the daily workflow's render-<run_id> artifacts live.
    artifact_repo: str = "Golf4Life27/Roman"
    # Cozy scene nights (romanfeed/live/scene.py): "off", "alternate" (every
    # other night, plus every theme's peak night) or "always".
    scenes: str = "off"
    # Length of the composed piece a scene night loops under the scene.
    music_minutes: int = 120


@dataclass
class ShortsSettings:
    """Vertical Shorts cut from images the long videos already showed.

    `enabled` is the publish switch: off, the shorts command renders and
    writes metadata but uploads nothing. It is the owner's call to turn on."""

    enabled: bool = False
    per_day: int = 3
    seconds: float = 35.0
    width: int = 1080
    height: int = 1920
    fps: int = 30
    privacy: str = "public"
    title_template: str = "{lead_by} | Calm Space for Sleep"
    # How many of a parent video's images get scored before the best is cut.
    candidates: int = 6
    # Spoken "what you're looking at" from the image's own archive caption
    # (romanfeed/narration.py). Needs GOOGLE_TTS_API_KEY; without it, music only.
    narration: bool = True
    voice: str = "en-US-Chirp3-HD-Charon"


@dataclass
class CozySettings:
    """Cozy animated scenes (romanfeed/cozy): the library, the monthly
    generator's budget, and the weekly cozy upload.

    `enabled` is the weekly upload's publish switch, the owner's call: off,
    the job renders the video and its metadata as an artifact and uploads
    nothing. `monthly_credits` caps Runway API spend (1 credit = $0.01);
    0 leaves the generator off."""
    repo: str = "Golf4Life27/Roman"
    release_tag: str = "cozy-scenes"
    enabled: bool = False
    privacy: str = "public"
    hours: float = 3.0
    title_template: str = "Cozy {scene} · {length} Relaxing Space Music for Sleep · {extra}"
    monthly_credits: int = 0
    # Scenes to keep per theme for the weeks ahead; the generator tops up
    # the soonest themes below this first.
    per_theme: int = 2
    lookahead_days: int = 45
    image_model: str = "gemini_image3_pro"
    video_model: str = "seedance2"


@dataclass
class CrosspostSettings:
    """Each Short's social cut, posted to TikTok and Instagram through Zernio
    (romanfeed/crosspost.py). The relay is a Make scenario that holds the
    Zernio key; its webhook URL is the CROSSPOST_RELAY_URL secret.

    mode is the owner's switch:
      off   social cuts are rendered (as artifacts, to watch) and nothing posts
      test  TikTok only, as a draft in the TikTok app's Creator Inbox
            (private until posted there); Instagram is skipped
      on    both platforms, public, at the next slot"""
    mode: str = "off"
    tiktok_account: str = ""      # Zernio account ids (GET /accounts), not secrets
    instagram_account: str = ""
    # Posting times, local to `timezone`. Each Short is scheduled for the
    # first slot at least `lead_minutes` after it is rendered; the Shorts
    # workflow runs ~50 minutes before each slot (.github/workflows/shorts.yml).
    slots: list[str] = field(default_factory=lambda: ["12:00", "19:00", "21:30", "23:30"])
    timezone: str = "America/Chicago"
    lead_minutes: int = 5
    made_with_ai: bool = True     # TikTok's AI disclosure: the voice is synthetic
    # Instagram Trial Reels: shown to non-followers first. Off by default;
    # worth a test once the account has some posts.
    instagram_trial: bool = False
    release_tag: str = "social-clips"
    keep_days: int = 30           # clips are deleted from the release after this
    hashtags: list[str] = field(default_factory=lambda: ["#space", "#sleepmusic", "#ambient", "#relaxing"])
    # The weekly cozy clip (romanfeed/cozy/clip.py, cozy-clip.yml): the
    # season's cozy scene, posted on its own slot so it never doubles up with
    # a Short. Posting follows `mode` like everything above.
    cozy_slots: list[str] = field(default_factory=lambda: ["20:15"])
    cozy_seconds: int = 20        # two passes of the 10 s loop, so it replays seamlessly
    cozy_hashtags: list[str] = field(default_factory=lambda: ["#cozy", "#ambience", "#sleep", "#cozyvibes"])


@dataclass
class ChannelConfig:
    channel: ChannelInfo
    video: VideoSettings
    sources: list[SourceSettings]
    audio: AudioSettings
    publish: PublishSettings
    live: LiveSettings = field(default_factory=LiveSettings)
    path: Path | None = None
    shorts: ShortsSettings = field(default_factory=ShortsSettings)
    cozy: CozySettings = field(default_factory=CozySettings)
    crosspost: CrosspostSettings = field(default_factory=CrosspostSettings)

    @property
    def enabled_sources(self) -> list[SourceSettings]:
        return [s for s in self.sources if s.enabled]


def _build(cls, data: dict[str, Any] | None):
    data = dict(data or {})
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown keys for {cls.__name__}: {sorted(unknown)}")
    return cls(**data)


def load_config(path: str | Path) -> ChannelConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    sources = [_build(SourceSettings, s) for s in raw.get("sources", [])]
    cfg = ChannelConfig(
        channel=_build(ChannelInfo, raw.get("channel")),
        video=_build(VideoSettings, raw.get("video")),
        sources=sources,
        audio=_build(AudioSettings, raw.get("audio")),
        publish=_build(PublishSettings, raw.get("publish")),
        live=_build(LiveSettings, raw.get("live")),
        path=path,
        shorts=_build(ShortsSettings, raw.get("shorts")),
        cozy=_build(CozySettings, raw.get("cozy")),
        crosspost=_build(CrosspostSettings, raw.get("crosspost")),
    )
    if cfg.publish.mode not in {"dry-run", "upload"}:
        raise ValueError("publish.mode must be 'dry-run' or 'upload'")
    if cfg.publish.privacy not in {"private", "unlisted", "public"}:
        raise ValueError("publish.privacy must be private, unlisted or public")
    if cfg.shorts.privacy not in {"private", "unlisted", "public"}:
        raise ValueError("shorts.privacy must be private, unlisted or public")
    if not 15 <= cfg.shorts.seconds <= 60:
        raise ValueError("shorts.seconds must be between 15 and 60")
    if cfg.audio.source not in {"library", "composed"}:
        raise ValueError("audio.source must be 'library' or 'composed'")
    _check_live(cfg.live)
    if cfg.live.scenes not in {"off", "alternate", "always"}:
        raise ValueError("live.scenes must be off, alternate or always")
    if cfg.cozy.privacy not in {"private", "unlisted", "public"}:
        raise ValueError("cozy.privacy must be private, unlisted or public")
    if cfg.crosspost.mode not in {"off", "test", "on"}:
        raise ValueError("crosspost.mode must be off, test or on")
    for t in cfg.crosspost.slots:
        hh, _, mm = t.partition(":")
        if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
            raise ValueError(f"crosspost.slots: {t!r} is not HH:MM")
    if not 0.5 <= cfg.cozy.hours <= 11.5:
        raise ValueError("cozy.hours must be between 0.5 and 11.5")
    return cfg


def _check_live(live: LiveSettings) -> None:
    """Fail at load time, not at 9 PM, on a live block that cannot work.

    hours above the ceiling is rejected rather than quietly clamped: someone
    who wrote `hours: 24` meant something this code will never do, and should
    hear so. (The runtime clamps anyway, as one more layer.)"""
    from zoneinfo import ZoneInfo

    if not 0 < live.hours <= LIVE_MAX_HOURS:
        raise ValueError(f"live.hours must be > 0 and <= {LIVE_MAX_HOURS} (YouTube archives streams under 12 h only)")
    try:
        hh, mm = (int(x) for x in live.start_local.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except ValueError:
        raise ValueError("live.start_local must be HH:MM") from None
    try:
        ZoneInfo(live.timezone)
    except Exception:
        raise ValueError(f"live.timezone {live.timezone!r} is not an IANA time zone") from None
    if live.keep_files < 1:
        raise ValueError("live.keep_files must be at least 1")
