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
