#!/usr/bin/env bash
# Runs the offline test suite (fast, no network, no hardware).  bash run_tests.sh [pytest args]
set -uo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] && [ ! -x .venv/Scripts/python.exe ]; then
  if command -v uv >/dev/null 2>&1; then uv venv .venv -q && uv pip install -q -p .venv -r requirements.txt
  else python3 -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt; fi
fi
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
exec "$PY" -m pytest tests -q -p no:cacheprovider "$@"
