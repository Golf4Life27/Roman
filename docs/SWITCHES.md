# Switches

Everything that publishes, or changes what the public sees, and where it is
turned on. Each is the owner's call; nothing here flips itself.

| Switch | Where | State | What turning it on does |
|---|---|---|---|
| Daily uploads | repo variable `ROMANFEED_ENABLED`, `publish.mode`, `publish.privacy` | **on** (true / upload / public) | Mon/Wed/Fri: 1h + 8h video, public |
| Shorts | `shorts.enabled` in `config/channels/deep-space-ambient.yaml` | **off** | 3 Shorts a day (09:10, 14:10, 18:40 CDT), public, each linking its full sleep video. Off, the Shorts workflow still renders them as 3-day artifacts to watch first |
| Original music | `audio.source: composed` in the channel yaml | **off** (`library`) | Every new video and Short gets music composed by `romanfeed/audio/composer.py` instead of the Suno manifest |
| Nightly live stream | `live.enabled` in the channel yaml + a server | **off**, no server | ~10 h stream from 9 PM Central, ends < 12 h, saves as a public video. Switch-on rule: 100 subscribers or 15 watch hours a day. See LIVE_STREAM.md |
| Retitle back catalogue | `Retitle videos` workflow, `dry_run` false | not run | Rewrites titles/descriptions/tags of every long upload for sleep searches. Needs the manage-scope token |
| Sleep thumbnails on old videos | `Retrofit thumbnails` workflow with `subject` | not run | Replaces the custom thumbnail on the listed videos |
| Weekly stats email | secrets `SMTP_USER`/`SMTP_PASSWORD`, variable `STATS_EMAIL_TO` | not set up | Monday 08:07 CDT email: subscribers, watch hours, pace to 1,000 / 4,000 by 2027-01-31. See STATS_EMAIL.md |

Built in and on for every new long video (no switch): the 12 s intro card,
the 14–40 s subscribe prompt, sleep-search titles and descriptions, and the
8h cut fading to black at 45 minutes with the music playing on
(`video.sleep_dark_after_minutes`; 0 turns it off).

## What the API cannot do

A Short's **Related video** link (the tappable one under a Short) can only be
set in YouTube Studio; the Data API does not expose it. Each Short carries the
full video's URL as the first line of its description and says "Full 8-hour
sleep video on @SpaceScreens" on screen, which is as far as automation reaches.
Setting the related video by hand in Studio is optional, about 10 seconds a Short.

## Quota (10,000 units a day by default)

| Day | Spend |
|---|---|
| Render day (Mon/Wed/Fri), Shorts on | 2 uploads + 3 Shorts = 5 × 1,600 + 2 thumbnails × 50 = 8,100 |
| Other days, Shorts on | 3 × 1,600 = 4,800 |
| Retitle, one-off | 50 per changed video + a few list calls |

The audit form filed earlier declares 2 uploads per run, 6 a week. Turning
Shorts on makes it 27 uploads a week; update the form's usage line when the
switch is flipped.
