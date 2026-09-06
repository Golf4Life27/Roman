# Handoff to Cowork: generate and register the music library

Owner: Alex. Repo: github.com/Golf4Life27/Roman (branch `main`).
Goal: 10 licensed ambient tracks registered in `assets/music/manifest.yaml`,
committed, so the nightly Space Screens upload can go live.

## 0. One-time local setup

```bash
git clone https://github.com/Golf4Life27/Roman.git && cd Roman
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                                              # expect all passing
```

ffmpeg is bundled through `imageio-ffmpeg`; nothing else to install.

## 1. Suno (Pro plan, already purchased 2026-09-06)

Settings, identical for every track:

- **Custom** mode ON, **Instrumental** ON, Lyrics empty.
- Model: newest available (v4.5 or v5). Duration: the longest offered, 6–8 min.
  If a track comes out short, **Extend** from the end, then "Get Whole Song".
- **Exclude styles:** `drums, percussion, beat, vocals, singing, lyrics, guitar, lead melody, fast, upbeat, EDM`
- Each generation yields two takes. Keep the one with no sudden swells and no
  bright high frequencies. Sleep audio should be uneventful.
- Download **WAV** where offered, else MP3. Save to `assets/music/masters/`
  (git-ignored).

Prompts (Style field) and titles:

| # | Title | Style prompt |
|---|---|---|
| 1 | Drift | slow ambient drone, warm analog synth pads, very gradual evolution, no melody, no percussion, deep space atmosphere, extremely calm, long reverb tails, C major, 50 bpm feel |
| 2 | Nebula Light | ethereal shimmering ambient pads, glassy soft textures, slow swells that rise and fall over 30 seconds, celestial, sparse, no drums, gentle and weightless |
| 3 | Low Orbit | deep dark ambient, sub bass drone, slow filter sweeps, cinematic space, minimal harmonic movement, no rhythm, meditative, very low and warm |
| 4 | Starfield | soft granular ambient textures, high airy pads, distant faint bell tones far in the background, very quiet, meditative, no beat, slowly drifting |
| 5 | Cold Dawn | Brian Eno style ambient, sparse soft piano notes drowned in reverb, huge space between notes, minimal, spacious, no percussion, calm and cold |
| 6 | Long Exposure | tape saturated ambient loops, warm hiss, slowly shifting major seventh chords, lo-fi ambient, no beats, nostalgic and sleepy |
| 7 | Ion Wind | airy breathy synth pads, soft white noise swells like slow wind, floating, no melody, no drums, continuous and hypnotic |
| 8 | Deep Field | choir-like synth pads, sacred ambient, chord changes every 20 seconds, cathedral reverb, slow and reverent, no percussion, no lead |
| 9 | Sleeping Giant | very slow dark ambient drone, subtle harmonic movement, hypnotic, low frequencies, barely changing, no rhythm, for deep sleep |
| 10 | Signal | gentle slow pulsing ambient pad without drums, soft binaural style textures, healing frequencies feel, warm, minimal, extremely relaxing |

Licence: Suno Pro assigns commercial rights to output generated while
subscribed, provided it is downloaded. Save the subscription receipt as
`secrets/licences/suno-pro-2026-09.pdf` (git-ignored) and note the account
email in each manifest entry.

## 2. Register each track

```bash
python -m romanfeed music add assets/music/masters/Drift.wav \
  --licence generated --genre ambient --title "Drift" --artist "Space Screens" \
  --notes "Suno Pro, generated 2026-09-06, account <suno login email>, receipt secrets/licences/suno-pro-2026-09.pdf"
```

Repeat for all ten. This transcodes to AAC, normalises to -18 LUFS, writes
`assets/music/ambient/<id>.m4a`, and appends the manifest entry. Then:

```bash
python -m romanfeed music list     # every row should read "ok"
```

## 3. Prove it with real music (private)

```bash
python -m romanfeed run config/channels/deep-space-ambient.yaml --images 8 --seconds 8 --private-test
```

Needs `secrets/youtube.token.json` locally (same JSON as the GitHub secret
`YOUTUBE_TOKEN_JSON`; copy it from the secret owner). A private [TEST]
video with the real soundtrack appears in Studio. Listen for loop seams
and level jumps. Fix by re-registering a better take.

## 4. Commit and push

```bash
git checkout -b music/suno-batch-1
git add assets/music/manifest.yaml assets/music/ambient/*.m4a
git commit -m "Add first licensed ambient batch (Suno Pro)"
git push -u origin music/suno-batch-1
```

Open a PR to `main` and merge once CI is green. Masters and receipts must
NOT be committed (they are git-ignored; check `git status` before commit).

## 5. Go live

1. In `config/channels/deep-space-ambient.yaml` set `publish.mode: upload`
   (leave `privacy: private` until the API audit clears). Commit, PR, merge.
2. GitHub → repo → Settings → Secrets and variables → Actions → **Variables**
   → New repository variable `ROMANFEED_ENABLED` = `true`.
3. The nightly job runs at 03:00 UTC. First morning: check Studio, watch the
   video, set it Public by hand. Repeat daily until the API audit clears, then
   set `publish.privacy: public`.

## Reference

- `docs/MONETIZATION_GUARDRAILS.md` — why placeholder audio is refused and
  why Epidemic/Artlist are not options.
- `docs/YOUTUBE_API_SETUP.md` — token, audit, troubleshooting.
- `assets/music/README.md` — the intake command and licence values.
