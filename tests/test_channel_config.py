"""The live channel config must always load: a broken YAML stops every job."""
from pathlib import Path

from romanfeed.config import load_config


def test_every_channel_config_loads():
    for path in Path("config/channels").glob("*.yaml"):
        cfg = load_config(path)
        assert cfg.channel.slug and cfg.channel.name
