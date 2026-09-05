# Architecture

## The daily loop

```
sources ──▶ curation ──▶ render ──▶ audio ──▶ publish ──▶ ledger
 (fetch)    (select)    (clips)    (mix)     (meta+api)   (sqlite)
```

`romanfeed/pipeline.py::run` executes the loop once per channel config. Every
stage writes to disk under `data/work/<slug>/` so a failed run is inspectable,
and the ledger is only updated after a successful render so a crash never
"burns" images.

| Stage | Module | Input → Output | Swap-in point |
|---|---|---|---|
| Sources | `sources/` | config → `list[ImageAsset]` | New archive (ESA/Webb CC-BY, STScI RSS, MAST) = new class in `REGISTRY` |
| Curation | `curation/selector.py` | candidates + ledger → chosen assets | Aesthetic scoring model (CLIP/VLM) slots in before `chosen.append` |
| Render | `render/` | assets → clips → silent MP4 | Depth-map parallax, upscaling, 4K/HDR: change `prepare_frame` / `Segment` |
| Audio | `audio/` | manifest → soundtrack | Per-video generated music: write file + manifest entry, same path |
| Publish | `publish/` | assets + tracks → title/desc/tags → upload | Thumbnail generator, A/B titles, scheduled publish |
| Ledger | `state.py` | everything → `data/state.db` | Analytics pull-back (views, retention) to close the loop |

## Design rules

1. **Config is the product.** A channel is one YAML file. A sibling channel
   (different genre, different pacing, 4K) is a copy of that file.
2. **Nothing publishable enters without provenance.** Images carry source,
   credit and licence; music must be in the manifest with a licence. The
   publish step refuses non-publishable audio.
3. **Idempotent days.** Selection is seeded by date; re-running the same day
   produces the same video. Different days differ.
4. **No repeats until the pool cycles.** The ledger tracks used assets per
   channel and auto-resets when the pool is exhausted.
5. **Every step is a plain function over files.** Any stage can be replaced
   by a better model or service without touching the others.

## Render pipeline detail

- Frames are prepared with Pillow at 2× output size (cover-fit; portrait
  images get a blurred, darkened self-backdrop), with a lower-third caption
  (title, one-line description, credit) burned in.
- Each frame becomes one clip via ffmpeg `zoompan` (alternating zoom in/out,
  rotating drift anchors) with fade in/out, so concatenation is a simple
  stream copy and scales to hundreds of clips.
- Audio is concatenated/looped to the video length with long fades and a
  gain cut, then muxed with `-shortest`.

Render cost today: about 30 s wall time per 8 s 1080p clip on a 2-vCPU box
(zoompan is single-threaded). A 60-minute video at 45 s/image is roughly
80 clips × ~2.5 min ≈ 3–4 h on GitHub's hosted runner, inside the 6 h limit but
worth optimising (see ROADMAP).

## Runtime

- Local: `python -m romanfeed run ...`
- Scheduled: `.github/workflows/daily.yml` at 03:00 UTC, ledger persisted via
  actions/cache, video kept 3 days as an artifact. Dry-run until secrets exist.
- Future: a small always-on box or a cloud job if render time or the 4K
  upload sizes outgrow hosted runners.
