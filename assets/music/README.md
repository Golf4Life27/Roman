# Music

Register tracks with:

```bash
python -m romanfeed music add ~/Downloads/Drift.wav --licence generated \
    --title "Drift" --artist "Space Screens" --notes "Suno Pro, generated 2026-09-06"
```

That transcodes to AAC 192k, normalises loudness to -18 LUFS (EBU R128,
two-pass), writes `<genre>/<id>.m4a`, and appends the manifest entry. The
`.m4a` files are committed so the CI runner has them; raw masters (WAV, MP3)
are git-ignored. The pipeline refuses to upload a video whose soundtrack has
any track without a publishable licence, so this folder is the single
control point for Content ID risk.

Target: 6-10 tracks per genre, 5-8 minutes each, no drums, no vocals.
Keep the Suno Pro subscription receipt; the licence is tied to it.

## Composed music (no manifest needed)

`romanfeed/audio/composer.py` writes original slow ambient pieces in code:
a seeded key and mode, a slow voice-led chord progression, detuned
wavetable pads, a tonic drone, sparse pentatonic bells, a swelling "air"
layer and a long synthetic reverb, loudness-normalised to -18 LUFS. Same
seed, same piece; different seed, different piece. Every piece is
licence `owned` and never touches this folder.

```bash
python -m romanfeed compose ~/Desktop/preview.m4a --seconds 180 --seed test-1
```

To soundtrack a channel with it instead of this library, set in the
channel YAML:

```yaml
audio:
  source: composed          # default: library
  composed_pieces: 8        # distinct pieces per run (seed = run seed + index)
  composed_seconds: 450     # length of each; 8 x 450 s = 60 min before looping
```

Composing costs about 3 s of CPU per minute of audio; the pieces of a run
are composed in parallel (an hour takes about a minute on a 4-vCPU runner).
