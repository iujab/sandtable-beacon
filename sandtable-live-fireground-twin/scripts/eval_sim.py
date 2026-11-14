#!/usr/bin/env python3
"""Evaluation on the uwb-kit SIMULATOR (no hardware): writes results/sim_eval.json.

Everything measured here is simulated: positions come from the kit's simulator (Gaussian range
noise, outliers and dropouts as declared in the scenario file and in hello.sim.noise) through the
kit's own solver, then through Sandtable's registration and map matching, exactly as in the demo.

For each rig (room scale 3 anchors + 2 tags; tabletop 4 anchors + 1 tag) it starts a private demo
(ports 1913/1914), then measures:
  A. position error in the building frame and room-level accuracy, at points spread over the floor;
  B. no-motion mayday latency (time from the crew stopping to the reflex mayday);
  C. false maydays while a crew keeps walking.
Usage: python scripts/eval_sim.py
"""
from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sandtable.spatial import Twin  # noqa: E402

PORT, HUB = 1913, 1914
BASE = f"http://127.0.0.1:{PORT}"


def call(path: str, body: dict | None = None, timeout: float = 10.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def unit(state: dict, uid: str = "E1") -> dict:
    return next(u for u in state["units"] if u["id"] == uid)


def wait(cond, timeout: float, period: float = 0.1):
    end = time.time() + timeout
    while time.time() < end:
        st = call("/api/state")
        if cond(st):
            return st
        time.sleep(period)
    return None


def run_rig(scenario: str, twin: Twin, n_mayday: int) -> dict:
    env = dict(os.environ, LLM_BACKEND="replay", SANDTABLE_REPLAY_PACE_S="0", SANDTABLE_DATA_DIR=str(ROOT / "data" / "eval"), SANDTABLE_AGENT_FALLBACK_AFTER_S="5")
    proc = subprocess.Popen([sys.executable, "-m", "sandtable.demo", "--port", str(PORT), "--hub-port", str(HUB), "--scenario", scenario],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if call("/healthz").get("hub_connected"):
                    break
            except Exception:
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("the evaluation demo did not come up")
        time.sleep(1.5)
        hub = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{HUB}/state", timeout=5).read())
        hello = hub["hello"]
        scale = call("/api/state") and json.loads(urllib.request.urlopen(BASE + "/api/bundle", timeout=5).read())
        reg_scale = None
        # A. accuracy at the centre of every room and at every third corridor node
        nodes = twin.data["graph"]["nodes"]
        targets = [n for n in nodes if n["kind"] in ("sweep", "stair") and twin.room(n["room"])["kind"] != "corridor"]
        targets += [n for i, n in enumerate(n for n in nodes if n["kind"] == "sweep" and twin.room(n["room"])["kind"] == "corridor") if i % 3 == 0]
        errs, room_ok, sigmas, per_room_kind = [], [], [], {}
        for n in targets:
            call("/api/unit/move", {"unit": "E1", "x": n["at"][0], "y": n["at"][1]})
            time.sleep(1.0)
            for _ in range(12):
                u = unit(call("/api/state"))
                e = math.hypot(u["x"] - n["at"][0], u["y"] - n["at"][1])
                errs.append(e)
                ok = u["room"] == n["room"]
                room_ok.append(ok)
                per_room_kind.setdefault(twin.room(n["room"])["kind"], []).append(ok)
                if u.get("sigma_m") is not None:
                    sigmas.append(u["sigma_m"])
                time.sleep(0.1)
        errs_sorted = sorted(errs)
        # registration scale from two anchors
        st = call("/api/state")
        # B. mayday latency
        suites = [r for r in twin.rooms("suite")][:n_mayday]
        lat, walk_false = [], 0
        for r in suites:
            call("/api/reset", {})
            time.sleep(0.6)
            call("/api/alarm", {})
            if not wait(lambda s: s["phase"] == "operations", 60):
                continue
            c = twin.room_centre(r["id"])
            door = next(d for d in twin.data["doors"] if d["rooms"][0] == r["id"])
            for p in (twin.node(door["node"])["at"], door["at"], c):          # walk in through the door
                call("/api/unit/move", {"unit": "E1", "x": p[0], "y": p[1]})
                time.sleep(0.7)
            t_stop = time.time()
            got = wait(lambda s: s["mayday"] is not None, 40, 0.05)
            if got:
                lat.append(time.time() - t_stop)
        # C. a crew that keeps walking the corridor loop for 40 s
        call("/api/reset", {})
        time.sleep(0.6)
        call("/api/alarm", {})
        wait(lambda s: s["phase"] == "operations", 60)
        ring = [n for n in nodes if n["kind"] == "sweep" and twin.room(n["room"])["kind"] == "corridor"]
        t_end, i = time.time() + 40, 0
        while time.time() < t_end:
            n = ring[i % len(ring)]
            call("/api/unit/move", {"unit": "E1", "x": n["at"][0], "y": n["at"][1]})
            time.sleep(0.9)
            i += 1
            if call("/api/state")["mayday"]:
                walk_false += 1
                break
        hello_ws = None
        try:
            import asyncio
            import websockets

            async def get_hello():
                async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
                    return json.loads(await ws.recv())
            hello_ws = asyncio.run(get_hello())
            reg_scale = hello_ws["registration"]["scale"]
        except Exception:
            pass
        return {
            "scenario": scenario, "hub_source": hello.get("source"), "simulated": hello.get("simulated"), "profile": hello.get("profile"),
            "noise_model": (hello.get("sim") or {}).get("noise") or json.loads((ROOT / scenario).read_text()).get("noise"),
            "registration_scale": reg_scale,
            "accuracy": {"points": len(targets), "samples": len(errs),
                         "error_building_m": {"mean": round(statistics.mean(errs), 2), "median": round(statistics.median(errs), 2),
                                              "p90": round(errs_sorted[int(0.9 * len(errs_sorted))], 2), "max": round(max(errs), 2)},
                         "error_rig_cm": {"median": round(100 * statistics.median(errs) / reg_scale, 1) if reg_scale else None},
                         "room_level_accuracy_pct": round(100 * sum(room_ok) / len(room_ok), 1),
                         "room_level_accuracy_pct_by_room_kind": {k: round(100 * sum(v) / len(v), 1) for k, v in sorted(per_room_kind.items())},
                         "reported_sigma_building_m_median": round(statistics.median(sigmas), 2) if sigmas else None},
            "mayday": {"threshold_s": float(os.environ.get("SANDTABLE_NO_MOTION_S", "8")), "trials": len(suites), "detected": len(lat),
                       "latency_s": {"mean": round(statistics.mean(lat), 2), "min": round(min(lat), 2), "max": round(max(lat), 2)} if lat else None,
                       "false_maydays_while_walking_40s": walk_false},
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(1.0)


def main() -> None:
    twin = Twin(json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text()))
    out = {"what": "Sandtable on the uwb-kit simulator. All numbers are SIMULATED; no UWB hardware was attached.",
           "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rigs": []}
    for scenario, n in (("hardware/scenarios/sandtable_rig_3A2T.json", 5), ("hardware/scenarios/sandtable_table_4A1T.json", 3)):
        print("evaluating", scenario, flush=True)
        res = run_rig(scenario, twin, n)
        out["rigs"].append(res)
        print(json.dumps(res["accuracy"]), json.dumps(res["mayday"]), flush=True)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "sim_eval.json").write_text(json.dumps(out, indent=1))
    print("wrote results/sim_eval.json")


if __name__ == "__main__":
    main()
