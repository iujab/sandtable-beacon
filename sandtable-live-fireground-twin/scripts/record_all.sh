#!/usr/bin/env bash
# Records the command agent with a real model: every exercise script (beacon, scout, mayday) on both rig scenarios
# (Linux bench helper; needs the claude CLI, or LLM_BACKEND=anthropic with ANTHROPIC_API_KEY).
# Each run plays one storyboard on the simulator and writes every new model response into fixtures/llm_cache/.
# Idempotent: a prompt that is already in the cache replays from it, so only new prompts call the model.
# Then it prints, per run, the end step, the backends of the pre-plan and after-action report, and every
# turn that fell back to the deterministic policy (a fallback means that turn has no recording: re-run or fix).
#
#   bash scripts/record_all.sh                       app on 1810, hub on 1910 (stops whatever runs there)
#   PORT=1913 HUB_PORT=1914 bash scripts/record_all.sh
#   DEMO_ARGS="--no-beacon" ...                    extra launcher arguments (do not start the vendored Beacon server)
#   SCRIPTS="beacon" SCENARIOS="hardware/scenarios/sandtable_rig_3A2T.json" bash scripts/record_all.sh
# The beacon script records against the labelled Beacon sample (SANDTABLE_BEACON=sample) unless told otherwise: a
# recording only replays against the same phones, so the demo must then run with the same source.
set -uo pipefail
cd "$(dirname "$0")/.."
BACKEND="${LLM_BACKEND:-claude-cli}"
PORT="${PORT:-1810}"; HUB_PORT="${HUB_PORT:-$((PORT + 100))}"
SCRIPTS="${SCRIPTS:-beacon scout mayday}"
export SANDTABLE_BEACON="${SANDTABLE_BEACON:-sample}"
SCENARIOS="${SCENARIOS:-hardware/scenarios/sandtable_rig_3A2T.json hardware/scenarios/sandtable_rig_4A1T.json}"
PY=.venv/bin/python
BASE="http://127.0.0.1:$PORT"
mkdir -p data
before=$(ls fixtures/llm_cache | wc -l)
summary=()
for sc in $SCENARIOS; do
  for script in $SCRIPTS; do
    name="record_$(basename "$sc" .json)_$script"
    log="data/$name.log"
    PORT="$PORT" HUB_PORT="$HUB_PORT" bash scripts/stop_demo.sh >/dev/null; sleep 1
    LLM_BACKEND="$BACKEND" LLM_RECORD=1 "$PY" -m sandtable.demo --scenario "$sc" --port "$PORT" --hub-port "$HUB_PORT" ${DEMO_ARGS:-} > "$log" 2>&1 &
    for i in $(seq 1 60); do                                   # wait for the board and the hub link
      curl -s -m 2 "$BASE/healthz" | "$PY" -c "import json,sys; sys.exit(0 if json.load(sys.stdin).get('hub_connected') else 1)" 2>/dev/null && break
      sleep 1
    done
    curl -s -m 5 -X POST "$BASE/api/storyboard" -H 'Content-Type: application/json' -d "{\"action\": \"start\", \"script\": \"$script\", \"mode\": \"auto\"}" > /dev/null
    sleep 5
    step="?"
    for i in $(seq 1 400); do                                  # a live model is slow: up to 20 minutes per run
      step=$(curl -s -m 3 "$BASE/api/state" | "$PY" -c "import json,sys; s=json.load(sys.stdin)['storyboard']; print(s['step'] if not s['running'] else 'running')" 2>/dev/null || echo "?")
      if [ "$step" = "done" ] || [ "$step" = "failed" ] || [ "$step" = "stopped" ]; then break; fi
      sleep 3
    done
    pre=$(curl -s -m 3 "$BASE/api/preplan" | "$PY" -c "import json,sys; print((json.load(sys.stdin) or {}).get('backend'))" 2>/dev/null)
    aar=$(curl -s -m 3 "$BASE/api/aar" | "$PY" -c "import json,sys; print((json.load(sys.stdin) or {}).get('backend'))" 2>/dev/null)
    fallbacks=$(grep -c "reflex-fallback on" "$log")
    scripted=$(curl -s -m 3 "$BASE/api/aar" | "$PY" -c "import json,sys; a=json.load(sys.stdin) or {}; print(sum(1 for e in a.get('timeline', []) if e.get('kind') == 'fallback'))" 2>/dev/null)
    line="$(basename "$sc" .json) / $script: $step; pre-plan $pre, after-action $aar; agent turns on the fallback policy: $fallbacks; exercise-script fallbacks: ${scripted:-?}"
    echo "$line"
    grep "reflex-fallback on\|timed out waiting" "$log" | sed 's/^/    /'
    summary+=("$line")
  done
done
PORT="$PORT" HUB_PORT="$HUB_PORT" bash scripts/stop_demo.sh >/dev/null
echo
echo "== summary (logs in data/record_*.log)"
printf '%s\n' "${summary[@]}"
echo "cache entries: $before before, $(ls fixtures/llm_cache | wc -l) now"
echo "next: bash run_tests.sh   (the replay tests must pass with no fallback)"
