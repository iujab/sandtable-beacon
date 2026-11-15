"""Matterport live adapter (Model API, GraphQL), exercised against the live API with the event's keys.

Endpoint https://api.matterport.com/api/models/graph, HTTP basic auth with the API token id and secret.
The GraphQL documents are in matterport_queries.py (checked by introspection).

Data handling (the owner's rule: nothing fetched from Matterport is published):
  - every response is cached under data/live/matterport/cache/ (git-ignored, skipped by the publisher)
    and reused; `refresh=True` (or MATTERPORT_REFRESH=1) refetches;
  - the normalised twin is cached at data/live/matterport/twin_<model>.json and `get_model()` reads it;
  - `add_tag()` is a dry run unless MATTERPORT_WRITE_TAGS=1: the mutation is built, checked against the
    schema and logged under data/live/, and the tag is kept locally. Nothing is written to the model.

`normalize_model()` turns the API's model into the Sandtable twin (docs/CONTRACTS.md 3.1): building frame
in metres, z up (the Model API is z up), the model's axes shifted so the plan starts near (1, 1).
What is real and what is approximated is stated in `source.note` and listed in `source.approximations`.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Callable

from .. import config
from . import matterport_queries as Q

ENDPOINT = "https://api.matterport.com/api/models/graph"
LIVE_DIR = config.LIVE_DIR / "matterport"          # private root that never moves (config.DATA_DIR may itself switch to data/live)
CACHE_DIR = LIVE_DIR / "cache"
M_PER_DEG = 111320.0

# --- the client -----------------------------------------------------------------------------------


class GraphClient:
    """POSTs GraphQL with basic auth; every successful response is cached on disk and reused."""

    def __init__(self, token_id: str, token_secret: str, cache_dir: Path | None = CACHE_DIR, transport: Callable | None = None):
        self._auth = (token_id, token_secret)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._transport = transport
        self.requests_sent = 0

    def _key(self, doc: str, variables: dict) -> str:
        return hashlib.sha256(json.dumps([doc, variables or {}], sort_keys=True).encode()).hexdigest()[:24]

    def query(self, doc: str, variables: dict | None = None, refresh: bool = False) -> dict:
        variables = variables or {}
        path = self.cache_dir / f"{self._key(doc, variables)}.json" if self.cache_dir else None
        if path and path.exists() and not refresh:
            body = json.loads(path.read_text(encoding="utf-8")).get("body") or {}
            if body.get("data") is not None and not body.get("errors"):
                return body["data"]
        if self._transport:
            data = self._transport(doc, variables)
            body = {"data": data}
        else:
            import httpx
            r = httpx.post(ENDPOINT, json={"query": doc, "variables": variables}, auth=self._auth, timeout=90.0)
            r.raise_for_status()
            body = r.json()
        self.requests_sent += 1
        if body.get("errors"):
            raise RuntimeError("Matterport GraphQL error: " + "; ".join(str(e.get("message")) for e in body["errors"][:3]))
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"status": 200, "body": body}), encoding="utf-8")
            tmp.replace(path)
        return body["data"]


# --- the adapter --------------------------------------------------------------------------------------


class MatterportLive:
    mode = "live"

    def __init__(self, token_id: str, token_secret: str, model_id: str, transport: Callable | None = None,
                 cache_dir: Path | None = LIVE_DIR, refresh: bool = False, dry_run: bool = True, exercise_floor: str | None = None,
                 storey: int | None = None, suite: str | None = None):
        self.model_id = model_id
        self.storey, self.suite = storey, suite
        self.live_dir = Path(cache_dir) if cache_dir else None
        self.client = GraphClient(token_id, token_secret, (self.live_dir / "cache") if self.live_dir else None, transport)
        self.refresh, self.dry_run, self.exercise_floor = refresh, dry_run, exercise_floor
        self._twin: dict | None = None

    @classmethod
    def from_env(cls) -> "MatterportLive":
        """Keys from the environment. The model: MATTERPORT_MODEL_ID when set (it wins), else the building pick in
        data/live/demo_building.json (config.demo_building()). Storey and suite: MATTERPORT_STOREY / MATTERPORT_SUITE,
        else the pick's `floor` / `suite` when the pick is the same model."""
        from . import AdapterError
        missing = [k for k in ("MATTERPORT_TOKEN_ID", "MATTERPORT_TOKEN_SECRET") if not os.environ.get(k)]
        try:
            pick = config.demo_building() or {}             # the building pick (data/live/, never published)
        except (OSError, ValueError):
            pick = {}
        model_id = os.environ.get("MATTERPORT_MODEL_ID") or pick.get("model_id")
        if not model_id:
            missing.append("MATTERPORT_MODEL_ID (or a model_id in data/live/demo_building.json)")
        if missing:
            raise AdapterError("MATTERPORT_MODE=live but these are not set: " + ", ".join(missing))
        storey, suite = os.environ.get("MATTERPORT_STOREY"), os.environ.get("MATTERPORT_SUITE")
        if pick.get("model_id") == model_id:
            storey = storey if storey not in (None, "") else pick.get("floor")
            suite = suite if suite not in (None, "") else pick.get("suite")
        return cls(os.environ["MATTERPORT_TOKEN_ID"], os.environ["MATTERPORT_TOKEN_SECRET"], str(model_id),
                   refresh=os.environ.get("MATTERPORT_REFRESH") == "1", dry_run=os.environ.get("MATTERPORT_WRITE_TAGS") != "1",
                   exercise_floor=os.environ.get("MATTERPORT_EXERCISE_FLOOR") or None,
                   storey=int(storey) if storey not in (None, "") else None, suite=str(suite) if suite else None)

    # ---- raw API reads (cached)
    def raw_model(self) -> dict:
        m = self.client.query(Q.MODEL, {"modelId": self.model_id}, refresh=self.refresh).get("model")
        if not m:
            raise RuntimeError("the Matterport model was not found or is not visible to this token")
        return m

    def floorplans(self) -> list[dict]:
        try:
            return ((self.client.query(Q.FLOORPLANS, {"modelId": self.model_id}, refresh=self.refresh).get("model") or {}).get("assets") or {}).get("floorplans") or []
        except Exception:
            return []

    def geo_samples(self, model: dict) -> list[dict]:
        """Matterport's own lat/lon for three model points around the scan (origin, +10 m x, +10 m y)."""
        locs = [l["position"] for l in model.get("locations") or [] if l.get("position")]
        if not locs:
            return []
        cx = round(sum(p["x"] for p in locs) / len(locs), 1)
        cy = round(sum(p["y"] for p in locs) / len(locs), 1)
        out = []
        for px, py in ((cx, cy), (cx + 10.0, cy), (cx, cy + 10.0)):
            try:
                g = ((self.client.query(Q.GEO_LOCATION_OF, {"modelId": self.model_id, "point": {"x": px, "y": py, "z": 0.0}},
                                        refresh=self.refresh).get("model") or {}).get("geocoordinates") or {}).get("geoLocationOf")
            except Exception:
                return []
            if not g or g.get("lat") in (None, "") or g.get("long") in (None, ""):
                return []
            out.append({"model": [px, py], "lat": float(g["lat"]), "lon": float(g["long"]), "status": g.get("status")})
        return out

    # ---- the twin (cached)
    def twin_path(self) -> Path | None:
        return self.live_dir / f"twin_{self.model_id}.json" if self.live_dir else None

    def get_model(self) -> dict:
        if self._twin is not None:
            return self._twin
        path = self.twin_path()
        if path and path.exists() and not self.refresh:
            self._twin = json.loads(path.read_text(encoding="utf-8"))
        else:
            m = self.raw_model()
            self._twin = normalize_model(m, geo_samples=self.geo_samples(m), floorplans=self.floorplans(), exercise_floor=self.exercise_floor,
                                         storey=self.storey, suite=self.suite)
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._twin, indent=1), encoding="utf-8")
                tmp.replace(path)
        self._twin.setdefault("tags", [])
        self._twin["tags"] = [t for t in self._twin["tags"] if not str(t.get("id", "")).startswith("dryrun-")] + self._dryrun_tags()
        return self._twin

    def list_tags(self) -> list[dict]:
        return self.get_model()["tags"]

    # ---- tags: dry run unless MATTERPORT_WRITE_TAGS=1
    def _dryrun_log(self) -> Path | None:
        return self.live_dir / f"tags_dryrun_{self.model_id}{config.INSTANCE}.json" if self.live_dir else None   # one log per board (config.set_instance)

    def clear_overlay(self) -> None:
        """Each incident starts clean: in a dry run, forget the pins the agent would have written (the log and the twin's dryrun-
        tags), so no run's prompts carry the pins of the runs before it. Tags really written to the model are left alone."""
        if not self.dry_run:
            return
        log = self._dryrun_log()
        if log is not None and log.exists():
            log.unlink()
        if isinstance(self._twin, dict):
            self._twin["tags"] = [t for t in self._twin.get("tags", []) if not str(t.get("id", "")).startswith("dryrun-")]

    def _dryrun_tags(self) -> list[dict]:
        p = self._dryrun_log()
        if not p or not p.exists():
            return []
        return [e["tag"] for e in json.loads(p.read_text(encoding="utf-8"))]

    def _check_mutation(self) -> list[str]:
        """Problems with ADD_TAG against the live schema (introspection, cached); empty when it matches."""
        problems = []
        try:
            mut = (self.client.query(Q.INTROSPECT_TYPE, {"name": "Mutation"}).get("__type") or {}).get("fields") or []
            inp = (self.client.query(Q.INTROSPECT_TYPE, {"name": "MattertagDetails"}).get("__type") or {}).get("inputFields") or []
        except Exception as exc:
            return [f"schema not checked: {type(exc).__name__}"]
        if "addMattertag" not in {f["name"] for f in mut}:
            problems.append("Mutation.addMattertag is missing")
        names = {f["name"] for f in inp}
        problems += [f"MattertagDetails.{f} is missing" for f in Q.ADD_TAG_INPUT_FIELDS if f not in names]
        return problems

    def add_tag(self, floor: str, position: list[float], label: str, description: str = "", kind: str = "observation") -> dict:
        twin = self.get_model()
        x, y, z = (list(position) + [1.2])[:3]
        mx, my, mz = _from_bldg(float(x), float(y), float(z), twin["_frame"])
        variables = {"modelId": self.model_id, "floorId": floor, "color": "#ff3b30", "label": label[:80], "description": description,
                     "enabled": True, "anchorPositionX": mx, "anchorPositionY": my, "anchorPositionZ": mz,
                     "stemEnabled": True, "stemNormalX": 0.0, "stemNormalY": 0.0, "stemNormalZ": 1.0, "stemLength": 0.3}
        tag = {"id": None, "kind": kind, "label": label, "floor": floor, "position": [round(float(v), 2) for v in (x, y, z)], "description": description}
        if self.dry_run:
            problems = self._check_mutation()
            log = self._dryrun_log()
            entries = json.loads(log.read_text(encoding="utf-8")) if log and log.exists() else []
            tag["id"] = f"dryrun-{len(entries) + 1}"
            tag["status"] = ("dry run: addMattertag built and checked against the live schema, NOT sent (set MATTERPORT_WRITE_TAGS=1 to write)"
                             if not problems else "dry run: NOT sent; schema check found: " + "; ".join(problems))
            if log:
                log.parent.mkdir(parents=True, exist_ok=True)
                entries.append({"mutation": "addMattertag", "variables": variables, "schema_problems": problems, "tag": tag})
                log.write_text(json.dumps(entries, indent=1), encoding="utf-8")
            twin["tags"].append(tag)
            return tag
        data = self.client.query(Q.ADD_TAG, variables, refresh=True)
        tag["id"] = data["addMattertag"]["id"]
        tag["status"] = "written to the Matterport model (Model API addMattertag)"
        twin["tags"].append(tag)
        return tag


