# The history channel ("On This Day")

TikTok **@this.day.in.histoy**. Three narrated vertical videos a day about
the date's events, made and posted with no one involved
(`.github/workflows/history.yml`, `romanfeed/history/`).

## How a day's videos are made

1. **Events.** Wikipedia's "On this day" lists for the date (the Main Page
   picks first). Skipped: war, death, disaster, crime, and divisive
   politics (trials, party congresses, scandals). Preferred: firsts,
   inventions, records, flights, broadcasts. Three are chosen at least 15
   years apart, ordered differently each year.
2. **Images.** Only a photo of the event's own subject (or the item it
   marks "pictured"), only from Wikimedia Commons, and only if it is free
   worldwide: public domain or CC0. US-only public domain (e.g. a 1928
   drawing still protected in the UK), flags, logos, maps and small images
   are rejected.
3. **Script.** Claude (Opus 5.5) writes 50-75 words from the event line
   plus the full text of its main articles, opening with the date and
   closing with "Follow for one day in history, every day." Then a checker
   rejects any number or capitalised name the source does not contain; one
   retry names the offending words, and a second failure falls back to a
   template that only reuses the source's own sentences. Without
   `ANTHROPIC_API_KEY` the template is used.
4. **Video.** 1080x1920, its own look (sepia blur, framed photo with a slow
   zoom, gold serif date header), a Google TTS voice
   (`history.voice`), captions, and music composed for the clip under the
   voice.
5. **Post.** Per `crosspost.mode` in `config/channels/this-day-in-history.yaml`:
   one video at each of 09:30, 13:30 and 18:30 Central. The caption credits
   Wikipedia as the source and the image's author and licence. Videos
   without a voice are never posted.

## Switches and what each needs

| | Where | Now |
|---|---|---|
| Posting | `crosspost.mode` + `crosspost.tiktok_account` in the history config | off, no account yet |
| Script writer | repository secret `ANTHROPIC_API_KEY` | not set: template scripts |
| Voice | repository secret `GOOGLE_TTS_API_KEY` (shared with Space Screens) | set |

A manual run (Actions, History, Run workflow) renders without posting by
default; tick `crosspost_test` to send the day's videos to the TikTok
Creator Inbox as drafts.
