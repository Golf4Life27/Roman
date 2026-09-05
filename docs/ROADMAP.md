# Roadmap

Ordered by what unlocks the most downstream value. Each item is small enough
for one session.

## Now (before first public upload)

1. **Pick the channel name and register handles** — see CHANNEL_SETUP.md.
   Register the sibling handles at the same time.
2. **Music strategy decision** — commission vs paid AI generation. Then fill
   `assets/music/manifest.yaml` with 6–10 ambient tracks. This unblocks
   `publish.mode: upload`.
3. **Google Cloud project + OAuth** — enable YouTube Data API v3, create OAuth
   client, run one local upload to mint `secrets/youtube.token.json`, store it
   as the `YOUTUBE_TOKEN_JSON` GitHub secret. File the API audit form.
4. **Thumbnail generator** — full-bleed image + big duration badge
   ("8 HOURS"), no faces. Pillow, same module family as captions.
5. **Channel branding** — banner (2560×1440, safe area 1546×423), 800×800
   avatar, watermark, description with credit/non-affiliation block.

## Next (first month live)

6. **Multi-length cuts** — render 1 h / 3 h / 8 h from the same asset set
   (concat is cheap once clips exist); sleep cuts fade to black at 30–60 min.
7. **Render performance** — parallel clip rendering, or GPU encode on a
   self-hosted runner; needed for 8 h cuts and for 4K.
8. **Text intro card** — 10-second opening card naming tonight's subjects
   (originality signal + retention hook).
9. **ESA/Webb + ESA/Hubble sources** (CC BY 4.0) for higher-quality press
   images than the NASA library's mixed bag.
10. **Analytics pull-back** — YouTube Analytics API → ledger, so subject mix,
    length and pacing are chosen by retention data, not guesses.

## Later (Roman era, early 2027)

11. **Roman first-light switch** — confirm `science_after`, add STScI/MAST
    press feed, promote Roman assets to headline subject automatically.
12. **Sibling channels** — copy the config: lofi, piano, pure ambient, 4K HDR
    OLED. Same pipeline, same ledger DB, separate channel slugs.
13. **24/7 live stream** — always-on watch hours from the rendered library.
14. **Aesthetic scoring** — vision model ranks candidates so the best frames
    lead each video.
15. **Depth-map parallax / HDR grading** — the "real motion design" lever
    that puts distance between us and template slideshows.
