#!/usr/bin/env bash
# Runs the offline test suite (replay-mode LLM, fixture data, fake hub) and writes results/tests.json. Well under two minutes.
set -uo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] && [ ! -x .venv/Scripts/python.exe ]; then
  if command -v uv >/dev/null 2>&1; then uv venv .venv -q && uv pip install -q -p .venv -r requirements.txt
  else python3 -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt; fi
fi
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
START=$(date +%s)
OUT=$(LLM_BACKEND=replay SANDTABLE_DATA_MODE=fixture "$PY" -m pytest -q -p no:cacheprovider tests "$@" 2>&1); CODE=$?
echo "$OUT" | tail -4
mkdir -p results
"$PY" - "$OUT" "$START" <<'PYEOF'
import json, re, sys, time
out, start = sys.argv[1], int(sys.argv[2])
passed = int((re.search(r"(\d+) passed", out) or [0, 0])[1]); failed = int((re.search(r"(\d+) failed", out) or [0, 0])[1])
json.dump({"command": "bash run_tests.sh", "passed": passed, "failed": failed, "duration_s": int(time.time()) - start, "last_run": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, open("results/tests.json", "w"), indent=1)
PYEOF
exit $CODE
