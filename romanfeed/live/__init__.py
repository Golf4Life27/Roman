"""Nightly ~10-hour live stream built from the channel's rendered library.

Runs on an always-on server, never in GitHub Actions (jobs die at 6 h). Off
until the owner sets `live.enabled` in the channel config. Every run ends
before 12 hours so YouTube keeps the recording as a public VOD; see
docs/LIVE_STREAM.md for the three caps that guarantee it.
"""
