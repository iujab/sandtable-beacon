"""Rung 2 (link shadow) against the scout simulator (builder R1's sandtable/scout/sim.py). SIMULATED numbers.

    python hardware/eval_shadow.py          -> hardware/shadow_eval.json

For each seed: the scene as thrown (victim where the scene puts it), then the same scene with the victim moved inside
the pucks' convex hull (at a crossing of two links, and at the pucks' centroid). No baseline (pucks thrown into a room
where the person already lies): the cross-link fit. Reports detection, false alarms and position error.
"""
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandtable import config                       # noqa: E402
from sandtable.scout import scene, shadow, sim     # noqa: E402
from sandtable.spatial import Twin                 # noqa: E402

SEEDS = [310, 311, 312, 313, 314, 7, 21, 99]


def crossing(P):
    for (a, b), (c, d) in itertools.combinations(itertools.combinations(sorted(P), 2), 2):
        if len({a, b, c, d}) < 4:
            continue
        M = np.array([P[b] - P[a], P[c] - P[d]]).T
        if abs(np.linalg.det(M)) < 1e-3:
            continue
        s, u = np.linalg.solve(M, P[c] - P[a])
        if 0.2 < s < 0.8 and 0.2 < u < 0.8:
            return P[a] + s * (P[b] - P[a])
    return None


def main():
    twin = Twin(json.loads(config.TWIN_JSON.read_text()))
    rows = []
    for seed in SEEDS:
        for victim in (True, False):
            sc = scene.build_scene(twin, "r310", seed=seed, victim=victim)
            P = {p["id"]: np.array([p["x"], p["y"]]) for p in sc["pucks"]}
            Pl = {k: v.tolist() for k, v in P.items()}
            cases = [("as-thrown", None)]
            if victim:
                cases += [("in-hull-crossing", crossing(P)), ("in-hull-centroid", np.mean(list(P.values()), 0))]
            for name, xy in cases:
                if name != "as-thrown" and xy is None:
                    continue
                s2 = json.loads(json.dumps(sc))
                if xy is not None:
                    s2["victim"]["x"], s2["victim"]["y"] = float(xy[0]), float(xy[1])
                v = s2["victim"]
                r = shadow.locate(sim.simulate(s2)["ranges"], Pl, bounds=sc["polygon"])
                inh = bool(v and shadow.in_hull(Pl, v["x"], v["y"]))
                err = math.dist([r["x"], r["y"]], [v["x"], v["y"]]) if (v and r["x"] is not None) else None
                rows.append({"seed": seed, "case": name if victim else "no-victim", "victim_in_hull": inh,
                             "present": r["present"], "err_m": None if err is None else round(err, 2),
                             "sigma_m": r["sigma_m"], "confidence": r["confidence"], "links_down": len(r["links_down"])})
    inh = [r for r in rows if r["victim_in_hull"]]
    outh = [r for r in rows if r["case"] == "as-thrown" and not r["victim_in_hull"]]
    none = [r for r in rows if r["case"] == "no-victim"]
    errs = sorted(r["err_m"] for r in inh if r["err_m"] is not None)
    out = {"simulated": True, "what": "rung 2 link-shadow locator (no baseline: cross-link fit) on builder R1's scout "
                                      "simulator, Suite 310, 5 pucks; victims moved into the hull for the in-hull cases",
           "noise": "R1 sim noise model (sandtable/scout/sim.py NOISE: knife-edge body shadow, rssi/fp/los as the kit)",
           "victim_in_hull": {"n": len(inh), "detected": sum(r["present"] for r in inh),
                              "err_median_m": float(np.median(errs)) if errs else None,
                              "err_p90_m": float(np.percentile(errs, 90)) if errs else None,
                              "err_max_m": max(errs) if errs else None},
           "victim_outside_hull_as_thrown": {"n": len(outh), "reported_present": sum(r["present"] for r in outh)},
           "no_victim": {"n": len(none), "false_alarms": sum(r["present"] for r in none)},
           "rows": rows}
    (ROOT / "hardware" / "shadow_eval.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
