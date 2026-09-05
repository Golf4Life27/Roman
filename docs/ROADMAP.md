# Roadmap

Ordered by what unlocks the most downstream value. Each item is small enough
for one session.

## Now (before first public upload)

1. ~~Pick the channel name and register handles~~ — **done 2026-09-05: Space
   Screens, @SpaceScreens (live ~2026-09-19).** Still open: register
   @SpaceSleepScreen and @SpaceScreensavers as blockers.
2. **Music strategy decision** — commission vs paid AI generation. Then
   register 6–10 ambient tracks with `romanfeed music add ... --licence ...`.
   This unblocks `publish.mode: upload`.
3. **Google Cloud project + OAuth** — follow YOUTUBE_API_SETUP.md
   (`romanfeed auth` mints the token; store it as `YOUTUBE_TOKEN_JSON`).
   File the API audit form the same day.
4. **Thumbnail generator** — full-bleed image + big duration badge
   ("8 HOURS"), no faces. Pillow, same module family as captions.
5. ~~Channel branding~~ — **done 2026-09-05**: `branding/banner.jpg`,
   `branding/avatar.png` (`scripts/make_branding.py`), paste-ready text in
   STUDIO_PASTE.md. Still open: watermark and trailer once a video exists.

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
