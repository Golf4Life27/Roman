#!/usr/bin/env bash
# One-time setup of the nightly live-stream server (Ubuntu 24.04).
#
# Run as root from a checkout of the repo on the server:
#   sudo bash deploy/live/setup.sh [--youtube-token path/to/youtube.token.json] [--env path/to/env]
#
# The live network (two more streams, live.lanes in the channel config):
#   sudo bash /opt/romanfeed/app/deploy/live/setup.sh lanes
# asks for each lane's Studio stream key (Enter keeps the one already set),
# writes it to /etc/romanfeed/env, installs the lane units and turns on the
# timer of every lane that has a key. Typed keys are not echoed back.
#
# It installs ffmpeg + Python, creates the `romanfeed` service user, puts the
# code in /opt/romanfeed/app with its own venv, places the secrets 0600 under
# /etc/romanfeed, and installs the systemd units -- DISABLED. Nothing streams
# until the owner enables the timers (deploy/live/README.md) AND sets
# live.enabled in the channel config. Safe to re-run: it updates in place.
set -euo pipefail

YT_TOKEN=""
ENV_SRC=""
LANES=0
while [ $# -gt 0 ]; do
  case "$1" in
    lanes) LANES=1; shift ;;
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
LANE_UNITS="romanfeed-lane@.service romanfeed-lane-two.timer romanfeed-lane-three.timer"

if [ "$LANES" -eq 1 ]; then
  [ -f "$ETC/env" ] || { echo "no $ETC/env: run the full setup first" >&2; exit 1; }
  echo "== live lanes: stream keys (Studio -> Go live -> Stream -> stream key list -> Create new stream key)"
  for pair in "two:YOUTUBE_STREAM_KEY_2" "three:YOUTUBE_STREAM_KEY_3"; do
    lane="${pair%%:*}"; var="${pair#*:}"
    if grep -q "^$var=." "$ETC/env"; then have="set"; else have="not set"; fi
    read -r -s -p "   lane $lane key ($var, $have; Enter keeps it): " key; echo
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    if [ -n "$key" ]; then
      sed -i "/^$var=/d" "$ETC/env"
      printf '%s=%s\n' "$var" "$key" >> "$ETC/env"
      echo "   lane $lane: key saved (ends in ...${key: -4}; check it against Studio)"
    fi
  done
  chown romanfeed:romanfeed "$ETC/env"; chmod 0600 "$ETC/env"
  for u in $LANE_UNITS; do
    install -m 0644 "$APP/deploy/live/$u" "/etc/systemd/system/$u"
  done
  systemctl daemon-reload
  for pair in "two:YOUTUBE_STREAM_KEY_2" "three:YOUTUBE_STREAM_KEY_3"; do
    lane="${pair%%:*}"; var="${pair#*:}"
    if grep -q "^$var=." "$ETC/env"; then
      systemctl enable --now "romanfeed-lane-$lane.timer" >/dev/null
      echo "   lane $lane: timer on, next start $(systemctl show -p NextElapseUSecRealtime --value "romanfeed-lane-$lane.timer")"
    else
      echo "   lane $lane: no key, timer left off"
    fi
  done
  echo "Done. The GitHub secrets YOUTUBE_STREAM_KEY_2 and YOUTUBE_STREAM_KEY_3 must hold the same keys."
  exit 0
fi

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
for u in romanfeed-sync.service romanfeed-sync.timer romanfeed-live.service romanfeed-live.timer $LANE_UNITS; do
  install -m 0644 "$APP/deploy/live/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload

ffmpeg -hide_banner -version | head -1
echo
echo "Done. Timers are disabled. Next steps: deploy/live/README.md"
