#!/bin/sh
# Starts the simulated Reachy Mini (unless REACHY_HOST is set), then the web dashboards.
#
# Environment variables:
#   ANTHROPIC_API_KEY  Claude key for new stories (without it: one built-in story)
#   APP_PASSWORD       password for the web page (strongly recommended on the internet)
#   PORT               web port (default 8080)
#   STT_MODEL          Whisper model: tiny, base, small (default), medium
#   REACHY_HOST        address of a real Reachy Mini daemon (e.g. over Tailscale); empty = simulator
#   REACHY_PORT        that daemon's port (default 8000)
set -e
PORT="${PORT:-8080}"
STT_MODEL="${STT_MODEL:-small}"

if [ -z "$REACHY_HOST" ]; then
  echo "Starting the simulated Reachy Mini..."
  reachy-mini-daemon --sim --headless --no-media --scene minimal \
      --fastapi-host 127.0.0.1 --fastapi-port 8000 --no-preload-datasets &
  i=0
  until curl -fs http://127.0.0.1:8000/api/daemon/status | grep -q '"state":"running"'; do
    i=$((i + 1))
    if [ "$i" -gt 180 ]; then echo "The simulator did not start." >&2; exit 1; fi
    sleep 1
  done
  set -- --port 8000 --speaker browser
else
  echo "Connecting to the Reachy Mini at $REACHY_HOST:${REACHY_PORT:-8000}..."
  set -- --host "$REACHY_HOST" --port "${REACHY_PORT:-8000}" --speaker "${SPEAKER:-browser}"
fi

exec python -m reachy_buddy web "$@" --no-sound --no-browser \
    --web-host 0.0.0.0 --web-port "$PORT" --stt-model "$STT_MODEL"
