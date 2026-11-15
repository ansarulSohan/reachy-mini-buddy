#!/usr/bin/env bash
# Start the Reachy Mini MuJoCo simulator: the daemon plus a 3D viewer window.
# Leave it running, then start the program in a second terminal:
#     .venv/bin/python -m reachy_buddy demo
#
# Env vars:  SCENE=minimal|empty   REACHY_PORT=<port>  (default 8000, or 8100 if 8000 is taken)
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "No .venv found - run ./setup.sh first." >&2
  exit 1
fi

SCENE="${SCENE:-minimal}"
PORT="${REACHY_PORT:-8000}"

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
is_reachy() { curl -s -m 1 "http://127.0.0.1:$1/api/daemon/status" | grep -q robot_name; }

if port_busy "$PORT"; then
  if is_reachy "$PORT"; then
    echo "A Reachy Mini daemon is already running on port $PORT." >&2
    exit 0
  fi
  if [[ -z "${REACHY_PORT:-}" ]]; then
    echo "Port $PORT is used by another program; using 8100 instead."
    PORT=8100
    if port_busy "$PORT" && is_reachy "$PORT"; then
      echo "A Reachy Mini daemon is already running on port $PORT." >&2
      exit 0
    fi
  else
    echo "Port $PORT is busy. Pick another one with REACHY_PORT=<port>." >&2
    exit 1
  fi
fi

echo "Starting Reachy Mini simulator (scene: $SCENE, port: $PORT)..."
echo "First launch can take a minute while GStreamer scans its plugins."
if [[ "$(uname)" == "Darwin" ]]; then
  # MuJoCo's viewer needs mjpython on macOS.
  exec .venv/bin/mjpython -m reachy_mini.daemon.app.main --sim --scene "$SCENE" --fastapi-port "$PORT" "$@"
else
  exec .venv/bin/reachy-mini-daemon --sim --scene "$SCENE" --fastapi-port "$PORT" "$@"
fi
