# Adding a channel

Each channel is one file in `config/channels/` plus one daily workflow. The
ocean channel (Deep Blue Screens, `ocean-ambient.yaml`, `ocean-daily.yml`)
is the pattern for the lanes after it.

1. **Footage with a clear right to use.** Public domain, CC0 or CC BY only:
   the `commons` source enforces that per file and every photo's credit and
   licence goes in the description. Never share-alike, non-commercial or
   no-derivatives files, never clips from other channels.
2. **Its own look.** `channel.subject` picks the title words, description and
   photo filter (`romanfeed/publish/metadata.py`, `curation/selector.py`);
   `audio.style` picks the composed music (`audio/composer.py`);
   `video.thumbnail_line` and `thumbnail_plate` set the thumbnail. Reused,
   look-alike channels are what YouTube's "reused content" review turns down.
3. **The YouTube channel.** The owner creates it (a new account needs his
   yes), as a channel under the Google account that owns Space Screens.
4. **Upload access.** `connect-channel.yml`: run 1 prints a consent link, the
   owner picks the new channel and allows, and run 2 takes the address the
   browser lands on. The token is stored encrypted on the `channel-tokens`
   release with the channel id it was proven to upload to; the daily job
   refuses any upload that lands elsewhere.
5. **Daily workflow.** Copy `ocean-daily.yml`, change `SLUG` and the cron.
   It stays idle until step 4 is done.
6. **Monday report.** Automatic: `romanfeed siblings` and `romanfeed supply`
   cover every config whose `channel.subject` is not `space`.

`romanfeed supply config/channels/<slug>.yaml` says how many days of daily
long videos the unused photos cover.