# --- geometry helpers ----------------------------------------------------------------------------------


def _from_bldg(x: float, y: float, z: float, frame: dict) -> tuple[float, float, float]:
    return (round(x + frame["dx"], 4), round(y + frame["dy"], 4), round(z, 4))


def _area(poly: list) -> float:
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def _pip(pt, poly) -> bool:
    x, y, inside, j = pt[0], pt[1], False, len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _seg_hit(p, q, a, b):
    r, s = (q[0] - p[0], q[1] - p[1]), (b[0] - a[0], b[1] - a[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((a[0] - p[0]) * s[1] - (a[1] - p[1]) * s[0]) / den
    u = ((a[0] - p[0]) * r[1] - (a[1] - p[1]) * r[0]) / den
    return (p[0] + t * r[0], p[1] + t * r[1]) if 0.0 < t < 1.0 and 0.0 <= u <= 1.0 else None


def _hull(points: list) -> list:
    pts = sorted(set((round(p[0], 3), round(p[1], 3)) for p in points))
    if len(pts) < 3:
        return [list(p) for p in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [list(p) for p in lower[:-1] + upper[:-1]]


def _outer_face(segments: list[tuple[tuple, tuple]]) -> list | None:
    """Outer boundary of a planar set of segments that share endpoints: dangling spurs are pruned, the faces
    are traced with the next-clockwise rule, and the face with the largest area is returned (counter-clockwise)."""
    key = lambda p: (round(p[0], 3), round(p[1], 3))  # noqa: E731
    adj: dict[tuple, set] = {}
    for a, b in segments:
        ka, kb = key(a), key(b)
        if ka != kb:
            adj.setdefault(ka, set()).add(kb)
            adj.setdefault(kb, set()).add(ka)
    changed = True
    while changed:                                     # prune spurs
        changed = False
        for v in [v for v, n in adj.items() if len(n) < 2]:
            for w in adj.pop(v, ()):
                if w in adj:
                    adj[w].discard(v)
            changed = True
    if len(adj) < 3:
        return None
    order = {v: sorted(n, key=lambda w: math.atan2(w[1] - v[1], w[0] - v[0])) for v, n in adj.items()}
    seen, best = set(), None
    for u in adj:
        for v in adj[u]:
            if (u, v) in seen:
                continue
            face, a, b = [], u, v
            for _ in range(4 * len(adj) + 8):
                if (a, b) in seen:
                    break
                seen.add((a, b))
                face.append(a)
                nb = order[b]
                i = nb.index(a)
                a, b = b, nb[(i - 1) % len(nb)]       # next edge: the neighbour just clockwise of where we came from
            if len(face) >= 3:
                ar = abs(_area(face))
                if best is None or ar > best[0]:
                    best = (ar, face)
    if not best:
        return None
    poly = [list(p) for p in best[1]]
    return poly if _area(poly) > 0 else poly[::-1]


def _interior_point(poly: list) -> list:
    cx, cy = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
    if _pip((cx, cy), poly):
        return [round(cx, 2), round(cy, 2)]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    best = None
    for i in range(1, 12):                             # scan a grid for the inside point furthest from the edges
        for j in range(1, 12):
            q = (min(xs) + (max(xs) - min(xs)) * i / 12, min(ys) + (max(ys) - min(ys)) * j / 12)
            if _pip(q, poly):
                d = min(_seg_dist(q, poly[k], poly[(k + 1) % len(poly)]) for k in range(len(poly)))
                if best is None or d > best[0]:
                    best = (d, q)
    return [round(best[1][0], 2), round(best[1][1], 2)] if best else [round(cx, 2), round(cy, 2)]


def _seg_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / den))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


# --- naming -------------------------------------------------------------------------------------------

_KIND_WORDS = (("elevator", "elevator"), ("lift", "elevator"), ("stair", "stair"), ("hall", "corridor"), ("corridor", "corridor"),
               ("lobby", "lobby"), ("entry", "lobby"), ("foyer", "lobby"), ("reception", "lobby"), ("bath", "restroom"),
               ("restroom", "restroom"), ("toilet", "restroom"), ("utility", "mechanical"), ("closet", "mechanical"),
               ("storag", "mechanical"), ("mechanical", "mechanical"), ("electrical", "mechanical"), ("laundry", "mechanical"))


def _room_kind(words: list[str]) -> str:
    for w in words:
        low = (w or "").lower()
        for needle, kind in _KIND_WORDS:
            if needle in low:
                return kind
    return "suite"


_NOISE_TAGS = {"other", "unfurnished", "furnished", "house", "bedroom", "living", "dining", "pool", "patio", "outdoor_other", "basement", "garage"}


def _room_name(r: dict) -> str:
    label = (r.get("label") or "").strip()
    if label:
        return {"Storag": "Storage"}.get(label, label)
    for c in r.get("classifications") or []:
        if c.get("label") and c["label"].lower() not in ("other", "void"):
            return c["label"]
    for t in r.get("tags") or []:                      # Matterport's automatic tags; residential ones are noise in a commercial scan
        if t and t.lower() not in _NOISE_TAGS:
            return t.replace("_", " ").capitalize()
    return "Room"


def _suite_of(label: str) -> str | None:
    m = re.search(r"\b(?:suite|ste|unit|#)\s*([0-9]{2,4}[A-Z]?)\b", label or "", re.I)
    return m.group(1) if m else None


def _compass(bearing: float) -> str:
    return ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")[int(((bearing % 360) + 22.5) // 45) % 8]


# --- normalisation ------------------------------------------------------------------------------------------


def fit_georef(samples: list[dict], dx: float, dy: float) -> dict | None:
    """Origin (bldg 0,0) and bearing of +x from Matterport's lat/lon for a model point, +10 m x and +10 m y."""
    if len(samples) < 3:
        return None
    lat0 = samples[0]["lat"]
    k = math.cos(math.radians(lat0))
    en = [((s["lon"] - samples[0]["lon"]) * M_PER_DEG * k, (s["lat"] - lat0) * M_PER_DEG) for s in samples]
    ex, ey = en[1], en[2]
    b = math.atan2(ex[0], ex[1])                                       # compass bearing of +x
    y_expected = math.degrees(b) - 90.0
    y_actual = math.degrees(math.atan2(ey[0], ey[1]))
    mirrored = abs(((y_actual - y_expected + 180) % 360) - 180) > 45
    scale = (math.hypot(*ex) / 10.0, math.hypot(*ey) / 10.0)
    x0, y0 = samples[0]["model"][0] - dx, samples[0]["model"][1] - dy   # the first sample in the building frame
    east0, north0 = x0 * math.sin(b) - y0 * math.cos(b), x0 * math.cos(b) + y0 * math.sin(b)
    return {"origin": {"lat": round(lat0 - north0 / M_PER_DEG, 7), "lon": round(samples[0]["lon"] - east0 / (M_PER_DEG * k), 7)},
            "bearing_deg": round(math.degrees(b) % 360.0, 2),
            "method": "fitted to Matterport geoLocationOf at three model points", "mirrored": mirrored,
            "scale_check": [round(s, 3) for s in scale], "status": samples[0].get("status")}


def normalize_model(model: dict, geo_samples: list[dict] | None = None, floorplans: list[dict] | None = None,
                    exercise_floor: str | None = None, storey: int | None = None, suite: str | None = None) -> dict:
    """`storey`: the building storey of the scan's lowest floor (a suite scan does not know it; the building record does),
    used for floors[].label so tenants join by floor. `suite`: the suite number of a single-suite scan, put on its rooms."""
    floors_raw = sorted(model.get("floors") or [], key=lambda f: f.get("sequence", 0))
    locs_raw = [l for l in model.get("locations") or [] if l.get("position") and (l.get("floor") or {}).get("id")]
    if not locs_raw:
        raise RuntimeError("the Matterport model has no sweep locations with positions")
    if not floors_raw:
        floors_raw = [{"id": fid, "label": None, "sequence": i} for i, fid in enumerate(dict.fromkeys(l["floor"]["id"] for l in locs_raw))]
    approx: list[str] = []

    # frame: the model's axes, shifted so the plan starts near (1, 1)
    xs = [l["position"]["x"] for l in locs_raw] + [v["position"]["x"] for f in floors_raw for v in f.get("vertices") or []]
    ys = [l["position"]["y"] for l in locs_raw] + [v["position"]["y"] for f in floors_raw for v in f.get("vertices") or []]
    dx, dy = round(min(xs) - 1.0, 3), round(min(ys) - 1.0, 3)
    P = lambda x, y: [round(x - dx, 3), round(y - dy, 3)]  # noqa: E731

    # floor edges: walls, invisible dividers and their openings
    verts, edges = {}, {}
    for f in floors_raw:
        for v in f.get("vertices") or []:
            verts[(f["id"], v["id"])] = P(v["position"]["x"], v["position"]["y"])
        for e in f.get("edges") or []:
            vs = [verts.get((f["id"], v["id"])) for v in e.get("vertices") or []]
            if len(vs) == 2 and all(vs):
                edges[e["id"]] = {"floor": f["id"], "a": vs[0], "b": vs[1], "type": e.get("type") or "wall", "openings": e.get("openings") or []}

    # sweeps per floor and floor elevations
    z_by_floor: dict[str, list[float]] = {}
    for l in locs_raw:
        z_by_floor.setdefault(l["floor"]["id"], []).append(l["position"]["z"])
    med = {fid: sorted(zs)[len(zs) // 2] for fid, zs in z_by_floor.items()}
    floor_ids = [f["id"] for f in floors_raw if f["id"] in z_by_floor or any(e["floor"] == f["id"] for e in edges.values())]
    base = min(med.values())
    elev = {fid: round(med.get(fid, base) - base, 2) for fid in floor_ids}
    f2f = sorted(elev.values())
    f2f_h = round(f2f[1] - f2f[0], 2) if len(f2f) > 1 and f2f[1] - f2f[0] > 1.8 else None

    # rooms
    rooms, room_raw, void_ids = [], {}, set()
    from_boundary = traced = hull = from_sweeps = 0
    for r in model.get("rooms") or []:
        fid = (r.get("floor") or {}).get("id")
        if fid not in floor_ids:
            continue
        if any((c.get("label") or "").lower() == "void" for c in r.get("classifications") or []):
            void_ids.add(r["id"])
            continue
        segs = [(edges[e["id"]]["a"], edges[e["id"]]["b"]) for e in (r.get("boundary") or {}).get("edges") or [] if e["id"] in edges]
        poly = None
        if segs:
            poly = _outer_face(segs)
            if poly:
                from_boundary += 1
                deg: dict = {}
                for a, b in segs:
                    for p in (tuple(a), tuple(b)):
                        deg[p] = deg.get(p, 0) + 1
                traced += any(d != 2 for d in deg.values())
            else:
                poly = _hull([p for s in segs for p in s])
                hull += 1
        if not poly:
            pts = [P(l["position"]["x"], l["position"]["y"]) for l in locs_raw if (l.get("room") or {}).get("id") == r["id"]]
            if not pts:
                continue
            d = r.get("dimensions") or {}
            hw, hd = max(1.2, (d.get("width") or 2.4) / 2), max(1.2, (d.get("depth") or 2.4) / 2)
            cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            poly = [[round(cx - hw, 2), round(cy - hd, 2)], [round(cx + hw, 2), round(cy - hd, 2)], [round(cx + hw, 2), round(cy + hd, 2)], [round(cx - hw, 2), round(cy + hd, 2)]]
            from_sweeps += 1
        name = _room_name(r)
        kind = _room_kind([r.get("label") or ""] + [c.get("label") for c in r.get("classifications") or []] + list(r.get("tags") or []))
        d = r.get("dimensions") or {}
        area = d.get("areaFloor")
        rooms.append({"id": r["id"], "floor": fid, "kind": kind, "label": name, "suite": suite or _suite_of(r.get("label") or ""),
                      "polygon": [[round(p[0], 2), round(p[1], 2)] for p in poly],
                      "area_m2": round(area, 1) if isinstance(area, (int, float)) and area > 0 else round(abs(_area(poly)), 1),
                      "height_m": round(d["height"], 2) if isinstance(d.get("height"), (int, float)) else f2f_h,
                      "source": {"classification": [c.get("label") for c in r.get("classifications") or []], "tags": r.get("tags") or [],
                                 "label": r.get("label") or None}})
        room_raw[r["id"]] = r
    # number repeated names per floor ("Office 1", "Office 2", ...), west to east then south to north
    for fid in floor_ids:
        groups: dict[str, list] = {}
        for rm in rooms:
            if rm["floor"] == fid:
                groups.setdefault(rm["label"], []).append(rm)
        for label, rs in groups.items():
            if len(rs) > 1:
                rs.sort(key=lambda q: (round(_interior_point(q["polygon"])[0]), _interior_point(q["polygon"])[1]))
                for i, rm in enumerate(rs):
                    rm["label"] = f"{label} {i + 1}"
    room_by_id = {r["id"]: r for r in rooms}
    if from_boundary < len(rooms):
        approx.append(f"{len(rooms) - from_boundary} of {len(rooms)} room outlines are not the scan's boundary ({hull} convex hulls of boundary edges, "
                      f"{from_sweeps} boxes from sweep positions and room dimensions)")
    if any(rm["height_m"] is None or not isinstance((room_raw[rm['id']].get('dimensions') or {}).get('height'), (int, float)) for rm in rooms):
        approx.append("room heights missing from the scan are set to the floor-to-floor height of the sweeps" if f2f_h else "room heights missing from the scan are left empty")

    # which rooms share each edge (for doors)
    edge_rooms: dict[str, list[str]] = {}
    for rid, r in room_raw.items():
        for e in (r.get("boundary") or {}).get("edges") or []:
            edge_rooms.setdefault(e["id"], []).append(rid)

    def locate_room(fid: str, pt) -> str | None:
        inside = [r for r in rooms if r["floor"] == fid and _pip(pt, r["polygon"])]
        if inside:
            return min(inside, key=lambda r: abs(_area(r["polygon"])))["id"]
        cands = [r for r in rooms if r["floor"] == fid]
        return min(cands, key=lambda r: min(_seg_dist(pt, r["polygon"][k], r["polygon"][(k + 1) % len(r["polygon"])]) for k in range(len(r["polygon"]))))["id"] if cands else None

    # walls and doors
    walls = [{"floor": e["floor"], "a": e["a"], "b": e["b"]} for e in edges.values() if e["type"] == "wall"]
    doors, door_gaps = [], []          # door_gaps: (edge id, centre, half width) where a walking link may cross a wall
    lowest = floor_ids[0]
    for eid, e in edges.items():
        for o in e["openings"]:
            if o.get("type") not in ("doorway", "opening"):
                continue
            t = float(o.get("relativeCenter") or 0.5)
            at = [round(e["a"][0] + t * (e["b"][0] - e["a"][0]), 2), round(e["a"][1] + t * (e["b"][1] - e["a"][1]), 2)]
            width = round(float(o.get("width") or 0.9), 2)
            door_gaps.append((eid, at, width / 2))
            rs = [rid for rid in edge_rooms.get(eid, []) if rid in room_by_id]
            if not rs:
                continue
            if len(rs) >= 2:
                a, b = rs[0], rs[1]
                ka, kb = room_by_id[a]["kind"], room_by_id[b]["kind"]
                outer_first = ka in ("corridor", "lobby") and kb not in ("corridor", "lobby")
                inner, outer = (b, a) if outer_first else (a, b)
                kind = "stair_door" if "stair" in (ka, kb) else "opening" if o["type"] == "opening" else "door"
            else:
                inner, outer = rs[0], None
                kind = "exit" if e["floor"] == lowest else "door"
            doors.append({"id": f"d{len(doors) + 1:02d}", "floor": e["floor"], "at": at, "width_m": width, "rooms": [inner, outer],
                          "kind": kind, "node": None, "source": o.get("label") or o.get("type")})
    for e in edges.values():                       # invisible edges: open dividers between rooms, never walls
        if e["type"] != "wall":
            door_gaps.append((None, [(e["a"][0] + e["b"][0]) / 2, (e["a"][1] + e["b"][1]) / 2], math.dist(e["a"], e["b"]) / 2 + 0.1))

    def blocked(fid: str, p, q, allow_edge: str | None = None) -> bool:
        """True when the straight segment p-q passes through a wall away from every doorway/opening of that floor."""
        for eid, e in edges.items():
            if e["floor"] != fid or e["type"] != "wall":
                continue
            h = _seg_hit(p, q, e["a"], e["b"])
            if h is None:
                continue
            if eid == allow_edge:
                continue
            if any(math.dist(h, c) <= hw + 0.2 for ge, c, hw in door_gaps if ge in (eid, None)):
                continue
            return True
        return False

    def door_on(fid: str, p, q) -> str | None:
        for d in doors:
            if d["floor"] == fid and _seg_dist(d["at"], p, q) <= d["width_m"] / 2 + 0.3:
                return d["id"]
        return None

    # graph: sweeps
    nodes, gedges, sweeps = [], [], []
    node_of: dict[str, str] = {}
    pos3: dict[str, list] = {}
    for l in locs_raw:
        fid = l["floor"]["id"]
        if fid not in floor_ids:
            continue
        p = P(l["position"]["x"], l["position"]["y"])
        rid = (l.get("room") or {}).get("id")
        if rid not in room_by_id:
            rid = locate_room(fid, p)
        nid = f"n{len(nodes) + 1:03d}"
        node_of[l["id"]] = nid
        pos3[nid] = [p[0], p[1], round(l["position"]["z"], 2)]
        nodes.append({"id": nid, "floor": fid, "at": p, "room": rid, "kind": "stair" if rid and room_by_id[rid]["kind"] == "stair" else "sweep"})
    ndict = {n["id"]: n for n in nodes}
    seen_pairs, dropped_wall, dropped_floor, cross_floor = set(), 0, 0, 0
    for l in locs_raw:
        a = node_of.get(l["id"])
        for nb in l.get("neighbors") or []:
            b = node_of.get(nb)
            if not a or not b or frozenset((a, b)) in seen_pairs:
                continue
            seen_pairs.add(frozenset((a, b)))
            na, nbn = ndict[a], ndict[b]
            if na["floor"] != nbn["floor"]:
                if "stair" in (na["kind"], nbn["kind"]):
                    gedges.append({"a": a, "b": b, "len_m": round(math.dist(pos3[a], pos3[b]), 2), "via": None})
                    cross_floor += 1
                else:
                    dropped_floor += 1
                continue
            if blocked(na["floor"], na["at"], nbn["at"]):
                dropped_wall += 1
                continue
            dd = door_on(na["floor"], na["at"], nbn["at"])
            gedges.append({"a": a, "b": b, "len_m": round(math.dist(na["at"], nbn["at"]), 2), "via": f"door:{dd}" if dd else None})
    sweep_links = len(gedges)

    # rooms without a sweep get one node inside them
    room_nodes = 0
    for rm in rooms:
        if not any(n["room"] == rm["id"] for n in nodes):
            nid = f"n{len(nodes) + 1:03d}"
            n = {"id": nid, "floor": rm["floor"], "at": _interior_point(rm["polygon"]), "room": rm["id"], "kind": "room"}
            nodes.append(n)
            ndict[nid] = n
            room_nodes += 1
    if room_nodes:
        approx.append(f"{room_nodes} rooms have no sweep (nobody stood a camera there): each gets one node at an interior point")

    # door nodes, linked to the nearest reachable node on each side
    def nearest(fid, rid, at, exclude=()):
        pool = [n for n in nodes if n["floor"] == fid and n["room"] == rid and n["kind"] not in ("door", "exit") and n["id"] not in exclude]
        pool.sort(key=lambda n: math.dist(n["at"], at))
        return pool
    door_links = 0
    for d in doors:
        nid = f"n{len(nodes) + 1:03d}"
        side_room = d["rooms"][1] or d["rooms"][0]
        n = {"id": nid, "floor": d["floor"], "at": d["at"], "room": side_room, "kind": "exit" if d["kind"] == "exit" else "door"}
        nodes.append(n)
        ndict[nid] = n
        d["node"] = nid
        for rid in [r for r in d["rooms"] if r]:
            for cand in nearest(d["floor"], rid, d["at"])[:3]:
                if not blocked(d["floor"], d["at"], cand["at"]):
                    gedges.append({"a": nid, "b": cand["id"], "len_m": round(math.dist(d["at"], cand["at"]), 2), "via": f"door:{d['id']}"})
                    door_links += 1
                    break

    # stairs: stair rooms joined across floors by position
    stair_rooms = [r for r in rooms if r["kind"] == "stair"]
    shafts: list[list[dict]] = []
    for r in sorted(stair_rooms, key=lambda r: floor_ids.index(r["floor"])):
        c = _interior_point(r["polygon"])
        for s in shafts:
            if all(o["floor"] != r["floor"] for o in s) and any(_pip(c, o["polygon"]) or math.dist(c, _interior_point(o["polygon"])) < 3.0 for o in s):
                s.append(r)
                break
        else:
            shafts.append([r])
    # flights: link the stair rooms of one shaft on consecutive floors when the scan did not
    adj: dict[str, set] = {}
    for e in gedges:
        adj.setdefault(e["a"], set()).add(e["b"])
        adj.setdefault(e["b"], set()).add(e["a"])
    inferred_flights = 0
    for s in shafts:
        for lo, hi in zip(s, s[1:]):
            lo_n = [n for n in nodes if n["room"] == lo["id"]]
            hi_n = [n for n in nodes if n["room"] == hi["id"]]
            if not lo_n or not hi_n or any(b["id"] in adj.get(a["id"], ()) for a in lo_n for b in hi_n):
                continue
            a, b = min(((a, b) for a in lo_n for b in hi_n), key=lambda ab: math.dist(ab[0]["at"], ab[1]["at"]))
            rise = abs(elev.get(hi["floor"], 0) - elev.get(lo["floor"], 0))
            gedges.append({"a": a["id"], "b": b["id"], "len_m": round(math.hypot(math.dist(a["at"], b["at"]), rise) + rise, 2), "via": None})
            inferred_flights += 1
    if inferred_flights:
        approx.append(f"{inferred_flights} stair flights linked by position (the scan has no sweep link between those floors)")

    # connectivity: join stray components to the main one with the shortest wall-free link on the same floor
    def components():
        par = {n["id"]: n["id"] for n in nodes}

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        for e in gedges:
            par[find(e["a"])] = find(e["b"])
        comp: dict[str, list] = {}
        for n in nodes:
            comp.setdefault(find(n["id"]), []).append(n)
        return sorted(comp.values(), key=len, reverse=True)
    joined = forced = 0
    for _ in range(len(nodes)):
        comps = components()
        if len(comps) <= 1:
            break
        main, stray = comps[0], comps[1]
        pairs = sorted(((a, b) for a in stray for b in main if a["floor"] == b["floor"]), key=lambda ab: math.dist(ab[0]["at"], ab[1]["at"]))
        pick = next(((a, b) for a, b in pairs[:400] if not blocked(a["floor"], a["at"], b["at"])), None)
        if pick is None and pairs:
            pick, forced = pairs[0], forced + 1
        if pick is None:
            break
        a, b = pick
        dd = door_on(a["floor"], a["at"], b["at"])
        gedges.append({"a": a["id"], "b": b["id"], "len_m": round(math.dist(a["at"], b["at"]), 2), "via": f"door:{dd}" if dd else None})
        joined += 1
    if joined:
        approx.append(f"{joined} links added to join parts of the plan the sweep graph left apart ({forced} of them through a wall: check those on the render)")

    # stairs list, discharge from the nearest street door of the lowest floor
    fl_index = {fid: i for i, fid in enumerate(floor_ids)}
    rooms_on = lambda fid: [r for r in rooms if r["floor"] == fid]  # noqa: E731
    ex_floor = None
    if exercise_floor:
        ex_floor = next((f["id"] for f in floors_raw if exercise_floor in (f["id"], f.get("label"), str(fl_index.get(f["id"], -1) + 1))), None)
    if not ex_floor:
        ex_floor = max(floor_ids, key=lambda fid: (len([r for r in rooms_on(fid) if any(n["room"] == r["id"] for n in nodes)]), fl_index[fid]))
    exits_street = [d for d in doors if d["kind"] == "exit"]
    stairs = []
    for i, s in enumerate(sorted(shafts, key=lambda s: (-len(s), _interior_point(s[0]["polygon"])[0]))):
        here = next((r for r in s if r["floor"] == ex_floor), s[0])
        sid = "stair" + chr(ord("A") + i)
        for r in s:
            r["label"] = f"Stair {chr(ord('A') + i)}" + (f" ({_floor_label(floors_raw, r['floor'], fl_index)})" if len(s) > 1 and r["floor"] != here["floor"] else "")
        door = next((d["id"] for d in doors if d["kind"] == "stair_door" and here["id"] in d["rooms"]), None)
        foot = s[0]
        disc = None
        if exits_street and foot["floor"] == lowest:
            c = _interior_point(foot["polygon"])
            ex = min(exits_street, key=lambda d: math.dist(d["at"], c))
            disc = {"door": ex["id"], "dist_m": round(math.dist(ex["at"], c), 1)}
        stairs.append({"id": sid, "label": f"Stair {chr(ord('A') + i)}", "room": here["id"], "door": door, "floors": [r["floor"] for r in s],
                       "discharge": disc, "standpipe": None, "roof_access": None})
    fallback_exit = False
    if not any(st["room"] in {r["id"] for r in rooms_on(ex_floor)} for st in stairs):
        # no stair on the exercise floor (a suite scan): the suite's entrances are the ways out. An entrance is a doorway on the
        # outer wall of the scanned plan (an edge on only one room's boundary); without one, the lobby/corridor is used.
        ents = [d for d in doors if d["floor"] == ex_floor and d["rooms"][1] is None]
        for d in ents:
            d["kind"] = "exit"
            ndict[d["node"]]["kind"] = "exit"
        letter = len(stairs)
        if ents:
            for i, d in enumerate(ents):
                stairs.append({"id": "stair" + chr(ord("A") + letter + i), "label": "Suite entrance" + (f" {i + 1}" if len(ents) > 1 else ""),
                               "room": d["rooms"][0], "door": d["id"], "floors": [ex_floor], "node": d["node"],
                               "discharge": "into the building's common corridor (outside the scan); the building's stairs are not in the scan",
                               "standpipe": None, "roof_access": None})
            approx.append(f"no stair in the scan: the {len(ents)} doorway(s) on the outer wall of the suite are its entrances and are used as the exits")
        else:
            rid = next((r["id"] for r in rooms_on(ex_floor) if r["kind"] in ("lobby", "corridor")), None) or (rooms_on(ex_floor) or [{"id": None}])[0]["id"]
            if rid:
                stairs.append({"id": "stair" + chr(ord("A") + letter), "label": "Suite entrance", "room": rid, "door": None, "floors": [ex_floor],
                               "discharge": "into the building (outside the scan)", "standpipe": None, "roof_access": None})
                approx.append("no stair and no outer doorway in the scan: the lobby/corridor room is marked as the suite entrance and exit")
        fallback_exit = True
    # an exit's own node must be the first node of its room, because the spatial engine resolves a stair to its room's first node
    first = [s["node"] for s in stairs if s.get("node")]
    if first:
        nodes.sort(key=lambda n: (n["id"] not in first, first.index(n["id"]) if n["id"] in first else 0))

    # floors
    floors = []
    for fid in floor_ids:
        f = next(x for x in floors_raw if x["id"] == fid)
        segs = [(e["a"], e["b"]) for e in edges.values() if e["floor"] == fid]
        outline = _outer_face(segs) if segs else None
        if not outline or abs(_area(outline)) < 0.5 * sum(abs(_area(r["polygon"])) for r in rooms_on(fid)):
            pts = [p for r in rooms_on(fid) for p in r["polygon"]] + [n["at"] for n in nodes if n["floor"] == fid]
            outline = _hull(pts)
            approx.append(f"floor {fl_index[fid] + 1} outline is the convex hull of its rooms and sweeps")
        floors.append({"id": fid, "index": fl_index[fid], "label": str((storey if storey is not None else 1) + fl_index[fid]),
                       "name": f.get("label") or f"Floor {fl_index[fid] + 1}",
                       "elevation_m": elev.get(fid, 0.0), "outline": [[round(p[0], 2), round(p[1], 2)] for p in outline],
                       "modelled": any(n["floor"] == fid for n in nodes) and bool(rooms_on(fid))})

    # discharge text needs the georeference for the compass side
    georef = fit_georef(geo_samples or [], dx, dy)
    g = model.get("geolocation") or {}
    gc = model.get("geocoordinates") or {}
    if georef is None:
        lat = gc.get("latitude") if gc.get("latitude") is not None else (float(g["lat"]) if g.get("lat") else None)
        lon = gc.get("longitude") if gc.get("longitude") is not None else (float(g["long"]) if g.get("long") else None)
        georef = {"origin": {"lat": lat or 0.0, "lon": lon or 0.0}, "bearing_deg": 90.0,
                  "method": "the model's point placed at the plan's corner, +x assumed east (Matterport gave no point conversion)", "mirrored": False}
        approx.append("georeference: the model's single geolocation, with +x assumed east; verify on the aerial")
    elif georef.get("mirrored"):
        approx.append("georeference: Matterport's conversion is mirrored relative to the plan; the board's frame cannot mirror, so check the aerial")
    allx = [p[0] for f in floors for p in f["outline"]]
    ally = [p[1] for f in floors for p in f["outline"]]
    centre = ((min(allx) + max(allx)) / 2, (min(ally) + max(ally)) / 2)
    b = math.radians(georef["bearing_deg"])
    for st in stairs:
        d = st["discharge"]
        if isinstance(d, dict):
            door = next(x for x in doors if x["id"] == d["door"])
            vx, vy = door["at"][0] - centre[0], door["at"][1] - centre[1]
            bearing = math.degrees(math.atan2(vx * math.sin(b) - vy * math.cos(b), vx * math.cos(b) + vy * math.sin(b)))
            st["discharge"] = f"{_floor_label(floors_raw, lowest, fl_index)}: street door on the {_compass(bearing)} side, about {round(d['dist_m'])} m from the foot of the stair"
    if model.get("geolocation") and georef.get("method", "").startswith("fitted"):
        lat0, lon0 = float(g["lat"]), float(g["long"])
        e_, n_ = centre[0] * math.sin(b) - centre[1] * math.cos(b), centre[0] * math.cos(b) + centre[1] * math.sin(b)
        clat, clon = georef["origin"]["lat"] + n_ / M_PER_DEG, georef["origin"]["lon"] + e_ / (M_PER_DEG * math.cos(math.radians(georef["origin"]["lat"])))
        georef["check"] = {"plan_centre_to_model_geolocation_m": round(math.hypot((clon - lon0) * M_PER_DEG * math.cos(math.radians(lat0)), (clat - lat0) * M_PER_DEG), 1)}

    # tags
    out_tags = []
    for t in model.get("mattertags") or []:
        pos = t.get("anchorPosition") or t.get("position")
        if not pos:
            continue
        low = ((t.get("label") or "") + " " + (t.get("description") or "")).lower()
        kind = next((k for k in ("standpipe", "electrical", "extinguisher", "refuge", "hazard", "exit") if k in low), "tag")
        out_tags.append({"id": t["id"], "kind": kind, "label": t.get("label"), "floor": (t.get("floor") or {}).get("id"),
                         "position": P(pos["x"], pos["y"]) + [round(pos["z"], 2)], "description": t.get("description") or ""})

    # sweeps
    nbrs: dict[str, list[str]] = {}
    sweep_of_node = {v: k for k, v in node_of.items()}
    for e in gedges:
        for u, v in ((e["a"], e["b"]), (e["b"], e["a"])):
            if u in sweep_of_node and v in sweep_of_node:
                nbrs.setdefault(u, []).append(sweep_of_node[v])
    for l in locs_raw:
        nid = node_of.get(l["id"])
        if nid:
            sweeps.append({"id": l["id"], "node": nid, "floor": l["floor"]["id"], "room": ndict[nid]["room"], "position": pos3[nid], "neighbors": nbrs.get(nid, [])})

    fp = floorplans or []
    note = ("Matterport Model API (live scan). Real from the scan: room outlines (the scan's room boundaries"
            f"{'' if from_boundary == len(rooms) else f' for {from_boundary} of {len(rooms)} rooms'}), walls and doorways (the floor-plan edges and their openings), "
            f"room names (labels and Matterport's room classifications), sweep positions and Matterport's sweep-to-sweep neighbour links ({sweep_links} kept; "
            f"{dropped_wall} dropped because they pass through a wall away from any doorway, {dropped_floor} dropped across floors outside a stair). "
            f"Built here: door nodes linked to the nearest sweep on each side; stairs are rooms classified Staircase joined across floors by position; "
            f"street doors are doorways on the outer wall of the lowest floor; floor labels are renumbered 1..n from Matterport's floor order. "
            f"Georeference: {georef.get('method')}. Not in the scan: standpipes, sprinklers, roof access, suite numbers"
            + ("" if not fallback_exit else "; the exit is the suite entrance") + ".")
    if approx:
        note += " Approximations: " + "; ".join(approx) + "."
    return {"schema": "sandtable.twin/1",
            "source": {"kind": "matterport-live", "synthetic": False, "placeholder": False, "note": note, "approximations": approx,
                       "counts": {"rooms": len(rooms), "rooms_from_boundary": from_boundary, "rooms_traced_complex": traced, "void_regions_skipped": len(void_ids),
                                  "walls": len(walls), "doors": len(doors), "street_doors": len(exits_street), "sweeps": len(sweeps),
                                  "sweep_links_kept": sweep_links, "sweep_links_dropped_wall": dropped_wall, "cross_floor_stair_links": cross_floor,
                                  "door_links": door_links, "room_nodes": room_nodes, "links_added_for_connectivity": joined, "forced_links": forced,
                                  "floorplan_assets": len(fp), "mattertags": len(out_tags)},
                       "floorplans": [{"floor": (f.get("floor") or {}).get("id"), "format": f.get("format"), "width": f.get("width"), "height": f.get("height"),
                                       "resolution": f.get("resolution"), "origin": f.get("origin"), "flags": f.get("flags")} for f in fp]},
            "model": {"id": model.get("id"), "name": model.get("name"), "address": (model.get("address") or {}).get("address"),
                      "city": (model.get("address") or {}).get("locality"), "state": (model.get("address") or {}).get("administrativeArea")},
            "georef": georef,
            "_frame": {"dx": dx, "dy": dy, "up_axis": "z"},
            "exercise_floor": ex_floor, "floors": floors, "rooms": rooms, "walls": walls, "doors": doors, "stairs": stairs,
            "sweeps": sweeps, "tags": out_tags, "graph": {"nodes": nodes, "edges": gedges}}


def _floor_label(floors_raw: list, fid: str, fl_index: dict) -> str:
    return f"floor {fl_index.get(fid, 0) + 1}"
