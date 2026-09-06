from pathlib import Path

import pytest
from PIL import Image

from romanfeed.sources.base import ImageAsset


@pytest.fixture
def sample_asset(tmp_path: Path) -> ImageAsset:
    p = tmp_path / "neb.png"
    im = Image.new("RGB", (2400, 1350))
    px = im.load()
    for x in range(0, 2400, 8):
        for y in range(0, 1350, 8):
            px[x, y] = (x % 255, y % 255, 120)
    im.save(p)
    return ImageAsset(
        asset_id="nasa:TEST1", title="Test Nebula", url="https://example/x.png",
        source="NASA Image Library", credit="NASA/ESA", description="A test nebula " * 30,
        date="2024-01-01", keywords=["nebula"], local_path=p,
    )
