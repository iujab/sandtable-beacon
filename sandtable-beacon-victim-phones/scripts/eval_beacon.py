#!/usr/bin/env python3
"""SIMULATED evaluation of Sandtable Beacon: every number and figure the project quotes about locating phones.

    nice .venv/bin/python scripts/eval_beacon.py              # everything below, < 2 min
    nice .venv/bin/python scripts/eval_beacon.py --main       # results/eval_beacon.json + figures/eval_{error_vs_pucks,area_vs_pucks,
                                                              #   bearing,twin_pruning,geometry_share,storyboard}.png
    nice .venv/bin/python scripts/eval_beacon.py --layouts    # results/eval_layouts.json + figures/eval_layouts{,_diagram}.png
    nice .venv/bin/python scripts/eval_beacon.py --two-puck   # results/eval_two_puck.json + figures/eval_two_puck{,_map}.png
    nice .venv/bin/python scripts/eval_beacon.py --quick      # smoke run of everything (seconds; used by the tests)

Everything here is SIMULATED with the stated noise model (beacon/sim.py docstring + models.DEFAULT_NOISE, both copied
into the JSON). Nothing is a measurement.

Pipeline per trial (what the server does): a `beacon.sim.World` with pucks thrown into the fire room and one opt-in
phone; the world is stepped at 10 Hz and every observation of the phone goes into a `beacon.track.PhoneTrack`, whose
`estimate` (locator + hysteresis) is called 4 times a second during the last second of OBS_S (3 s) of observations
(the server calls it 4 times a second throughout; the hysteresis only looks at consecutive estimates, and the full run
re-checks a sample at full cadence: `cadence_check` in the JSON); the published geometry is scored against the truth. Twin on and twin off are two trackers fed the SAME observations (paired). Methods are
paired too: the same seeds give the same throws and phone positions for ble-rssi, uwb-twr and uwb-twr+bearing.

Outputs: results/eval_beacon.json, results/eval_layouts.json, figures/eval_*.png.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import time
from multiprocessing import get_context
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from beacon import layout as puck_layout, locate, models, prune  # noqa: E402
from beacon.sim import SIM_MODEL, World, load_scenario_file, scaled_noise  # noqa: E402
from beacon.twin import Twin, _pip, _poly_dist  # noqa: E402

try:
    from beacon.track import PhoneTrack  # noqa: E402
    PIPELINE = "beacon.track.PhoneTrack (median of the last UWB ranges, mean RSSI, hysteresis) -> beacon.locate.estimate"
except ImportError:                                                    # pragma: no cover
    PhoneTrack = None
    PIPELINE = "direct: median UWB / mean RSSI of the last 5 s per puck -> beacon.locate.estimate (beacon.track missing)"

TWIN_JSON = ROOT / "fixtures" / "building" / "twin.json"
SCENARIO = "suite-310"                      # the fire room and the demo phone come from the demo scenario
METHODS = ["ble-rssi", "uwb-twr", "uwb-twr+bearing"]
GEOMS = ["fix", "candidates", "ring", "zone", "none"]
SIM_HZ = 10.0
EST_HZ = 4.0
OBS_S = 3.0
EST_LAST_S = 1.0           # estimates at 4 Hz during the last second only (cadence check in the full run: same output)
BASE_SEED = 20261114
MISMATCH = 1.5

FULL = {"main_trials": 90, "mismatch_trials": 50, "cold_trials": 200, "layout_trials": 50, "layout_bearing_trials": 60,
        "cadence_check": 60, "tp_positions": 400, "tp_draws": 2, "tp_false_positions": 150, "tp_ref_draws": 1, "tp_cadence_check": 40}
QUICK = {"main_trials": 2, "mismatch_trials": 1, "cold_trials": 4, "layout_trials": 2, "layout_bearing_trials": 2,
         "cadence_check": 0, "tp_positions": 6, "tp_draws": 1, "tp_false_positions": 3, "tp_ref_draws": 1, "tp_cadence_check": 0}
TP_LAYOUTS = ["door-wall", "exterior-wall", "careless"]
TP_REF = "three-pucks (door-wall pair + far corner)"
TP_FALSE_REACH_M = 6.0         # false-statement phones: in a neighbouring room, within this distance of the fire room
WRONG_FIX_M = 1.5

SWEEP = [(0.05, 0.0), (0.05, 0.05), (0.05, 0.10), (0.10, 0.0), (0.10, 0.05), (0.10, 0.10)]
SPACINGS = [0.1, 0.3, 0.5]                  # two boards on one thrown device
SEPARATIONS = [2.0, 3.0, 4.0]               # two separate pucks near the door

# ---- worker state ----------------------------------------------------------------------------------------------------
_W: dict = {}


def _init_worker(nice: int = 10):
    try:
        os.nice(nice)
    except (AttributeError, OSError):
        pass
    _state()


def _state() -> dict:
    if not _W:
        data = json.loads(TWIN_JSON.read_text(encoding="utf-8"))
        tw = Twin(data)
        _W["twin"] = tw
        _W["ctx"] = {True: locate.Context(tw, cell_m=0.25, use_twin=True),
                     False: locate.Context(tw, cell_m=0.25, use_twin=False)}
    return _W


def fire_room_and_neighbours(tw: Twin) -> tuple[str, list[str]]:
    room = load_scenario_file(SCENARIO)["room"]
    poly = tw.room(room)["polygon"]
    neigh = []
    for r in tw.rooms():
        if r["id"] == room or r["floor"] != tw.room(room)["floor"]:
            continue
        # shares a stretch of boundary: some edge midpoint of one polygon lies on the other's boundary
        pts = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(r["polygon"], r["polygon"][1:] + r["polygon"][:1])]
        pts += [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(poly, poly[1:] + poly[:1])]
        if any(_poly_dist(p, poly)[0] < 0.05 and _poly_dist(p, r["polygon"])[0] < 0.05 for p in pts):
            neigh.append(r["id"])
    return room, sorted(neigh)


def _point_in_room(rng, tw: Twin, room_id: str, clear: float = 0.3, centre=None, rmin=None, rmax=None):
    poly = tw.room(room_id)["polygon"]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    for _ in range(5000):
        p = (float(rng.uniform(min(xs), max(xs))), float(rng.uniform(min(ys), max(ys))))
        if not _pip(p, poly) or _poly_dist(p, poly)[0] < clear:
            continue
        if centre is not None and not (rmin <= math.dist(p, centre) <= rmax):
            continue
        return p
    raise RuntimeError(f"no point in {room_id} matching the constraints")


def _angdiff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ---- one trial ---------------------------------------------------------------------------------------------------------

class _DirectTrack:                                                      # pragma: no cover - only without beacon.track
    def __init__(self, eid):
        self.u, self.b, self.br = {}, {}, None

    def add(self, o):
        if o["kind"] == "uwb":
            self.u.setdefault(o["puck"], []).append((o["t"], o["d_m"], o.get("los")))
        elif o["kind"] == "sos":
            self.b.setdefault(o["puck"], []).append((o["t"], o["rssi"]))
        elif o["kind"] == "bearing":
            self.br = o

    def estimate(self, ctx, pucks, now, noise=None):
        rs = []
        for p, v in self.u.items():
            f = [s for s in v if now - s[0] <= 5.0]
            if f:
                rs.append({"puck": p, "kind": "uwb", "d_m": float(np.median([s[1] for s in f])), "n": len(f),
                           "los": float(np.median([s[2] for s in f]))})
        for p, v in self.b.items():
            f = [s for s in v if now - s[0] <= 5.0]
            if f and p not in self.u:
                rs.append({"puck": p, "kind": "ble", "rssi": float(np.mean([s[1] for s in f])), "n": len(f)})
        br = None
        if self.br and now - self.br["t"] <= 3.0:
            br = {"puck": self.br["puck"], "sigma_deg": self.br["sigma_deg"],
                  "from_puck_deg": models.compass_to_bldg_deg(self.br["to_puck_compass_deg"], ctx.georef_bearing_deg)}
        return locate.estimate(ctx, pucks, rs, br, None, noise)


def simulate(spec: dict) -> list[dict]:
    """Run one trial; return one record per locator context (twin on / off)."""
    st = _state()
    tw = st["twin"]
    world_noise = spec.get("world_noise") or models.DEFAULT_NOISE
    loc_noise = spec.get("loc_noise") or models.DEFAULT_NOISE
    w = World(tw, seed=spec["seed"], noise=world_noise, eid_rotate_s=1e9)
    if spec.get("throw"):
        w.throw_pucks(spec["room"], n=spec["throw"], seed=spec["seed"], strategy=spec.get("strategy", "scatter"))
        believed = w.pucks
    else:
        w.set_pucks(spec["pucks_true"], exact=True)
        believed = spec["pucks_believed"]
    px, py = spec["phone"]
    method = spec["method"]
    w.upsert_phone("phone", px, py, uwb=method != "ble-rssi", pointing=method == "uwb-twr+bearing", chip="U1",
                   report={"status": "trapped", "people": 1})
    trackers = {use: (PhoneTrack("x") if PhoneTrack else _DirectTrack("x")) for use in spec["contexts"]}
    t0 = 1_800_000_000.0 + (spec["seed"] % 100000)
    steps = int(round(OBS_S * SIM_HZ))
    est = {}
    n_obs = {"uwb": 0, "sos": 0, "bearing": 0}
    pts_uwb: dict[str, list[float]] = {}
    ms = 0.0
    n_est = 0
    for k in range(1, steps + 1):
        now = t0 + k / SIM_HZ
        for o in w.step(now):
            if o["kind"] == "cold":
                continue
            if o["kind"] in n_obs:
                n_obs[o["kind"]] += 1
            if o["kind"] == "uwb":
                pts_uwb.setdefault(o["puck"], []).append(o["d_m"])
            for tr in trackers.values():
                tr.add(o)
        t_rel = k / SIM_HZ
        last_s = spec.get("est_last_s", EST_LAST_S)
        if abs((t_rel / (1.0 / EST_HZ)) - round(t_rel / (1.0 / EST_HZ))) < 1e-6 and (
                not last_s or t_rel > OBS_S - last_s - 1e-9):
            for use, tr in trackers.items():
                tic = time.perf_counter()
                est[use] = tr.estimate(st["ctx"][use], believed, now, noise=loc_noise)
                ms += (time.perf_counter() - tic) * 1000
                n_est += 1
    truth_room = tw.locate(px, py)["room"]
    out = []
    for use, e in est.items():
        rec = score(e, px, py, truth_room)
        rec.update({"twin": use, "trial": spec["trial"], "seed": spec["seed"], "phone": [round(px, 2), round(py, 2)],
                    "n_obs": dict(n_obs), "est_ms": round(ms / max(1, n_est), 2), "arm": spec["arm"]})
        if spec.get("layout"):
            rec.update(layout_metrics(spec, e, px, py, believed, pts_uwb, w))
        out.append(rec)
    return out


def score(e: dict, px: float, py: float, truth_room: str | None) -> dict:
    g = e.get("geometry") or "none"
    rec = {"geometry": g, "method_published": e.get("method"), "hpd_area_m2": e.get("hpd_area_m2"),
           "confidence": e.get("confidence"), "sigma_m": e.get("sigma_m"), "fix_err_m": None, "inside3": None,
           "inside_locate": bool(locate.inside(e, px, py)) if g != "none" else None, "wrong_fix": None,
           "room_published": e.get("room"), "room_ok": None, "truth_room": truth_room,
           "n_candidates": len(e.get("candidates") or []), "n_pruned": len(e.get("pruned") or []),
           "pruned_near_truth": any(math.hypot(q["x"] - px, q["y"] - py) <= 1.0 for q in e.get("pruned") or []),
           "point_err_m": None, "debug_pair": {k: v for k, v in (e.get("debug") or {}).items() if k.startswith("pair_")}}
    if g == "fix":
        err = math.hypot(e["x"] - px, e["y"] - py)
        rec["fix_err_m"] = round(err, 3)
        rec["point_err_m"] = round(err, 3)
        rec["inside3"] = bool(err <= 3.0 * max(float(e["sigma_m"] or 0.0), 1e-6))
        rec["wrong_fix"] = not rec["inside3"]
    elif g == "candidates":
        d = [(math.hypot(c["x"] - px, c["y"] - py), c) for c in e["candidates"]]
        rec["inside3"] = any(dd <= 3.0 * max(float(c["sigma_m"] or 0.0), 1e-6) for dd, c in d)
        rec["point_err_m"] = round(min(dd for dd, _ in d), 3) if d else None
    elif g == "ring":
        r = e["ring"]
        dist = math.hypot(px - r["cx"], py - r["cy"])
        th = math.degrees(math.atan2(py - r["cy"], px - r["cx"])) % 360.0
        in_arc = any(((th - a0) % 360.0) <= ((a1 - a0) % 360.0 if (a1 - a0) % 360.0 else 360.0) + 5.0
                     or _angdiff(th, a0) <= 5.0 or _angdiff(th, a1) <= 5.0 for a0, a1 in r["arcs"])
        rec["inside3"] = bool(abs(dist - r["r_m"]) <= 3.0 * max(float(r["sigma_m"] or 0.0), 1e-6) and in_arc)
    elif g == "zone":
        z = e["zone"]
        rec["inside3"] = bool(_pip((px, py), z["polygon"])) if z.get("polygon") else (z.get("room") == truth_room)
    if e.get("room") is not None:
        rec["room_ok"] = e["room"] == truth_room
    return rec


# ---- layouts (the owner's questions) -----------------------------------------------------------------------------------

def layout_metrics(spec, e, px, py, believed, pts_uwb, w) -> dict:
    lay = spec["layout"]
    out = {"layout": lay["name"]}
    if lay["kind"] not in ("one-device", "two-pucks"):
        if e.get("geometry") == "fix":
            c = lay["centre"]
            out["cross_range_point_m"] = round(_cross(c, (px, py), (e["x"], e["y"])), 3)
        return out
    (ax, ay), (bx, by) = [(p["x"], p["y"]) for p in spec["pucks_true"]]
    cx, cy = (ax + bx) / 2, (ay + by) / 2
    b = math.hypot(bx - ax, by - ay)
    beta = math.degrees(math.atan2(by - ay, bx - ax))
    th_true = math.degrees(math.atan2(py - cy, px - cx)) % 360.0
    R = math.hypot(px - cx, py - cy)
    # (1) the locator's own pair bearing (debug.pair_bearing_deg and its mirror)
    dbg = e.get("debug") or {}
    if dbg.get("pair_bearing_deg") is not None:
        main, alt = float(dbg["pair_bearing_deg"]), float(dbg["pair_bearing_alt_deg"])
        em, ea = _angdiff(main, th_true), _angdiff(alt, th_true)
        out["bearing_err_deg"] = round(min(em, ea), 2)
        out["mirror_wrong_side"] = bool(ea < em)
        out["pair_side_mass"] = dbg.get("pair_side_mass")
        out["cross_range_m"] = round(R * abs(math.sin(math.radians(min(em, ea)))), 3)
    # (2) closed-form check: trilateration on the median range of each board
    da = pts_uwb.get(spec["pucks_true"][0]["id"]) or []
    db = pts_uwb.get(spec["pucks_true"][1]["id"]) or []
    if da and db:
        ra, rb = float(np.median(da)), float(np.median(db))
        Rm = math.sqrt(max((ra * ra + rb * rb) / 2.0 - b * b / 4.0, 1e-6))
        cos_t = float(np.clip((ra * ra - rb * rb) / (2.0 * b * Rm), -1.0, 1.0))   # angle from the a->b axis
        t_est = math.degrees(math.acos(cos_t))
        cands = [(beta + t_est) % 360.0, (beta - t_est) % 360.0]
        out["tri_bearing_err_deg"] = round(min(_angdiff(c, th_true) for c in cands), 2)
        out["tri_n"] = min(len(da), len(db))
    # reference with NO range information: "the phone is straight out from the device" (perpendicular to the baseline),
    # scored the same way (nearer of the two sides). Any bearing error near this number carries no information.
    out["uninformed_err_deg"] = round(min(_angdiff((beta + 90.0) % 360.0, th_true), _angdiff((beta - 90.0) % 360.0, th_true)), 2)
    # closed form sigma_theta = sqrt(2) * sigma_eff / (b |sin theta|), theta = angle between baseline and phone
    sin_t = abs(math.sin(math.radians(th_true - beta)))
    out["sin_theta"] = round(sin_t, 3)
    out["R_m"] = round(R, 2)
    out["baseline_m"] = round(b, 3)
    if e.get("geometry") in ("fix", "candidates"):
        pts = [(e["x"], e["y"])] if e["geometry"] == "fix" else [(c["x"], c["y"]) for c in e["candidates"]]
        best = min(pts, key=lambda q: math.hypot(q[0] - px, q[1] - py))
        out["cross_range_point_m"] = round(_cross((cx, cy), (px, py), best), 3)
    return out


def _cross(c, truth, est) -> float:
    ux, uy = truth[0] - c[0], truth[1] - c[1]
    n = math.hypot(ux, uy) or 1e-9
    nx, ny = -uy / n, ux / n
    return abs((est[0] - truth[0]) * nx + (est[1] - truth[1]) * ny)


def sweep_noise(white: float, bias: float) -> dict:
    n = copy.deepcopy(models.DEFAULT_NOISE)
    n["uwb"]["sigma_los_m"] = white
    n["uwb"]["link_bias_sigma_m"] = bias
    n["uwb"]["body_block_prob"] = 0.0
    return n


def _layout_geometry(tw, rng, room, kind, size):
    """True puck positions for one layout around a 'thrown' centre (Scout's throw model for the landing point)."""
    poly = tw.room(room)["polygon"]
    for _ in range(400):
        tmp = World(tw, seed=int(rng.integers(0, 2 ** 31)))
        tmp.throw_pucks(room, n=1, strategy="scatter")
        c = (tmp.truth()["pucks"][0]["x"], tmp.truth()["pucks"][0]["y"])
        phi = float(rng.uniform(0.0, math.pi))
        u = (math.cos(phi), math.sin(phi))
        a = (c[0] - size / 2 * u[0], c[1] - size / 2 * u[1])
        b = (c[0] + size / 2 * u[0], c[1] + size / 2 * u[1])
        clear = 0.1 if kind == "one-device" else 0.3
        if all(_pip(p, poly) and _poly_dist(p, poly)[0] >= clear for p in (a, b)):
            return c, [{"id": "P1", "x": a[0], "y": a[1]}, {"id": "P2", "x": b[0], "y": b[1]}]
    raise RuntimeError("could not place the layout")


def layout_specs(tw, cfg, room) -> list[dict]:
    specs = []
    noises = [(f"white {wsd:.2f} m, link bias {bsd:.2f} m, no body block", sweep_noise(wsd, bsd), wsd, bsd) for wsd, bsd in SWEEP]
    noises.append(("default (models.DEFAULT_NOISE)", None, None, None))
    sid = 0
    layouts = [("one-device", s, f"two boards on one device, {s:.1f} m apart") for s in SPACINGS]
    layouts += [("two-pucks", s, f"two separate pucks {s:.0f} m apart") for s in SEPARATIONS]
    layouts += [("three-pucks", None, "three separate pucks (thrown)"), ("one-puck-bearing", None, "one puck + the phone's bearing")]
    for kind, size, name in layouts:
        for nlabel, noise, wsd, bsd in noises:
            for bearing in (False, True):
                if kind == "one-puck-bearing" and not bearing:
                    continue
                if bearing and kind != "one-puck-bearing" and noise is not None:
                    continue                      # bearing arms at the default noise only (cost)
                n_tr = cfg["layout_bearing_trials"] if bearing else cfg["layout_trials"]
                for i in range(n_tr):
                    rng = np.random.default_rng([BASE_SEED, 77, SPACINGS.index(size) if kind == "one-device" else
                                                 (10 + SEPARATIONS.index(size) if kind == "two-pucks" else (20 if kind == "three-pucks" else 21)), i])
                    sid += 1
                    spec = {"seed": int(rng.integers(0, 2 ** 31)), "trial": i, "room": room,
                            "method": "uwb-twr+bearing" if bearing else "uwb-twr",
                            "world_noise": noise, "loc_noise": noise, "contexts": [True, False],
                            "arm": {"exp": "layout", "layout": kind, "size_m": size, "name": name, "noise": nlabel,
                                    "white_m": wsd, "bias_m": bsd, "bearing": bearing}}
                    if kind in ("one-device", "two-pucks"):
                        c, pts = _layout_geometry(tw, rng, room, kind, size)
                        sig = models.DEFAULT_NOISE["puck"]["position_sigma_m"]
                        if kind == "one-device":           # rigid device: one common position error, exact baseline
                            off = rng.normal(0.0, sig, 2)
                            bel = [{"id": p["id"], "x": p["x"] + off[0], "y": p["y"] + off[1], "sigma_m": sig} for p in pts]
                        else:
                            bel = [{"id": p["id"], "x": p["x"] + rng.normal(0, sig), "y": p["y"] + rng.normal(0, sig),
                                    "sigma_m": sig} for p in pts]
                        spec.update({"pucks_true": pts, "pucks_believed": bel})
                    else:
                        n = 3 if kind == "three-pucks" else 1
                        tmp = World(tw, seed=spec["seed"])
                        tmp.throw_pucks(room, n=n, seed=spec["seed"], strategy="scatter")
                        tp = tmp.truth()["pucks"]
                        c = (float(np.mean([p["x"] for p in tp])), float(np.mean([p["y"] for p in tp])))
                        spec.update({"throw": n})
                    spec["phone"] = _point_in_room(rng, tw, room, 0.3, centre=c, rmin=2.0, rmax=8.0)
                    spec["layout"] = {"kind": kind, "name": name, "centre": c}
                    specs.append(spec)
    return specs


# ---- main arms ---------------------------------------------------------------------------------------------------------

def main_specs(tw, cfg, room, neigh) -> list[dict]:
    specs = []
    for n in range(1, 6):
        for i in range(cfg["main_trials"] + cfg["mismatch_trials"]):
            rng = np.random.default_rng([BASE_SEED, 1, n, i])
            seed = int(rng.integers(0, 2 ** 31))
            where = room if rng.random() < 0.6 else neigh[int(rng.integers(0, len(neigh)))]
            phone = _point_in_room(rng, tw, where, 0.3)
            mism = i >= cfg["main_trials"]
            for m in METHODS:
                spec = {"seed": seed, "trial": i, "room": room, "throw": n, "phone": phone, "method": m,
                        "contexts": [True] if mism else [True, False],
                        "arm": {"exp": "mismatch" if mism else "main", "method": m, "pucks": n,
                                "phone_room_kind": "fire" if where == room else "neighbour"}}
                if mism:
                    spec["world_noise"] = scaled_noise(models.DEFAULT_NOISE, MISMATCH)
                specs.append(spec)
    return specs


# ---- aggregation -------------------------------------------------------------------------------------------------------

def _q(v, q):
    v = [x for x in v if x is not None]
    return round(float(np.quantile(v, q)), 3) if v else None


def _rate(v):
    v = [x for x in v if x is not None]
    return round(float(np.mean(v)), 3) if v else None


def aggregate(recs: list[dict]) -> dict:
    n = len(recs)
    out = {"n": n, "geometry_share": {g: round(sum(r["geometry"] == g for r in recs) / max(1, n), 3) for g in GEOMS}}
    fixes = [r for r in recs if r["geometry"] == "fix"]
    out["fix_n"] = len(fixes)
    out["fix_err_median_m"] = _q([r["fix_err_m"] for r in fixes], 0.5)
    out["fix_err_p90_m"] = _q([r["fix_err_m"] for r in fixes], 0.9)
    out["fix_sigma_median_m"] = _q([r["sigma_m"] for r in fixes], 0.5)
    out["hpd_area_median_m2"] = _q([r["hpd_area_m2"] for r in recs], 0.5)
    out["hpd_area_median_by_geometry_m2"] = {g: _q([r["hpd_area_m2"] for r in recs if r["geometry"] == g], 0.5) for g in GEOMS}
    pub = [r for r in recs if r["geometry"] != "none"]
    out["truth_inside_3sigma"] = _rate([r["inside3"] for r in pub])
    out["truth_inside_3sigma_by_geometry"] = {g: _rate([r["inside3"] for r in recs if r["geometry"] == g]) for g in GEOMS[:4]}
    out["truth_inside_locate_2sigma"] = _rate([r["inside_locate"] for r in pub])
    out["confidence_mean"] = _rate([r["confidence"] for r in pub])
    out["wrong_fix_rate_of_fixes"] = _rate([r["wrong_fix"] for r in fixes])
    out["wrong_fix_rate_of_trials"] = round(sum(bool(r["wrong_fix"]) for r in recs) / max(1, n), 3)
    out["fix_err_gt_1m_rate_of_fixes"] = _rate([r["fix_err_m"] > 1.0 for r in fixes])
    roomed = [r for r in recs if r["room_published"] is not None]
    out["room_published_share"] = round(len(roomed) / max(1, n), 3)
    out["room_accuracy"] = _rate([r["room_ok"] for r in roomed])
    out["pruned_any_share"] = round(sum(r["n_pruned"] > 0 for r in recs) / max(1, n), 3)
    out["pruned_near_truth_share"] = round(sum(bool(r["pruned_near_truth"]) for r in recs) / max(1, n), 3)
    out["est_ms_median"] = _q([r["est_ms"] for r in recs], 0.5)
    return out


def _key(a: dict, *names) -> str:
    return "|".join(f"{k}={a.get(k)}" for k in names)


def summarize_main(recs: list[dict]) -> dict:
    groups: dict[tuple, list] = {}
    for r in recs:
        a = r["arm"]
        groups.setdefault((a["exp"], a["method"], a["pucks"], r["twin"]), []).append(r)
    arms = []
    for (exp, m, n, twin), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], METHODS.index(kv[0][1]), kv[0][2], not kv[0][3])):
        row = {"exp": exp, "method": m, "pucks": n, "twin": twin, **aggregate(rs)}
        row["fire_room_only"] = aggregate([r for r in rs if r["arm"]["phone_room_kind"] == "fire"])
        arms.append(row)
    return {"arms": arms}


