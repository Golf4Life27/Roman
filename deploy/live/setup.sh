#!/usr/bin/env bash
# One-time setup of the nightly live-stream server (Ubuntu 24.04).
#
# Run as root from a checkout of the repo on the server:
#   sudo bash deploy/live/setup.sh [--youtube-token path/to/youtube.token.json] [--env path/to/env]
#
# It installs ffmpeg + Python, creates the `romanfeed` service user, puts the
# code in /opt/romanfeed/app with its own venv, places the secrets 0600 under
# /etc/romanfeed, and installs the systemd units -- DISABLED. Nothing streams
# until the owner enables the timers (deploy/live/README.md) AND sets
# live.enabled in the channel config. Safe to re-run: it updates in place.
set -euo pipefail

YT_TOKEN=""
ENV_SRC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --youtube-token) YT_TOKEN="$2"; shift 2 ;;
    --env) ENV_SRC="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
APP=/opt/romanfeed/app
VENV=/opt/romanfeed/venv
ETC=/etc/romanfeed
LIB=/var/lib/romanfeed/live

echo "== packages"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q ffmpeg python3-venv python3-pip fonts-dejavu-core rsync

echo "== user"
id romanfeed >/dev/null 2>&1 || useradd --system --home-dir /var/lib/romanfeed --create-home --shell /usr/sbin/nologin romanfeed

echo "== code -> $APP"
mkdir -p "$APP"
if [ "$SRC" != "$APP" ]; then
  rsync -a --delete --exclude data/ --exclude output/ --exclude secrets/ --exclude .venv/ "$SRC/" "$APP/"
fi
chown -R romanfeed:romanfeed /opt/romanfeed

echo "== venv"
sudo -u romanfeed python3 -m venv "$VENV"
sudo -u romanfeed "$VENV/bin/pip" install -q --upgrade pip
sudo -u romanfeed "$VENV/bin/pip" install -q -e "$APP[youtube]"

echo "== secrets -> $ETC (0600, owner romanfeed)"
install -d -m 0700 -o romanfeed -g romanfeed "$ETC"
if [ -n "$ENV_SRC" ]; then
  install -m 0600 -o romanfeed -g romanfeed "$ENV_SRC" "$ETC/env"
elif [ ! -f "$ETC/env" ]; then
  install -m 0600 -o romanfeed -g romanfeed "$APP/deploy/live/env.example" "$ETC/env"
  echo "   wrote a template $ETC/env -- fill in GITHUB_TOKEN before the first sync"
fi
if [ -n "$YT_TOKEN" ]; then
  install -m 0600 -o romanfeed -g romanfeed "$YT_TOKEN" "$ETC/youtube.token.json"
fi
[ -f "$ETC/youtube.token.json" ] || echo "   no $ETC/youtube.token.json yet -- copy in a token minted with: romanfeed auth --scope manage"

echo "== library -> $LIB"
install -d -m 0750 -o romanfeed -g romanfeed "$LIB"

echo "== systemd units (installed, NOT enabled)"
for u in romanfeed-sync.service romanfeed-sync.timer romanfeed-live.service romanfeed-live.timer; do
  install -m 0644 "$APP/deploy/live/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload

ffmpeg -hide_banner -version | head -1
echo
echo "Done. Timers are disabled. Next steps: deploy/live/README.md"
