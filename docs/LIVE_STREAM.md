# Nightly live stream

**Status: built, switched off.** Turn it on when the channel reaches
**100 subscribers or 15 watch hours a day**, whichever comes first. Before
then a live stream mostly plays to nobody, and the server would cost money
for nothing.

## What it does

Every night at **9 PM US Central** (America/Chicago, so it follows daylight
saving), a small always-on server goes live on the channel with about
**10 hours** of the channel's own rendered space videos, back to back, with
their music. It ends by itself before morning. Because it ends in under
12 hours, YouTube keeps the recording as a normal **public video** on the
channel — one new long sleep video a night, for free.

- The videos come from the daily render workflow. Each render's 1-hour cut
  is kept on GitHub for 3 days; the server pulls every new one down once a
  day (3 PM Central) and keeps the newest **12** (about 12 hours of material).
- Each video is converted once, when it arrives, into a copy YouTube's live
  ingest likes (a keyframe every 2 seconds, a steady 4.5 Mbps). The nightly
  run then just sends those files as they are, so it needs almost no CPU.
- The order is shuffled per night (same date → same order), never opens with
  the same video as the night before, and only repeats a video when the
  library holds less than 10 hours.
- The broadcast is public, not made for kids, starts automatically when the
  video arrives, stops automatically when it ends, and lets viewers scrub
  back (DVR). Its title names the lead image and the date; the description
  lists the night's line-up, the channel's sleep keywords, and a one-click
  subscribe link (`https://www.youtube.com/@SpaceScreens?sub_confirmation=1`).

It never runs 24/7 and cannot run without an end. One night, one broadcast,
then the program exits.

## How it is guaranteed to stop (three layers, plus one)

YouTube does not archive a live stream that runs past 12 hours. So the
length is capped at **11.5 hours** no matter what the config says, and three
independent things enforce it:

1. **ffmpeg stops itself.** It is started with `-t <seconds>` set to the
   night's length (10 h by default; never more than 41,400 s = 11.5 h). When
   that runs out it stops sending, and YouTube's auto-stop ends the broadcast.
2. **A wall-clock watchdog.** The Python process kills ffmpeg if it is still
   running 15 minutes after the planned length (so at most 11 h 45 m).
3. **The API.** Whatever happened — normal end, crash, watchdog — the program
   always tells YouTube to complete the broadcast
   (`liveBroadcasts.transition` → `complete`). If it never went live at all,
   the empty broadcast is deleted instead so no stale "upcoming" event sits
   on the channel.

Plus the server itself: the systemd service has `RuntimeMaxSec=12h`, and it
is never restarted automatically. A night the server misses (down at 9 PM) is
skipped, not run late.

The config refuses `live.hours` above 11.5 at load time, and the code clamps
to 11.5 again at run time.

## Why a server, and which one

GitHub Actions jobs are killed at 6 hours, so a 10-hour stream needs a
machine that is always on. Needs: ~2 vCPU (the once-a-day transcode), ~40 GB
disk (12 stream-ready hours ≈ 25 GB plus working space), and bandwidth of
10 h × 4.5 Mbps ≈ **20 GB a night ≈ 610 GB a month** upstream.

Prices checked 2026-10-01, monthly, before tax:

| Server | vCPU | RAM | Disk | Transfer included | Price/month | 610 GB fits? |
|---|---|---|---|---|---|---|
| **Hetzner Cloud CX23** (Germany/Finland) | 2 | 4 GB | 40 GB SSD | 20 TB | **€5.49** + primary IPv4 (~€0.50) ≈ €6 | yes, 3% of it |
| DigitalOcean Basic Droplet 2 GB | 1 | 2 GB | 50 GB SSD | 2 TB | $12 | yes |
| Vultr Regular Performance 2 GB | 2 | 2 GB | — | 3 TB (+2 TB free account pool) | $15 | yes |

Hetzner went from €3.99 to €5.49 for the CX23 on 15 June 2026; the IPv4
address is billed on top.

