#!/usr/bin/env python3
"""Generate the SYNTHETIC fixture twin (fixtures/building/twin.json) from site.json.

What is real: the floor's size, position and orientation (fitted to the real
building footprint in site.json), the number of floors, and the suite numbers
of the exercise floor (from public business registrations).
What is synthetic: the whole interior layout (corridors, suite partitions,
stairs, core, doors, sweeps, tags). It is a plausible centre-core office floor
with a ring corridor, NOT a Matterport scan, and is labelled so in `source`.

The output is shaped like a normalised Matterport model (floors, rooms, sweeps
with positions and neighbours, tags) plus the navigation graph. Deterministic.
Usage: python scripts/build_twin.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "fixtures" / "building" / "site.json"
OUT = ROOT / "fixtures" / "building" / "twin.json"

CW = 2.4            # corridor width
END = 6.0           # depth of the suites at the two ends of the floor
STAIR = 4.5         # length of a stair along the core
INSET = 0.98        # the fitted rectangle is shrunk a little so it lies inside the footprint


def rect(u0, v0, u1, v1):
    return [[u0, v0], [u1, v0], [u1, v1], [u0, v1]]


def area(poly):
    return round(abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))) / 2.0, 1)


def ang_diff(a, b):
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def main() -> None:
    site = json.loads(SITE.read_text())
    prop, frame = site["property"], site.get("frame")
    if not frame:
        raise SystemExit("site.json has no 'frame' (fitted footprint rectangle); run scripts/build_site.py first")
    L, W = round(frame["length_m"] * INSET, 2), round(frame["width_m"] * INSET, 2)
    stories = int(prop.get("stories") or 3)
    floor_h = min(4.3, (prop.get("height_m") or stories * 4.0) / stories)

    # Long axis u, short axis v (right-handed: v is 90 degrees counter-clockwise of u).
    # Pick the direction of u so that the final plan is as close to north-up as possible.
    b = frame["bearing_deg"] % 360.0
    north_south = min(ang_diff(b, 0), ang_diff(b, 180)) < 45
    target = 0.0 if north_south else 90.0
    bu = b if ang_diff(b, target) <= ang_diff(b + 180, target) else (b + 180) % 360.0
    rotate = north_south                       # output x = W - v, y = u  (x east, y north)
    x_bearing = (bu + 90.0) % 360.0 if rotate else bu

    def out_xy(p):
        u, v = p
        return [round(W - v, 2), round(u, 2)] if rotate else [round(u, 2), round(v, 2)]

    # georeference: bldg (0,0) in the output frame
    lat_c, lon_c = frame["centre"]["lat"], frame["centre"]["lon"]
    bx = math.radians(x_bearing)
    cx, cy = (W / 2, L / 2) if rotate else (L / 2, W / 2)
    east0 = -(cx * math.sin(bx) - cy * math.cos(bx))
    north0 = -(cx * math.cos(bx) + cy * math.sin(bx))
    origin = {"lat": round(lat_c + north0 / 111320.0, 7), "lon": round(lon_c + east0 / (111320.0 * math.cos(math.radians(lat_c))), 7)}

    def geo_to_uv(lat, lon):
        east = (lon - origin["lon"]) * 111320.0 * math.cos(math.radians(origin["lat"]))
        north = (lat - origin["lat"]) * 111320.0
        x = east * math.sin(bx) + north * math.cos(bx)
        y = -east * math.cos(bx) + north * math.sin(bx)
        return (y, W - x) if rotate else (x, y)

    # Stair A sits at the end of the core nearest the main entrance.
    ent = (site.get("context", {}).get("entrances") or [None])[0]
    a_high = bool(ent) and geo_to_uv(ent["lat"], ent["lon"])[0] > L / 2
    side_of = {s: v.get("bearing_deg") for s, v in (site.get("sides") or {}).items()}

    def side_facing(bearing):
        if not side_of:
            return None
        return min(side_of, key=lambda s: ang_diff(side_of[s], bearing))

    band = round(min(9.5, max(6.0, (W - 2 * CW - 8.0) / 2)), 2)
    U0, U1 = END, L - END                      # corridor ring extent along u
    S0, S1 = band, band + CW                   # corridor on the low-v side
    N0, N1 = W - band - CW, W - band           # corridor on the high-v side
    C0, C1 = U0 + CW, U1 - CW                  # core extent along u
    vc = (S1 + N0) / 2

    ex = max(2, stories)
    tenants = [t for t in site.get("tenants", []) if t.get("floor")]
    hazard_floors = sorted({t["floor"] for t in tenants if t.get("life_hazard") and t["floor"] >= 2})
    ex_floor_no = hazard_floors[-1] if hazard_floors else ex
    FLOOR = f"f{ex_floor_no}"

    rooms, doors, walls, seen = [], [], [], set()

    def add_room(rid, kind, label, poly, suite=None, registered=None):
        r = {"id": rid, "floor": FLOOR, "kind": kind, "label": label, "suite": suite, "polygon": poly, "area_m2": area(poly), "height_m": 2.7}
        if registered is not None:
            r["registered"] = registered
        rooms.append(r)
        for i in range(len(poly)):
            a, c = poly[i], poly[(i + 1) % len(poly)]
            key = tuple(sorted([tuple(a), tuple(c)]))
            if key not in seen:
                seen.add(key)
                walls.append({"floor": FLOOR, "a": list(key[0]), "b": list(key[1])})

    def add_door(did, at, pair, kind="door", width=0.9):
        doors.append({"id": did, "floor": FLOOR, "at": at, "width_m": width, "rooms": pair, "kind": kind})

    # corridor ring; names are assigned after the transform from their compass position
    corr = {"cS": rect(U0, S0, U1, S1), "cN": rect(U0, N0, U1, N1), "cW": rect(U0, S1, C0, N0), "cE": rect(C1, S1, U1, N0)}
    corr_dir = {"cS": (L / 2, 0.0), "cN": (L / 2, W), "cW": (0.0, W / 2), "cE": (L, W / 2)}   # the facade each one runs along

    def compass(p_uv):
        x, y = out_xy(p_uv)
        fx, fy = (W, L) if rotate else (L, W)
        dx, dy = x - fx / 2, y - fy / 2
        bearing = (x_bearing + math.degrees(math.atan2(-dy, dx))) % 360.0     # +y is 90 deg counter-clockwise of +x
        return ["North", "East", "South", "West"][int(((bearing + 45) % 360) // 90)]

    corr_id = {}
    for key, poly in corr.items():
        name = compass(corr_dir[key])
        rid = "rc" + name[0]
        corr_id[key] = rid
        add_room(rid, "corridor", f"{name} corridor", poly)

    # core, from the low-u end to the high-u end
    low, high = ("B", "A") if a_high else ("A", "B")
    rest = (C1 - C0) - 2 * STAIR
    cuts = [C0, C0 + STAIR]
    for share in (0.30, 0.28, 0.20):
        cuts.append(round(cuts[-1] + rest * share, 2))
    cuts += [C1 - STAIR, C1]
    core = [(f"rs{low}", "stair", f"Stair {low}"), ("rwc", "restroom", "Restrooms"), ("rel", "lobby", "Elevator lobby"),
            ("rev", "elevator", "Elevators"), ("rme", "mechanical", "Electrical / mechanical"), (f"rs{high}", "stair", f"Stair {high}")]
    for (rid, kind, label), ua, ub in zip(core, cuts, cuts[1:]):
        add_room(rid, kind, label, rect(ua, S1, ub, N0))
    add_door(f"ds{low}", [C0, vc], [f"rs{low}", corr_id["cW"]], "stair_door", 1.1)
    add_door(f"ds{high}", [C1, vc], [f"rs{high}", corr_id["cE"]], "stair_door", 1.1)
    lob = (cuts[2] + cuts[3]) / 2
    add_door("delS", [lob, S1], ["rel", corr_id["cS"]], "opening", 2.4)
    add_door("delN", [lob, N0], ["rel", corr_id["cN"]], "opening", 2.4)
    add_door("dwc", [(cuts[1] + cuts[2]) / 2, S1], ["rwc", corr_id["cS"]])
    add_door("dme", [(cuts[4] + cuts[5]) / 2, N0], ["rme", corr_id["cN"]])

    # perimeter suites, numbered around the ring with the real suite numbers of this floor
    n = max(3, round(L / 8.6))
    step = L / n
    slots = []                                   # (polygon, door point, corridor key) in ring order
    for i in range(n):
        ua, ub = i * step, (i + 1) * step
        slots.append((rect(ua, 0.0, ub, S0), [min(max((ua + ub) / 2, U0 + 1.2), U1 - 1.2), S0], "cS"))
    slots.append((rect(U1, S0, L, N1), [U1, vc], "cE"))
    for i in range(n - 1, -1, -1):
        ua, ub = i * step, (i + 1) * step
        slots.append((rect(ua, N1, ub, W), [min(max((ua + ub) / 2, U0 + 1.2), U1 - 1.2), N1], "cN"))
    slots.append((rect(0.0, S0, U0, N1), [U0, vc], "cW"))
    real = sorted({t["suite"] for t in tenants if t["floor"] == ex_floor_no} | {a["suite"] for a in site.get("available_spaces", []) if a.get("floor") == ex_floor_no})
    numbers, k = list(real), ex_floor_no * 100
    while len(numbers) < len(slots):
        if str(k) not in numbers:
            numbers.append(str(k))
        k += 1
    numbers = sorted(numbers[:len(slots)])
    for (poly, door_at, ckey), num in zip(slots, numbers):
        add_room(f"r{num}", "suite", f"Suite {num}", poly, num, registered=num in real)
        add_door(f"d{num}", door_at, [f"r{num}", corr_id[ckey]])

    # navigation graph ---------------------------------------------------------
    nodes, edges = [], []

    def add_node(at, room, kind):
        nid = f"n{len(nodes) + 1:03d}"
        nodes.append({"id": nid, "floor": FLOOR, "at": [round(at[0], 2), round(at[1], 2)], "room": room, "kind": kind})
        return nid

    at_of = lambda nid: nodes[int(nid[1:]) - 1]["at"]

    def add_edge(a, c, via=None):
        edges.append({"a": a, "b": c, "len_m": round(math.dist(at_of(a), at_of(c)), 2), "via": via})

    us, un = (S0 + S1) / 2, (N0 + N1) / 2
    uw, ue = U0 + CW / 2, U1 - CW / 2
    nx = max(4, round((ue - uw) / 3.0))
    ny = max(2, round((un - us) / 3.2))
    ring_pts = [((uw + (ue - uw) * i / nx, us), corr_id["cS"]) for i in range(nx + 1)]
    ring_pts += [((ue, us + (un - us) * j / ny), corr_id["cE"]) for j in range(1, ny)]
    ring_pts += [((uw + (ue - uw) * i / nx, un), corr_id["cN"]) for i in range(nx, -1, -1)]
    ring_pts += [((uw, us + (un - us) * j / ny), corr_id["cW"]) for j in range(ny - 1, 0, -1)]
    ring = [add_node(p, room, "sweep") for p, room in ring_pts]
    for i in range(len(ring)):
        add_edge(ring[i], ring[(i + 1) % len(ring)])
    interior = {}
    for r in rooms:
        if r["kind"] in ("corridor", "elevator"):
            continue
        xs, ys = [p[0] for p in r["polygon"]], [p[1] for p in r["polygon"]]
        interior[r["id"]] = add_node(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2), r["id"], "stair" if r["kind"] == "stair" else "sweep")
    for d in doors:
        dn = add_node(d["at"], d["rooms"][1], "door")
        d["node"] = dn
        add_edge(dn, min(ring, key=lambda nid: math.dist(at_of(nid), d["at"])), via=f"door:{d['id']}")
        add_edge(dn, interior[d["rooms"][0]], via=f"door:{d['id']}")

    # transform everything into the output frame ---------------------------------
    for r in rooms:
        r["polygon"] = [out_xy(p) for p in r["polygon"]]
    for w in walls:
        w["a"], w["b"] = out_xy(w["a"]), out_xy(w["b"])
    for d in doors:
        d["at"] = out_xy(d["at"])
    for nd in nodes:
        nd["at"] = out_xy(nd["at"])
    for e in edges:
        e["len_m"] = round(math.dist(at_of(e["a"]), at_of(e["b"])), 2)
    sweeps = []
    for nd in nodes:
        if nd["kind"] in ("sweep", "stair"):
            nbrs = [e["b"] if e["a"] == nd["id"] else e["a"] for e in edges if nd["id"] in (e["a"], e["b"])]
            sweeps.append({"id": "s" + nd["id"][1:], "node": nd["id"], "floor": FLOOR, "room": nd["room"],
                           "position": [nd["at"][0], nd["at"][1], 1.5], "neighbors": ["s" + q[1:] for q in nbrs]})

    def stair_side(rid):
        room = next(r for r in rooms if r["id"] == rid)
        px = sum(p[0] for p in room["polygon"]) / 4
        py = sum(p[1] for p in room["polygon"]) / 4
        fx, fy = (W, L) if rotate else (L, W)
        bearing = (x_bearing + math.degrees(math.atan2(-(py - fy / 2), px - fx / 2))) % 360.0
        return side_facing(bearing)

    floor_ids = [f"f{i}" for i in range(1, stories + 1)]
    stairs = []
    for letter in ("A", "B"):
        side = stair_side(f"rs{letter}")
        stairs.append({"id": f"stair{letter}", "label": f"Stair {letter}", "room": f"rs{letter}", "door": f"ds{letter}", "floors": floor_ids,
                       "discharge": f"Side {side}" + (" (address side, main entrance)" if side == "A" else "") if side else "ground floor",
                       "standpipe": True, "roof_access": letter == "B"})
    door_at = {d["id"]: d["at"] for d in doors}
    room_c = {r["id"]: [round(sum(p[0] for p in r["polygon"]) / 4, 2), round(sum(p[1] for p in r["polygon"]) / 4, 2)] for r in rooms}
    tags = [
        {"id": "tg1", "kind": "standpipe", "label": "Standpipe outlet, Stair A", "floor": FLOOR, "position": room_c["rsA"] + [1.0], "description": "2.5 in outlet on the stair landing (synthetic fixture)"},
        {"id": "tg2", "kind": "standpipe", "label": "Standpipe outlet, Stair B", "floor": FLOOR, "position": room_c["rsB"] + [1.0], "description": "2.5 in outlet on the stair landing (synthetic fixture)"},
        {"id": "tg3", "kind": "electrical", "label": "Electrical room", "floor": FLOOR, "position": room_c["rme"] + [1.5], "description": "Floor distribution panels (synthetic fixture)"},
        {"id": "tg4", "kind": "extinguisher", "label": "Extinguisher cabinet", "floor": FLOOR, "position": door_at["delS"] + [1.2], "description": "(synthetic fixture)"},
        {"id": "tg5", "kind": "refuge", "label": "Area of refuge, Stair B landing", "floor": FLOOR, "position": door_at["dsB"] + [1.2], "description": "(synthetic fixture)"},
    ]
    fx, fy = (W, L) if rotate else (L, W)
    outline = [[0.0, 0.0], [fx, 0.0], [fx, fy], [0.0, fy]]
    floors = [{"id": f"f{i}", "index": i - 1, "label": str(i), "elevation_m": round((i - 1) * floor_h, 2), "outline": outline, "modelled": i == ex_floor_no}
              for i in range(1, stories + 1)]
    twin = {
        "schema": "sandtable.twin/1",
        "source": {"kind": "synthetic-fixture", "synthetic": True, "placeholder": bool(site.get("placeholder")),
                   "note": ("Synthetic interior (centre core, ring corridor) fitted to the real building footprint and numbered with the real suite numbers of this floor. "
                            "It is a stand-in for the Matterport twin in fixture mode and is not a scan of the building: partitions, doors, stairs and tags are invented.")},
        "model": {"id": "fixture-twin", "name": prop.get("name"), "address": prop.get("address")},
        "georef": {"origin": origin, "bearing_deg": round(x_bearing, 2)},
        "exercise_floor": FLOOR,
        "floors": floors, "rooms": rooms, "walls": walls, "doors": doors, "stairs": stairs, "sweeps": sweeps, "tags": tags,
        "graph": {"nodes": nodes, "edges": edges},
    }
    OUT.write_text(json.dumps(twin, indent=1))
    print(f"wrote {OUT.name}: floor {fx} x {fy} m (exercise floor {FLOOR} of {stories}), x axis bearing {x_bearing:.1f} deg, "
          f"{len(rooms)} rooms, {len(doors)} doors, {len(nodes)} nodes, {len(edges)} edges; suites {numbers}; registered {real}")


if __name__ == "__main__":
    main()
