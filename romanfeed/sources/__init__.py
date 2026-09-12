"""Image sources. Each source yields ImageAsset objects with provenance and licence."""
from __future__ import annotations

from romanfeed.config import SourceSettings
from romanfeed.sources.base import ImageAsset, ImageSource
from romanfeed.sources.esa_archive import EsaArchive
from romanfeed.sources.nasa_images import NasaImageLibrary
from romanfeed.sources.roman import RomanTelescopeSource

REGISTRY: dict[str, type[ImageSource]] = {
    "esa_archive": EsaArchive,
    "nasa_images": NasaImageLibrary,
    "roman": RomanTelescopeSource,
}


def build_source(settings: SourceSettings) -> ImageSource:
    try:
        cls = REGISTRY[settings.type]
    except KeyError as e:
        raise ValueError(f"Unknown source type '{settings.type}'. Known: {sorted(REGISTRY)}") from e
    return cls(settings)


__all__ = ["ImageAsset", "ImageSource", "build_source", "REGISTRY"]
