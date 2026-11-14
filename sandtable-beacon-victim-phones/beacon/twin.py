# Twin model and spatial helpers for Beacon.
#
# COPIED from Sandtable (lane L01): sandtable/spatial/__init__.py, unchanged below this header,
# so that this module stands alone. When Beacon is vendored into the Sandtable repository as
# `sandtable/beacon/`, delete this file and use `from sandtable.spatial import Twin` instead.
# Credit: the Sandtable lead and builders.
# ---- original file follows ----
"""Spatial engine: the twin as a queryable model.

`Twin` wraps a twin dict (docs/CONTRACTS.md section 3.1) and answers the spatial
questions the reflex layer and the command agent ask: where is this point, how
do I walk from here to there avoiding what is blocked, which stair is the best
way out, does this track pass through a wall. `Registration` maps the UWB rig
("site") frame into the building frame.

Pure Python, no I/O, deterministic.
"""
from __future__ import annotations

import heapq
import math
from typing import Iterable, Sequence

__all__ = ["Twin", "Registration", "fit_similarity", "default_registration", "plan_anchor_drops", "hose_stretch", "geo_distance_m"]

HOSE_PER_STOREY_M = 9.0        # assumption: hose laid per storey of a return stair
HOSE_LENGTH_M = 15.24          # one 50 ft length


# --- small geometry helpers -------------------------------------------------------

def _pip(pt: Sequence[float], poly: Sequence[Sequence[float]]) -> bool:
    x, y = pt[0], pt[1]
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _seg_point_dist(p, a, b) -> tuple[float, tuple[float, float]]:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / den))
    q = (ax + t * dx, ay + t * dy)
    return math.hypot(p[0] - q[0], p[1] - q[1]), q


def _poly_dist(p, poly) -> tuple[float, tuple[float, float]]:
    best = (float("inf"), (p[0], p[1]))
    for i in range(len(poly)):
        d = _seg_point_dist(p, poly[i], poly[(i + 1) % len(poly)])
        if d[0] < best[0]:
            best = d
    return best