def _arm(arms, exp, m, n, twin=True):
    return next((a for a in arms if a["exp"] == exp and a["method"] == m and a["pucks"] == n and a["twin"] == twin), None)


def effects(arms) -> dict:
    eff = {"bearing": [], "twin_pruning": [], "mismatch": []}
    for n in range(1, 6):
        a, b = _arm(arms, "main", "uwb-twr", n), _arm(arms, "main", "uwb-twr+bearing", n)
        if a and b:
            eff["bearing"].append({"pucks": n, "fix_share_without": a["geometry_share"]["fix"], "fix_share_with": b["geometry_share"]["fix"],
                                   "fix_err_median_without_m": a["fix_err_median_m"], "fix_err_median_with_m": b["fix_err_median_m"],
                                   "hpd_area_median_without_m2": a["hpd_area_median_m2"], "hpd_area_median_with_m2": b["hpd_area_median_m2"],
                                   "wrong_fix_rate_without": a["wrong_fix_rate_of_trials"], "wrong_fix_rate_with": b["wrong_fix_rate_of_trials"]})
        for m in METHODS:
            on, off = _arm(arms, "main", m, n, True), _arm(arms, "main", m, n, False)
            if on and off:
                eff["twin_pruning"].append({"method": m, "pucks": n, "fix_share_off": off["geometry_share"]["fix"], "fix_share_on": on["geometry_share"]["fix"],
                                            "candidates_share_off": off["geometry_share"]["candidates"], "candidates_share_on": on["geometry_share"]["candidates"],
                                            "hpd_area_median_off_m2": off["hpd_area_median_m2"], "hpd_area_median_on_m2": on["hpd_area_median_m2"],
                                            "truth_inside_off": off["truth_inside_3sigma"], "truth_inside_on": on["truth_inside_3sigma"],
                                            "wrong_fix_rate_off": off["wrong_fix_rate_of_trials"], "wrong_fix_rate_on": on["wrong_fix_rate_of_trials"]})
            mm = _arm(arms, "mismatch", m, n, True)
            if on and mm:
                eff["mismatch"].append({"method": m, "pucks": n, "truth_inside_matched": on["truth_inside_3sigma"], "truth_inside_mismatch": mm["truth_inside_3sigma"],
                                        "wrong_fix_rate_matched": on["wrong_fix_rate_of_trials"], "wrong_fix_rate_mismatch": mm["wrong_fix_rate_of_trials"],
                                        "fix_err_median_matched_m": on["fix_err_median_m"], "fix_err_median_mismatch_m": mm["fix_err_median_m"]})
    return eff


