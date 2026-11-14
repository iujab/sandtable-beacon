#!/usr/bin/env bash
# One command: installs what is missing, then starts the Beacon demo (simulated by default).
# Thin wrapper around the cross-platform launcher:  python -m beacon.demo  (works the same on Windows).
#   bash run_demo.sh                       board on http://127.0.0.1:1885, simulated world + real locator
#   bash run_demo.sh --story               same, and plays the ~35 s walk-through
#   bash run_demo.sh --source fake         the hand-made sample replayed in phases (SAMPLE)
#   bash run_demo.sh --source live --lan   real iPhones post to /api/ingest over Wi-Fi (binds 0.0.0.0, prints the URLs)
#   bash run_demo.sh --tailnet             bind to the tailnet address (and 127.0.0.1)
#   bash run_demo.sh --port 1886 ...       another port (or PORT=1886 bash run_demo.sh)
#   bash run_demo.sh --daemon ...          detach (pid in .demo.pid, log in data/demo.log);  bash run_demo.sh --stop  ends it
set -euo pipefail
cd "$(dirname "$0")"
if [ "${1:-}" = "--stop" ]; then exec bash scripts/stop_demo.sh; fi
if [ ! -x .venv/bin/python ] && [ ! -x .venv/Scripts/python.exe ]; then
  echo "[run_demo] creating .venv and installing requirements (first run only)"
  if command -v uv >/dev/null 2>&1; then uv venv .venv -q && uv pip install -q -p .venv -r requirements.txt
  else python3 -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt; fi
fi
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
ARGS=(); DAEMON=0
for a in "$@"; do if [ "$a" = "--daemon" ]; then DAEMON=1; else ARGS+=("$a"); fi; done
if [ "$DAEMON" = 1 ]; then
  mkdir -p data
  nohup setsid "$PY" -m beacon.demo "${ARGS[@]}" > data/demo.log 2>&1 &
  echo $! > .demo.pid
  echo "[run_demo] started in the background (pid $(cat .demo.pid)); log: data/demo.log"
else
  exec "$PY" -m beacon.demo "${ARGS[@]}"
fi
