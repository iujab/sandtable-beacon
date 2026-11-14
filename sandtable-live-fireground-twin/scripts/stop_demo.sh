#!/usr/bin/env bash
# Stops whatever listens on the Sandtable app and hub ports (default 1810 and 1910). Linux helper for the bench.
PORT="${PORT:-1810}"; HUB_PORT="${HUB_PORT:-$((PORT + 100))}"
for p in "$PORT" "$HUB_PORT"; do
  for pid in $(ss -ltnpH "sport = :$p" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do kill "$pid" 2>/dev/null && echo "stopped pid $pid on port $p"; done
done
rm -f "$(dirname "$0")/../.demo.pid"
exit 0
