"""Choose the scout exercise's demo scene once, at build time (never at run time).

    python scripts/choose_scout_seed.py

Runs the full scout pipeline of this tree (scene -> simulate -> self_locate -> static_echoes -> register -> find_victims ->
find_mismatches, i.e. ScoutSession with the sim engine) in the fire room for seeds 310, 311, ... and keeps the FIRST seed for
which the scan locates a breathing person within 0.6 m of the simulator's truth. Every seed tried is written down with its
outcome, so the choice is visible: the demo shows a scene where the radar works, and the honesty lives in the numbers (the
measured detection rate over many throws, results/scout_ablation.json, goes into every scan's limits).
Writes fixtures/scout/demo_scene.json; ScoutSession.deploy() uses its seed when no seed is given.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "fixtures" / "scout" / "demo_scene.json"
TOL_M, FIRST, LAST, N_PUCKS = 0.6, 310, 400, 5


def main() -> int:
    os.environ["SANDTABLE_SCOUT_ENGINE"] = "sim"
    from sandtable.adapters import build_provider
    from sandtable.scout.session import ScoutSession, fire_room
    from sandtable.spatial import Twin
    provider = build_provider()
    twin = Twin(provider.twin_raw())
    room = fire_room(twin, provider)
    tried, chosen = [], None
    for seed in range(FIRST, LAST + 1):
        s = ScoutSession(twin, provider, lambda m: None, None)
        s.pace = 0.0
        scan = asyncio.run(s.deploy(room, N_PUCKS, seed))
        if s.engine != "sim":
            print(f"[seed] the sim engine did not run ({s.fallback_reason}); nothing chosen", file=sys.stderr)
            return 1
        truth = (s.truth() or {}).get("victim") or {}
        located = [v for v in scan["victims"] if v.get("x") is not None and v.get("state") == "breathing"]
        err = min((math.dist((v["x"], v["y"]), (truth["x"], truth["y"])) for v in located), default=None) if truth else None
        row = {"seed": seed, "located": bool(located), "error_m": round(err, 2) if err is not None else None,
               "truth_bpm": truth.get("breathing_bpm"), "reported_bpm": located[0].get("breathing_bpm") if located else None}
        tried.append(row)
        print(f"[seed] {seed}: located={row['located']} error={row['error_m']}")
        if err is not None and err <= TOL_M:
            chosen = seed
            break
    if chosen is None:
        print("[seed] no seed in range met the rule", file=sys.stderr)
        return 1
    OUT.write_text(json.dumps({"schema": "sandtable.scout_demo_scene/1", "room": room, "n_pucks": N_PUCKS, "seed": chosen,
                               "rule": f"the first seed from {FIRST} upward for which the full pipeline locates a breathing person within {TOL_M} m of the simulator's truth",
                               "tried": tried, "found_on_try": len(tried), "simulated": True,
                               "note": "Chosen once at build time by scripts/choose_scout_seed.py, never at run time. The detection rate over many throws is in "
                                       "results/scout_ablation.json and in every scan's limits.",
                               "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=1) + "\n")
    print(f"[seed] chosen {chosen} after {len(tried)} tries -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