def summarize_layouts(recs: list[dict]) -> dict:
    groups: dict[tuple, list] = {}
    for r in recs:
        a = r["arm"]
        groups.setdefault((a["layout"], a["size_m"], a["noise"], a["bearing"], r["twin"]), []).append(r)
    rows = []
    for (lay, size, noise, bearing, twin), rs in groups.items():
        a0 = rs[0]["arm"]
        row = {"layout": lay, "name": a0["name"], "size_m": size, "noise": noise, "white_m": a0["white_m"], "bias_m": a0["bias_m"],
               "bearing": bearing, "twin": twin, **aggregate(rs)}
        be = [r.get("bearing_err_deg") for r in rs]
        if any(v is not None for v in be):
            row["bearing_err_median_deg"] = _q(be, 0.5)
            row["bearing_err_p90_deg"] = _q(be, 0.9)
            row["mirror_ambiguity_rate"] = round(sum(r["geometry"] in ("candidates", "ring") for r in rs) / max(1, len(rs)), 3)
            row["mirror_wrong_side_rate"] = _rate([r.get("mirror_wrong_side") for r in rs])
            row["cross_range_median_m"] = _q([r.get("cross_range_m") for r in rs], 0.5)
            row["cross_range_p90_m"] = _q([r.get("cross_range_m") for r in rs], 0.9)
            row["tri_bearing_err_median_deg"] = _q([r.get("tri_bearing_err_deg") for r in rs], 0.5)
            row["tri_bearing_err_p90_deg"] = _q([r.get("tri_bearing_err_deg") for r in rs], 0.9)
            row["uninformed_bearing_err_median_deg"] = _q([r.get("uninformed_err_deg") for r in rs], 0.5)
            if a0["white_m"] is not None:
                n_s = float(np.median([r.get("tri_n") or 1 for r in rs]))
                s_eff = math.sqrt(a0["white_m"] ** 2 * (math.pi / 2.0) / max(n_s, 1.0) + a0["bias_m"] ** 2)
                pred = []
                for r in rs:
                    st = max(r.get("sin_theta") or 0.0, 1e-3)
                    sig = math.degrees(math.sqrt(2.0) * s_eff / (r["baseline_m"] * st))
                    pred.append(min(sig, 103.9))           # a uniform angle has sigma 180/sqrt(3) = 103.9 deg
                row["closed_form"] = {"formula": "sigma_theta = sqrt(2) * sigma_range_eff / (b * |sin theta|)",
                                      "sigma_range_eff_m": round(s_eff, 4),
                                      "sigma_range_eff_text": f"sqrt(white^2 * (pi/2) / n + bias^2), n = {n_s:g} samples per board "
                                                              "(median of n: pi/2 variance factor)",
                                      "predicted_sigma_theta_median_deg": _q(pred, 0.5),
                                      "predicted_median_abs_err_deg": _q([0.674 * p for p in pred], 0.5)}
        row["point_err_median_m"] = _q([r.get("point_err_m") for r in rs], 0.5)
        row["cross_range_point_median_m"] = _q([r.get("cross_range_point_m") for r in rs], 0.5)
        if lay in ("two-pucks", "one-device"):
            row["twin_single_rate"] = None
        rows.append(row)
    # paired twin effect: how often the twin leaves exactly one solution where twin-off had two
    by_trial: dict[tuple, dict] = {}
    for r in recs:
        a = r["arm"]
        by_trial.setdefault((a["layout"], a["size_m"], a["noise"], a["bearing"], r["trial"]), {})[r["twin"]] = r
    for row in rows:
        pairs = [v for k, v in by_trial.items() if k[:4] == (row["layout"], row["size_m"], row["noise"], row["bearing"]) and len(v) == 2]
        if pairs and row["twin"]:
            amb_off = [p for p in pairs if p[False]["geometry"] in ("candidates", "ring")]
            row["twin_single_rate"] = round(sum(p[True]["geometry"] == "fix" for p in amb_off) / max(1, len(amb_off)), 3) if amb_off else None
            row["twin_single_rate_n"] = len(amb_off)
    order = {"one-device": 0, "two-pucks": 1, "three-pucks": 2, "one-puck-bearing": 3}
    rows.sort(key=lambda r: (order[r["layout"]], r["size_m"] or 0, r["noise"], r["bearing"], not r["twin"]))
    return {"rows": rows}


