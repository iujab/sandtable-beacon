#!/usr/bin/env python3
"""Runs the exercise against a running demo and reports where each agent turn came from (replay / live / fallback).

    python scripts/check_replay.py auto [BASE] [SCRIPT]     SIM autopilot; SCRIPT = beacon (default) | scout | mayday
    python scripts/check_replay.py assist [BASE] [SCRIPT]   guided mode; this script plays the human who moves the Engine 1 token
Writes results/replay_check_<script>_<mode>_<profile>.json (data/live/results/ when the board has a live layer: sponsor-derived
runs stay private). Exit code 1 if any turn fell back to the deterministic policy.
"""
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:1810"
MODE = sys.argv[1] if len(sys.argv) > 1 else "auto"
SCRIPT = sys.argv[3] if len(sys.argv) > 3 else "beacon"
if SCRIPT not in ("beacon", "scout", "mayday"):
    sys.exit("SCRIPT must be beacon, scout or mayday")


def call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


async def main():
    events, hello = [], {}
    bundle = call("/api/bundle")

    async def listen():
        async with websockets.connect(BASE.replace("http", "ws") + "/ws", max_size=None) as ws:
            async for raw in ws:
                m = json.loads(raw)
                if m["type"] == "hello":
                    hello.update(m)
                elif m["type"] in ("agent", "radio"):
                    events.append(m)
    task = asyncio.create_task(listen())
    call("/api/reset", {})
    await asyncio.sleep(1.5)
    t0 = time.time()
    call("/api/storyboard", {"action": "start", "mode": MODE, "script": SCRIPT})
    moved = False
    while time.time() - t0 < 900:
        await asyncio.sleep(0.25)
        st = call("/api/state")
        sb = st["storyboard"]
        e1 = next(u for u in st["units"] if u["id"] == "E1")
        if MODE == "assist" and not moved and sb["step"].startswith("YOUR MOVE") and e1["route"]:
            moved = True                                         # the "human": step the token along the displayed route
            for x, y in e1["route"]["polyline"]:
                call("/api/unit/move", {"unit": "E1", "x": x, "y": y})
                await asyncio.sleep(0.45)
        if not sb["running"] and sb["step"] in ("done", "failed", "stopped"):
            break
    await asyncio.sleep(1.0)
    task.cancel()
    runs = {}
    for e in events:
        if e["type"] == "agent":
            r = runs.setdefault(e["run"], set())
            r.add(e["backend"])
    out = {"script": SCRIPT, "mode": MODE, "profile": hello.get("hub", {}).get("profile"), "simulated": hello.get("simulated"), "llm_backend": hello.get("modes", {}).get("llm"),
           "final_step": call("/api/state")["storyboard"]["step"], "phase": call("/api/state")["phase"], "wall_s": round(time.time() - t0, 1),
           "runs": {k: sorted(v) for k, v in runs.items()}, "radio_messages": sum(1 for e in events if e["type"] == "radio"),
           "fallback_runs": sorted(k for k, v in runs.items() if "reflex-fallback" in v),
           "preplan_backend": (call("/api/preplan") or {}).get("backend"), "aar_backend": (call("/api/aar") or {}).get("backend"),
           "metrics_simulated": (call("/api/aar") or {}).get("metrics"), "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    live = any((v or {}).get("mode") == "live" for k, v in (bundle.get("labels") or {}).items() if isinstance(v, dict))
    outdir = ROOT / "data" / "live" / "results" if live else ROOT / "results"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"replay_check_{SCRIPT}_{MODE}_{out['profile']}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 1 if out["fallback_runs"] or out["phase"] != "terminated" else 0

sys.exit(asyncio.run(main()))
