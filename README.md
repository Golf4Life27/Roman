# RomanFeed

A fully automated, long-form YouTube channel pipeline: real space telescope
imagery (Hubble and Webb today, the Nancy Grace Roman Space Telescope once its
first images land) rendered as slow, captioned sleep/focus videos with calm
music, published daily with no hands on the keyboard.

Roman launched on 2026-08-30 and NASA expects first observations by early
2027. The channel warms up on public NASA imagery now so it is monetised
before Roman's images arrive.

## Status

Scaffold. The full loop runs end to end in dry-run mode: fetch → curate →
render → soundtrack → metadata → (upload) → ledger. Nothing uploads until
`publish.mode: upload` is set and licensed music exists.

## Quick start

```bash
pip install -e ".[dev]"            # ffmpeg is bundled via imageio-ffmpeg; system ffmpeg is used if present
pytest -q
python -m romanfeed run config/channels/deep-space-ambient.yaml --images 6 --seconds 8 --dry-run
```

That downloads six NASA images, renders a 48-second 1080p video with Ken Burns
motion, captions and credits, mixes a placeholder drone, and writes the exact
YouTube upload request it *would* have sent next to the MP4 in `output/`.

Other commands:

```bash
python -m romanfeed fetch config/channels/deep-space-ambient.yaml   # list candidate images per source
python -m romanfeed ledger                                          # what has been rendered / published
python -m romanfeed music add track.m4a --licence generated --title Drift --notes "Suno Pro"
python -m romanfeed music list                                      # publishable? file present?
python -m romanfeed auth                                            # one-time YouTube OAuth (see docs/YOUTUBE_API_SETUP.md)
```

## Layout

```
config/channels/*.yaml     one file per channel: pacing, sources, genre, publish rules
romanfeed/sources/         NASA Image Library + Roman poller (add more here)
romanfeed/curation/        selection: no repeats, min resolution, Roman first
romanfeed/render/          Pillow captions + ffmpeg zoompan clips + concat + mux
romanfeed/audio/           licence-enforced music manifest + soundtrack assembly
romanfeed/publish/         metadata templating + YouTube Data API upload (dry-run default)
romanfeed/pipeline.py      the daily job
romanfeed/state.py         SQLite ledger (data/state.db)
assets/music/manifest.yaml the only door music can enter through
.github/workflows/         ci.yml (tests) and daily.yml (scheduled render; gated by ROMANFEED_ENABLED, manual smoke-test)
docs/                      architecture, channel setup research, monetisation guardrails, roadmap
```

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit, and where new AI capabilities plug in
- [docs/CHANNEL_SETUP.md](docs/CHANNEL_SETUP.md) — channel name shortlist and the YouTube Studio settings checklist
- [docs/MONETIZATION_GUARDRAILS.md](docs/MONETIZATION_GUARDRAILS.md) — policy, licensing and credit rules the pipeline enforces
- [docs/YOUTUBE_API_SETUP.md](docs/YOUTUBE_API_SETUP.md) — one-time Google Cloud + OAuth + audit walkthrough
- [docs/STUDIO_PASTE.md](docs/STUDIO_PASTE.md) — paste-ready channel description, keywords and upload defaults
- [docs/HANDOFF_COWORK_MUSIC.md](docs/HANDOFF_COWORK_MUSIC.md) — desktop runbook: generate the Suno library, register it, go live
- [docs/ROADMAP.md](docs/ROADMAP.md) — what to build next, in order