def recommendation(rows) -> dict:
    """Numbers only: per layout at the default noise, twin on, without bearing (one puck: with bearing)."""
    rec = {}
    for r in rows:
        if r["noise"].startswith("default") and r["twin"] and (r["bearing"] == (r["layout"] == "one-puck-bearing")):
            key = r["name"]
            rec[key] = {"fix_share": r["geometry_share"]["fix"], "point_err_median_m": r["point_err_median_m"],
                        "fix_err_median_m": r["fix_err_median_m"], "hpd_area_median_m2": r["hpd_area_median_m2"],
                        "truth_inside_3sigma": r["truth_inside_3sigma"], "wrong_fix_rate_of_trials": r["wrong_fix_rate_of_trials"],
                        "bearing_err_median_deg": r.get("bearing_err_median_deg"),
                        "uninformed_bearing_err_median_deg": r.get("uninformed_bearing_err_median_deg"),
                        "tri_bearing_err_median_deg": r.get("tri_bearing_err_median_deg"),
                        "cross_range_median_m": r.get("cross_range_median_m"), "mirror_ambiguity_rate": r.get("mirror_ambiguity_rate"),
                        "twin_single_rate": r.get("twin_single_rate"), "n": r["n"]}
    for r in rows:
        if r["noise"].startswith("default") and r["twin"] and r["bearing"] and r["layout"] != "one-puck-bearing":
            rec[r["name"] + " + bearing"] = {"fix_share": r["geometry_share"]["fix"], "point_err_median_m": r["point_err_median_m"],
                                             "fix_err_median_m": r["fix_err_median_m"], "hpd_area_median_m2": r["hpd_area_median_m2"],
                                             "truth_inside_3sigma": r["truth_inside_3sigma"],
                                             "wrong_fix_rate_of_trials": r["wrong_fix_rate_of_trials"], "n": r["n"]}
    return rec


