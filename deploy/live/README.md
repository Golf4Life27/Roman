# Live-stream server

**Easiest install:** paste `cloud-init.yaml` (two values filled in) into the
Hetzner "Cloud config" box when creating the server; see docs/LIVE_STREAM.md.
The manual route below still works.

Files for the always-on box that runs the nightly ~10-hour stream. What it
does and why is in [docs/LIVE_STREAM.md](../../docs/LIVE_STREAM.md); this page
is only the commands.

| File | What it is |
|---|---|
| `setup.sh` | One-time install on Ubuntu 24.04 (re-runnable to update) |
| `env.example` | Template for `/etc/romanfeed/env` (`GITHUB_TOKEN`, `YOUTUBE_TOKEN_PATH`) |
| `romanfeed-sync.service` / `.timer` | Daily 15:00 Chicago: pull new renders, transcode, prune |
| `romanfeed-live.service` / `.timer` | Daily 21:00 Chicago: one capped broadcast, then exit |

## Install (timers stay off)

```bash
# on the server, as root, from a checkout of the repo
# (scp/rsync it over, or clone with a token that has Contents: read)
sudo bash deploy/live/setup.sh \
  --youtube-token /path/to/youtube.token.json \
  --env /path/to/filled-in-env
```

The YouTube token must be minted with `romanfeed auth --scope manage` on a
machine with a browser; the server never runs the browser flow.

## Check it before switching anything on

```bash
sudo -u romanfeed bash -c 'set -a; . /etc/romanfeed/env; cd /opt/romanfeed/app; \
  /opt/romanfeed/venv/bin/romanfeed live sync --library-dir /var/lib/romanfeed/live'
sudo -u romanfeed bash -c 'set -a; . /etc/romanfeed/env; cd /opt/romanfeed/app; \
  /opt/romanfeed/venv/bin/romanfeed live start --dry-run --library-dir /var/lib/romanfeed/live'
```

The dry run prints the playlist, the broadcast body and the exact ffmpeg
command (stream key masked) and sends nothing.

## Switch on

1. Turn on the library sync first, a few days ahead, so the library fills:
   ```bash
   sudo systemctl enable --now romanfeed-sync.timer
   ```
2. Set the config switch. In `config/channels/deep-space-ambient.yaml` add
   ```yaml
   live:
     enabled: true
   ```
   commit it, bring the server copy up to date (re-run `setup.sh` from the
   updated checkout), and then enable the nightly timer:
   ```bash
   sudo systemctl enable --now romanfeed-live.timer
   systemctl list-timers 'romanfeed-*'      # shows the next 21:00 Chicago run
   ```

## Switch off / stop tonight

```bash
sudo systemctl disable --now romanfeed-live.timer   # no more nights
sudo systemctl stop romanfeed-live.service          # end tonight's stream now (completes the broadcast)
```

Format check of the library (a regular night joins the files without re-encoding, so one odd file
can leave YouTube at "Preparing stream"):
`sudo -u romanfeed /opt/romanfeed/venv/bin/romanfeed live doctor --library-dir /var/lib/romanfeed/live`

Logs: `journalctl -u romanfeed-live -u romanfeed-sync --since today`. The
stream key never appears in them: ffmpeg's output is masked before logging.
