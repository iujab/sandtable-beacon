#!/usr/bin/env python3
"""Honesty watchdog (SIMULATED): drive the simulator and the real session through what an adversarial judge did, and
check at every step that nothing published claims more than the measurements support.

    nice .venv/bin/python scripts/honesty_check.py            # all scenarios -> results/honesty_check.json; exit 1 on failure
    nice .venv/bin/python scripts/honesty_check.py --short    # a quick version (the test runs it)

No HTTP server: `beacon.sim.World` produces observations, `beacon.session.BeaconSession` ingests them and steps at 4 Hz,
exactly as `beacon/server.py` does (`session.ingest`, `session.step`, `session.set_pucks`). The session's clock is the
simulated clock (the module's `time.time` is redirected to it for the run), so a 2-minute scene runs in seconds.

Invariants checked for every published phone at every step:
  * coordinates only with geometry == "fix" (x, y, cov, geo, node are null otherwise),
  * a stale phone has no dot (x, y null),
  * a phone that said `bye` is gone at the next step, and stays gone,
  * a phone whose user pressed "I am out" has geometry "none",
  * a phone silent for longer than the retention time is dropped.
For every published fix: the error against the simulator's truth; a fix is WRONG when the error exceeds
max(3 sigma_m, 1.0 m). A scenario fails when more than 3 % of its fix-steps are wrong or an invariant breaks.
Everything here is simulated; the noise model is models.DEFAULT_NOISE and beacon/sim.py (live-like: distance only).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time as _time
from multiprocessing import get_context
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beacon import layout as puck_layout, models  # noqa: E402
from beacon import session as session_mod  # noqa: E402
from beacon.sim import World, load_scenario_file  # noqa: E402
from beacon.twin import Twin  # noqa: E402

TWIN_JSON = ROOT / "fixtures" / "building" / "twin.json"
SITE_JSON = ROOT / "fixtures" / "building" / "site.json"
T0 = 1_800_000_000.0
SIM_HZ, STEP_HZ = 10.0, 4.0
WRONG_SHARE_MAX = 0.03
WRONG_MIN_M = 1.0
SEED = 20261114
SEEDS_TEXT = "each scene runs twice: with the demo scenario's seed (what the board shows) and with one other seed"


class _SimClock:
    """Stands in for the `time` module inside beacon.session: time() is the simulated clock, the rest is real."""

    def __init__(self, t: float):
        self.t = t

    def time(self) -> float:
        return self.t

    def __getattr__(self, name):
        return getattr(_time, name)


class Run:
    def __init__(self, name: str, *, board_sharing: str = "pairs", retention_s: float | None = None, seed: int = SEED):
        self.name = name
        self.twin_raw = json.loads(TWIN_JSON.read_text(encoding="utf-8"))
        self.site_raw = json.loads(SITE_JSON.read_text(encoding="utf-8"))
        self.twin = Twin(self.twin_raw)
        self.sc = load_scenario_file("suite-310")
        self.room = self.sc["room"]
        self.floor = self.twin.room(self.room)["floor"]
        self.label = self.twin.room(self.room)["label"]
        self.clock = _SimClock(T0)
        self._saved_time = session_mod.time
        session_mod.time = self.clock
        self.world = World(self.twin_raw, seed=seed, board_sharing=board_sharing)
        kw = {"retention_s": retention_s} if retention_s is not None else {}
        self.session = session_mod.BeaconSession(self.twin_raw, self.site_raw, source="sim", simulated=True,
                                                 use_roster=False, persist_path=None, **kw)
        self.t = T0
        self.k = 0
        self.last_event, self.last_event_t = "start", T0
        self.bye_eids: set[str] = set()
        self.bye_ids: set[str] = set()
        self.silent: dict[str, float] = {}          # eid -> time it went silent (removed from the world)
        self.steps = self.phone_steps = self.fix_steps = self.wrong = 0
        self.violations: dict[str, dict] = {}
        self.worst = None
        self.events: list[str] = []
        self.rng_state = 0

    def close(self):
        session_mod.time = self._saved_time

    # ---- actions
    def event(self, text: str):
        self.last_event, self.last_event_t = text, self.t
        self.events.append(f"t={self.t - T0:.1f} s: {text}")

    def place(self, pucks: list[dict], text: str, merge: bool = False):
        """The operator puts pucks on the board at these spots; the physical boards are within a few cm of them."""
        import numpy as np
        rng = np.random.default_rng([SEED, 7, self.rng_state])
        self.rng_state += 1
        board = [{"id": p["id"], "x": float(p["x"]), "y": float(p["y"])} for p in pucks]
        if merge:
            keep = {p["id"]: p for p in self.session.pucks}
            for p in board:
                keep[p["id"]] = p
            board = [{"id": p["id"], "x": float(p["x"]), "y": float(p["y"])} for p in sorted(keep.values(), key=lambda q: q["id"])]
        truth = [{"id": p["id"], "x": p["x"] + float(rng.normal(0, 0.03)), "y": p["y"] + float(rng.normal(0, 0.03))} for p in board]
        self.world.set_pucks(truth, exact=True)
        self.session.set_pucks(board, room=self.room, floor=self.floor)
        self.event(text)

    def phone(self, key: str, x: float, y: float, text: str | None = None, **kw):
        self.world.upsert_phone(key, x, y, **kw)
        if text:
            self.event(text)

    def scenario_phone(self, key: str, text: str):
        ph = next(p for p in self.sc["phones"] if p["key"] == key)
        self.world.upsert_phone(key, ph["x"], ph["y"], floor=ph.get("floor"), opt_in=ph.get("opt_in", True), uwb=ph.get("uwb", True),
                                pointing=ph.get("pointing", False), report=ph.get("report"), chip=ph.get("chip", "U1"),
                                camera_assist=ph.get("camera_assist", False), model=ph.get("model"))
        self.event(text)

    def silence(self, key: str, text: str):
        eid = self._eid(key)
        self.world.remove_phone(key)
        if eid:
            self.silent[eid] = self.t
        self.event(text)

    def bye(self, key: str, text: str):
        eid = self._eid(key)
        self.world.remove_phone(key)
        if eid:
            self.session.ingest({"kind": "bye", "t": self.t, "eid": eid})
            self.bye_eids.add(eid)
            pid = self.session._eid_to_id.get(eid) if hasattr(self.session, "_eid_to_id") else None
            if pid:
                self.bye_ids.add(pid)
        self.event(text)

    def scale(self, s: float):
        self.session.set_rig_scale(s)
        self.event(f"exercise scale set to {s:g}")

    def _eid(self, key: str) -> str | None:
        return next((p["eid"] for p in self.world.truth()["phones"] if p["key"] == key), None)

    # ---- the clock
    def run(self, seconds: float, during=None):
        n = int(round(seconds * SIM_HZ))
        step_every = int(round(SIM_HZ / STEP_HZ))          # world steps per session step
        for _ in range(n):
            self.k += 1
            self.t = T0 + self.k / SIM_HZ
            self.clock.t = self.t
            if during is not None:
                during(self)
            for o in self.world.step(self.t):
                try:
                    self.session.ingest(o)
                except ValueError as exc:
                    self._violate("observation rejected", str(exc))
            if self.k % step_every == 0:
                self.check(self.session.step(self.t))

    # ---- checks
    def _violate(self, kind: str, detail: str):
        v = self.violations.setdefault(kind, {"count": 0, "first": None})
        v["count"] += 1
        if v["first"] is None:
            v["first"] = {"t_s": round(self.t - T0, 2), "detail": detail, "after": self.last_event,
                          "since_s": round(self.t - self.last_event_t, 2)}

    def check(self, b: dict):
        self.steps += 1
        truth = {}
        for p in self.world.truth()["phones"]:
            if p.get("eid"):
                truth[p["eid"]] = p
                if p.get("prev_eid"):
                    truth.setdefault(p["prev_eid"], p)
        for ph in b.get("phones") or []:
            self.phone_steps += 1
            g = ph.get("geometry")
            pid, eid = ph.get("id"), ph.get("eid")
            if g != "fix":
                bad = [k for k in ("x", "y", "cov", "geo", "node") if ph.get(k) is not None]
                if bad:
                    self._violate("coordinates without a fix", f"{pid} geometry {g} carries {bad}")
            if ph.get("stale") and (ph.get("x") is not None or ph.get("y") is not None):
                self._violate("stale phone with a dot", f"{pid} stale at ({ph.get('x')}, {ph.get('y')})")
            if eid in self.bye_eids or pid in self.bye_ids:
                self._violate("phone published after bye", f"{pid} ({eid})")
            if (ph.get("report") or {}).get("safe_outside") and g != "none":
                self._violate("safe-outside phone with a position", f"{pid} geometry {g}")
            gone = self.silent.get(eid)
            if gone is not None and self.t - gone > self.session.retention_s + 2.0:
                self._violate("phone kept past retention", f"{pid} silent for {self.t - gone:.0f} s")
            if g == "fix" and not ph.get("stale") and ph.get("x") is not None:
                tr = truth.get(eid)
                if tr is None:
                    continue
                self.fix_steps += 1
                err = math.hypot(ph["x"] - tr["x"], ph["y"] - tr["y"])
                sig = float(ph.get("sigma_m") or 0.0)
                thr = max(3.0 * sig, WRONG_MIN_M)
                if err > thr:
                    self.wrong += 1
                ratio = err / thr
                if self.worst is None or ratio > self.worst["ratio"]:
                    self.worst = {"ratio": round(ratio, 3), "t_s": round(self.t - T0, 2), "phone": pid, "truth_key": tr["key"],
                                  "err_m": round(err, 3), "sigma_m": sig, "threshold_m": round(thr, 3),
                                  "published": [ph["x"], ph["y"]], "truth": [round(tr["x"], 2), round(tr["y"], 2)],
                                  "truth_room": tr.get("room"), "published_room": ph.get("room"),
                                  "assumptions": [a.get("text") for a in ph.get("assumptions") or []],
                                  "after": self.last_event, "since_s": round(self.t - self.last_event_t, 2)}

    def result(self) -> dict:
        wrong_share = self.wrong / self.fix_steps if self.fix_steps else 0.0
        ok = wrong_share <= WRONG_SHARE_MAX and not self.violations
        return {"name": self.name, "board_sharing": self.world.sim_model["board_sharing"],
                "reports_los": self.world.sim_model["uwb_link"]["reports_los"],
                "duration_s": round(self.t - T0, 1), "steps": self.steps, "phone_steps": self.phone_steps,
                "fix_steps": self.fix_steps, "fix_share_of_phone_steps": round(self.fix_steps / self.phone_steps, 3) if self.phone_steps else 0.0,
                "wrong_fix_steps": self.wrong, "wrong_share_of_fix_steps": round(wrong_share, 4),
                "invariant_violations": self.violations, "worst_fix": self.worst, "events": self.events, "pass": ok}


# ---- scenarios -----------------------------------------------------------------------------------------------------------

def _layouts(r: Run) -> dict:
    return {s: puck_layout.recommend(r.twin, r.room, n=2, strategy=s)["pucks"] for s in ("door-wall", "careless")}


def sc_walkthrough(r: Run, f: float):
    L = _layouts(r)
    r.place([L["door-wall"][0]], "puck 1 placed at the door")
    r.run(5 * f)
    r.scenario_phone("trapped-310", "trapped phone starts sharing")
    r.run(15 * f)
    r.place([L["door-wall"][0], L["careless"][1]], "careless puck 2 dropped mid-room")
    r.run(20 * f)
    r.place([L["door-wall"][0], L["door-wall"][1]], "puck 2 slid to the door wall")
    r.run(20 * f)
    r.scenario_phone("iphone15-310", "second phone starts sharing")
    r.run(20 * f)
    r.scenario_phone("out-310", "a phone outside presses 'I am out'")
    r.run(15 * f)


def sc_careless_hold(r: Run, f: float):
    L = _layouts(r)
    r.place(L["careless"], "both pucks mid-room (careless layout)")
    r.scenario_phone("trapped-310", "trapped phone sharing")
    r.scenario_phone("iphone15-310", "second phone sharing")
    r.run(120 * f)


def sc_puck_moves(r: Run, f: float):
    L = _layouts(r)
    r.place(L["door-wall"], "door-wall pucks")
    r.scenario_phone("trapped-310", "trapped phone sharing")
    r.scenario_phone("iphone15-310", "second phone sharing")
    r.run(15 * f)
    p1, p2 = L["door-wall"]
    moves = [("P2 moved far (3.5 m into the room)", {"id": "P2", "x": p2["x"] - 3.5, "y": p2["y"] + 0.5}),
             ("P2 nudged 0.4 m", {"id": "P2", "x": p2["x"] - 3.1, "y": p2["y"] + 0.7}),
             ("P1 moved far (to the far wall)", {"id": "P1", "x": 1.0, "y": 7.6}),
             ("P1 nudged 0.3 m", {"id": "P1", "x": 1.3, "y": 7.5}),
             ("both back on the door wall", None)]
    for text, mv in moves:
        if mv is None:
            r.place(L["door-wall"], text)
        else:
            r.place([mv], text, merge=True)
        r.run(15 * f)


def sc_drag(r: Run, f: float):
    L = _layouts(r)
    r.place(L["door-wall"], "door-wall pucks")
    r.phone("dragged", 2.0, 2.0, "phone placed deep in the room", chip="U1", model="iPhone 14",
            report={"status": "trapped", "people": 1, "home_suite": "310"})
    r.run(8 * f)
    path = [(2.0, 2.0), (6.0, 3.0), (8.6, 7.2), (10.7, 7.2), (10.7, 11.0)]
    r.event("phone dragged across the room, through the door and up the corridor")
    _walk(r, "dragged", path, 0.5, f)
    r.run(10 * f)
    r.phone("dragged2", 4.0, 5.5, "second phone placed", chip="U1", model="iPhone 14",
            report={"status": "injured", "people": 1, "home_suite": "310"})
    r.run(8 * f)
    r.event("second phone dragged straight through the wall into Suite 309")
    _walk(r, "dragged2", [(4.0, 5.5), (4.0, 12.0)], 0.5, f)
    r.run(10 * f)


def _walk(r: Run, key: str, path, speed: float, f: float):
    dt = 0.5
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / (speed * dt)))
        for i in range(1, n + 1):
            x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
            r.phone(key, x, y)
            r.last_event = f"{key} dragged to ({x:.1f}, {y:.1f}) in {r.twin.locate(x, y)['room_label']}"
            r.last_event_t = r.t
            r.run(dt * max(f, 0.5))


def sc_scale(r: Run, f: float):
    L = _layouts(r)
    r.place(L["door-wall"], "door-wall pucks")
    r.scenario_phone("trapped-310", "trapped phone sharing")
    r.run(20 * f)
    r.scale(3.0)
    r.run(20 * f)
    r.scale(1.0)
    r.run(20 * f)


def sc_stops(r: Run, f: float):
    L = _layouts(r)
    r.place(L["door-wall"], "door-wall pucks")
    r.scenario_phone("trapped-310", "trapped phone sharing")
    r.run(15 * f)
    r.silence("trapped-310", "the phone stops (battery dead): no more observations")
    r.run(max(r.session.stale_s, r.session.retention_s) + 15)


def sc_bye(r: Run, f: float):
    L = _layouts(r)
    r.place(L["door-wall"], "door-wall pucks")
    r.scenario_phone("trapped-310", "trapped phone sharing")
    r.scenario_phone("iphone15-310", "second phone sharing")
    r.run(15 * f)
    r.bye("trapped-310", "trapped phone pressed 'Stop sharing' (bye)")
    r.run(15 * f)


def sc_sharing(r: Run, f: float):
    L = _layouts(r)
    r.place(L["door-wall"], "door-wall pucks")
    r.scenario_phone("trapped-310", "trapped phone sharing")
    r.scenario_phone("iphone15-310", "second phone sharing")
    r.run(60 * f)


SCENARIOS = [
    ("walkthrough", sc_walkthrough, {}),
    ("careless-hold-120s", sc_careless_hold, {}),
    ("puck-moved-while-ranging", sc_puck_moves, {}),
    ("phone-dragged-into-next-room", sc_drag, {}),
    ("exercise-scale-changed", sc_scale, {}),
    ("phone-stops-stale-then-dropped", sc_stops, {"retention_s": 60.0}),
    ("bye", sc_bye, {}),
    ("two-phones-board-sharing-pairs", sc_sharing, {"board_sharing": "pairs"}),
    ("two-phones-board-sharing-turns", sc_sharing, {"board_sharing": "turns"}),
]


def run_one(args) -> dict:
    name, short, seed = args
    fn, kw = next((f, k) for n, f, k in SCENARIOS if n == name)
    try:
        os.nice(5)
    except (AttributeError, OSError):
        pass
    r = Run(name, seed=seed, **kw)
    try:
        fn(r, 0.25 if short else 1.0)
        res = r.result()
        res["seed"] = seed
        return res
    finally:
        r.close()


def merge(parts: list[dict]) -> dict:
    """One scenario over several seeds: counts summed, worst case over all, violations merged."""
    out = {k: parts[0][k] for k in ("name", "board_sharing", "reports_los")}
    for k in ("steps", "phone_steps", "fix_steps", "wrong_fix_steps"):
        out[k] = sum(p[k] for p in parts)
    out["duration_s"] = sum(p["duration_s"] for p in parts)
    out["fix_share_of_phone_steps"] = round(out["fix_steps"] / out["phone_steps"], 3) if out["phone_steps"] else 0.0
    out["wrong_share_of_fix_steps"] = round(out["wrong_fix_steps"] / out["fix_steps"], 4) if out["fix_steps"] else 0.0
    viol: dict = {}
    for p in parts:
        for k, v in p["invariant_violations"].items():
            m = viol.setdefault(k, {"count": 0, "first": v["first"], "seed": p["seed"]})
            m["count"] += v["count"]
    out["invariant_violations"] = viol
    worst = [dict(p["worst_fix"], seed=p["seed"]) for p in parts if p.get("worst_fix")]
    out["worst_fix"] = max(worst, key=lambda w: w["ratio"]) if worst else None
    out["per_seed"] = [{k: p[k] for k in ("seed", "steps", "fix_steps", "wrong_fix_steps", "wrong_share_of_fix_steps", "events")} for p in parts]
    out["pass"] = out["wrong_share_of_fix_steps"] <= WRONG_SHARE_MAX and not viol
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--short", action="store_true", help="quarter-length scenes (the test)")
    ap.add_argument("--out", default=str(ROOT / "results" / "honesty_check.json"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default=None, help="comma-separated scenario names")
    args = ap.parse_args(argv)
    names = [n for n, _, _ in SCENARIOS if not args.only or n in args.only.split(",")]
    seeds = [int(load_scenario_file("suite-310").get("seed") or 310), SEED]
    jobs = [(n, args.short, sd) for n in names for sd in seeds]
    t0 = _time.time()
    workers = max(1, min(4, args.workers))
    if workers > 1 and len(jobs) > 1:
        mp = get_context("fork") if hasattr(os, "fork") else get_context("spawn")
        with mp.Pool(workers) as pool:
            parts = pool.map(run_one, jobs)
    else:
        parts = [run_one(j) for j in jobs]
    res = [merge([p for p in parts if p["name"] == n]) for n in names]
    import beacon.locate as locate
    out = {"simulated": True, "generated_by": "scripts/honesty_check.py" + (" --short" if args.short else ""),
           "what": "adversarial watchdog: beacon.sim.World + beacon.session.BeaconSession on a simulated clock, the judge's moves",
           "thresholds": {"wrong_fix": f"error > max(3 sigma_m, {WRONG_MIN_M} m)", "max_wrong_share_of_fix_steps": WRONG_SHARE_MAX},
           "invariants": ["coordinates (x, y, cov, geo, node) only with geometry == 'fix'", "a stale phone has no dot",
                          "a phone that said bye is not published again", "a safe-outside phone has geometry 'none'",
                          "a silent phone is dropped after the retention time", "every simulated observation is accepted"],
           "observations": "live-like: UWB distance only (no los/rssi/fp), as from an iPhone",
           "seeds": {"values": seeds, "text": SEEDS_TEXT},
           "noise_model": models.DEFAULT_NOISE,
           "locator_mirror_guarantee": "mirror_guarantee" in getattr(locate, "geometry_rules", {}),
           "scenarios": {r["name"]: r for r in res}, "pass": all(r["pass"] for r in res),
           "failed": [r["name"] for r in res if not r["pass"]], "runtime_s": round(_time.time() - t0, 1)}
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)
    for r in res:
        print(f"{'PASS' if r['pass'] else 'FAIL'} {r['name']}: {r['fix_steps']} fix-steps, wrong {100 * r['wrong_share_of_fix_steps']:.1f}%, "
              f"violations {sorted(r['invariant_violations'])}")
    print(json.dumps({"simulated": True, "pass": out["pass"], "failed": out["failed"], "runtime_s": out["runtime_s"]}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