# ---- cold count arm ----------------------------------------------------------------------------------------------------

def cold_trial(spec) -> list[dict]:
    st = _state()
    tw = st["twin"]
    room, neigh = spec["room"], spec["neigh"]
    w = World(tw, seed=spec["seed"])
    w.throw_pucks(room, n=3, seed=spec["seed"])
    rng = np.random.default_rng([BASE_SEED, 9, spec["trial"]])
    k_room = int(rng.integers(0, 9))
    if k_room:
        w.add_cold_devices(room, k_room)
    for r in neigh:
        k = int(rng.integers(0, 4))
        if k:
            w.add_cold_devices(r, k)
    last = {}
    for k in range(1, int(12 * SIM_HZ) + 1):
        for o in w.step(1000.0 + k / SIM_HZ):
            if o["kind"] == "cold":
                last[o["puck"]] = o
    tr = w.truth()["cold"]["by_room"].get(room, {"devices": 0, "advertising": 0})
    out = []
    for pid, o in sorted(last.items()):
        count = o["near"] + o["mid"]
        # count_range: the same rule as beacon/session.py (W3) _presence, with no opt-in phone in the room
        lo = count // 2
        hi = max(lo, count + math.ceil(o["far"] / 2))
        out.append({"trial": spec["trial"], "puck": pid, "count": count, "far": o["far"], "range": [lo, hi],
                    "true_advertising": tr["advertising"], "true_devices": tr["devices"]})
    return out


def summarize_cold(recs) -> dict:
    err = [r["count"] - r["true_advertising"] for r in recs]
    return {"n_windows": len(recs), "n_trials": len({r["trial"] for r in recs}),
            "count_rule": "count = near + mid of one puck's 10 s window; count_range = [count // 2, count + ceil(far / 2)] "
                          "(beacon/session.py rule, no opt-in phone in the room)",
            "truth": "advertising devices physically in the puck's room (true_advertising) and all devices there (true_devices)",
            "count_minus_true_advertising_mean": round(float(np.mean(err)), 2) if err else None,
            "count_abs_err_median": _q([abs(e) for e in err], 0.5),
            "band_covers_true_advertising": _rate([r["range"][0] <= r["true_advertising"] <= r["range"][1] for r in recs]),
            "band_covers_true_devices": _rate([r["range"][0] <= r["true_devices"] <= r["range"][1] for r in recs]),
            "count_zero_while_devices_present": _rate([r["count"] == 0 for r in recs if r["true_devices"] > 0]),
            "bands_dbm": dict(models.COLD_BANDS_DBM),
            "mid_band_edge_los_m": round(10 ** ((models.DEFAULT_NOISE["ble"]["tx_1m_dbm"] - models.COLD_BANDS_DBM["mid"])
                                                / (10 * models.DEFAULT_NOISE["ble"]["n_los"])), 2)}