Sources:
- Hetzner: [privatedevops.com — Hetzner June 2026 cloud price increase](https://privatedevops.com/news/hetzner-june-2026-cloud-price-increase-what-to-do),
  [northflank.com — Hetzner cloud server price increases](https://northflank.com/blog/hetzner-cloud-server-price-increases),
  [wz-it.com — Hetzner price increase June 2026](https://wz-it.com/en/blog/hetzner-price-increase-june-2026-cpx-ccx-alternatives/)
- DigitalOcean: [vpsbenchmarks.com — DigitalOcean Basic Regular 2GB](https://www.vpsbenchmarks.com/hosters/digitalocean/plans/std_2gb_1core),
  [costbench.com — DigitalOcean pricing change Feb 2026](https://costbench.com/changelog/digitalocean-price-decrease-2026-02/)
- Vultr: [whtop.com — Vultr Regular Performance 2vCPU 2GB](https://www.whtop.com/amp/plans/vultr.com/137598),
  [Vultr blog — 2 TB free monthly egress](https://blogs.vultr.com/vultr-announces-reduced-bandwidth-pricing-2-tb-of-free-monthly-egress-free-ingress-and-global-pooling)

**Decision: Hetzner Cloud CX23, Ubuntu 24.04, about €6 a month (~€72 a
year).** It is half the price of the next option, has twice the CPU of the
$12 DigitalOcean droplet (which matters for the daily transcode), twice the
RAM of either, and 30× the transfer we need. The CX line is only sold in the
EU (Germany/Finland); that is fine for this job — YouTube's RTMP ingest
routes to the nearest ingest point, and a sleep stream does not care about a
few seconds of extra latency. 40 GB of disk is the tight part, so the library
keeps 12 videos and the sync stops before the disk falls under 5 GB free.

## One-time setup (only after the owner says yes)

Nothing here costs money until step 2.

1. **Owner decision.** Channel at 100 subscribers or 15 watch hours/day, and
   the owner agrees to ~€6/month.
2. **Buy the server.** Hetzner Cloud → new server → CX23, Ubuntu 24.04, an SSH
   key, primary IPv4 on.
3. **Enable live streaming on the channel.** YouTube Studio → Create → Go
   live. The first time, YouTube asks to verify the channel by phone and then
   takes **up to 24 hours** to activate live streaming. Do this a day ahead.
4. **Re-mint the YouTube token with the manage scope**, on a computer with a
   browser: `romanfeed auth --scope manage`. Live broadcasts need
   `youtube.force-ssl`, which the upload-only token does not have. (The new
   token also covers uploads, so it can replace the `YOUTUBE_TOKEN_JSON`
   GitHub secret too.)
5. **Make a GitHub token for the server.** GitHub → Settings → Developer
   settings → Fine-grained tokens → repository `Golf4Life27/Roman` only,
   permission **Actions: read**. It only downloads the render artifacts.
6. **Install.** Copy the repo to the server and run
   `sudo bash deploy/live/setup.sh --youtube-token … --env …`
   (details in [deploy/live/README.md](../deploy/live/README.md)). Timers are
   installed but off.
7. **Fill the library.** `sudo systemctl enable --now romanfeed-sync.timer`
   a few days ahead, then check `romanfeed live start --dry-run` prints a
   sensible playlist, title and command.
8. **Switch on.** Add `live: {enabled: true}` to
   `config/channels/deep-space-ambient.yaml`, update the server copy, and
   `sudo systemctl enable --now romanfeed-live.timer`.

To stop: `sudo systemctl disable --now romanfeed-live.timer`.

## Settings

All in the channel config under `live:` (defaults shown; the channel file has
no `live:` block yet, so these defaults — off — are what applies):

```yaml
live:
  enabled: false            # the owner's switch
  hours: 10.0               # per night; refused above 11.5
  start_local: "21:00"      # must match deploy/live/romanfeed-live.timer
  timezone: America/Chicago
  library_dir: data/live    # the server passes /var/lib/romanfeed/live
  keep_files: 12            # newest N stream-ready videos kept
  min_free_gb: 5.0          # sync stops before free disk drops below this
  artifact_repo: Golf4Life27/Roman
```

## Commands

```
romanfeed live sync               # pull new render artifacts, transcode, prune
romanfeed live plan               # print tonight's playlist; sends nothing
romanfeed live start --dry-run    # plan + broadcast body + ffmpeg command, key masked; sends nothing
romanfeed live start              # tonight's broadcast; refuses unless live.enabled is true
```

The stream key is created once (a reusable YouTube live stream), stored in
`<library>/stream.json` with 0600 permissions, and never printed or logged.
