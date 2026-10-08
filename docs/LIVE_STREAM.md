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

**Cozy scene nights** (`live.scenes: alternate`): every other night, and
every holiday, meteor-peak and launch night, the stream plays one seasonal
cozy scene loop all night instead of the videos, under music composed for
that night (see COZY_SCENES.md). The 3 PM sync downloads the scene, encodes
it once (a keyframe every 2 s, the closing duplicate frame dropped, a small
"Space Screens · Subscribe to travel through time" line burned in) and
composes 2 hours of music; at 9 PM ffmpeg loops both with a plain stream
copy, under the same caps. If anything about the scene fails, the night falls
back to the videos. With the Studio stream key the title is Studio's, so it
stays the same on both kinds of night.

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

The owner does four things, none in a terminal. The stream uses Studio's
persistent stream key, so it needs no extra YouTube API permission (the API
path needs youtube.force-ssl, which Google gates behind an app review).

1. **Live streaming on** (done 2026-10-03; YouTube takes 24 h to activate).
2. **Stream key + auto start/stop.** Studio -> Create -> Go live -> **Stream**.
   Set the title/description once (they carry over night to night), turn on
   **Auto-start** and **Auto-stop** in the stream settings, set visibility
   **Public**, and copy the **Stream key**.
3. **GitHub token** for downloading the daily renders:
   github.com/settings/personal-access-tokens/new -> Repository access: only
   Golf4Life27/Roman -> Permissions: **Actions: Read** -> Generate, copy.
4. **Rent the server** (Hetzner CX23, about €6/month): Create server ->
   Ubuntu 24.04 -> CX23 -> paste `deploy/live/cloud-init.yaml` into **Cloud
   config** with the key and token filled in -> Create.

From then on the server updates itself from the repo every day at 15:00
Central before pulling new renders. The stream starts only when
`live: {enabled: true}` is in the channel config -- a repo change, not a
server change.

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