# ---- run ---------------------------------------------------------------------------------------------------------------

def _run(pool, fn, specs, chunks=4):
    out = []
    if pool is None:
        for s in specs:
            out.extend(fn(s))
        return out
    for r in pool.imap_unordered(fn, specs, chunksize=max(1, chunks)):
        out.extend(r)
    return out


# ---- two pucks (the owner's live demo) ----------------------------------------------------------------------------------

def _far_corner_puck(tw: Twin, room: str, inset: float = 0.35) -> dict:
    poly = tw.room(room)["polygon"]
    door = next(d["at"] for d in tw.data["doors"] if room in d["rooms"])
    corner = max(poly, key=lambda q: math.dist(q, door))
    cx, cy = sum(q[0] for q in poly) / len(poly), sum(q[1] for q in poly) / len(poly)
    ux, uy = cx - corner[0], cy - corner[1]
    n = math.hypot(ux, uy)
    k = inset * math.sqrt(2.0)
    return {"id": "P3", "x": round(corner[0] + k * ux / n, 2), "y": round(corner[1] + k * uy / n, 2)}


def two_puck_trial(spec: dict) -> list[dict]:
    """Live-like: UWB ranges only (no los / rssi / fp: the iPhone gives a distance), no Bluetooth, no bearing, a still
    phone, 3 s of ranges per puck. Twin on. Then the occupant's statement (the fire room) is applied with prune."""
    st = _state()
    tw = st["twin"]
    room = spec["room"]
    w = World(tw, seed=spec["seed"], eid_rotate_s=1e9)
    if spec["layout"] == TP_REF:
        rng = np.random.default_rng([spec["seed"], 31])
        sd = SIM_MODEL["throw"]["layout_truth_sd_m"]
        rec = spec["pucks_rec"]
        w.set_pucks([dict(p, x=p["x"] + rng.normal(0, sd), y=p["y"] + rng.normal(0, sd)) for p in rec], exact=True)
        believed = [dict(p, sigma_m=models.DEFAULT_NOISE["puck"]["position_sigma_m"]) for p in rec]
    else:
        believed = w.throw_pucks(room, n=2, seed=spec["seed"], strategy=spec["layout"])
    px, py = spec["phone"]
    w.upsert_phone("phone", px, py, uwb=True, pointing=False, chip="U1", report={"status": "trapped"})
    tr = PhoneTrack("x") if PhoneTrack else _DirectTrack("x")
    ctx = st["ctx"][True]
    t0 = 1_800_000_000.0 + (spec["seed"] % 100000)
    steps = int(round(OBS_S * SIM_HZ))
    last_s = spec.get("est_last_s", EST_LAST_S)
    e = None
    for k in range(1, steps + 1):
        now = t0 + k / SIM_HZ
        for o in w.step(now):
            if o["kind"] != "uwb":
                continue
            tr.add({"kind": "uwb", "t": o["t"], "puck": o["puck"], "eid": o["eid"], "d_m": o["d_m"]})
        t_rel = k / SIM_HZ
        if abs(t_rel * EST_HZ - round(t_rel * EST_HZ)) < 1e-6 and (not last_s or t_rel > OBS_S - last_s - 1e-9):
            e = tr.estimate(ctx, believed, now)
    label = tw.room(room)["label"]
    s = prune.apply_statement(e, room, label, f"the occupant says they are in {label}", "occupant-statement")
    g0, g1 = e["geometry"], s["geometry"]
    if g0 == "fix":
        outcome, pub = "twin_fix", e
    elif g0 == "candidates" and g1 == "fix":
        outcome, pub = "statement_fix", s
    elif g1 == "candidates":
        outcome, pub = "cannot_choose", s
    else:
        outcome, pub = "ring_or_other", s
    rec = {"layout": spec["layout"], "false_statement": spec["false_statement"], "trial": spec["trial"], "draw": spec["draw"],
           "phone": [round(px, 2), round(py, 2)], "truth_room": tw.locate(px, py)["room"], "outcome": outcome,
           "geometry_twin": g0, "geometry_after_statement": g1, "fix_err_m": None, "sigma_m": None, "wrong_fix": None,
           "hpd_area_m2": e.get("hpd_area_m2"), "conflict": bool(s.get("conflict")),
           "dismissed_rooms": [c.get("room") for c in e.get("candidates") or [] if c.get("room") != room] if outcome == "statement_fix" else [],
           "twin_pruned": len(e.get("pruned") or []), "n_uwb_pucks": sum(1 for r in (e.get("ranges") or []) if r.get("kind") == "uwb")}
    if pub["geometry"] == "fix":
        err = math.hypot(pub["x"] - px, pub["y"] - py)
        sig = float(pub.get("sigma_m") or 0.0)
        rec.update({"fix_err_m": round(err, 3), "sigma_m": sig, "wrong_fix": bool(err > 3.0 * sig or err > WRONG_FIX_M)})
    return [rec]


def two_puck_specs(tw: Twin, cfg: dict, room: str, neigh: list[str]) -> tuple[list[dict], dict]:
    specs = []
    rec2 = {lay: puck_layout.recommend(tw, room, n=2, strategy=lay) for lay in TP_LAYOUTS}
    ref = [dict(p) for p in rec2["door-wall"]["pucks"]] + [_far_corner_puck(tw, room)]
    fire_poly = tw.room(room)["polygon"]
    for i in range(cfg["tp_positions"]):
        rng = np.random.default_rng([BASE_SEED, 41, i])
        phone = _point_in_room(rng, tw, room, 0.3)
        for j in range(max(cfg["tp_draws"], cfg["tp_ref_draws"])):
            seed = int(np.random.default_rng([BASE_SEED, 42, i, j]).integers(0, 2 ** 31))
            for lay in TP_LAYOUTS + [TP_REF]:
                if j >= (cfg["tp_ref_draws"] if lay == TP_REF else cfg["tp_draws"]):
                    continue
                specs.append({"room": room, "layout": lay, "phone": phone, "seed": seed, "trial": i, "draw": j,
                              "false_statement": False, "pucks_rec": ref if lay == TP_REF else None})
    for i in range(cfg["tp_false_positions"]):
        rng = np.random.default_rng([BASE_SEED, 43, i])
        for _ in range(1000):
            where = neigh[int(rng.integers(0, len(neigh)))]
            phone = _point_in_room(rng, tw, where, 0.3)
            if _poly_dist(phone, fire_poly)[0] <= TP_FALSE_REACH_M:
                break
        for j in range(cfg["tp_draws"]):
            seed = int(np.random.default_rng([BASE_SEED, 44, i, j]).integers(0, 2 ** 31))
            for lay in TP_LAYOUTS:
                specs.append({"room": room, "layout": lay, "phone": phone, "seed": seed, "trial": i, "draw": j,
                              "false_statement": True, "pucks_rec": None})
    info = {lay: {k: rec2[lay][k] for k in ("pucks", "wall", "spacing_m", "mirror_falls", "certain", "why")} for lay in TP_LAYOUTS}
    info[TP_REF] = {"pucks": ref, "why": "the door-wall pair plus a third puck 0.35 m in from the corner farthest from the door"}
    return specs, info


