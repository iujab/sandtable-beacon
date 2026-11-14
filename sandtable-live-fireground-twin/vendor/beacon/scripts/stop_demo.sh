#!/usr/bin/env bash
# Stops the Beacon demo: the pid in .demo.pid (from run_demo.sh --daemon), then a listener on PORT (default 1885)
# if it was started from this directory. Linux/macOS helper; on Windows close the launcher window (Ctrl+C).
cd "$(dirname "$0")/.."
PORT="${PORT:-1885}"
if [ -f .demo.pid ]; then
  pid=$(cat .demo.pid)
  if kill "$pid" 2>/dev/null; then echo "stopped pid $pid (.demo.pid)"; fi
  rm -f .demo.pid
fi
if command -v ss >/dev/null 2>&1; then
  for pid in $(ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
    # only a listener that was started from THIS directory (another copy of the module may own the port)
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || true)
    if [ -z "$cwd" ] || [ "$cwd" = "$(pwd -P)" ]; then
      kill "$pid" 2>/dev/null && echo "stopped pid $pid on port $PORT"
    else
      echo "port $PORT is held by pid $pid, started from another directory: left running"
    fi
  done
fi
exit 0
