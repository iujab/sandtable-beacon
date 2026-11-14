#!/usr/bin/env bash
# Stops whatever listens on the Sandtable app and hub ports (default 1810 and 1910). Linux helper for the bench.
PORT="${PORT:-1810}"; HUB_PORT="${HUB_PORT:-$((PORT + 100))}"
for p in "$PORT" "$HUB_PORT"; do
  for pid in $(ss -ltnpH "sport = :$p" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do kill "$pid" 2>/dev/null && echo "stopped pid $pid on port $p"; done
done
# the pid files are removed only when the process they name is gone (several instances may run on different ports)
for f in "$(dirname "$0")/../.demo.pid" "$(dirname "$0")/../data/live/demo.pid"; do
  [ -f "$f" ] && ! kill -0 "$(cat "$f" 2>/dev/null)" 2>/dev/null && rm -f "$f"
done
exit 0
