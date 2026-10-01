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


@dataclass
class PublishSettings:
    mode: str = "dry-run"  # dry-run | upload
    privacy: str = "private"  # private | unlisted | public
    category_id: str = "28"  # Science & Technology
    title_template: str = "{lead} | {subject} | {length} Relaxing Space Video for Sleep | Telescope Screensaver"
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


@dataclass
class ChannelConfig:
    channel: ChannelInfo
    video: VideoSettings
    sources: list[SourceSettings]
    audio: AudioSettings
    publish: PublishSettings
    live: LiveSettings = field(default_factory=LiveSettings)
    path: Path | None = None

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
    )
    if cfg.publish.mode not in {"dry-run", "upload"}:
        raise ValueError("publish.mode must be 'dry-run' or 'upload'")
    if cfg.publish.privacy not in {"private", "unlisted", "public"}:
        raise ValueError("publish.privacy must be private, unlisted or public")
    _check_live(cfg.live)
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