def _tp_summary(rs: list[dict]) -> dict:
    n = len(rs)
    outs = ["twin_fix", "statement_fix", "cannot_choose", "ring_or_other"]
    out = {"n": n, "outcome_share": {o: round(sum(r["outcome"] == o for r in rs) / max(1, n), 3) for o in outs}}
    for o in ("twin_fix", "statement_fix"):
        fx = [r for r in rs if r["outcome"] == o]
        out[o] = {"n": len(fx), "wrong_fix_rate_of_these": _rate([r["wrong_fix"] for r in fx]),
                  "fix_err_median_m": _q([r["fix_err_m"] for r in fx], 0.5), "fix_err_p90_m": _q([r["fix_err_m"] for r in fx], 0.9),
                  "sigma_median_m": _q([r["sigma_m"] for r in fx], 0.5)}
    fx = [r for r in rs if r["fix_err_m"] is not None]
    out["all_fixes"] = {"n": len(fx), "share_of_trials": round(len(fx) / max(1, n), 3),
                        "wrong_fix_rate_of_fixes": _rate([r["wrong_fix"] for r in fx]),
                        "wrong_fix_rate_of_trials": round(sum(bool(r["wrong_fix"]) for r in rs) / max(1, n), 3),
                        "fix_err_median_m": _q([r["fix_err_m"] for r in fx], 0.5), "fix_err_p90_m": _q([r["fix_err_m"] for r in fx], 0.9)}
    out["hpd_area_median_m2"] = _q([r["hpd_area_m2"] for r in rs], 0.5)
    rooms: dict[str, int] = {}
    for r in rs:
        for q in r["dismissed_rooms"]:
            rooms[str(q)] = rooms.get(str(q), 0) + 1
    out["dismissed_mirror_rooms"] = rooms
    out["conflict_share"] = round(sum(r["conflict"] for r in rs) / max(1, n), 3)
    out["twin_pruned_share"] = round(sum(r["twin_pruned"] > 0 for r in rs) / max(1, n), 3)
    return out


