from pathlib import Path

import pytest
from PIL import Image

from romanfeed.sources.base import ImageAsset


@pytest.fixture
def sample_asset(tmp_path: Path) -> ImageAsset:
    p = tmp_path / "neb.png"
    # Dense noise, like real sky: no exact-black pixels, so the flat-black
    # check in the selector sees a normal frame rather than a detector gap.
    im = Image.effect_noise((2400, 1350), 40).convert("RGB")
    im.save(p)
    return ImageAsset(
        asset_id="nasa:TEST1", title="Test Nebula", url="https://example/x.png",
        source="NASA Image Library", credit="NASA/ESA", description="A test nebula " * 30,
        date="2024-01-01", keywords=["nebula"], local_path=p,
    )
