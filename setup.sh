#!/usr/bin/env bash
# One-time setup: Python 3.12 virtualenv with the Reachy Mini SDK + MuJoCo simulator.
set -euo pipefail
cd "$(dirname "$0")"

if command -v uv >/dev/null 2>&1; then
  uv venv .venv --python 3.12
  uv pip install --python .venv/bin/python -r requirements.txt
else
  python3.12 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
echo
echo "Done. Next:"
echo "  1) ./run_sim.sh                              # terminal 1: simulator window"
echo "  2) .venv/bin/python -m reachy_buddy demo     # terminal 2: watch the interaction"