def summarize_two_puck(recs: list[dict], info: dict, neigh: list[str]) -> dict:
    res = {"layouts": {}, "false_statement": {}}
    for lay in TP_LAYOUTS + [TP_REF]:
        rs = [r for r in recs if r["layout"] == lay and not r["false_statement"]]
        if rs:
            res["layouts"][lay] = {**_tp_summary(rs), "pucks": info[lay]}
    for lay in TP_LAYOUTS:
        rs = [r for r in recs if r["layout"] == lay and r["false_statement"]]
        if rs:
            sm = _tp_summary(rs)
            sm["statement_produced_wrong_fix_rate_of_trials"] = round(
                sum(r["outcome"] == "statement_fix" and bool(r["wrong_fix"]) for r in rs) / max(1, len(rs)), 3)
            sm["by_true_room"] = {q: _tp_summary([r for r in rs if r["truth_room"] == q])["outcome_share"] for q in neigh
                                  if any(r["truth_room"] == q for r in rs)}
            res["false_statement"][lay] = sm
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true", help="smoke run: a few trials per arm, every experiment")
    ap.add_argument("--main", action="store_true", help="method x pucks x twin + mismatch + cold -> results/eval_beacon.json")
    ap.add_argument("--layouts", action="store_true", help="two boards on one device vs separate pucks -> results/eval_layouts.json")
    ap.add_argument("--two-puck", action="store_true", help="the owner's two-puck demo layouts -> results/eval_two_puck.json")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--results-dir", default=str(ROOT / "results"))
    ap.add_argument("--figures-dir", default=str(ROOT / "figures"))
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--scale", type=float, default=1.0, help="multiply the trial counts")
    args = ap.parse_args(argv)
    run_all = not (args.main or args.layouts or args.two_puck)
    do_main, do_layouts, do_tp = run_all or args.main, run_all or args.layouts, run_all or args.two_puck
    t_start = time.time()
    cfg = dict(QUICK if args.quick else FULL)
    if args.scale != 1.0:
        cfg = {k: (max(1, int(round(v * args.scale))) if v else 0) for k, v in cfg.items()}
    res_dir, fig_dir = Path(args.results_dir), Path(args.figures_dir)
    res_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)
    st = _state()
    tw = st["twin"]
    room, neigh = fire_room_and_neighbours(tw)
    workers = max(1, min(4, args.workers))
    mp = get_context("fork") if hasattr(os, "fork") else get_context("spawn")
    pool = mp.Pool(workers, initializer=_init_worker) if workers > 1 else None
    timings: dict = {}
    written = []
    cmd = "scripts/eval_beacon.py" + "".join(f" --{k}" for k, v in (("quick", args.quick), ("main", args.main),
                                                                       ("layouts", args.layouts), ("two-puck", args.two_puck)) if v)
    common = {"simulated": True, "generated_by": cmd,
              "noise_model": {"noise": copy.deepcopy(models.DEFAULT_NOISE), "sim": copy.deepcopy(SIM_MODEL),
                              "text": "see the beacon/sim.py module docstring (stated model); radio constants: models.DEFAULT_NOISE"},
              "pipeline": PIPELINE, "obs_s": OBS_S, "sim_hz": SIM_HZ, "estimate_hz": EST_HZ, "estimate_last_s": EST_LAST_S,
              "base_seed": BASE_SEED, "fire_room": room, "neighbours": neigh, "trial_counts": cfg, "quick": bool(args.quick),
              "locator_rules": {k: v for k, v in locate.geometry_rules.items() if k != "text"}}
    mrecs = crecs = lrecs = tprecs = None
    cadence = tp_cadence = None
    try:
        if do_main:
            t = time.time()
            specs = main_specs(tw, cfg, room, neigh)
            mrecs = _run(pool, simulate, specs, chunks=6)
            timings["main_s"] = round(time.time() - t, 1)
            if cfg.get("cadence_check"):
                t = time.time()
                chk = [dict(sp, est_last_s=0) for sp in specs if sp["arm"]["exp"] == "main"]
                chk = chk[:: max(1, len(chk) // cfg["cadence_check"])][: cfg["cadence_check"]]
                crs = _run(pool, simulate, chk, chunks=3)
                idx = {(r["arm"]["method"], r["arm"]["pucks"], r["trial"], r["twin"]): r for r in mrecs if r["arm"]["exp"] == "main"}
                same = [idx[(r["arm"]["method"], r["arm"]["pucks"], r["trial"], r["twin"])]["geometry"] == r["geometry"]
                        and idx[(r["arm"]["method"], r["arm"]["pucks"], r["trial"], r["twin"])]["fix_err_m"] == r["fix_err_m"] for r in crs]
                cadence = {"text": "estimates every 0.25 s over the whole 3 s (as the server) vs only during the last second (this run)",
                           "records": len(same), "identical_geometry_and_fix": int(sum(same))}
                timings["cadence_check_s"] = round(time.time() - t, 1)
            t = time.time()
            crecs = _run(pool, cold_trial, [{"seed": BASE_SEED + 500 + i, "trial": i, "room": room, "neigh": neigh}
                                            for i in range(cfg["cold_trials"])], chunks=4)
            timings["cold_s"] = round(time.time() - t, 1)
        if do_layouts:
            t = time.time()
            lrecs = _run(pool, simulate, layout_specs(tw, cfg, room), chunks=6)
            timings["layouts_s"] = round(time.time() - t, 1)
        if do_tp:
            t = time.time()
            tspecs, tp_info = two_puck_specs(tw, cfg, room, neigh)
            tprecs = _run(pool, two_puck_trial, tspecs, chunks=8)
            timings["two_puck_s"] = round(time.time() - t, 1)
            if cfg.get("tp_cadence_check"):
                t = time.time()
                chk = [dict(sp, est_last_s=0) for sp in tspecs if not sp["false_statement"]][: cfg["tp_cadence_check"]]
                crs = _run(pool, two_puck_trial, chk, chunks=3)
                idx = {(r["layout"], r["trial"], r["draw"], r["false_statement"]): r for r in tprecs}
                same = [idx[(r["layout"], r["trial"], r["draw"], r["false_statement"])]["outcome"] == r["outcome"]
                        and idx[(r["layout"], r["trial"], r["draw"], r["false_statement"])]["fix_err_m"] == r["fix_err_m"] for r in crs]
                tp_cadence = {"text": "estimates every 0.25 s over 3 s vs only in the last second (this run)",
                              "records": len(same), "identical_outcome_and_fix": int(sum(same))}
                timings["two_puck_cadence_check_s"] = round(time.time() - t, 1)
    finally:
        if pool is not None:
            pool.close()
            pool.join()

    figs = not args.no_figures
    if do_layouts:
        lay = summarize_layouts(lrecs)
        layouts_json = {**common, "title": "SIMULATED: phone locating with two boards on one device vs separate pucks",
                        "questions": ["(a) two boards rigidly 0.1, 0.3, 0.5 m apart on ONE thrown device, phone 2 to 8 m away in the fire room",
                                      "(b) two separate pucks 2, 3, 4 m apart near the door",
                                      "(c) reference: three separate pucks; one puck plus the phone's bearing"],
                        "definitions": {
                            "bearing_err_deg": "angle between the true direction device-centre -> phone and the locator's pair bearing "
                                               "(debug.pair_bearing_deg), after resolving to the nearer of it and its mirror (pair_bearing_alt_deg)",
                            "mirror_ambiguity_rate": "share of trials whose published geometry keeps both sides (candidates or ring)",
                            "mirror_wrong_side_rate": "share of trials whose preferred side (more posterior mass) is the mirror of the truth",
                            "cross_range_m": "true distance x sin(resolved bearing error)",
                            "cross_range_point_m": "error of the published point (fix, or the nearest candidate) perpendicular to the true direction",
                            "tri_bearing_err_deg": "same as bearing_err_deg, from plain trilateration on the median range of each board "
                                                   "(independent of the locator)",
                            "uninformed_bearing_err_deg": "reference with no range information: the guess 'straight out from the device' "
                                                          "(perpendicular to the baseline), scored the same way; a bearing error near it carries "
                                                          "no information (the room around the device already limits the directions)",
                            "twin_single_rate": "of the trials where twin-off published two sides (candidates or ring), the share where twin-on "
                                                "published one fix (paired)",
                            "noise sweep": "white sigma 0.05 / 0.10 m x static link bias 0 / 0.05 / 0.10 m, body block off (for two boards on one "
                                           "device a body block is common to both links, which a per-link model cannot represent); "
                                           "'default' = models.DEFAULT_NOISE with body blocks; locator told the same noise (matched)",
                            "bearing arms": "U1 phone pointed at the nearest visible puck (aim error 20 deg), default noise only"},
                        "trials_per_arm": {"no_bearing": cfg["layout_trials"], "bearing": cfg["layout_bearing_trials"]},
                        **lay, "recommendation": recommendation(lay["rows"])}
        if figs:
            t = time.time()
            layout_figures(layouts_json, fig_dir, tw, room)
            timings["layout_figures_s"] = round(time.time() - t, 1)
        _write(res_dir / "eval_layouts.json", {**layouts_json, "timings_s": timings})
        written.append("eval_layouts.json")
    if do_tp:
        tp = summarize_two_puck(tprecs, tp_info, neigh)
        tp_json = {**common, "title": "SIMULATED: the owner's two-puck demo: how often two pucks give one answer, and how often it is wrong",
                   "live_like": "UWB ranges only (no los, rssi or fp fields: the iPhone gives a distance), no Bluetooth, no bearing, a still "
                                "U1 phone, 3 s of ranges per puck at about 5 Hz; default noise model (body blocks, static link bias); twin on",
                   "definitions": {
                       "twin_fix": "the locator (twin on) publishes a fix by itself",
                       "statement_fix": "the locator publishes two candidates and beacon.prune.apply_statement with the occupant's stated "
                                        "suite (the fire room) leaves one: a fix that rests on what was said",
                       "cannot_choose": "two (or more) candidates remain after the statement (both in the room, or none in it)",
                       "ring_or_other": "ring, zone or none",
                       "wrong_fix": f"a published fix whose error exceeds 3 sigma_m or {WRONG_FIX_M} m",
                       "false_statement": f"the phone is really in a neighbouring room ({', '.join(neigh)}, within {TP_FALSE_REACH_M:g} m of the fire "
                                          "room) but the occupant says the fire room",
                       "positions": f"{cfg['tp_positions']} phone positions uniform over the fire room (>= 0.3 m from walls) x "
                                    f"{cfg['tp_draws']} noise draws per layout (the reference: {cfg['tp_ref_draws']}); "
                                    f"false statement: {cfg['tp_false_positions']} positions x {cfg['tp_draws']} draws",
                       "pucks": "placed by hand at beacon.layout.recommend's spots: the locator gets those spots, the truth is within "
                                "a few cm (N(0, 0.03 m) per axis)"},
                   **tp, "cadence_check": tp_cadence}
        if figs:
            t = time.time()
            two_puck_figures(tp_json, tprecs, fig_dir, tw, room)
            timings["two_puck_figures_s"] = round(time.time() - t, 1)
        _write(res_dir / "eval_two_puck.json", {**tp_json, "timings_s": timings})
        written.append("eval_two_puck.json")
    if do_main:
        main_ = summarize_main(mrecs)
        beacon_json = {**common, "title": "SIMULATED evaluation of the Beacon locator (method x pucks x twin, model mismatch, cold count)",
                       "definitions": {
                           "geometry_share": "share of trials whose published geometry (after 3 s, PhoneTrack hysteresis) is each kind",
                           "fix_err_m": "distance from the published fix to the true phone position",
                           "truth_inside_3sigma": "fix: error <= 3 sigma_m; candidates: within 3 sigma_m of a candidate; ring: |d - r_m| <= "
                                                  "3 sigma_m and inside the arcs (5 deg slack); zone: inside the zone polygon. Over trials with a "
                                                  "published geometry (not 'none')",
                           "truth_inside_locate_2sigma": "locate.inside(): the 2-sigma regions the locator's `confidence` uses",
                           "wrong_fix": "a published fix whose error exceeds 3 sigma_m",
                           "room_accuracy": "published room == true room, over trials that publish a room",
                           "pruned_near_truth_share": "share of trials where a candidate the twin removed was within 1 m of the truth",
                           "pucks": "thrown from the door (Scout's scatter model, strategy 'scatter') for every puck count, so the counts are "
                                    "comparable; the owner's hand-placed two-puck layouts are in results/eval_two_puck.json",
                           "phone positions": f"60 % in the fire room, 40 % in a neighbour ({', '.join(neigh)}), >= 0.3 m from walls",
                           "mismatch": f"the world's noise = scaled_noise(DEFAULT_NOISE, {MISMATCH}) (every sigma, bias, loss and "
                                       "outlier/dropout/body-block probability x1.5); the locator assumes DEFAULT_NOISE; twin on"},
                       **main_, "effects": effects(main_["arms"]), "cold": summarize_cold(crecs), "cadence_check": cadence}
        if figs:
            t = time.time()
            beacon_json["storyboard"] = fig_storyboard(tw, room, fig_dir)
            main_figures(beacon_json, fig_dir)
            timings["main_figures_s"] = round(time.time() - t, 1)
        timings["total_s"] = round(time.time() - t_start, 1)
        _write(res_dir / "eval_beacon.json", {**beacon_json, "timings_s": timings})
        written.append("eval_beacon.json")
    timings["total_s"] = round(time.time() - t_start, 1)
    print(json.dumps({"simulated": True, "timings_s": timings, "results": [str(res_dir / w_) for w_ in written]}))
    return 0


def _write(path: Path, obj: dict):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
