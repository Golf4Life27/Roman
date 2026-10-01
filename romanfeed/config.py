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


@dataclass
class ChannelConfig:
    channel: ChannelInfo
    video: VideoSettings
    sources: list[SourceSettings]
    audio: AudioSettings
    publish: PublishSettings
    path: Path | None = None
    shorts: ShortsSettings = field(default_factory=ShortsSettings)

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
        path=path,
        shorts=_build(ShortsSettings, raw.get("shorts")),
    )
    if cfg.publish.mode not in {"dry-run", "upload"}:
        raise ValueError("publish.mode must be 'dry-run' or 'upload'")
    if cfg.publish.privacy not in {"private", "unlisted", "public"}:
        raise ValueError("publish.privacy must be private, unlisted or public")
    if cfg.shorts.privacy not in {"private", "unlisted", "public"}:
        raise ValueError("shorts.privacy must be private, unlisted or public")
    if not 15 <= cfg.shorts.seconds <= 60:
        raise ValueError("shorts.seconds must be between 15 and 60")
    return cfg
