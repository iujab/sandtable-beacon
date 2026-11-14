"""The owner's live setup as a scene, and a frozen recorded run of it, for the hands-free `beacon` exercise.

Setup (owner, 12:35): exactly TWO pucks, tape-measured and clicked onto the plan (placed, not thrown, not self-located), and
two victims' iPhones. Each puck gives a distance only, so each phone is two rings and two candidate points, mirror images
across the line between the pucks. The positions below are chosen by computing where each mirror image lands:
  - phone 1: its mirror falls outside the building (south of the exterior wall), so the twin removes it and it becomes a fix;
  - phone 2: its mirror stays inside the same room, well clear of the walls, so both candidates stay possible (an honest
    "cannot choose": search both, or move a puck).

    python scripts/make_beacon_story.py            write fixtures/beacon/two_puck_scene.json and record fixtures/beacon/story_two_pucks.json
    python scripts/make_beacon_story.py --scene    the scene only (no recording)

The recording runs the VENDORED Beacon module (vendor/beacon, lane L16's work) in-process, simulated, and keeps the Beacons
objects at the script's steps. The hands-free script replays them, so the agent sees the same objects on every run (the
live simulator has ranging noise: rounded radii and weights could flip between runs and recorded model turns would miss).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SCENE = ROOT / "fixtures" / "beacon" / "two_puck_scene.json"
STORY = ROOT / "fixtures" / "beacon" / "story_two_pucks.json"

PUCKS = [{"id": "P1", "x": 7.6, "y": 2.2}, {"id": "P2", "x": 3.4, "y": 1.9}]         # tape-measured inside the fire room
PHONES = [
    {"key": "victim-phone-1", "x": 2.0, "y": 6.3,
     "report": {"status": "trapped", "text": "Two of us in the back office, smoke under the door", "people": 2, "can_move": False,
                "battery_pct": 38, "floor_reported": 3, "home_suite": "310"}},
    {"key": "victim-phone-2", "x": 6.0, "y": 0.9,
     "report": {"status": "injured", "text": "Fell near the window, leg hurt", "people": 1, "can_move": False,
                "battery_pct": 71, "floor_reported": 3, "home_suite": "310"}},
]


def mirror(p, a, b):
    ux, uy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(ux, uy)
    ux, uy = ux / L, uy / L
    k = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
    fx, fy = a[0] + k * ux, a[1] + k * uy
    return [round(2 * fx - p[0], 2), round(2 * fy - p[1], 2)]


def _pip(pt, poly):
    x, y = pt
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _wall_dist(pt, poly):
    best = 1e9
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        vx, vy = b[0] - a[0], b[1] - a[1]
        L2 = vx * vx + vy * vy
        k = max(0.0, min(1.0, ((pt[0] - a[0]) * vx + (pt[1] - a[1]) * vy) / L2)) if L2 else 0.0
        best = min(best, math.hypot(pt[0] - a[0] - k * vx, pt[1] - a[1] - k * vy))
    return best


def scene() -> dict:
    from sandtable.spatial import Twin
    twin = Twin(json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text()))
    room = twin.room("r310")
    outline = twin.floor(room["floor"])["outline"]
    a, b = (PUCKS[0]["x"], PUCKS[0]["y"]), (PUCKS[1]["x"], PUCKS[1]["y"])
    phones = []
    for i, ph in enumerate(PHONES):
        t = (ph["x"], ph["y"])
        m = mirror(t, a, b)
        assert _pip(t, room["polygon"]) and _wall_dist(t, room["polygon"]) >= 0.5, f"phone {i + 1} must stand inside the room"
        out_bldg = not _pip(m, outline)
        in_room = _pip(m, room["polygon"]) and _wall_dist(m, room["polygon"]) >= 0.5
        expect = "fix: the mirror candidate is outside the building, the twin removes it" if out_bldg else \
                 "candidates: the mirror stays inside the room, both remain possible" if in_room else "check: the mirror lands near a wall"
        phones.append({**ph, "truth": {"x": ph["x"], "y": ph["y"]}, "mirror": {"x": m[0], "y": m[1], "outside_building": out_bldg,
                       "inside_room_clear_of_walls": in_room, "apart_m": round(math.dist(t, m), 2)}, "expect": expect})
    assert phones[0]["mirror"]["outside_building"], "phone 1's mirror must leave the building"
    assert phones[1]["mirror"]["inside_room_clear_of_walls"] and phones[1]["mirror"]["apart_m"] >= 1.5, "phone 2's mirror must stay in the room"
    return {"schema": "sandtable.beacon_scene/1", "title": "Two pucks, two phones (the owner's live setup)", "room": "r310", "room_label": room["label"],
            "floor": room["floor"],
            "note": "Two UWB pucks placed by hand at tape-measured positions (distance only, no direction) and two victims' phones. "
                    "Phone positions are SIMULATED truth for the simulator; on real hardware the phones are wherever the people are.",
            "pucks": [{**p, "placed": "by hand", "how": "tape-measured and clicked onto the plan"} for p in PUCKS],
            "puck_line": {"from": "P1", "to": "P2"}, "phones": phones}


def record(sc: dict) -> dict:
    """Run the vendored Beacon module in-process and keep the Beacons objects at the script's steps."""
    sys.path.insert(0, str(ROOT / "vendor" / "beacon"))
    from fastapi.testclient import TestClient
    from beacon.server import create_app
    commit = None
    vend = (ROOT / "vendor" / "beacon" / "VENDORED.md").read_text()
    if "commit `" in vend:
        commit = vend.split("commit `", 1)[1].split("`", 1)[0]
    frames = []
    with TestClient(create_app()) as c:
        def beacons():
            return c.get("/api/beacons").json()

        def phone(obj, n):
            ps = [p for p in obj.get("phones") or [] if p.get("opt_in")]
            return ps[n] if len(ps) > n else None

        def keep(step, obj, what):
            frames.append({"step": step, "what": what, "beacons": obj})
            print(f"[story] {step}: {what}")

        def poll(cond, timeout=25.0):
            end = time.time() + timeout
            while time.time() < end:
                obj = beacons()
                if cond(obj):
                    return obj
                time.sleep(0.2)
            raise SystemExit(f"[story] timed out (last: {json.dumps(beacons())[:300]})")
        c.post("/api/reset", json={})
        c.post("/api/pucks", json={"pucks": [{k: p[k] for k in ("id", "x", "y")} for p in sc["pucks"]], "room": sc["room"], "floor": sc["floor"]})
        keep("pucks", poll(lambda o: len(o.get("pucks") or []) == 2), "two pucks placed by hand")
        p1, p2 = sc["phones"]
        c.post("/api/sim/phone", json={"key": p1["key"], "x": p1["x"], "y": p1["y"], "floor": sc["floor"], "opt_in": True, "uwb": True, "pointing": False, "report": p1["report"]})
        seen = set()
        obj = poll(lambda o: phone(o, 0) is not None)
        while True:
            ph = phone(obj, 0)
            g = ph["geometry"] if ph else None
            if g and g not in seen:
                seen.add(g)
                keep(f"phone1_{g}", obj, f"phone 1 heard as {g}")
            if g == "fix":
                break
            obj = poll(lambda o: phone(o, 0) is not None and phone(o, 0)["geometry"] not in seen, 30)
        stable = poll(lambda o: phone(o, 0) and phone(o, 0)["geometry"] == "fix" and (phone(o, 0).get("track") or {}).get("updates", 0) >= 20)
        frames[-1] = {"step": "phone1_fix", "what": "phone 1: two rings, two candidates, the twin removed the mirror outside the building: a fix", "beacons": stable}
        print("[story] phone1_fix (settled)")
        c.post("/api/sim/phone", json={"key": p2["key"], "x": p2["x"], "y": p2["y"], "floor": sc["floor"], "opt_in": True, "uwb": True, "pointing": False, "report": p2["report"]})
        obj = poll(lambda o: phone(o, 1) is not None and phone(o, 1)["geometry"] in ("candidates", "fix") and (phone(o, 1).get("track") or {}).get("updates", 0) >= 20)
        keep("phone2_candidates", obj, f"phone 2 heard: {phone(obj, 1)['geometry']} (both mirror candidates inside the room)")
        rep = {**p1["report"], "safe_outside": True, "status": "ok", "text": "Out with the rescue team"}
        c.post("/api/sim/phone", json={"key": p1["key"], "x": p1["x"], "y": p1["y"], "floor": sc["floor"], "opt_in": True, "uwb": True, "pointing": False, "report": rep})
        obj = poll(lambda o: phone(o, 0) is not None and (phone(o, 0).get("report") or {}).get("safe_outside"))
        keep("after_rescue", obj, "phone 1 reports it is out (safe outside)")
    return {"schema": "sandtable.beacon_story/1", "scene": "two_puck_scene.json",
            "note": "RECORDED RUN of the SIMULATED Beacon module (vendor/beacon, lane L16's work), replayed by the hands-free beacon exercise so the "
                    "command agent sees identical objects on every run. Simulated: no puck or phone hardware was in the loop.",
            "recorded": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "vendored_commit": commit, "frames": frames}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", action="store_true")
    args = ap.parse_args(argv)
    sc = scene()
    SCENE.write_text(json.dumps(sc, indent=1) + "\n")
    print(f"[story] scene: phone 1 mirror {sc['phones'][0]['mirror']}; phone 2 mirror {sc['phones'][1]['mirror']}")
    if args.scene:
        return 0
    story = record(sc)
    STORY.write_text(json.dumps(story, indent=1) + "\n")
    for f in story["frames"]:
        print(" ", f["step"], [(p["id"], p["geometry"], len(p.get("candidates") or []), len(p.get("pruned") or [])) for p in f["beacons"].get("phones") or []])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
