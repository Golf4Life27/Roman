# Cross-posting to TikTok and Instagram

Every Short gets a second cut, the **social cut**, for TikTok @spacescreens
and Instagram @space.screens:

| | YouTube Short | Social cut |
|---|---|---|
| Picture, music, facts | same | same |
| Closing words | "The full 8-hour version is on the channel. Subscribe to …" | "The full 8-hour version is on YouTube, at Space Screens. Follow for more space to fall asleep to." |
| Closing plate | Subscribe … / Full 8-hour sleep video on @SpaceScreens | Full 8-hour version on YouTube / @SpaceScreens |

## How a clip travels

1. The Shorts workflow runs four times a day and renders both cuts.
2. The social cut is attached to the `social-clips` release on this public
   repo, which gives it a public link. Clips older than 30 days are deleted.
3. GitHub sends the post to the **relay**: the Make scenario "Space Screens:
   post relay (GitHub -> Zernio)", which holds the Zernio key. The relay only
   allows reading accounts and account health, creating posts, listing and
   reading posts, and the posting queue, never deleting. Its webhook URL is
   the `CROSSPOST_RELAY_URL` repository secret.
4. The post joins Zernio's posting queue "Space Screens social (4 a day)":
   12:00, 19:00, 21:30 and 23:30 Central. Zernio gives it the next free slot
   and posts it to both accounts. GitHub starts scheduled runs hours late, so
   the queue, not the run time, decides when a clip goes out.

A failed run just means one fewer clip waiting, and the run page says so (an
annotation, so the YouTube Short and its ledger row are never lost to a
posting problem).

## The daily check

`.github/workflows/crosspost-check.yml` runs `romanfeed crosspost check`
every morning. It reads, through the relay:

- account health: both accounts can post, tokens valid, nothing to reconnect
- the last day's posts: any that **failed** (with each platform's error) or
  are still waiting 45 minutes past their slot
- the queue: at least one post waiting, while `crosspost.mode` is `on`

When something needs you, it opens one GitHub issue labelled
`crosspost-alert` that @mentions you, so GitHub emails it; later failures
comment on the same issue, and the next clean run closes it. Run it by hand
from the Actions tab (Crosspost check, Run workflow) any time.

Zernio's account list has a "last successful publish" field that stays empty
even after posts go out, so the check reads the posts themselves instead.

## The switch

`crosspost.mode` in `config/channels/deep-space-ambient.yaml`, the owner's call:

| mode | What happens |
|---|---|
| `off` | social cuts are rendered as run artifacts (watch them), nothing posts |
| `test` | TikTok only, as a draft in the TikTok app's Creator Inbox (private until you post it there); Instagram is skipped |
| `on` | both platforms, public, at the slots |

A manual Shorts run with **crosspost_test** ticked sends its social cut to
the TikTok Creator Inbox as a draft, whatever the mode says. TikTok lets this
account post only public videos through the API, so "only me" is not
available; the inbox draft is the private test.

The relay sends Zernio the request body as JSON text: Make's "Make an API
call" step garbles a body passed as an object, which Zernio then rejects. Nothing can force `on` except the
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

## The weekly cozy clip

Saturdays, `.github/workflows/cozy-clip.yml` turns the season's cozy scene
(the same calendar pick as the live stream) into a 20-second vertical clip:
the whole scene over a blurred copy of itself, its name above, and "Cozy
nights live on YouTube · @SpaceScreens" below. Twenty seconds is exactly two
passes of the loop, so TikTok's replay does not jump. It posts at 20:15
Central (`crosspost.cozy_slots`), a slot of its own, and follows
`crosspost.mode` like everything else. The caption says "AI-animated scene,
original music."
