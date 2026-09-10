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
    title_template: str = "{subject} | {length} Relaxing Space Video for Sleep | Real Telescope Screensaver"
    description_template: str = ""
    tags: list[str] = field(default_factory=list)
    made_for_kids: bool = False


@dataclass
class ChannelConfig:
    channel: ChannelInfo
    video: VideoSettings
    sources: list[SourceSettings]
    audio: AudioSettings
    publish: PublishSettings
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
        path=path,
    )
    if cfg.publish.mode not in {"dry-run", "upload"}:
        raise ValueError("publish.mode must be 'dry-run' or 'upload'")
    if cfg.publish.privacy not in {"private", "unlisted", "public"}:
        raise ValueError("publish.privacy must be private, unlisted or public")
    return cfg
