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
# --- bootstrap: a .venv with the requirements (uv when present, else python3 -m venv + pip); fails loudly, never half-way
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
if [ ! -x "$PY" ] || ! "$PY" -c "import fastapi, uvicorn, numpy, segno" >/dev/null 2>&1; then
  echo "[run_demo] installing requirements into .venv (first run only)"
  if command -v uv >/dev/null 2>&1; then
    { [ -x "$PY" ] || uv venv .venv -q; } && uv pip install -q -p .venv -r requirements.txt || { echo "[run_demo] uv could not install the requirements"; exit 1; }
  else
    SYS=""; for c in python3 python; do if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; then SYS="$c"; break; fi; done
    if [ -z "$SYS" ]; then echo "[run_demo] Python 3.10 or newer is needed (or install uv: https://docs.astral.sh/uv/)"; exit 1; fi
    if [ ! -x .venv/bin/python ] && [ ! -x .venv/Scripts/python.exe ]; then
      if ! "$SYS" -m venv .venv; then
        rm -rf .venv
        echo "[run_demo] '$SYS -m venv' failed. On Debian/Ubuntu: sudo apt install python3-venv  (or install uv: https://docs.astral.sh/uv/)"
        exit 1
      fi
    fi
    PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
    "$PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
    "$PY" -m pip install -q -r requirements.txt || { echo "[run_demo] pip could not install the requirements"; exit 1; }
  fi
  PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
fi
ARGS=(); DAEMON=0
for a in "$@"; do if [ "$a" = "--daemon" ]; then DAEMON=1; else ARGS+=("$a"); fi; done
if [ "$DAEMON" = 1 ]; then
  mkdir -p data
  nohup setsid "$PY" -m beacon.demo ${ARGS[@]+"${ARGS[@]}"} > data/demo.log 2>&1 &
  echo $! > .demo.pid
  echo "[run_demo] started in the background (pid $(cat .demo.pid)); log: data/demo.log"
else
  exec "$PY" -m beacon.demo ${ARGS[@]+"${ARGS[@]}"}
fi
