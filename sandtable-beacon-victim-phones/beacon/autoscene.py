"""A simulated scene for whatever twin is loaded (lead-owned).

The named scenes in fixtures/beacon/scenarios/ are written against the fixture twin. When the module runs on another
twin (BEACON_TWIN_JSON / BEACON_BUNDLE_DIR, see beacon/config.py) the walk-through needs a scene made for that
twin: this builds one from geometry alone. Everything it produces is SIMULATED: invented phones placed in a real or
fixture room. It reads room polygons and doors; it uses no tenant data and writes nothing.

    scene = autoscene.build(twin)                 # same schema as the scenario files (sandtable.beacon.scenario/1)
    scene = autoscene.build(twin, room_id="r12")

The scene: two pucks in the door-wall layout of the room (beacon.layout), an opt-in iPhone 14 deep in the room
(trapped, 2 people, cannot move), an opt-in iPhone 15 elsewhere in the room (injured), an opt-in phone whose user
pressed "I am out", and two phones with no app that only feed the cold device count. As in the fixture scene, the
phones' keys are chosen so that neither phone's links are body-blocked in this scene (a scripted scene, like the
positions; the evaluation uses random draws).
"""
from __future__ import annotations

import math

from . import config, layout
from .twin import _pip, _poly_dist


def _interior_points(poly, margin: float, step: float = 0.4) -> list[tuple[float, float]]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    pts = []
    x = min(xs) + margin
    while x <= max(xs) - margin + 1e-9:
        y = min(ys) + margin
        while y <= max(ys) - margin + 1e-9:
            if _pip((x, y), poly) and _poly_dist((x, y), poly)[0] >= margin:
                pts.append((round(x, 2), round(y, 2)))
            y += step
        x += step
    return pts


def _unblocked_key(base: str, seed: int) -> str:
    """A phone key whose simulated links to P1 and P2 are not body-blocked for the first few puck placements.
    Uses the simulator's own draw; falls back to the base key when the simulator changes."""
    try:
        import numpy as np
        from . import models, sim
        p = float(models.DEFAULT_NOISE["uwb"].get("body_block_prob", 0.0))
        if p <= 0.0:
            return base
        for i in range(60):
            key = base if i == 0 else f"{base}-{i}"
            ok = True
            for puck in ("P1", "P2"):
                for ver in range(0, 6):
                    rng = np.random.default_rng([seed, sim._STREAM["link"], sim._h(puck), ver, sim._h(key), 0])
                    rng.normal()
                    if rng.random() < p:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                return key
    except Exception:
        pass
    return base


def build(twin, room_id: str | None = None, seed: int | None = None) -> dict:
    """A scenario dict for `room_id` of `twin` (default: config.default_room(twin)). ValueError when the twin has no usable room."""
    room_id = room_id or config.default_room(twin)
    if not room_id:
        raise ValueError("the twin has no room to build a scene in")
    seed = config.SIM_SEED if seed is None else int(seed)
    room = twin.room(room_id)
    poly = [[float(p[0]), float(p[1])] for p in room["polygon"]]
    lay = layout.recommend(twin, room_id, n=2, strategy="door-wall")
    a, b = (lay["pucks"][0]["x"], lay["pucks"][0]["y"]), (lay["pucks"][1]["x"], lay["pucks"][1]["y"])
    ux, uy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(ux, uy) or 1.0

    def off_line(q):                      # distance from the line through the two pucks
        return abs((q[0] - a[0]) * uy - (q[1] - a[1]) * ux) / L

    def reach(q):                         # the farther of the two pucks
        return max(math.dist(q, a), math.dist(q, b))

    pts = []
    for margin in (0.8, 0.5, 0.3):
        pts = _interior_points(poly, margin)
        if len(pts) >= 6:
            break
    if not pts:
        c = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
        pts = [(round(c[0], 2), round(c[1], 2))]
    max_reach = 0.6 * float(__import__("beacon.models", fromlist=["DEFAULT_NOISE"]).DEFAULT_NOISE["uwb"]["max_range_m"])

    def walls(q):                         # walls of the twin between the spot and either puck (a room need not be convex)
        try:
            return max(int(twin.walls_crossed(a, q, room.get("floor"))), int(twin.walls_crossed(b, q, room.get("floor"))))
        except Exception:
            return 0

    in_reach = [q for q in pts if reach(q) <= max_reach] or pts
    near = [q for q in in_reach if walls(q) == 0] or [q for q in in_reach if walls(q) <= 1] or in_reach
    trapped = max(near, key=lambda q: (off_line(q), -reach(q)))
    rest = [q for q in near if math.dist(q, trapped) >= 1.5 and off_line(q) >= min(1.2, 0.5 * off_line(trapped))] or [q for q in near if q != trapped] or [trapped]
    second = max(rest, key=lambda q: math.dist(q, trapped) + 0.5 * off_line(q))
    others = sorted([q for q in pts if q not in (trapped, second)], key=lambda q: -min(math.dist(q, trapped), math.dist(q, second)))
    floor = room.get("floor") or twin.exercise_floor
    outline = twin.floor(floor).get("outline") or poly
    out_xy = (round(min(p[0] for p in outline) - 12.0, 2), round(sum(p[1] for p in outline) / len(outline), 2))
    suite = str(room["suite"]) if room.get("suite") else None
    label = room.get("label") or room_id

    def report(status, text, people, can_move, battery, safe=False):
        r = {"status": status, "text": text, "people": people, "can_move": can_move, "battery_pct": battery,
             "floor_reported": None, "platform": "ios", "safe_outside": safe}
        if suite:
            r["home_suite"] = suite
        return r

    phones = [
        {"key": _unblocked_key("trapped-auto", seed), "room": room_id, "x": trapped[0], "y": trapped[1], "opt_in": True, "uwb": True,
         "chip": "U1", "model": "iPhone 14", "pointing": False,
         "report": report("trapped", "Two of us, smoke under the door", 2, False, 41)},
        {"key": _unblocked_key("iphone15-auto", seed), "room": room_id, "x": second[0], "y": second[1], "opt_in": True, "uwb": True,
         "chip": "U2", "model": "iPhone 15", "pointing": False, "camera_assist": False,
         "report": report("injured", "Hurt my leg, cannot walk", 1, False, 63)},
        {"key": "out-auto", "room": None, "outside": True, "x": out_xy[0], "y": out_xy[1], "opt_in": True, "uwb": True,
         "chip": "U1", "model": "iPhone 13", "pointing": False,
         "report": report("ok", "I am out, at the assembly point", 1, True, 88, safe=True)},
    ]
    for i, q in enumerate(others[:2]):
        phones.append({"key": f"noapp-auto-{i + 1}", "room": room_id, "x": q[0], "y": q[1], "opt_in": False})
    return {
        "schema": "sandtable.beacon.scenario/1", "name": "auto", "simulated": True,
        "title": f"{label}: two pucks on the door wall, a trapped iPhone 14 and an injured iPhone 15 (simulated phones, built for the loaded twin)",
        "description": ("SIMULATED scene built from the loaded twin's geometry (beacon/autoscene.py): invented phones in a room of the twin, "
                        "two pucks in the door-wall layout. No phone here is real and nothing here is a measurement."),
        "floor": floor, "room": room_id, "seed": seed,
        "pucks": {"throw": {"room": room_id, "n": 2, "seed": seed, "strategy": "door-wall"}},
        "phones": phones, "cold_devices": [],
    }
