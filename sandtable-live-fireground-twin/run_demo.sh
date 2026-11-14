#!/usr/bin/env bash
# One command: installs what is missing, then starts the demo in simulated / fixture / replay mode.
# This is only a thin wrapper around the cross-platform launcher:  python -m sandtable.demo  (see README for Windows).
#   bash run_demo.sh                 board on http://127.0.0.1:1810, simulated UWB hub on 1910, Beacon module (vendor/beacon) on 1885
#   bash run_demo.sh --storyboard    same, and plays the exercise hands-free (default script: beacon)
#   bash run_demo.sh --no-beacon     do not start the Beacon module (the board relays its labelled sample)
#   bash run_demo.sh --tailnet       bind the board to the tailnet address
#   bash run_demo.sh --daemon ...    detach (pid in .demo.pid, log in data/demo.log; in live mode both under data/live/);  bash run_demo.sh --stop  ends it
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
LOGDIR=data; PIDFILE=.demo.pid
if [ "${SANDTABLE_DATA_MODE:-}" = "live" ] || [ "${COSTAR_MODE:-}" = "live" ] || [ "${MATTERPORT_MODE:-}" = "live" ] || [ "${VEXCEL_MODE:-}" = "live" ]; then
  LOGDIR=data/live; PIDFILE=data/live/demo.pid   # live mode: the log may name rooms of the real building, so it stays private,
fi                                               # and a live instance never takes over the pid file of the standing fixture demo
if [ "$DAEMON" = 1 ]; then
  mkdir -p "$LOGDIR"
  nohup setsid "$PY" -m sandtable.demo "${ARGS[@]}" > "$LOGDIR/demo.log" 2>&1 &
  echo $! > "$PIDFILE"
  echo "[run_demo] started in the background (pid $(cat "$PIDFILE")); log: $LOGDIR/demo.log"
else
  exec "$PY" -m sandtable.demo "${ARGS[@]}"
fi
