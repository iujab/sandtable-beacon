"""Scout radar evaluation: how many thrown pucks it takes to find a breathing person. Everything is SIMULATED.

    .venv/bin/python scripts/eval_scout.py              # full run (about 4 min on 4 cores), then figures and doc blocks
    .venv/bin/python scripts/eval_scout.py --render     # figures and doc blocks again from the existing result files
    .venv/bin/python scripts/eval_scout.py --seeds 3 --no-write   # smoke test

For N = 1..5 pucks and every seed, in the exercise's fire room (chosen by `sandtable.scout.session.fire_room`, the
same rule as `Director._fire_room()`; nothing about the room is hard-coded here), the pipeline the live session runs
(`ScoutSession._run_sim`): scene -> simulate -> self_locate -> static_echoes -> register into the twin ->
find_victims inside the room polygon -> find_mismatches, plus the link-shadow fallback (`shadow.locate`) on the same
ranges. Four scenes per seed and N:

    reach    a breathing victim kept within `victim_reach_m` (the scene default) of the nearest puck
    natural  a breathing victim anywhere in the far part of the room (`victim_reach_m=None`): the honest field case
    empty    no victim (false alarms)
    still    a victim who neither breathes nor moves (must not be found; the method's stated limit)

Leakage guard: the simulator's ground truth is popped off the recording before any DSP, registration or mismatch call
and the scene dict is never passed to them, so a module that read `rec["truth"]` would fail here. The script also
records every source line of dsp/register/mismatch/shadow that mentions "truth".

Outputs: results/scout_ablation.json, results/scout_placement.json, figures/scout_ablation.png,
figures/scout_placement.png, figures/scout_example.png, the table block in docs/readme_radar_section.md and the
"Simulated evidence" section at the end of docs/RADAR_FEASIBILITY.md (both between eval_scout markers; every number
in them is read from the result files by this script).

Seeds 1000-1059 are disjoint from the seeds the engine was developed on (300-315 and 310, RADAR_NOTES.md).
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):     # one BLAS thread per worker process
    os.environ.setdefault(_v, "1")

import argparse                                                                # noqa: E402
import inspect                                                                 # noqa: E402
import json                                                                    # noqa: E402
import math                                                                    # noqa: E402
import sys                                                                     # noqa: E402
import time                                                                    # noqa: E402
from concurrent.futures import ProcessPoolExecutor                             # noqa: E402
from datetime import datetime                                                  # noqa: E402
from pathlib import Path                                                       # noqa: E402

import numpy as np                                                             # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandtable.scout import dsp, geometry, mismatch, register, scene, shadow, sim   # noqa: E402
from sandtable.scout import session as scout_session                                # noqa: E402

RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
DOCS = ROOT / "docs"
ABLATION_JSON = RESULTS / "scout_ablation.json"
PLACEMENT_JSON = RESULTS / "scout_placement.json"
COMMAND = ".venv/bin/python scripts/eval_scout.py"

SEED0 = 1000
N_SEEDS = 60
DEV_SEEDS = list(range(300, 316))
PUCKS = (1, 2, 3, 4, 5)
REACH_DEFAULT = inspect.signature(scene.build_scene).parameters["victim_reach_m"].default
CONDITIONS = {
    "reach": {"victim": True, "victim_state": "breathing", "victim_reach_m": REACH_DEFAULT},
    "natural": {"victim": True, "victim_state": "breathing", "victim_reach_m": None},
    "empty": {"victim": False},
    "still": {"victim": True, "victim_state": "still", "victim_reach_m": REACH_DEFAULT},
}
NEAR_M = (0.6, 1.0)          # "found" radii for the victim
DEBRIS_HIT_M = 0.8           # a mismatch flag within this of the true debris counts as finding it
VICTIM_FLAG_M = 1.0          # an unexpected-reflector flag within this of the victim is the victim, not debris
WRONG_POSE_M = 0.5           # registered pucks off by more than this RMS: the wrong pose (a correct one is cm)
RANGE_OK_M = geometry.M_PER_TAP   # two pucks: the reported echo path is "right" within one CIR tap


# ------------------------------------------------------------------------------------------------ small statistics
def wilson(k: int, n: int, z: float = 1.959964) -> list:
    """Wilson score 95% interval for k successes out of n."""
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


def rate(k: int, n: int) -> dict:
    return {"k": int(k), "n": int(n), "rate": None if n == 0 else round(k / n, 3), "ci95": wilson(k, n)}


def qstats(xs) -> dict | None:
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return None
    a = np.asarray(xs)
    return {"n": len(xs), "median": round(float(np.median(a)), 3), "p90": round(float(np.percentile(a, 90)), 3),
            "max": round(float(a.max()), 3)}


def hull_area(pts: np.ndarray) -> float:
    p = sorted(map(tuple, pts.tolist()))
    if len(p) < 3:
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for q in p:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(p):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], q) <= 0:
            hi.pop()
        hi.append(q)
    h = np.array(lo[:-1] + hi[:-1])
    if len(h) < 3:
        return 0.0
    x, y = h[:, 0], h[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def procrustes_rms(A: np.ndarray, B: np.ndarray) -> float:
    """RMS distance between A and B after the best rotation, mirror and offset (no scale): the self-location error."""
    A0, B0 = A - A.mean(0), B - B.mean(0)
    U, _, Vt = np.linalg.svd(A0.T @ B0)
    return float(np.sqrt(np.mean(np.sum((A0 @ (U @ Vt) - B0) ** 2, axis=1))))


# ------------------------------------------------------------------------------------------------ the world
_W: dict = {}


def world() -> dict:
    """The twin, the fire room and its door, read exactly as the live session reads them."""
    if not _W:
        from sandtable.adapters import build_provider
        from sandtable.spatial import Twin
        prov = build_provider()
        twin = Twin(prov.twin_raw())
        room = scout_session.fire_room(twin, prov)
        r = twin.room(room)
        door = scout_session.room_door(twin, room)
        _W.update(twin=twin, room=room, label=r["label"], poly=[[float(x), float(y)] for x, y in r["polygon"]],
                  door=[float(v) for v in door["at"]] if door else None, door_id=door["id"] if door else None,
                  height_m=float(r.get("height_m") or 0.0) or None)
    return _W


def run_pipeline(n: int, seed: int, cond: str):
    """One throw through the live pipeline. Returns (scene truth, recording without truth, pipeline outputs)."""
    w = world()
    sc = scene.build_scene(w["twin"], w["room"], n, seed, **CONDITIONS[cond])
    rec = sim.simulate(sc)
    rec.pop("truth", None)                               # leakage guard: nothing below can see the ground truth
    loc = dsp.self_locate(rec)
    echoes = dsp.static_echoes(rec)
    reg = register.register(loc["pucks_rel"], echoes, {"polygon": w["poly"], "door": w["door"]})
    pxy = {k: [float(v[0]), float(v[1])] for k, v in reg["pucks"].items()}
    vic = dsp.find_victims(rec, pxy, None, w["poly"])
    mm = mismatch.find_mismatches(pxy, echoes, w["poly"])
    known = [{"at": [v["x"], v["y"]]} for v in vic.get("victims", []) if v.get("x") is not None]
    mm_known = mismatch.find_mismatches(pxy, echoes, w["poly"], known=known) if known else mm
    sh = shadow.locate(rec["ranges"], pxy, bounds=w["poly"]) if n >= 2 else None
    return sc, rec, {"loc": loc, "echoes": echoes, "reg": reg, "pxy": pxy, "vic": vic, "mm": mm, "mm_known": mm_known, "sh": sh}


def evaluate(task) -> dict:
    n, seed, cond = task
    t0 = time.perf_counter()
    sc, rec, out = run_pipeline(n, seed, cond)
    ids = [p["id"] for p in sc["pucks"]]
    P = np.array([[p["x"], p["y"]] for p in sc["pucks"]], float)
    truth_xy = {p["id"]: (p["x"], p["y"]) for p in sc["pucks"]}
    v = sc.get("victim")
    row: dict = {"n": n, "seed": seed, "cond": cond}

    # geometry of the throw (truth)
    sv = np.linalg.svd(P - P.mean(0), compute_uv=False) if n >= 2 else np.array([0.0, 0.0])
    row["hull_m2"] = round(hull_area(P), 2)
    row["flat"] = round(float(sv[1] / sv[0]), 3) if n >= 3 and sv[0] > 0 else None     # 0 = collinear, 1 = round
    door = world()["door"]
    row["deepest_puck_m"] = round(max(math.dist(q, door) for q in P.tolist()), 2) if door else None
    if v:
        vxy = (v["x"], v["y"])
        row["victim"] = [round(v["x"], 3), round(v["y"], 3)]
        row["d_nearest_m"] = round(min(math.dist(vxy, q) for q in P.tolist()), 2)
        row["d_centroid_m"] = round(math.dist(vxy, P.mean(0).tolist()), 2)
        row["in_hull"] = bool(n >= 3 and shadow.in_hull(truth_xy, *vxy))
        row["bpm_true"] = v.get("breathing_bpm")

    # self-location and registration
    loc, reg, pxy = out["loc"], out["reg"], out["pxy"]
    if n >= 2:
        rel = np.array([loc["pucks_rel"][k] for k in ids])
        row["selfloc_resid_m"] = round(float(loc["rms_m"]), 4)
        row["selfloc_err_m"] = round(procrustes_rms(rel, P), 4)
        errs = [math.dist(pxy[k], truth_xy[k]) for k in ids]
        row["reg"] = {"method": reg["method"], "ok": bool(reg["ok"]), "confident": bool(reg.get("confident")),
                      "ambiguity": reg.get("ambiguity"), "door_decided": bool(reg.get("door_decided")),
                      "rms_m": round(float(np.sqrt(np.mean(np.square(errs)))), 3),
                      "median_m": round(float(np.median(errs)), 3), "worst_m": round(max(errs), 3),
                      "matched": reg.get("matched")}
        row["n_echoes"] = len(out["echoes"])

    # the victim
    vic = out["vic"]
    vs = vic.get("victims") or []
    row["why"] = vic.get("why")
    rep = None
    if vs:
        rep = vs[0]
        row["reported"] = True
        row["rep"] = {k: rep.get(k) for k in ("x", "y", "sigma_m", "confidence", "n_links", "state", "breathing_bpm",
                                              "breathing_conf", "excess_m") if k in rep}
    else:
        row["reported"] = False
    if v and rep is not None:
        if rep.get("x") is not None:
            e = math.dist((rep["x"], rep["y"]), (v["x"], v["y"]))
            row["err_m"] = round(e, 3)
            row["within_2sigma"] = bool(rep.get("sigma_m") and e <= 2 * float(rep["sigma_m"]))
        elif rep.get("excess_m") is not None and n == 2:      # two pucks: the echo's extra path, no position
            ex_true = float(geometry.bistatic_excess(v["x"], v["y"], P[0], P[1]))
            row["excess_err_m"] = round(abs(float(rep["excess_m"]) - ex_true), 3)
        if rep.get("breathing_bpm") is not None and v.get("breathing_bpm"):
            row["bpm_err"] = round(abs(float(rep["breathing_bpm"]) - float(v["breathing_bpm"])), 2)

    # debris and other mismatch flags (as the session calls it; and with the located victim passed as known)
    deb = [c["at"] for c in sc.get("changes", []) if c.get("kind") == "debris"]
    furn = [c["at"] for c in sc.get("clutter", [])]
    for key, mm in (("mm", out["mm"]), ("mm_known", out["mm_known"])):
        refl = [m for m in mm if m["kind"] == "unexpected_reflector"]
        hit = any(deb and math.dist(m["where"]["at"], deb[0]) <= DEBRIS_HIT_M for m in refl)
        false = [m for m in refl if not (deb and math.dist(m["where"]["at"], deb[0]) <= DEBRIS_HIT_M)]
        on_victim = [m for m in false if v and math.dist(m["where"]["at"], (v["x"], v["y"])) <= VICTIM_FLAG_M]
        rest = [m for m in false if m not in on_victim]
        on_furn = [m for m in rest if any(math.dist(m["where"]["at"], f) <= DEBRIS_HIT_M for f in furn)]
        row[key] = {"debris_hit": bool(hit), "false_reflectors": len(false), "on_victim": len(on_victim),
                    "on_furniture": len(on_furn), "ghost": len(rest) - len(on_furn),
                    "missing_wall": sum(1 for m in mm if m["kind"] == "missing_wall")}

    # link-shadow fallback on the same ranges and registered pucks (no baseline: thrown into an occupied room)
    sh = out["sh"]
    if sh is not None:
        row["shadow"] = {"present": bool(sh["present"]), "located": sh.get("x") is not None,
                         "links_down": len(sh.get("links_down") or []), "confidence": sh.get("confidence")}
        if v and sh.get("x") is not None:
            row["shadow"]["err_m"] = round(math.dist((sh["x"], sh["y"]), (v["x"], v["y"])), 3)
    row["runtime_s"] = round(time.perf_counter() - t0, 2)
    return row


# ------------------------------------------------------------------------------------------------ aggregation
def summarise(rows: list[dict]) -> dict:
    by = {}
    for r in rows:
        by.setdefault((r["n"], r["cond"]), []).append(r)
    per_n = {}
    for n in PUCKS:
        d: dict = {"pucks": n, "links": n * (n - 1) // 2}
        for cond in CONDITIONS:
            rs = by.get((n, cond), [])
            m = len(rs)
            rep = [r for r in rs if r["reported"]]
            c: dict = {"scans": m, "reported": rate(len(rep), m)}
            if cond in ("reach", "natural"):
                c["within_0.6m"] = rate(sum(1 for r in rs if (r.get("err_m") or 9e9) <= NEAR_M[0]), m)
                c["within_1.0m"] = rate(sum(1 for r in rs if (r.get("err_m") or 9e9) <= NEAR_M[1]), m)
                c["error_m"] = qstats(r.get("err_m") for r in rep)
                c["error_within_2sigma"] = rate(sum(1 for r in rep if r.get("within_2sigma")), sum(1 for r in rep if "err_m" in r))
                c["bpm_error"] = qstats(r.get("bpm_err") for r in rep)
                c["state_moving"] = sum(1 for r in rep if (r.get("rep") or {}).get("state") == "moving")
                if n == 2:
                    c["range_right"] = rate(sum(1 for r in rep if (r.get("excess_err_m") is not None and r["excess_err_m"] <= RANGE_OK_M)), len(rep))
                c["victim_d_nearest_m"] = qstats(r.get("d_nearest_m") for r in rs)
                c["reported_by_in_hull"] = {"in_hull": rate(sum(1 for r in rs if r.get("in_hull") and r["reported"]), sum(1 for r in rs if r.get("in_hull"))),
                                            "outside": rate(sum(1 for r in rs if not r.get("in_hull") and r["reported"]), sum(1 for r in rs if not r.get("in_hull")))}
                if n >= 2:
                    c["reported_with_wrong_registration"] = sum(1 for r in rep if r["reg"]["rms_m"] > WRONG_POSE_M)
            if cond == "still":
                c["within_1.0m_of_the_still_victim"] = rate(sum(1 for r in rs if (r.get("err_m") or 9e9) <= 1.0), m)
                c["error_m"] = qstats(r.get("err_m") for r in rep)
            if n >= 2:
                shp = [r for r in rs if r["shadow"]["present"]]
                c["shadow"] = {"present": rate(len(shp), m), "located": rate(sum(1 for r in rs if r["shadow"]["located"]), m)}
                if cond in ("reach", "natural"):
                    c["shadow"]["error_m"] = qstats(r["shadow"].get("err_m") for r in rs)
                    c["shadow"]["within_1.0m"] = rate(sum(1 for r in rs if (r["shadow"].get("err_m") or 9e9) <= 1.0), m)
                    inh = [r for r in rs if r.get("in_hull")]
                    c["shadow"]["present_when_victim_in_hull"] = rate(sum(1 for r in inh if r["shadow"]["present"]), len(inh))
            d[cond] = c
        allr = [r for cond in CONDITIONS for r in by.get((n, cond), [])]
        if n >= 2:
            regs = [r["reg"] for r in allr]
            wrong = [g for g in regs if g["rms_m"] > WRONG_POSE_M]
            d["self_location"] = {"scans": len(allr), "residual_m": qstats(r["selfloc_resid_m"] for r in allr),
                                  "error_after_alignment_m": qstats(r["selfloc_err_m"] for r in allr)}
            d["registration"] = {
                "scans": len(regs), "pooled_over": list(CONDITIONS),
                "methods": {mth: sum(1 for g in regs if g["method"] == mth) for mth in sorted({g["method"] for g in regs})},
                "puck_rms_m": qstats(g["rms_m"] for g in regs), "puck_median_m": qstats(g["median_m"] for g in regs),
                "worst_puck_m": round(max(g["worst_m"] for g in regs), 3),
                "confident": rate(sum(g["confident"] for g in regs), len(regs)),
                "ambiguous_ge_0.6": rate(sum(1 for g in regs if (g["ambiguity"] or 0) >= 0.6), len(regs)),
                "cannot_tell_apart_ge_0.85": rate(sum(1 for g in regs if (g["ambiguity"] or 0) >= 0.85), len(regs)),
                "door_decided": rate(sum(g["door_decided"] for g in regs), len(regs)),
                "wrong_pose": rate(len(wrong), len(regs)),
                "wrong_pose_marked_confident": sum(1 for g in wrong if g["confident"]),
                "puck_rms_m_when_right": qstats(g["rms_m"] for g in regs if g["rms_m"] <= WRONG_POSE_M),
                "puck_rms_m_when_confident": qstats(g["rms_m"] for g in regs if g["confident"]),
            }
            for key in ("mm", "mm_known"):
                d["debris" if key == "mm" else "debris_with_victim_known"] = {
                    "scans": len(allr), "pooled_over": list(CONDITIONS),
                    "flagged_within_0.8m": rate(sum(r[key]["debris_hit"] for r in allr), len(allr)),
                    "false_reflector_flags_per_scan": round(sum(r[key]["false_reflectors"] for r in allr) / max(len(allr), 1), 3),
                    "false_reflector_flags": sum(r[key]["false_reflectors"] for r in allr),
                    "flags_on_the_victim": sum(r[key]["on_victim"] for r in allr),
                    "flags_on_furniture": sum(r[key]["on_furniture"] for r in allr),
                    "ghost_flags": sum(r[key]["ghost"] for r in allr),
                    "ghost_flags_per_scan": round(sum(r[key]["ghost"] for r in allr) / max(len(allr), 1), 3),
                    "missing_wall_flags": sum(r[key]["missing_wall"] for r in allr)}
        d["why_examples"] = sorted({r["why"] for r in allr if r.get("why") and n <= 2})[:3]
        per_n[str(n)] = d
    return per_n


def placement(rows: list[dict]) -> dict:
    """Detection and error against where the victim lies relative to the pucks (breathing scenes, both placements)."""
    def bins(rs, key, edges, labels):
        out = []
        for (lo, hi), lab in zip(zip(edges[:-1], edges[1:]), labels):
            sub = [r for r in rs if r.get(key) is not None and lo <= r[key] < hi]
            out.append({"bin": lab, "lo": lo, "hi": None if hi == math.inf else hi, "scans": len(sub),
                        "reported": rate(sum(r["reported"] for r in sub), len(sub)),
                        "within_1.0m": rate(sum(1 for r in sub if (r.get("err_m") or 9e9) <= 1.0), len(sub)),
                        "error_m": qstats(r.get("err_m") for r in sub if r["reported"])})
        return out

    out = {}
    dn_edges = [0.0, 2.0, 3.0, 4.0, 5.0, 6.0, math.inf]
    dn_labels = ["< 2 m", "2-3 m", "3-4 m", "4-5 m", "5-6 m", ">= 6 m"]
    dc_edges = [0.0, 2.0, 3.0, 4.0, 5.0, 6.0, math.inf]
    for n in (3, 4, 5):
        # a 'natural' scene whose victim landed where the 'reach' scene of the same seed put it is the same scan: count it once
        seen, rs = set(), []
        for r in rows:
            if r["n"] == n and r["cond"] in ("reach", "natural"):
                key = (r["seed"], tuple(r["victim"]))
                if key not in seen:
                    seen.add(key)
                    rs.append(r)
        ha = sorted(r["hull_m2"] for r in rs)
        t1, t2 = (float(np.percentile(ha, 100 / 3)), float(np.percentile(ha, 200 / 3))) if ha else (0, 0)
        fl = [r for r in rs if r.get("flat") is not None]
        out[str(n)] = {
            "scans": len(rs),
            "by_distance_to_nearest_puck": bins(rs, "d_nearest_m", dn_edges, dn_labels),
            "by_distance_to_puck_centroid": bins(rs, "d_centroid_m", dc_edges, dn_labels),
            "by_hull_area": bins(rs, "hull_m2", [0.0, round(t1, 2), round(t2, 2), math.inf],
                                 [f"< {t1:.1f} m2 (smallest third)", f"{t1:.1f}-{t2:.1f} m2", f">= {t2:.1f} m2 (largest third)"]),
            "by_flatness": bins(fl, "flat", [0.0, 0.35, 0.6, math.inf], ["< 0.35 (near-collinear)", "0.35-0.6", ">= 0.6 (round)"]),
            "by_victim_in_hull": [{"bin": lab, "scans": len(sub), "reported": rate(sum(r["reported"] for r in sub), len(sub)),
                                   "error_m": qstats(r.get("err_m") for r in sub if r["reported"])}
                                  for lab, sub in (("inside the pucks' hull", [r for r in rs if r.get("in_hull")]),
                                                   ("outside", [r for r in rs if not r.get("in_hull")]))],
            "d_nearest_of_reported": qstats(r["d_nearest_m"] for r in rs if r["reported"]),
            "d_nearest_of_missed": qstats(r["d_nearest_m"] for r in rs if not r["reported"]),
            "flatness_range": [min(r["flat"] for r in fl), max(r["flat"] for r in fl)] if fl else None,
            "duplicates_dropped": sum(1 for r in rows if r["n"] == n and r["cond"] in ("reach", "natural")) - len(rs),
        }
        near = [r for r in rs if r["d_nearest_m"] < 4.0]
        far = [r for r in rs if r["d_nearest_m"] >= 4.0]
        out[str(n)]["nearest_puck_under_4m"] = rate(sum(r["reported"] for r in near), len(near))
        out[str(n)]["nearest_puck_4m_or_more"] = rate(sum(r["reported"] for r in far), len(far))
    f5 = out["5"]
    a, b = f5["nearest_puck_under_4m"], f5["nearest_puck_4m_or_more"]
    if not a["n"] or not b["n"]:
        return {"crew_advice": None, "per_pucks": out}
    out_adv = (f"Throw so that one puck lands deep, within about 4 m of the far corners: with 5 pucks the victim was reported "
               f"in {a['k']} of {a['n']} scans ({a['rate'] * 100:.0f}%) when a puck lay within 4 m of them and in {b['k']} of "
               f"{b['n']} ({b['rate'] * 100:.0f}%) beyond, while the spread and roundness of the constellation made no clear "
               f"difference.")
    return {"crew_advice": out_adv, "per_pucks": out}


def leakage_audit() -> dict:
    """Source lines of the detector modules that mention the ground truth (the runtime guard is the pop above)."""
    out = {}
    for mod in (dsp, register, mismatch, shadow):
        src = Path(mod.__file__).read_text(encoding="utf-8").splitlines()
        out[Path(mod.__file__).name] = [f"{i + 1}: {ln.strip()}" for i, ln in enumerate(src) if "truth" in ln.lower()]
    return {"runtime_guard": "rec.pop('truth') before self_locate, static_echoes, register, find_victims, find_mismatches "
                             "and shadow.locate; the scene dict is never passed to them",
            "source_lines_mentioning_truth": out}


def run(seeds: list[int], workers: int) -> tuple[dict, dict]:
    w = world()
    tasks = [(n, s, c) for n in sorted(PUCKS, reverse=True) for c in CONDITIONS for s in seeds]
    dev_tasks = [(n, s, "reach") for n in (5, 4) for s in DEV_SEEDS]
    t0 = time.time()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, initializer=world) as ex:
            rows = list(ex.map(evaluate, tasks + dev_tasks, chunksize=2))
    else:
        rows = [evaluate(t) for t in tasks + dev_tasks]
    dev_rows, rows = rows[len(tasks):], rows[:len(tasks)]
    wall = time.time() - t0
    dev = {str(n): {"reported": rate(sum(r["reported"] for r in dev_rows if r["n"] == n), sum(1 for r in dev_rows if r["n"] == n)),
                    "within_0.6m": rate(sum(1 for r in dev_rows if r["n"] == n and (r.get("err_m") or 9e9) <= 0.6),
                                        sum(1 for r in dev_rows if r["n"] == n))} for n in (5, 4)}
    rows.sort(key=lambda r: (r["n"], list(CONDITIONS).index(r["cond"]), r["seed"]))
    noise = dict(sim.DEFAULT_NOISE)
    head = {
        "simulated": True,
        "what": ("Scout multistatic UWB radar, end to end on the simulator of this tree (sandtable/scout/sim.py, adapted from "
                 "lane L08's tagless-uwb-echo-radar, L08's noise model): scene -> simulate -> self_locate -> static_echoes -> "
                 "register into the twin -> find_victims -> find_mismatches, plus shadow.locate on the same ranges. "
                 "The simulator was written alongside the detector; real boards will do worse."),
        "room": {"id": w["room"], "label": w["label"], "polygon": w["poly"], "door": w["door"], "door_id": w["door_id"],
                 "chosen_by": "sandtable.scout.session.fire_room (same rule as Director._fire_room)"},
        "seeds": [seeds[0], seeds[-1]] if seeds else [], "n_seeds": len(seeds),
        "seeds_note": "disjoint from the development seeds 300-315 (RADAR_NOTES.md)",
        "conditions": {k: {kk: vv for kk, vv in v.items()} for k, v in CONDITIONS.items()},
        "scan_s": scene.DURATION_S, "frame_hz": scene.FRAME_HZ, "noise_model": noise,
        "definitions": {
            "reported": "find_victims returned a victim (2 pucks: presence only, no position)",
            "within_X": "reported and within X m of the true victim, in the twin frame (registration errors included)",
            "error_m": "distance from the reported to the true victim among reported scans (median, 90th percentile, max)",
            "bpm_error": "|reported - true| breaths per minute among reported scans",
            "range_right": f"two pucks: reported extra path within one tap ({RANGE_OK_M:.3f} m) of the true one",
            "self_location.error_after_alignment_m": "RMS puck error of self_locate after the best rotation, mirror and offset",
            "registration.puck_rms_m": "RMS distance of the registered pucks from the true pucks in the twin frame, per scan",
            "wrong_pose": f"registration puck RMS above {WRONG_POSE_M} m (a mirrored or turned constellation)",
            "confident": "register()['confident'] (ambiguity < 0.7 and 5 or more echoes matched)",
            "ambiguous_ge_0.6": "ambiguity >= 0.6: the session adds an 'ambiguous registration' limit to the scan",
            "door_decided": "the echoes fit another pose about as well and the door prior chose",
            "debris.flagged_within_0.8m": f"an unexpected_reflector flag within {DEBRIS_HIT_M} m of the true debris",
            "debris.false_reflector_flags": f"unexpected_reflector flags farther than {DEBRIS_HIT_M} m from the debris "
                                            f"(flags_on_the_victim: of those, within {VICTIM_FLAG_M} m of the victim)",
            "debris_with_victim_known": "the same with the located victim passed to find_mismatches(known=...); the live "
                                        "session does not pass it",
            "shadow": "shadow.locate on the same ranges with the registered pucks, no baseline (cross-link fit)",
            "ci95": "Wilson score interval",
        },
        "runtime_s": round(wall, 1), "workers": workers,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": COMMAND,
    }
    audit = leakage_audit()
    audit["development_seed_check"] = {
        "what": "the same pipeline on the development seeds 300-315 ('reach' scene), to compare with the ad-hoc numbers in "
                "sandtable/scout/RADAR_NOTES.md (5 pucks: 9/16 reported, 8/16 within 0.6 m)", "seeds": [DEV_SEEDS[0], DEV_SEEDS[-1]],
        "per_pucks": dev}
    still = [r for r in rows if r["cond"] == "still" and r["n"] >= 3]
    audit["still_victim_check"] = {
        "what": "a victim who neither breathes nor moves, same placement rule as 'reach': a detector that knew the position "
                "would find them; this one must not", "scans_3_to_5_pucks": len(still),
        "reported_anywhere": sum(r["reported"] for r in still),
        "reported_within_1m_of_the_still_victim": sum(1 for r in still if (r.get("err_m") or 9e9) <= 1.0)}
    pl = placement(rows)
    abl = {"schema": "sandtable.scout_ablation/1", **head, "leakage_audit": audit,
           "per_pucks": summarise(rows), "rows": rows}
    plc = {"schema": "sandtable.scout_placement/1", "simulated": True, "crew_advice": pl["crew_advice"],
           "what": "Detection and location error against where the breathing victim lies relative to the thrown pucks. "
                   "Pools the 'reach' and 'natural' scenes of scout_ablation.json (the detector does not know which rule "
                   "placed the victim). Distances use the true puck positions.",
           "room": head["room"], "seeds": head["seeds"], "n_seeds": head["n_seeds"], "generated": head["generated"],
           "command": COMMAND, "per_pucks": pl["per_pucks"],
           "note": "The scene generator never put the victim inside the convex hull of the pucks (victim in the far part of "
                   "the room, pucks scattered from the door), so 'by_victim_in_hull' has no inside cases and the link-shadow "
                   "rung, which only sees a body on a puck-to-puck line, cannot fire in these scenes. Flatness = second / first "
                   "singular value of the centred puck positions (scene.py rejects throws below 0.18)."}
    return abl, plc


# ------------------------------------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=N_SEEDS)
    ap.add_argument("--seed0", type=int, default=SEED0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--render", action="store_true", help="only redraw figures and doc blocks from the result files")
    ap.add_argument("--no-write", action="store_true", help="print the summary, write nothing")
    a = ap.parse_args(argv)
    if not a.render:
        abl, plc = run(list(range(a.seed0, a.seed0 + a.seeds)), a.workers)
        if a.no_write:
            print(json.dumps({k: abl["per_pucks"][k] for k in abl["per_pucks"]}, indent=1)[:20000])
            print("runtime", abl["runtime_s"])
            return
        RESULTS.mkdir(exist_ok=True)
        rows = abl.pop("rows")                     # one compact line per scan keeps the file small and diffable
        head = json.dumps(abl, indent=1)
        ABLATION_JSON.write_text(head[:-2] + ',\n "rows": [\n' + ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows)
                                 + "\n ]\n}\n", encoding="utf-8")
        PLACEMENT_JSON.write_text(json.dumps(plc, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {ABLATION_JSON.relative_to(ROOT)} and {PLACEMENT_JSON.relative_to(ROOT)} in {abl['runtime_s']} s")
    render()


# ------------------------------------------------------------------------------------------------ figures (Pillow)
SS = 2                                                   # supersampling: draw at 2x, downsample (smooth lines)
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#fcfcfb"
BLUE, ORANGE, AQUA, RED = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"
BLUES = {3: "#a3c4ec", 4: "#5b97e0", 5: "#1d5bab"}


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "C:/Windows/Fonts"):
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size * SS)
    try:
        return ImageFont.truetype(name, size * SS)
    except OSError:
        return ImageFont.load_default()


class Canvas:
    def __init__(self, w: int, h: int):
        from PIL import Image, ImageDraw
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * SS, h * SS), SURF)
        self.d = ImageDraw.Draw(self.img)

    def text(self, xy, s, size=15, fill=INK, bold=False, anchor="la"):
        self.d.text((xy[0] * SS, xy[1] * SS), s, font=_font(size, bold), fill=fill, anchor=anchor)

    def line(self, pts, fill=INK, width=2.0):
        self.d.line([(x * SS, y * SS) for x, y in pts], fill=fill, width=max(1, int(round(width * SS))), joint="curve")

    def circle(self, xy, r, fill=None, outline=None, width=2.0):
        x, y = xy
        self.d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=fill, outline=outline,
                       width=max(1, int(round(width * SS))))

    def rect(self, box, fill=None, outline=None, width=1.0):
        self.d.rectangle([v * SS for v in box], fill=fill, outline=outline, width=max(1, int(round(width * SS))) if outline else 0)

    def save(self, path: Path):
        from PIL import Image
        self.img.resize((self.w, self.h), Image.LANCZOS).save(path, optimize=True)


class Axes:
    def __init__(self, cv: Canvas, box, xlim, ylim):
        self.cv, (self.x0, self.y0, self.x1, self.y1), self.xlim, self.ylim = cv, box, xlim, ylim

    def X(self, v):
        return self.x0 + (v - self.xlim[0]) / (self.xlim[1] - self.xlim[0]) * (self.x1 - self.x0)

    def Y(self, v):
        return self.y1 - (v - self.ylim[0]) / (self.ylim[1] - self.ylim[0]) * (self.y1 - self.y0)

    def frame(self, yticks, yfmt, xticks, xlabels, ylabel=None, xlabel=None):
        cv = self.cv
        for t in yticks:
            cv.line([(self.x0, self.Y(t)), (self.x1, self.Y(t))], fill=GRID, width=1)
            cv.text((self.x0 - 8, self.Y(t)), yfmt(t), size=13, fill=INK2, anchor="rm")
        cv.line([(self.x0, self.y1), (self.x1, self.y1)], fill=MUTED, width=1.2)
        for t, lab in zip(xticks, xlabels):
            cv.text((self.X(t), self.y1 + 10), lab, size=13, fill=INK2, anchor="ma")
        if ylabel:
            cv.text((self.x0 - 8, self.y0 - 22), ylabel, size=13, fill=INK2, anchor="la")
        if xlabel:
            cv.text(((self.x0 + self.x1) / 2, self.y1 + 34), xlabel, size=13, fill=INK2, anchor="ma")

    def series(self, xs, ys, lo=None, hi=None, color=BLUE, hollow=False, r=5.5, join=True):
        cv = self.cv
        pts = [(self.X(x), self.Y(y)) for x, y in zip(xs, ys) if y is not None]
        if join and len(pts) > 1:
            cv.line(pts, fill=color, width=2)
        for i, (x, y) in enumerate(zip(xs, ys)):
            if y is None:
                continue
            if lo is not None and lo[i] is not None:
                cx = self.X(x)
                cv.line([(cx, self.Y(lo[i])), (cx, self.Y(hi[i]))], fill=color, width=1.6)
                for v in (lo[i], hi[i]):
                    cv.line([(cx - 4, self.Y(v)), (cx + 4, self.Y(v))], fill=color, width=1.6)
            cv.circle((self.X(x), self.Y(y)), r, fill=SURF if hollow else color, outline=SURF if not hollow else color,
                      width=2 if hollow else 1.5)


def _pct(t):
    return f"{t * 100:.0f}%"


def fig_ablation(abl: dict, path: Path):
    P = abl["per_pucks"]
    cv = Canvas(1400, 640)
    room = abl["room"]["label"]
    cv.text((40, 26), "How many thrown pucks it takes to find a breathing person", size=22, bold=True)
    cv.text((40, 60), f"SIMULATED: {room}, {abl['n_seeds']} seeds per point, {abl['scan_s']:g} s scan, lane L08's noise model. "
                      "Bars: Wilson 95% interval.", size=14, fill=INK2)
    # legend
    lx = 40
    for col, lab in ((BLUE, f"victim within {REACH_DEFAULT:g} m of a puck"), (ORANGE, "natural placement: anywhere in the far part of the room")):
        cv.circle((lx + 6, 96), 6, fill=col, outline=col)
        cv.text((lx + 18, 96), lab, size=14, fill=INK, anchor="lm")
        lx += 26 + int(_font(14).getlength(lab) / SS) + 24
    # panel A: detection
    a = Axes(cv, (110, 190, 660, 520), (0.5, 5.5), (0.0, 1.0))
    a.frame([0, 0.25, 0.5, 0.75, 1.0], _pct, [1, 2, 3, 4, 5], ["1", "2", "3", "4", "5"],
            ylabel="victim reported (2 pucks: presence only)", xlabel="pucks thrown")
    cv.text((102, 132), "A. Detection", size=16, bold=True)
    for cond, col, dx in (("reach", BLUE, -0.09), ("natural", ORANGE, 0.09)):
        xs = [n + dx for n in PUCKS]
        rr = [P[str(n)][cond]["reported"] for n in PUCKS]
        a.series(xs, [r["rate"] for r in rr], [r["ci95"][0] for r in rr], [r["ci95"][1] for r in rr], color=col)
    fa = sum(P[str(n)]["empty"]["reported"]["k"] for n in PUCKS)
    fn = sum(P[str(n)]["empty"]["reported"]["n"] for n in PUCKS)
    stc = abl["leakage_audit"]["still_victim_check"]
    per = ", ".join(f"{n}: {P[str(n)]['empty']['reported']['k']}" for n in PUCKS)
    cv.text((110, 580), f"Empty room, false alarms: {fa} of {fn} scans (by pucks {per}).", size=13, fill=INK2)
    cv.text((110, 600), f"Victim who neither breathes nor moves, 3-5 pucks: reported within 1 m of them in "
                        f"{stc['reported_within_1m_of_the_still_victim']} of {stc['scans_3_to_5_pucks']} scans.", size=13, fill=INK2)
    # panel B: location error
    b = Axes(cv, (800, 190, 1350, 520), (0.5, 5.5), (0.0, 2.5))
    b.frame([0, 0.5, 1.0, 1.5, 2.0, 2.5], lambda t: f"{t:.1f} m", [1, 2, 3, 4, 5], ["1", "2", "3", "4", "5"],
            ylabel="location error of reported victims: median (dot) to 90th percentile", xlabel="pucks thrown")
    cv.text((792, 132), "B. Location error, in the twin frame", size=16, bold=True)
    for cond, col, dx in (("reach", BLUE, -0.09), ("natural", ORANGE, 0.09)):
        xs, med, lo, hi = [], [], [], []
        for n in (3, 4, 5):
            e = P[str(n)][cond]["error_m"]
            if e:
                xs.append(n + dx)
                med.append(min(e["median"], 2.5))
                lo.append(min(e["median"], 2.5))
                hi.append(min(e["p90"], 2.5))
        b.series(xs, med, lo, hi, color=col, join=False)
        for x, n in zip(xs, (3, 4, 5)):
            e = P[str(n)][cond]["error_m"]
            if e and e["p90"] > 2.5:
                cv.text((b.X(x) + (-8 if cond == "reach" else 8), b.Y(2.3)), f"90th pct {e['p90']:.1f} m (off scale)" if cond == "reach"
                        else f"{e['p90']:.1f} m", size=12, fill=INK2, anchor="rm" if cond == "reach" else "lm")
    cv.text((b.X(1), b.Y(0.25)), "nothing:", size=12, fill=MUTED, anchor="mb")
    cv.text((b.X(1), b.Y(0.25) + 2), "no echo", size=12, fill=MUTED, anchor="ma")
    cv.text((b.X(2), b.Y(0.25)), "presence", size=12, fill=MUTED, anchor="mb")
    cv.text((b.X(2), b.Y(0.25) + 2), "no position", size=12, fill=MUTED, anchor="ma")
    cv.text((800, 580), "Errors include registration into the twin: a mirrored pose moves the victim too.", size=13, fill=INK2)
    cv.text((800, 600), f"Data: results/scout_ablation.json ({COMMAND})", size=13, fill=INK2)
    cv.save(path)


def fig_placement(plc: dict, abl: dict, path: Path):
    cv = Canvas(1400, 600)
    cv.text((40, 26), "Detection falls with the distance from the victim to the nearest puck", size=22, bold=True)
    cv.text((40, 60), f"SIMULATED: {plc['room']['label']}, breathing victim, both placement rules pooled, {plc['n_seeds']} seeds per rule. "
                      "Bars: Wilson 95% interval; n = scans in the bin.", size=14, fill=INK2)
    bins_ = plc["per_pucks"]["5"]["by_distance_to_nearest_puck"]
    labels = [b["bin"] for b in bins_]
    a = Axes(cv, (110, 140, 1060, 500), (-0.5, len(labels) - 0.5), (0.0, 1.0))
    a.frame([0, 0.25, 0.5, 0.75, 1.0], _pct, list(range(len(labels))), labels, ylabel="victim reported",
            xlabel="distance from the victim to the nearest puck (true positions)")
    for n, dx in ((3, -0.16), (4, 0.0), (5, 0.16)):
        bs = plc["per_pucks"][str(n)]["by_distance_to_nearest_puck"]
        xs, ys, lo, hi = [], [], [], []
        for i, b in enumerate(bs):
            if b["scans"] >= 3:
                xs.append(i + dx)
                ys.append(b["reported"]["rate"])
                lo.append(b["reported"]["ci95"][0])
                hi.append(b["reported"]["ci95"][1])
        a.series(xs, ys, lo, hi, color=BLUES[n])
    for i, b in enumerate(bins_):
        cv.text((a.X(i), 552), f"n = {b['scans']}", size=12, fill=MUTED, anchor="ma")
    cv.text((a.X(-0.5), 552), "5 pucks:", size=12, fill=MUTED, anchor="ra")
    # legend + takeaway
    ly = 160
    for n in (5, 4, 3):
        cv.circle((1110, ly), 6, fill=BLUES[n], outline=BLUES[n])
        cv.text((1124, ly), f"{n} pucks", size=14, anchor="lm")
        ly += 28
    take = plc.get("crew_advice")
    if take:
        y = 280
        cv.text((1100, y), "For the crew", size=15, bold=True)
        y += 28
        for ln in _wrap(take, 34):
            cv.text((1100, y), ln, size=13, fill=INK2)
            y += 20
    cv.save(path)


def _wrap(s: str, width: int) -> list[str]:
    out, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 > width and cur:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def _ellipse_curve(a_xy, b_xy, ex: float, n: int = 180):
    """The floor-plane locus where geometry.bistatic_excess equals `ex` (the detector's own model), by bisection
    along rays from the middle of the pair."""
    a_xy, b_xy = np.asarray(a_xy, float), np.asarray(b_xy, float)
    mid = (a_xy + b_xy) / 2
    th = np.linspace(0, 2 * math.pi, n, endpoint=False)
    u = np.stack([np.cos(th), np.sin(th)], 1)
    lo, hi = np.zeros(n), np.full(n, 30.0)
    for _ in range(40):
        m = (lo + hi) / 2
        p = mid + m[:, None] * u
        f = geometry.bistatic_excess(p[:, 0], p[:, 1], a_xy, b_xy) - ex
        lo, hi = np.where(f < 0, m, lo), np.where(f < 0, hi, m)
    return mid + ((lo + hi) / 2)[:, None] * u


def pick_example(abl: dict) -> dict | None:
    """A typical detection: 5 pucks, victim within reach, reported; the scan whose error is nearest the median."""
    rs = [r for r in abl["rows"] if r["n"] == 5 and r["cond"] == "reach" and r.get("err_m") is not None]
    if not rs:
        return None
    med = float(np.median([r["err_m"] for r in rs]))
    return min(rs, key=lambda r: (abs(r["err_m"] - med), r["seed"]))


def fig_example(abl: dict, path: Path) -> dict | None:
    ex_row = pick_example(abl)
    if ex_row is None:
        return None
    sc, _rec, out = run_pipeline(5, ex_row["seed"], "reach")
    poly = np.asarray(world()["poly"], float)
    lo_, hi_ = poly.min(0), poly.max(0)
    W, H = 1400, 900
    cv = Canvas(W, H)
    scale = min(820 / (hi_[0] - lo_[0]), 700 / (hi_[1] - lo_[1]))
    ox, oy = 60, 120 + (hi_[1] - lo_[1]) * scale

    def T(x, y):
        return (ox + (x - lo_[0]) * scale, oy - (y - lo_[1]) * scale)
    vic = out["vic"]
    v_rep = vic["victims"][0]
    v_true = sc["victim"]
    cv.text((40, 26), "One simulated scan, drawn to scale", size=22, bold=True)
    cv.text((40, 60), f"SIMULATED: {world()['label']}, 5 pucks, seed {ex_row['seed']}: the detection whose error is nearest the "
                      f"median of the {abl['per_pucks']['5']['reach']['reported']['k']} detections in this condition.", size=14, fill=INK2)
    # echo map (sequential amber on the surface)
    em = vic.get("echo_map")
    if em and em.get("values"):
        nx, ny, c = em["nx"], em["ny"], em["cell_m"]
        vals = np.asarray(em["values"], float).reshape(ny, nx)
        base = np.array([0xfc, 0xfc, 0xfb], float)
        hot = np.array([0xc2, 0x4a, 0x0f], float)
        for j in range(ny):
            for i in range(nx):
                q = vals[j, i]
                if q <= 0.02:
                    continue
                col = tuple(int(v) for v in base + (hot - base) * min(1.0, q) ** 0.8)
                x0, y0 = em["origin"][0] + i * c, em["origin"][1] + j * c
                p0, p1 = T(x0, y0 + c), T(x0 + c, y0)
                cv.rect((p0[0], p0[1], p1[0], p1[1]), fill="#%02x%02x%02x" % col)
    # room outline and door
    pts = [T(*p) for p in poly.tolist()]
    cv.line(pts + [pts[0]], fill=INK, width=3)
    dx_, dy_ = world()["door"]
    cv.circle(T(dx_, dy_), 7, fill=SURF, outline=INK, width=2.5)
    cv.text((T(dx_, dy_)[0] - 12, T(dx_, dy_)[1]), "door", size=13, fill=INK2, anchor="rm")
    # ellipses of the links that detected
    pxy = out["pxy"]
    used = [pl for pl in vic.get("per_link", []) if pl.get("used") and pl.get("excess_m") is not None]
    for pl in used:
        curve = _ellipse_curve(pxy[pl["tx"]], pxy[pl["rx"]], float(pl["excess_m"]))
        inside = [geometry.pip(p, world()["poly"]) for p in curve.tolist()]
        seg = []
        for p, ok in zip(curve.tolist() + [curve[0].tolist()], inside + [inside[0]]):
            if ok:
                seg.append(T(*p))
            elif seg:
                if len(seg) > 1:
                    cv.line(seg, fill="#7f9fc4", width=1.4)
                seg = []
        if len(seg) > 1:
            cv.line(seg, fill="#7f9fc4", width=1.4)
    # debris truth and flags
    for ch in sc.get("changes", []):
        x, y = ch["at"]
        p = T(x, y)
        cv.rect((p[0] - 7, p[1] - 7, p[0] + 7, p[1] + 7), fill=INK2, outline=INK2)
    for m in out["mm"]:
        if m["kind"] == "unexpected_reflector":
            p = T(*m["where"]["at"])
            r = float(m["where"]["radius_m"]) * scale
            cv.circle(p, r, outline=RED, width=2)
    # pucks: true (hollow) and registered (filled)
    for pk in sc["pucks"]:
        cv.circle(T(pk["x"], pk["y"]), 9, outline=INK, width=2)
    for k, (x, y) in sorted(pxy.items()):
        p = T(x, y)
        cv.circle(p, 4.5, fill=BLUE, outline=BLUE)
        cv.text((p[0] + 12, p[1] - 12), k, size=13, fill=INK, bold=True)
    # victim: estimate with its 2-sigma ellipse, and the truth
    pt = T(v_true["x"], v_true["y"])
    cv.circle(pt, 7, fill=AQUA, outline=INK, width=1.5)
    cxx, cxy, cyy = v_rep["cov"]
    evals, evecs = np.linalg.eigh(np.array([[cxx, cxy], [cxy, cyy]]))
    tt = np.linspace(0, 2 * math.pi, 90)
    ell = (evecs @ (2 * np.sqrt(np.maximum(evals, 0))[:, None] * np.stack([np.cos(tt), np.sin(tt)]))).T + [v_rep["x"], v_rep["y"]]
    cv.line([T(*p) for p in ell.tolist()] + [T(*ell[0].tolist())], fill=BLUE, width=2.2)
    pe = T(v_rep["x"], v_rep["y"])
    for col, wd in ((SURF, 6), (BLUE, 3)):
        cv.line([(pe[0] - 10, pe[1] - 10), (pe[0] + 10, pe[1] + 10)], fill=col, width=wd)
        cv.line([(pe[0] - 10, pe[1] + 10), (pe[0] + 10, pe[1] - 10)], fill=col, width=wd)
    # scale bar
    sb = (ox, oy + 26)
    cv.line([sb, (sb[0] + 2 * scale, sb[1])], fill=INK, width=2)
    cv.text((sb[0] + scale, sb[1] + 6), "2 m", size=12, fill=INK2, anchor="ma")
    # legend and numbers
    x0, y = 960, 130
    items = [("hollow", "pucks, true position"), ("dot", "pucks, registered into the twin"),
             ("ell", f"links that detected ({len(used)} of 10): their echo ellipses"),
             ("cross", "victim, estimate with 2-sigma ellipse"), ("true", "victim, true position"),
             ("sq", "debris, true position"), ("flag", "mismatch flags (unexpected reflector)"),
             ("heat", "dynamic echo map (breathing energy)")]
    for kind, lab in items:
        cy = y + 9
        if kind == "hollow":
            cv.circle((x0 + 9, cy), 8, outline=INK, width=2)
        elif kind == "dot":
            cv.circle((x0 + 9, cy), 4.5, fill=BLUE, outline=BLUE)
        elif kind == "ell":
            cv.line([(x0, cy), (x0 + 18, cy)], fill="#7f9fc4", width=1.6)
        elif kind == "cross":
            cv.line([(x0 + 2, cy - 7), (x0 + 16, cy + 7)], fill=BLUE, width=3)
            cv.line([(x0 + 2, cy + 7), (x0 + 16, cy - 7)], fill=BLUE, width=3)
        elif kind == "true":
            cv.circle((x0 + 9, cy), 7, fill=AQUA, outline=INK, width=1.5)
        elif kind == "sq":
            cv.rect((x0 + 3, cy - 6, x0 + 15, cy + 6), fill=INK2, outline=INK2)
        elif kind == "flag":
            cv.circle((x0 + 9, cy), 8, outline=RED, width=2)
        elif kind == "heat":
            cv.rect((x0 + 1, cy - 7, x0 + 17, cy + 7), fill="#e4a27c", outline=None)
        cv.text((x0 + 30, cy), lab, size=13, anchor="lm")
        y += 30
    reg = out["reg"]
    err = math.dist((v_rep["x"], v_rep["y"]), (v_true["x"], v_true["y"]))
    regerr = ex_row["reg"]
    lines = [
        f"Victim error: {err:.2f} m (2-sigma {2 * v_rep['sigma_m']:.2f} m)",
        f"Breathing: {v_rep['breathing_bpm']} /min reported, {v_true['breathing_bpm']} true",
        f"Nearest puck to the victim: {ex_row['d_nearest_m']:.1f} m",
        f"Registration: {reg['method']}, {regerr['median_m'] * 100:.0f} cm median puck error,",
        f"  ambiguity {reg['ambiguity']}, confident: {'yes' if reg.get('confident') else 'no'}",
        f"Debris flagged: {'yes' if ex_row['mm']['debris_hit'] else 'no'}; other flags: {ex_row['mm']['false_reflectors']}",
    ]
    y += 16
    cv.text((x0, y), "This scan", size=15, bold=True)
    y += 28
    for ln in lines:
        cv.text((x0, y), ln, size=13, fill=INK2)
        y += 22
    cv.text((40, H - 30), "Simulated pucks and echoes; the room outline is the twin's. A coarse echo map, not a LiDAR-grade model.",
            size=13, fill=INK2)
    cv.save(path)
    return {"seed": ex_row["seed"], "err_m": round(err, 3)}


# ------------------------------------------------------------------------------------------------ doc blocks
README_SECTION = DOCS / "readme_radar_section.md"
FEASIBILITY = DOCS / "RADAR_FEASIBILITY.md"
PLACEHOLDER = "ABLATION_TABLE_PLACEHOLDER"
BEGIN = "<!-- eval_scout:begin (generated by scripts/eval_scout.py from results/scout_*.json; edit the script, not this block) -->"
END = "<!-- eval_scout:end -->"


def frac(r: dict, ci: bool = True) -> str:
    """'29/60 (48%, 95% CI 36-61%)'."""
    if not r or not r["n"]:
        return "n/a"
    s = f"{r['k']}/{r['n']} ({r['k'] / r['n'] * 100:.0f}%"
    if ci:
        s += f", {r['ci95'][0] * 100:.0f}-{r['ci95'][1] * 100:.0f}%"
    return s + ")"


def meters(q: dict | None, key: str = "median", nd: int = 2) -> str:
    return "n/a" if not q else f"{q[key]:.{nd}f} m"


def put_block(path: Path, block: str, placeholder: str | None = None, append: bool = False):
    """Replace the generated block between the markers, or the placeholder line, or append it (never anything else)."""
    text = path.read_text(encoding="utf-8")
    full = f"{BEGIN}\n{block.rstrip()}\n{END}"
    if BEGIN in text and END in text:
        a, b = text.index(BEGIN), text.index(END) + len(END)
        text = text[:a] + full + text[b:]
    elif placeholder and placeholder in text:
        text = text.replace(placeholder, full, 1)
    elif append:
        text = text.rstrip("\n") + "\n\n" + full + "\n"
    else:
        raise SystemExit(f"{path}: neither the markers nor the placeholder found; refusing to edit")
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _overlap(a: dict, b: dict) -> bool:
    return a["ci95"][0] <= b["ci95"][1] and b["ci95"][0] <= a["ci95"][1]


def _pc(r: dict) -> str:
    return f"{r['rate'] * 100:.0f}%" if r and r["n"] else "n/a"


def readme_block(abl: dict, plc: dict) -> str:
    P = abl["per_pucks"]
    S = {n: P[str(n)] for n in PUCKS}
    gives = {1: "nothing: a board cannot hear its own echo", 2: "presence and an echo range, no position",
             3: "a position with no redundancy", 4: "a position, one bad link can be outvoted",
             5: "a position, ten links"}
    rows = ["| Pucks | What it gives | Breathing victim reported (within "
            f"{REACH_DEFAULT:g} m of a puck) | Reported and within 1 m | Location error, median / 90th pct | Pucks placed in the twin: median error, wrong pose |",
            "|---|---|---|---|---|---|"]
    for n in PUCKS:
        d = S[n]
        r = d["reach"]
        if n == 1:
            rows.append(f"| 1 | {gives[1]} | {frac(r['reported'], False)} | {frac(r['within_1.0m'], False)} | n/a | n/a |")
            continue
        reg = d["registration"]
        regs = f"{reg['puck_rms_m']['median'] * 100:.1f} cm, wrong in {reg['wrong_pose']['k']}/{reg['wrong_pose']['n']}"
        if n == 2:
            rows.append(f"| 2 | {gives[2]} | {frac(r['reported'], False)}, and {d['empty']['reported']['k']}/{d['empty']['reported']['n']} "
                        f"with nobody there | no position | n/a | {reg['puck_rms_m']['median']:.1f} m, wrong in "
                        f"{reg['wrong_pose']['k']}/{reg['wrong_pose']['n']} |")
            continue
        e = r["error_m"]
        rows.append(f"| {n} | {gives[n]} | {frac(r['reported'])} | {frac(r['within_1.0m'], False)} | "
                    f"{e['median']:.2f} / {e['p90']:.2f} m | {regs} |")
    nat = {n: S[n]["natural"] for n in (3, 4, 5)}
    fa = {n: S[n]["empty"]["reported"] for n in PUCKS}
    still = abl["leakage_audit"]["still_victim_check"]
    deb = S[5]["debris"]
    line = (f"**Natural placement** (the victim anywhere in the far part of the room, not kept within reach; the honest field number): "
            f"5 pucks report {frac(nat[5]['reported'])}, {nat[5]['within_1.0m']['k']}/{nat[5]['within_1.0m']['n']} within 1 m; "
            f"4 pucks {frac(nat[4]['reported'], False)}; 3 pucks {frac(nat[3]['reported'], False)}. "
            f"**False alarms in an empty room:** {fa[3]['k']}/{fa[3]['n']}, {fa[4]['k']}/{fa[4]['n']} and {fa[5]['k']}/{fa[5]['n']} "
            f"with 3, 4 and 5 pucks (2 pucks: {fa[2]['k']}/{fa[2]['n']}). A victim who neither breathes nor moves was reported "
            f"within 1 m of where they lay in {still['reported_within_1m_of_the_still_victim']} of {still['scans_3_to_5_pucks']} scans "
            f"with 3 to 5 pucks. Debris was flagged within {DEBRIS_HIT_M:g} m in {_pc(deb['flagged_within_0.8m'])} of 5-puck scans, "
            f"with {deb['false_reflector_flags_per_scan']:.1f} other flags per scan, "
            f"{(deb['flags_on_the_victim'] + deb['flags_on_furniture']) / max(deb['false_reflector_flags'], 1) * 100:.0f}% of them on the "
            f"victim or on furniture the twin does not show. In {plc['per_pucks']['5']['duplicates_dropped']} of {abl['n_seeds']} "
            f"5-puck seeds the natural draw already fell within {REACH_DEFAULT:g} m of a puck, so the two placements share those scenes.")
    return "\n".join([
        f"**Simulated evidence: what N thrown pucks find.** SIMULATED, never measured: {abl['room']['label']}, {abl['n_seeds']} seeds "
        f"per row, a {abl['scan_s']:g} s scan, lane L08's noise model, on a simulator written alongside the detector. Real boards "
        "will do worse. Errors are in the twin frame, so a wrong registration counts against the result.",
        "",
        *rows,
        "",
        line,
        "",
        "![Detection and location error against the number of pucks, simulated](figures/scout_ablation.png)",
        "",
        f"Data: `results/scout_ablation.json` and `results/scout_placement.json`, written by `{COMMAND}` "
        f"(about {abl['runtime_s'] / 60:.0f} minutes on {abl['workers']} cores). More detail, and why 1, 2 and 3 pucks fail: "
        "[docs/RADAR_FEASIBILITY.md, Simulated evidence](docs/RADAR_FEASIBILITY.md#simulated-evidence).",
    ])


def feasibility_block(abl: dict, plc: dict) -> str:
    P = abl["per_pucks"]
    S = {n: P[str(n)] for n in PUCKS}
    nz = abl["noise_model"]
    audit = abl["leakage_audit"]
    dev = audit["development_seed_check"]["per_pucks"]
    still = audit["still_victim_check"]
    L: list[str] = []
    a = L.append
    a("## Simulated evidence")
    a("")
    a("This section was added by the evaluation script `scripts/eval_scout.py` and is regenerated by it; every number here is read "
      "from `results/scout_ablation.json` and `results/scout_placement.json`. **Everything in it is simulated.** The simulator "
      "(`sandtable/scout/sim.py`) was written alongside the detector it tests, by the same builder, using lane L08's noise model. "
      "**Real boards will do worse**: section 6.4 explains why the echo of a person across this room is marginal, and the one "
      "real-data test of the reused pipeline failed. Read every rate below as an upper bound on what the method can do, not as a "
      "prediction for the DWM3001CDK pucks.")
    a("")
    a("### What was run")
    a("")
    a(f"- The room is the exercise's fire room, read from the twin by the same rule the exercise uses: {abl['room']['label']} "
      f"(`{abl['room']['id']}`), door `{abl['room']['door_id']}`.")
    a(f"- For N = 1 to 5 pucks and {abl['n_seeds']} seeds each ({abl['seeds'][0]}-{abl['seeds'][1]}, disjoint from the seeds the "
      "engine was developed on), four scenes: a breathing victim kept within "
      f"{REACH_DEFAULT:g} m of the nearest puck (the scene's default), a breathing victim anywhere in the far part of the room "
      "(natural placement), an empty room, and a victim who neither breathes nor moves. Every scene also holds a debris pile "
      "that the twin does not show and five weak furniture reflectors along the walls.")
    a("- Each scan runs the pipeline of the live session: simulate the frames and ranges, self-locate the pucks from their "
      "ranges, extract the static echoes, register the pucks into the twin, look for a breathing or moving person inside the "
      "room polygon, and flag where the echoes disagree with the twin. The link-shadow fallback (`shadow.locate`) runs on the "
      "same ranges.")
    a(f"- Scan: {abl['scan_s']:g} s at {abl['frame_hz']:g} frames per second. Noise model (L08's): direct-path SNR "
      f"{nz['snr_db_1m']:g} dB at 1 m, static-path flicker {nz['flicker']:g} RMS with a {nz['flicker_tau_s']:g} s time constant "
      f"(fitted in L08 to a real DW1000 recording), ranges with {nz['range_sigma_m'] * 100:g} cm noise, breathing seen as "
      f"{nz['breath_mm']:g} mm of torso motion. The full model is in the result file.")
    a(f"- Command: `{COMMAND}`, about {abl['runtime_s'] / 60:.0f} minutes on {abl['workers']} cores. Intervals are Wilson 95% "
      "intervals. Location errors are measured in the twin frame, so a wrong registration counts against the result.")
    a("")
    a("### Finding the person")
    a("")
    a("| Pucks | Reported, victim within reach | Within 0.6 m | Within 1.0 m | Error, median / 90th pct / max | Breathing-rate error, median / max (per min) | Natural placement: reported | Natural: within 1.0 m | Empty room: false alarms | Still victim: reported (within 1 m of them) |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for n in PUCKS:
        d = S[n]
        r, nt = d["reach"], d["natural"]
        e, b = r.get("error_m"), r.get("bpm_error")
        es = f"{e['median']:.2f} / {e['p90']:.2f} / {e['max']:.2f} m" if e else ("no position" if n == 2 else "n/a")
        bs = f"{b['median']:.1f} / {b['max']:.1f}" if b else "n/a"
        st = d["still"]
        stw = st.get("within_1.0m_of_the_still_victim")
        sts = f"{frac(st['reported'], False)} ({stw['k']})" if stw and n >= 3 else frac(st["reported"], False)
        a(f"| {n} | {frac(r['reported'])} | {frac(r['within_0.6m'], False)} | {frac(r['within_1.0m'], False)} | {es} | {bs} | "
          f"{frac(nt['reported'])} | {frac(nt['within_1.0m'], False)} | {frac(d['empty']['reported'], False)} | {sts} |")
    a("")
    s5 = S[5]["reach"]
    a(f"With five pucks the reported uncertainty is honest in these scenes: {s5['error_within_2sigma']['k']} of "
      f"{s5['error_within_2sigma']['n']} reported positions lie within two reported sigmas of the truth. "
      "The natural-placement column is the one to quote for the field: a crew cannot choose where the victim lies. It draws the "
      "victim anywhere in the far part of the room; in "
      f"{plc['per_pucks']['5']['duplicates_dropped']} of the {abl['n_seeds']} five-puck seeds that draw already fell within "
      f"{REACH_DEFAULT:g} m of a puck, so the two columns share those scenes and differ only on the rest.")
    a("")
    a("![Detection and location error against the number of pucks (simulated)](../figures/scout_ablation.png)")
    a("")
    a("### Self-location, registration into the twin, and twin mismatches")
    a("")
    a("Pooled over the four scenes (four times the seeds per row). A pose is wrong when the registered pucks sit more than "
      f"{WRONG_POSE_M:g} m RMS from the truth (a mirrored or turned constellation; a right pose is centimetres off).")
    a("")
    a("| Pucks | Self-location error, median / max | Registered pucks: median RMS error | Worst puck | Wrong pose | Marked confident | Ambiguous (score of the next pose >= 0.6) | Decided by the door prior | Wrong pose marked confident | Debris flagged within 0.8 m | Other flags per scan (on the victim, on furniture, elsewhere) |")
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for n in PUCKS[1:]:
        d = S[n]
        sl, rg, db = d["self_location"]["error_after_alignment_m"], d["registration"], d["debris"]
        med = rg['puck_rms_m']['median']
        a(f"| {n} | {sl['median'] * 1000:.0f} / {sl['max'] * 1000:.0f} mm | {f'{med:.1f} m' if med >= 1 else f'{med * 100:.1f} cm'} | "
          f"{rg['worst_puck_m']:.1f} m | {frac(rg['wrong_pose'], False)} | {frac(rg['confident'], False)} | "
          f"{frac(rg['ambiguous_ge_0.6'], False)} | {frac(rg['door_decided'], False)} | {rg['wrong_pose_marked_confident']} | "
          f"{frac(db['flagged_within_0.8m'], False)} | {db['false_reflector_flags_per_scan']:.2f} ({db['flags_on_the_victim']}, "
          f"{db['flags_on_furniture']}, {db['ghost_flags']}) |")
    a("")
    r5, d5k = S[5]["registration"], S[5]["debris_with_victim_known"]
    a(f"- No wrong pose was ever marked confident (with 3 to 5 pucks: "
      f"{sum(S[n]['registration']['wrong_pose_marked_confident'] for n in (3, 4, 5))} cases). When five pucks registered correctly the "
      f"median puck error was {r5['puck_rms_m_when_right']['median'] * 100:.1f} cm. Self-location is millimetres because the "
      "simulated ranges have 3 cm of white noise and no bias; real ranging has antenna-delay and non-line-of-sight biases, "
      "so expect centimetres (section 5.3 shows how that error passes into the victim's position).")
    amb5 = S[5]["registration"]["ambiguous_ge_0.6"]
    a(f"- The ambiguity score is cautious: with five pucks it reached 0.6 (the level at which the session warns that another "
      f"pose fits almost as well) in {frac(amb5, False)} scans, while the pose was wrong in {frac(S[5]['registration']['wrong_pose'], False)}. "
      "A rectangular room has mirror poses that wall echoes alone cannot separate; the door prior does the rest.")
    db5 = S[5]["debris"]
    a(f"- With five pucks, {db5['flagged_within_0.8m']['k']} of {db5['scans']} scans had a flag on the debris, and the same scans "
      f"raised {db5['false_reflector_flags']} other unexpected-reflector flags: {db5['flags_on_the_victim']} on the victim, "
      f"{db5['flags_on_furniture']} on furniture and {db5['ghost_flags']} elsewhere. No wall was reported missing "
      f"({db5['missing_wall_flags']} flags; no wall is missing in these scenes).")
    a(f"- The mismatch finder also flags the victim: a person is a reflector the twin does not show. With five pucks, "
      f"{S[5]['debris']['flags_on_the_victim']} flags fell within {VICTIM_FLAG_M:g} m of the victim. The live session does not "
      f"pass the located victim to `find_mismatches(known=...)`; passing it leaves {d5k['flags_on_the_victim']}. Flags on "
      "furniture are real differences from the twin (the twin's polygons hold no furniture), so only the last number is a "
      "pure false flag.")
    a("")
    a("### Where the pucks land matters")
    a("")
    pl5 = plc["per_pucks"]["5"]
    a(f"Detection against the distance from the victim to the nearest puck (true positions; both placement rules pooled, a scene "
      f"counted once when both rules put the victim in the same place; 5 pucks, {pl5['scans']} scans):")
    a("")
    a("| Nearest puck | " + " | ".join(b["bin"] for b in pl5["by_distance_to_nearest_puck"]) + " |")
    a("|---|" + "---|" * len(pl5["by_distance_to_nearest_puck"]))
    for n in (5, 4, 3):
        bs = plc["per_pucks"][str(n)]["by_distance_to_nearest_puck"]
        a(f"| {n} pucks: reported | " + " | ".join(frac(b["reported"], False) if b["scans"] else "-" for b in bs) + " |")
    a("")
    hb, fb = pl5["by_hull_area"], pl5["by_flatness"]
    a("Against the spread of the constellation (hull area, thirds) with 5 pucks: " +
      ", ".join(f"{b['bin']} {frac(b['reported'], False)}" for b in hb) + ". Against how flat the throw is (second over first "
      "singular value; the scene generator rejects throws below 0.18): " + ", ".join(f"{b['bin']} {frac(b['reported'], False)}" for b in fb) +
      ". Neither shows a clear effect in this room; distance to the nearest puck does.")
    a("")
    a(f"**For the crew:** {plc['crew_advice']}")
    a("")
    a("![Detection against the distance from the victim to the nearest puck (simulated)](../figures/scout_placement.png)")
    a("")
    a("### The link-shadow fallback")
    a("")
    sh_any = sum(S[n][c]["shadow"]["present"]["k"] for n in PUCKS[1:] for c in CONDITIONS)
    sh_n = sum(S[n][c]["shadow"]["present"]["n"] for n in PUCKS[1:] for c in CONDITIONS)
    inh = sum(plc["per_pucks"][str(n)]["by_victim_in_hull"][0]["scans"] for n in (3, 4, 5))
    tot = sum(plc["per_pucks"][str(n)]["scans"] for n in (3, 4, 5))
    a(f"`shadow.locate` (no baseline: the pucks land in a room where the person already lies) reported presence in {sh_any} of "
      f"{sh_n} scans, victim or not. That is no false alarms, and also no detections: in these scenes the victim lay inside the "
      f"pucks' convex hull in {inh} of {tot} scans with 3 to 5 pucks, because the scene puts the victim in the far part of the room "
      "and the pucks land nearer the door. Link shadowing only sees a body that blocks a puck-to-puck line, so here it has nothing "
      "to see. `hardware/eval_shadow.py` tests it with the victim moved inside the hull. A crew that wants this rung must throw "
      "pucks beyond the person, which they cannot know in advance.")
    a("")
    a("### Why 1, 2 and 3 pucks fail")
    a("")
    d1, d2, d3 = S[1], S[2], S[3]
    a(f"- **One puck: nothing** ({frac(d1['reach']['reported'], False)} in every scene). This is section 3: the board cannot "
      "receive while it transmits, so there is no echo to process. The detector says so instead of guessing.")
    pres_same = _overlap(d2["reach"]["reported"], d2["empty"]["reported"])
    a(f"- **Two pucks: presence only, and in simulation not even that.** One pair is one ellipse (section 4): no position. "
      f"\"Someone is there\" came up in {frac(d2['reach']['reported'], False)} scans with a breathing victim within reach and in "
      f"{frac(d2['empty']['reported'], False)} with nobody in the room" +
      (", rates whose intervals overlap: with nothing to corroborate one link, the flicker of the static echoes (section 6.4) "
       "triggers it about as often as a person does." if pres_same else ".") +
      f" When it fired with a victim present, the echo's extra path was right within one tap in "
      f"{frac(d2['reach']['range_right'], False)}, and the breathing-rate error had a median of "
      f"{d2['reach']['bpm_error']['median']:.1f} per minute. Registration cannot work either: one pair hears too few wall "
      f"echoes to fix a pose (section 7), so the pose was wrong in {frac(d2['registration']['wrong_pose'], False)} scans "
      "(never marked confident).")
    big3 = d3["reach"]["error_m"]
    a(f"- **Three pucks: a position, often the wrong one.** Three ellipses cross, but at shallow angles for a throw clustered "
      "near the door (the dilution of precision in section 5.2), and with no redundant pair a "
      f"bad link cannot be outvoted (section 5). The victim within reach was reported in {frac(d3['reach']['reported'], False)} "
      f"scans but within 1 m in only {frac(d3['reach']['within_1.0m'], False)}; the 90th-percentile error is {big3['p90']:.1f} m. "
      f"{d3['reach']['reported_with_wrong_registration']} of those {d3['reach']['reported']['k']} reports came with a wrong "
      "registration: three pucks give "
      "three pairs of wall echoes, too few to tell the room's mirror-image poses apart (the next pose scored 0.6 or more of the "
      f"best in {frac(d3['registration']['ambiguous_ge_0.6'], False)} scans; wrong in {frac(d3['registration']['wrong_pose'], False)}). "
      f"The empty room raised {frac(d3['empty']['reported'], False)} false alarms and the still-victim scenes "
      f"{frac(d3['still']['reported'], False)} reports" +
      (", none within 1 m of the still victim: ghosts, not detections." if d3['still']['within_1.0m_of_the_still_victim']['k'] == 0 else "."))
    p5 = plc["per_pucks"]["5"]
    a(f"- **Four and five pucks** add pairs at new angles and redundancy (6 and 10 pairs): false alarms "
      f"{frac(S[4]['empty']['reported'], False)} and {frac(S[5]['empty']['reported'], False)}, wrong poses "
      f"{frac(S[4]['registration']['wrong_pose'], False)} and {frac(S[5]['registration']['wrong_pose'], False)}. What remains is "
      f"reach: with five pucks the missed victims lay a median {p5['d_nearest_of_missed']['median']:.1f} m from the nearest puck, "
      f"the reported ones {p5['d_nearest_of_reported']['median']:.1f} m. That is the 1/(R1 R2) echo loss of section 6.3.")
    a("")
    a("### Checks against leaked ground truth")
    a("")
    a("A number this good from a simulator invites the question whether the detector sees the answer. What was checked:")
    a("")
    src = audit["source_lines_mentioning_truth"]
    mention = "; ".join(f"`{k}`: {len(v)} line{'s' if len(v) != 1 else ''}" + (f" ({v[0].split(':', 1)[1].strip()[:60]}...)" if v else "")
                        for k, v in src.items())
    a("- **Runtime guard.** The script removes the ground truth from the recording before any call to `self_locate`, "
      "`static_echoes`, `register`, `find_victims`, `find_mismatches` or `shadow.locate`, and never passes them the scene. A "
      "module that read `rec[\"truth\"]` would fail. None did.")
    a(f"- **Source.** Lines that mention \"truth\" in the detector modules: {mention}. None of them imports the scene or the simulator.")
    a(f"- **The still victim.** A victim placed by the same rule, who neither breathes nor moves, was reported within 1 m of "
      f"where they lay in {still['reported_within_1m_of_the_still_victim']} of {still['scans_3_to_5_pucks']} scans with 3 to 5 "
      "pucks. A detector that knew the position would find them; this one only finds the breathing.")
    a(f"- **The development seeds.** On seeds {audit['development_seed_check']['seeds'][0]}-{audit['development_seed_check']['seeds'][1]}, "
      f"where `sandtable/scout/RADAR_NOTES.md` measured 5 pucks by hand at 9/16 reported and 8/16 within 0.6 m, this script gets "
      f"{frac(dev['5']['reported'], False)} reported and {frac(dev['5']['within_0.6m'], False)} within 0.6 m (4 pucks: "
      f"{frac(dev['4']['reported'], False)}). The held-out seeds above give a higher rate for 5 pucks "
      f"({frac(S[5]['reach']['reported'], False)}); with 16 seeds the two are within sampling noise "
      f"(Wilson intervals {dev['5']['reported']['ci95'][0] * 100:.0f}-{dev['5']['reported']['ci95'][1] * 100:.0f}% and "
      f"{S[5]['reach']['reported']['ci95'][0] * 100:.0f}-{S[5]['reach']['reported']['ci95'][1] * 100:.0f}%), so quote the larger sample "
      "and its interval, not a better detector.")
    a("")
    a("What is optimistic without being leakage: the twin's polygon is exactly the simulated room, so registration matches walls "
      "that are where the twin says; ranges carry no bias; the scene holds one person and nothing else moves (no crew in the "
      "doorway, no fire, no hose stream); pucks lie flat with no antenna pattern and no floor bounce; the detector's thresholds "
      "were tuned on this simulator. Each of these will cost detections on real boards.")
    a("")
    a("![One simulated scan drawn to scale: pucks true and registered, the echo ellipses of the links that detected, the estimate "
      "and the truth](../figures/scout_example.png)")
    return "\n".join(L)


def render():
    abl = json.loads(ABLATION_JSON.read_text(encoding="utf-8"))
    plc = json.loads(PLACEMENT_JSON.read_text(encoding="utf-8"))
    FIGURES.mkdir(exist_ok=True)
    fig_ablation(abl, FIGURES / "scout_ablation.png")
    fig_placement(plc, abl, FIGURES / "scout_placement.png")
    ex = fig_example(abl, FIGURES / "scout_example.png")
    print("figures written; example", ex)
    put_block(README_SECTION, readme_block(abl, plc), placeholder=PLACEHOLDER)
    put_block(FEASIBILITY, feasibility_block(abl, plc), append=True)
    print(f"doc blocks written: {README_SECTION.relative_to(ROOT)}, {FEASIBILITY.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
