# Switches

Everything that publishes, or changes what the public sees, and where it is
turned on. Each is the owner's call; nothing here flips itself.

| Switch | Where | State | What turning it on does |
|---|---|---|---|
| Daily uploads | repo variable `ROMANFEED_ENABLED`, `publish.mode`, `publish.privacy` | **on** (true / upload / public) | Mon/Wed/Fri: 1h + 8h video, public |
| Shorts | `shorts.enabled` in `config/channels/deep-space-ambient.yaml` | **on** (2026-10-01) | 4 Shorts a day (11:10, 18:10, 20:40, 22:40 CDT), public, each linking its full sleep video. Off, the Shorts workflow still renders them as 3-day artifacts to watch first |
| Cross-posting | `crosspost.mode` in the channel yaml + secret `CROSSPOST_RELAY_URL` | **on** (2026-10-09) | Each Short's social cut goes to TikTok and Instagram at 12:00, 19:00, 21:30, 23:30 Central. `test` = TikTok, private. See CROSSPOST.md |
| Original music | `audio.source: composed` in the channel yaml | **on** (2026-10-01) | Every new video and Short gets music composed by `romanfeed/audio/composer.py` instead of the Suno manifest |
| Nightly live stream | `live.enabled` in the channel yaml + a server | **on** (2026-10-05) | ~10 h stream from 9 PM Central, ends < 12 h, saves as a public video. See LIVE_STREAM.md |
| Cozy scene nights | `live.scenes` in the channel yaml | **alternate** (2026-10-08) | Every other night, and every holiday / meteor-peak / launch night, the stream loops a seasonal cozy scene instead of the space videos. `always` or `off`. See COZY_SCENES.md |
| Weekly cozy video | `cozy.enabled` in the channel yaml | **on** (owner, 2026-10-10) | Saturday 08:23 CDT: a 3-hour cozy scene video, marked as AI-generated. Off, a 6-minute preview is rendered as an artifact |
| New cozy scenes | repository secret `RUNWAYML_API_SECRET` + `cozy.monthly_credits` | **on**, key added 2026-10-10 (cap 2000 = $20/month; $50 of credits bought) | The 1st of each month: new scene loops for the coming 45 days' holidays and sky events, never past the cap |
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
| Saturday, Shorts + weekly cozy video | 5 × 1,600 + 50 = 8,050 |
| Other days, Shorts on | 4 × 1,600 = 6,400 |
| Retitle, one-off | 50 per changed video + a few list calls |

The audit form filed earlier declares 2 uploads per run, 6 a week. Turning
Shorts on makes it 27 uploads a week; update the form's usage line when the
switch is flipped.
