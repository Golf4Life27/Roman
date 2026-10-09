# Cross-posting to TikTok and Instagram

Every Short gets a second cut, the **social cut**, for TikTok @spacescreens
and Instagram @space.screens:

| | YouTube Short | Social cut |
|---|---|---|
| Picture, music, facts | same | same |
| Closing words | "The full 8-hour version is on the channel. Subscribe to …" | "The full 8-hour version is on YouTube, at Space Screens. Follow for more space to fall asleep to." |
| Closing plate | Subscribe … / Full 8-hour sleep video on @SpaceScreens | Full 8-hour version on YouTube / @SpaceScreens |

## How a clip travels

1. The Shorts workflow runs four times a day, ~50 minutes before each slot
   (12:00, 19:00, 21:30, 23:30 Central), and renders both cuts.
2. The social cut is attached to the `social-clips` release on this public
   repo, which gives it a public link. Clips older than 30 days are deleted.
3. GitHub sends the post to the **relay**: the Make scenario "Space Screens:
   post relay (GitHub -> Zernio)", which holds the Zernio key. The relay only
   allows reading the account list, creating posts and reading posts, never
   deleting. Its webhook URL is the `CROSSPOST_RELAY_URL` repository secret.
4. Zernio posts the clip to both accounts at the slot.

There is no queue to run dry: each Shorts run feeds its own slot. A failed
run leaves one slot empty, and the run page says so (an annotation, so the
YouTube Short and its ledger row are never lost to a posting problem).

## The switch

`crosspost.mode` in `config/channels/deep-space-ambient.yaml`, the owner's call:

| mode | What happens |
|---|---|
| `off` | social cuts are rendered as run artifacts (watch them), nothing posts |
| `test` | TikTok only, "only me" privacy; Instagram has no private posts, so skipped |
| `on` | both platforms, public, at the slots |

A manual Shorts run with **crosspost_test** ticked posts its social cut
privately to TikTok whatever the mode says. Nothing can force `on` except the
config.

## Each post

- Caption: image name and telescope, one calm line from the script, "Full
  8-hour sleep video on YouTube: @SpaceScreens", the image credit, 4-5 tags
  (#space #sleepmusic #ambient #relaxing, plus #jwst / #hubble).
- TikTok: public, comments on, duets and stitches off, **made with AI** on
  (the voice is synthetic).
- Instagram: a Reel. `crosspost.instagram_trial: true` would post Trial
  Reels (shown to non-followers first) instead; worth testing later.

## Ids (not secrets)

Zernio TikTok account `6ac840f23cbc6e876b0c5083`, Instagram account
`6ac846d0baf8dfa25ccbe8a9`; Make relay scenario 6566278, webhook 2912796,
Zernio connection 11587474.
