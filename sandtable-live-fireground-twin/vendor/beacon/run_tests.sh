#!/usr/bin/env bash
# Runs the offline test suite (fast, no network, no hardware).  bash run_tests.sh [pytest args]
set -uo pipefail
cd "$(dirname "$0")"
# --- bootstrap: a .venv with the requirements (uv when present, else python3 -m venv + pip); fails loudly, never half-way
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
if [ ! -x "$PY" ] || ! "$PY" -c "import fastapi, uvicorn, numpy, segno" >/dev/null 2>&1; then
  echo "[run_tests] installing requirements into .venv (first run only)"
  if command -v uv >/dev/null 2>&1; then
    { [ -x "$PY" ] || uv venv .venv -q; } && uv pip install -q -p .venv -r requirements.txt || { echo "[run_tests] uv could not install the requirements"; exit 1; }
  else
    SYS=""; for c in python3 python; do if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; then SYS="$c"; break; fi; done
    if [ -z "$SYS" ]; then echo "[run_tests] Python 3.10 or newer is needed (or install uv: https://docs.astral.sh/uv/)"; exit 1; fi
    if [ ! -x .venv/bin/python ] && [ ! -x .venv/Scripts/python.exe ]; then
      if ! "$SYS" -m venv .venv; then
        rm -rf .venv
        echo "[run_tests] '$SYS -m venv' failed. On Debian/Ubuntu: sudo apt install python3-venv  (or install uv: https://docs.astral.sh/uv/)"
        exit 1
      fi
    fi
    PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
    "$PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
    "$PY" -m pip install -q -r requirements.txt || { echo "[run_tests] pip could not install the requirements"; exit 1; }
  fi
  PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
fi
exec "$PY" -m pytest tests -q -p no:cacheprovider "$@"