def _seg_intersection(p, q, a, b):
    """Intersection point of segments pq and ab, or None (collinear overlaps are ignored)."""
    r = (q[0] - p[0], q[1] - p[1])
    s = (b[0] - a[0], b[1] - a[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((a[0] - p[0]) * s[1] - (a[1] - p[1]) * s[0]) / den
    u = ((a[0] - p[0]) * r[1] - (a[1] - p[1]) * r[0]) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (p[0] + t * r[0], p[1] + t * r[1])
    return None


def geo_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    north = (lat2 - lat1) * 111320.0
    east = (lon2 - lon1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(east, north)


# --- the twin -----------------------------------------------------------------------

class Twin:
    def __init__(self, data: dict):
        self.data = data
        self.exercise_floor: str = data.get("exercise_floor") or data["floors"][0]["id"]
        self._floors = {f["id"]: f for f in data["floors"]}
        self._rooms = {r["id"]: r for r in data["rooms"]}
        self._doors = {d["id"]: d for d in data["doors"]}
        self._stairs = {s["id"]: s for s in data.get("stairs", [])}
        self._nodes = {n["id"]: n for n in data["graph"]["nodes"]}
        self._sweep_of = {s["node"]: s["id"] for s in data.get("sweeps", []) if s.get("node")}
        self._adj: dict[str, list[tuple[str, float, str | None]]] = {nid: [] for nid in self._nodes}
        for e in data["graph"]["edges"]:
            self._adj[e["a"]].append((e["b"], float(e["len_m"]), e.get("via")))
            self._adj[e["b"]].append((e["a"], float(e["len_m"]), e.get("via")))
        self._room_nodes: dict[str, list[str]] = {}
        for nid, n in self._nodes.items():
            self._room_nodes.setdefault(n["room"], []).append(nid)
        self._door_node = {d["id"]: d.get("node") for d in data["doors"]}
        self._suite_room = {r["suite"]: r["id"] for r in data["rooms"] if r.get("suite")}
        g = data.get("georef") or {"origin": {"lat": 0.0, "lon": 0.0}, "bearing_deg": 90.0}
        self._lat0, self._lon0 = g["origin"]["lat"], g["origin"]["lon"]
        self._b = math.radians(g["bearing_deg"])

    # ---- lookups
    def floor(self, floor_id: str | None = None) -> dict:
        return self._floors[floor_id or self.exercise_floor]

    def room(self, room_id: str) -> dict:
        if room_id not in self._rooms:
            raise ValueError(f"unknown room {room_id!r}")
        return self._rooms[room_id]

    def rooms(self, kind: str | None = None) -> list[dict]:
        return [r for r in self.data["rooms"] if kind is None or r["kind"] == kind]

    def door(self, door_id: str) -> dict:
        if door_id not in self._doors:
            raise ValueError(f"unknown door {door_id!r}")
        return self._doors[door_id]

    def node(self, node_id: str) -> dict:
        if node_id not in self._nodes:
            raise ValueError(f"unknown node {node_id!r}")
        return self._nodes[node_id]

    def stair(self, stair_id: str) -> dict:
        if stair_id not in self._stairs:
            raise ValueError(f"unknown stair {stair_id!r}")
        return self._stairs[stair_id]

    def tags(self, kind: str | None = None) -> list[dict]:
        return [t for t in self.data.get("tags", []) if kind is None or t.get("kind") == kind]

    def exits(self) -> list[dict]:
        out = []
        for s in self.data.get("stairs", []):
            out.append({"id": s["id"], "label": s["label"], "room": s["room"], "door": s.get("door"),
                        "node": self._room_nodes.get(s["room"], [None])[0], "discharge": s.get("discharge"), "standpipe": s.get("standpipe")})
        return out

    def room_centre(self, room_id: str) -> list[float]:
        poly = self.room(room_id)["polygon"]
        return [sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)]

    # ---- geo
    def to_geo(self, x: float, y: float) -> list[float]:
        east = x * math.sin(self._b) - y * math.cos(self._b)
        north = x * math.cos(self._b) + y * math.sin(self._b)
        return [self._lat0 + north / 111320.0, self._lon0 + east / (111320.0 * math.cos(math.radians(self._lat0)))]

    def from_geo(self, lat: float, lon: float) -> list[float]:
        east = (lon - self._lon0) * 111320.0 * math.cos(math.radians(self._lat0))
        north = (lat - self._lat0) * 111320.0
        return [east * math.sin(self._b) + north * math.cos(self._b), -east * math.cos(self._b) + north * math.sin(self._b)]

    # ---- locate
    def locate(self, x: float, y: float, floor: str | None = None) -> dict:
        floor = floor or self.exercise_floor
        outline = self.floor(floor)["outline"]
        inside_floor = _pip((x, y), outline)
        off, snapped = (0.0, (x, y)) if inside_floor else _poly_dist((x, y), outline)
        room = None
        for r in self.data["rooms"]:
            if r["floor"] == floor and _pip(snapped, r["polygon"]):
                room = r
                break
        if room is None:                                  # on a wall line or just outside: nearest room
            cands = [(r, _poly_dist(snapped, r["polygon"])[0]) for r in self.data["rooms"] if r["floor"] == floor]
            if cands:
                room = min(cands, key=lambda c: c[1])[0]
        pool = self._room_nodes.get(room["id"], []) if room else []
        if not pool:
            pool = [nid for nid, n in self._nodes.items() if n["floor"] == floor]
        node = min(pool, key=lambda nid: math.dist(self._nodes[nid]["at"], snapped)) if pool else None
        sweeps = [nid for nid in self._sweep_of if self._nodes[nid]["floor"] == floor]
        sweep_node = min(sweeps, key=lambda nid: math.dist(self._nodes[nid]["at"], snapped)) if sweeps else None
        return {"floor": floor, "room": room["id"] if room else None, "room_label": room["label"] if room else None,
                "room_kind": room["kind"] if room else None, "suite": room.get("suite") if room else None,
                "node": node, "sweep": self._sweep_of.get(sweep_node), "snapped": [round(snapped[0], 2), round(snapped[1], 2)],
                "off_map_m": round(off, 2)}

    # ---- references
    def resolve(self, ref) -> dict:
        """Turn any reference (point, node, room, door, stair, suite number) into a graph node."""
        if isinstance(ref, (list, tuple)) and len(ref) >= 2:
            loc = self.locate(float(ref[0]), float(ref[1]))
            return {"kind": "point", "id": loc["node"], "label": loc["room_label"] or "floor", "at": [float(ref[0]), float(ref[1])], "node": loc["node"], "room": loc["room"]}
        if not isinstance(ref, str):
            raise ValueError(f"bad reference {ref!r}")
        if ref in self._nodes:
            n = self._nodes[ref]
            return {"kind": "node", "id": ref, "label": self._rooms[n["room"]]["label"] if n["room"] in self._rooms else ref, "at": n["at"], "node": ref, "room": n["room"]}
        if ref in self._stairs:
            s = self._stairs[ref]
            nid = self._room_nodes[s["room"]][0]
            return {"kind": "stair", "id": ref, "label": s["label"], "at": self._nodes[nid]["at"], "node": nid, "room": s["room"]}
        if ref in self._doors:
            d = self._doors[ref]
            return {"kind": "door", "id": ref, "label": f"door of {self._rooms[d['rooms'][0]]['label']}", "at": d["at"], "node": d["node"], "room": d["rooms"][1]}
        room_id = ref if ref in self._rooms else self._suite_room.get(ref.replace("Suite ", "").strip())
        if room_id:
            r = self._rooms[room_id]
            nodes = self._room_nodes.get(room_id, [])
            if not nodes:
                raise ValueError(f"room {room_id!r} has no walkable node")
            c = self.room_centre(room_id)
            nid = min(nodes, key=lambda q: math.dist(self._nodes[q]["at"], c))
            return {"kind": "room", "id": room_id, "label": r["label"], "at": self._nodes[nid]["at"], "node": nid, "room": room_id}
        raise ValueError(f"unknown reference {ref!r}")

    def _blocked_sets(self, blocked: Iterable[str]) -> tuple[set[str], set[frozenset]]:
        nodes: set[str] = set()
        edges: set[frozenset] = set()
        for ref in blocked or ():
            if ref in self._nodes:
                nodes.add(ref)
            elif ref in self._doors:
                nodes.add(self._door_node[ref])
            elif ref in self._stairs:
                s = self._stairs[ref]
                nodes.update(self._room_nodes.get(s["room"], []))
                if s.get("door") in self._door_node:
                    nodes.add(self._door_node[s["door"]])
            elif ref in self._rooms:
                nodes.update(self._room_nodes.get(ref, []))
            elif isinstance(ref, str) and "-" in ref and all(p in self._nodes for p in ref.split("-", 1)):
                edges.add(frozenset(ref.split("-", 1)))
            else:
                raise ValueError(f"unknown blocked reference {ref!r}")
        return nodes, edges

    def _dijkstra(self, src: str, bnodes: set[str], bedges: set[frozenset]) -> tuple[dict[str, float], dict[str, str]]:
        dist, prev = {src: 0.0}, {}
        heap = [(0.0, src)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, float("inf")):
                continue
            for v, w, _via in self._adj[u]:
                if v in bnodes or frozenset((u, v)) in bedges:
                    continue
                nd = d + w
                if nd < dist.get(v, float("inf")) - 1e-9:
                    dist[v], prev[v] = nd, u
                    heapq.heappush(heap, (nd, v))
        return dist, prev

    def route(self, src, dst, blocked: Iterable[str] = (), floor: str | None = None) -> dict:
        s = self.resolve(src)
        bnodes, bedges = self._blocked_sets(blocked)
        bnodes.discard(s["node"])
        if dst == "exit":
            dist, prev = self._dijkstra(s["node"], bnodes, bedges)
            best = None
            for ex in self.exits():
                d = dist.get(ex["node"])
                if d is not None and (best is None or d < best[0]):
                    best = (d, ex)
            if best is None:
                return self._fail(s, {"kind": "exit", "id": None, "label": "any exit"}, "no stair is reachable with the current hazards")
            t = self.resolve(best[1]["id"])
        else:
            t = self.resolve(dst)
            bnodes.discard(t["node"])
            dist, prev = self._dijkstra(s["node"], bnodes, bedges)
            if t["node"] not in dist:
                return self._fail(s, t, f"no walkable route to {t['label']} with the current hazards")
        path = [t["node"]]
        while path[-1] != s["node"]:
            path.append(prev[path[-1]])
        path.reverse()
        pts = [list(self._nodes[n]["at"]) for n in path]
        if s["kind"] == "point" and math.dist(s["at"], pts[0]) > 0.3:
            pts.insert(0, [round(s["at"][0], 2), round(s["at"][1], 2)])
        if t["kind"] == "point" and math.dist(t["at"], pts[-1]) > 0.3:
            pts.append([round(t["at"][0], 2), round(t["at"][1], 2)])
        length = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        rooms, doors = [], []
        for a, b in zip(path, path[1:]):
            via = next((v for n, _w, v in self._adj[a] if n == b), None)
            if via and via.startswith("door:") and via[5:] not in doors:
                doors.append(via[5:])
        for n in path:
            r = self._nodes[n]["room"]
            if not rooms or rooms[-1] != r:
                rooms.append(r)
        if t.get("room") and rooms and rooms[-1] != t["room"] and t["room"] in self._rooms:
            rooms.append(t["room"])
        return {"ok": True, "reason": None, "nodes": path, "polyline": pts, "length_m": round(length, 1), "rooms": rooms, "doors": doors,
                "src_resolved": {k: s[k] for k in ("kind", "id", "label")}, "dst_resolved": {k: t[k] for k in ("kind", "id", "label")},
                "steps": self._steps(path, s, t)}

    def _fail(self, s, t, reason):
        return {"ok": False, "reason": reason, "nodes": [], "polyline": [], "length_m": 0.0, "rooms": [], "doors": [],
                "src_resolved": {k: s[k] for k in ("kind", "id", "label")}, "dst_resolved": {k: t.get(k) for k in ("kind", "id", "label")}, "steps": []}

    def _steps(self, path: list[str], s: dict, t: dict) -> list[dict]:
        legs: list[dict] = []
        for n in path:
            room = self._nodes[n]["room"]
            if legs and legs[-1]["room"] == room:
                legs[-1]["nodes"].append(n)
            else:
                legs.append({"room": room, "nodes": [n]})
        steps, prev_dir = [], None
        for i, leg in enumerate(legs):
            pts = [self._nodes[n]["at"] for n in leg["nodes"]]
            start = self._nodes[legs[i - 1]["nodes"][-1]]["at"] if i else pts[0]
            end = pts[-1]
            dist = sum(math.dist(a, b) for a, b in zip([start] + pts, pts))
            d = (end[0] - start[0], end[1] - start[1])
            label = self._rooms[leg["room"]]["label"] if leg["room"] in self._rooms else leg["room"]
            if i == 0:
                text = f"Start: {s['label']}"
            else:
                turn = ""
                if prev_dir and math.hypot(*d) > 0.5 and math.hypot(*prev_dir) > 0.5:
                    cross = prev_dir[0] * d[1] - prev_dir[1] * d[0]
                    dot = prev_dir[0] * d[0] + prev_dir[1] * d[1]
                    ang = math.degrees(math.atan2(cross, dot))
                    turn = "left " if ang > 35 else "right " if ang < -35 else "straight "
                text = f"{turn}into {label}".strip()
                text = text[:1].upper() + text[1:] + (f", {round(dist)} m" if dist >= 1 else "")
            if math.hypot(*d) > 0.5:
                prev_dir = d
            steps.append({"text": text, "at": [round(start[0], 2), round(start[1], 2)], "dist_m": round(dist, 1)})
        if steps:
            steps.append({"text": f"Arrive: {t['label']}", "at": list(self._nodes[path[-1]]["at"]), "dist_m": 0.0})
        return steps

    def nearest_exit(self, src, blocked: Iterable[str] = ()) -> dict:
        return self.route(src, "exit", blocked)

    # ---- walls
    def crosses_wall(self, p0: Sequence[float], p1: Sequence[float], floor: str | None = None, margin: float = 0.15) -> bool:
        """True when the segment passes through a wall away from every door opening."""
        floor = floor or self.exercise_floor
        for w in self.data.get("walls", []):
            if w["floor"] != floor:
                continue
            hit = _seg_intersection(p0, p1, w["a"], w["b"])
            if hit is None:
                continue
            if any(d["floor"] == floor and math.dist(hit, d["at"]) <= d.get("width_m", 0.9) / 2 + margin for d in self.data["doors"]):
                continue
            return True
        return False

    def walls_crossed(self, p0: Sequence[float], p1: Sequence[float], floor: str | None = None, margin: float = 0.15) -> int:
        """How many walls the straight segment passes through (door openings do not count)."""
        floor = floor or self.exercise_floor
        hits: list[tuple[float, float]] = []
        for w in self.data.get("walls", []):
            if w["floor"] != floor:
                continue
            hit = _seg_intersection(p0, p1, w["a"], w["b"])
            if hit is None or any(math.dist(hit, h) < 0.08 for h in hits):
                continue
            if any(d["floor"] == floor and math.dist(hit, d["at"]) <= d.get("width_m", 0.9) / 2 + margin for d in self.data["doors"]):
                continue
            hits.append(hit)
        return len(hits)

    def describe(self) -> dict:
        """Compact, deterministic description for prompts."""
        f = self.floor()
        xs, ys = [p[0] for p in f["outline"]], [p[1] for p in f["outline"]]
        suites = [{"room": r["id"], "label": r["label"], "area_m2": round(r["area_m2"])} for r in self.rooms("suite")]
        other = [{"room": r["id"], "label": r["label"], "kind": r["kind"]} for r in self.data["rooms"] if r["kind"] != "suite"]
        stair_route = None
        ex = self.exits()
        if len(ex) >= 2:
            r = self.route(ex[0]["id"], ex[1]["id"])
            stair_route = {"from": ex[0]["id"], "to": ex[1]["id"], "length_m": round(r["length_m"]), "via": [self._rooms[q]["label"] for q in r["rooms"]]}
        return {"model": self.data["model"].get("name"), "synthetic": bool(self.data.get("source", {}).get("synthetic")),
                "floor": f["label"], "floor_id": f["id"], "floors_total": len(self.data["floors"]),
                "size_m": [round(max(xs) - min(xs)), round(max(ys) - min(ys))],
                "suites": suites, "other_rooms": other,
                "stairs": [{"id": s["id"], "label": s["label"], "discharge": s.get("discharge"), "standpipe": s.get("standpipe"), "roof_access": s.get("roof_access")} for s in self.data.get("stairs", [])],
                "tags": [{"kind": t["kind"], "label": t["label"]} for t in self.tags()],
                "stair_to_stair": stair_route}


# --- registration: UWB rig frame -> building frame --------------------------------------

class Registration:
    """X = a*x - m*b*y + tx ; Y = b*x + m*a*y + ty, with m = -1 for a mirrored rig."""

    def __init__(self, a: float, b: float, tx: float, ty: float, mirror: bool = False, method: str = "default", residual_m: float = 0.0):
        self.a, self.b, self.tx, self.ty, self.mirror = a, b, tx, ty, mirror
        self.method, self.residual_m = method, residual_m

    @property
    def scale(self) -> float:
        return math.hypot(self.a, self.b)

    @property
    def rotation_deg(self) -> float:
        return math.degrees(math.atan2(self.b, self.a))

    def apply(self, x: float, y: float) -> tuple[float, float]:
        m = -1.0 if self.mirror else 1.0
        return (self.a * x - m * self.b * y + self.tx, self.b * x + m * self.a * y + self.ty)

    def invert(self, X: float, Y: float) -> tuple[float, float]:
        m = -1.0 if self.mirror else 1.0
        s2 = self.a * self.a + self.b * self.b
        dx, dy = X - self.tx, Y - self.ty
        return ((self.a * dx + self.b * dy) / s2, m * (-self.b * dx + self.a * dy) / s2)

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "tx": self.tx, "ty": self.ty, "mirror": self.mirror, "scale": round(self.scale, 3),
                "rotation_deg": round(self.rotation_deg, 2), "method": self.method, "residual_m": round(self.residual_m, 3)}

    @classmethod
    def from_dict(cls, d: dict) -> "Registration":
        return cls(d["a"], d["b"], d["tx"], d["ty"], bool(d.get("mirror")), d.get("method", "loaded"), d.get("residual_m", 0.0))


def _fit(site_pts, bldg_pts, mirror: bool) -> Registration:
    z = [complex(p[0], -p[1] if mirror else p[1]) for p in site_pts]
    w = [complex(p[0], p[1]) for p in bldg_pts]
    zc, wc = sum(z) / len(z), sum(w) / len(w)
    den = sum(abs(q - zc) ** 2 for q in z)
    if den < 1e-12:
        raise ValueError("registration points coincide")
    alpha = sum((q - zc).conjugate() * (v - wc) for q, v in zip(z, w)) / den
    beta = wc - alpha * zc
    reg = Registration(alpha.real, alpha.imag, beta.real, beta.imag, mirror, "fit")
    res = [math.dist(reg.apply(p[0], p[1]), q) for p, q in zip(site_pts, bldg_pts)]
    reg.residual_m = math.sqrt(sum(r * r for r in res) / len(res))
    return reg


def fit_similarity(site_pts: Sequence[Sequence[float]], bldg_pts: Sequence[Sequence[float]], allow_mirror: bool = False) -> Registration:
    """Least-squares similarity transform from two or more point pairs."""
    if len(site_pts) != len(bldg_pts) or len(site_pts) < 2:
        raise ValueError("need two or more point pairs")
    best = _fit(site_pts, bldg_pts, False)
    if allow_mirror and len(site_pts) >= 3:
        alt = _fit(site_pts, bldg_pts, True)
        if alt.residual_m < best.residual_m:
            best = alt
    return best


def default_registration(anchors: dict, twin: Twin, scale: float | None = None) -> Registration:
    """Centre of the anchor rectangle onto the centre of the exercise floor, long axis onto long axis,
    scaled so the whole floor lies inside the anchor rectangle (or `scale` when given)."""
    ax = [a["x"] for a in anchors.values()]
    ay = [a["y"] for a in anchors.values()]
    outline = twin.floor()["outline"]
    fx, fy = [p[0] for p in outline], [p[1] for p in outline]
    la, wa = max(max(ax) - min(ax), 1e-6), max(max(ay) - min(ay), 1e-6)
    lf, wf = max(fx) - min(fx), max(fy) - min(fy)
    rotate = (la >= wa) != (lf >= wf)
    if rotate:
        s = scale or max(lf / wa, wf / la)
        a, b = 0.0, s                      # 90 degrees: site x -> building +y
    else:
        s = scale or max(lf / la, wf / wa)
        a, b = s, 0.0
    cx, cy = (max(ax) + min(ax)) / 2, (max(ay) + min(ay)) / 2
    reg = Registration(a, b, 0.0, 0.0, False, "default: anchor rectangle onto the floor outline")
    px, py = reg.apply(cx, cy)
    reg.tx, reg.ty = (max(fx) + min(fx)) / 2 - px, (max(fy) + min(fy)) / 2 - py
    return reg


# --- spatial tools the agent uses -----------------------------------------------------------

def plan_anchor_drops(twin: Twin, floor: str | None = None, k: int = 4) -> dict:
    """Greedy choice of k anchor drop points for tracking coverage of the floor.

    An anchor is "usable" from a point when the straight path crosses at most one interior wall
    (UWB ranges through one light partition with a known positive bias; more walls are not counted).
    Coverage = share of walkable graph nodes with 3 or more usable anchors, the minimum for a 2-D fix.
    The strict line-of-sight share is reported as well. It is a planning aid, not a survey."""
    floor = floor or twin.exercise_floor
    nodes = [n for n in twin.data["graph"]["nodes"] if n["floor"] == floor]
    doors = {d["node"]: d for d in twin.data["doors"]}
    cands = []
    for n in nodes:
        d = doors.get(n["id"])
        if d and d["kind"] == "stair_door":
            cands.append((n, f"{twin.room(d['rooms'][0])['label']} door"))
        elif d and d["kind"] == "opening":
            cands.append((n, f"{twin.room(d['rooms'][0])['label']} opening"))
        elif n["kind"] == "sweep" and twin.room(n["room"])["kind"] == "corridor" and len(twin._adj[n["id"]]) >= 2:
            cands.append((n, twin.room(n["room"])["label"]))
    walls = {c[0]["id"]: {n["id"]: twin.walls_crossed(c[0]["at"], n["at"], floor) for n in nodes} for c in cands}
    usable = {cid: {nid for nid, w in ws.items() if w <= 1} for cid, ws in walls.items()}
    los = {cid: {nid for nid, w in ws.items() if w == 0} for cid, ws in walls.items()}
    chosen: list[tuple[dict, str]] = []
    count = {n["id"]: 0 for n in nodes}
    for _ in range(min(k, len(cands))):
        def gain(c):
            cid = c[0]["id"]
            covered = sum(1 for nid in count if count[nid] + (nid in usable[cid]) >= 3)
            partial = sum(min(3, count[nid] + (nid in usable[cid])) for nid in count)
            spread = min((math.dist(c[0]["at"], o[0]["at"]) for o in chosen), default=0.0)
            return (covered, partial, round(spread, 1), -int(cid[1:]))
        best = max((c for c in cands if c not in chosen), key=gain)
        chosen.append(best)
        for nid in usable[best[0]["id"]]:
            count[nid] += 1
    los_count = {n["id"]: sum(1 for c in chosen if n["id"] in los[c[0]["id"]]) for n in nodes}
    total = max(1, len(nodes))
    return {"anchors": [{"id": f"A{i + 1}", "at": c[0]["at"], "node": c[0]["id"], "near": c[1]} for i, c in enumerate(chosen)],
            "coverage_pct": round(100 * sum(1 for v in count.values() if v >= 3) / total),
            "line_of_sight_pct": round(100 * sum(1 for v in los_count.values() if v >= 3) / total),
            "mean_usable": round(sum(count.values()) / total, 1),
            "method": "greedy placement on stair doors, lobby openings and corridor points; coverage = share of walkable nodes with 3 or more anchors "
                      "reachable through at most one interior wall; line_of_sight_pct is the strict wall-free share"}


def hose_stretch(twin: Twin, site: dict, target, stair: str = "stairA", apparatus_id: str = "E1", standpipe: bool = True) -> dict:
    """Hose needed to reach `target` on the exercise floor: outside leg from the aerial/geo layer, inside leg from the twin."""
    ctx = site.get("context", {})
    app = next((a for a in ctx.get("apparatus_positions", []) if a["id"] == apparatus_id), None)
    ent = next((e for e in ctx.get("entrances", []) if e.get("leads_to") == stair), None) or (ctx.get("entrances") or [None])[0]
    outside = geo_distance_m(app["lat"], app["lon"], ent["lat"], ent["lon"]) if app and ent else 0.0
    st = twin.resolve(stair)
    lobby = math.dist(twin.from_geo(ent["lat"], ent["lon"]), st["at"]) if ent else 0.0
    floor_index = twin.floor()["index"]
    inside = twin.route(stair, target)
    if not inside["ok"]:
        raise ValueError(inside["reason"])
    notes = [f"outside leg: {apparatus_id} to the entrance, straight line between their geographic positions",
             f"vertical leg: {HOSE_PER_STOREY_M:g} m of hose per storey of stair (assumption)",
             "inside leg: walking route in the twin from the stair door to the target"]
    if standpipe:
        vertical = HOSE_PER_STOREY_M
        total = vertical + inside["length_m"]
        via = f"standpipe outlet in {st['label']} one floor below, then {inside['dst_resolved']['label']}"
        notes.append("standpipe operation: the attack line starts at the outlet one floor below; the outside leg is supply hose only")
    else:
        vertical = HOSE_PER_STOREY_M * floor_index
        total = outside + lobby + vertical + inside["length_m"]
        via = f"hand stretch from {apparatus_id} through the entrance and up {st['label']}"
        notes.append("ground floor leg: straight line from the entrance to the stair (ground floor not modelled)")
    return {"outside_m": round(outside), "ground_floor_m": round(lobby), "vertical_m": round(vertical), "inside_m": round(inside["length_m"]),
            "total_m": round(total), "lengths_50ft": math.ceil(total / HOSE_LENGTH_M), "standpipe": standpipe, "via": via, "notes": notes}
