"""Matterport live adapter (Model API, GraphQL).

WRITTEN FROM DOCUMENTATION, NOT EXERCISED WITHOUT A KEY.
Endpoint: https://api.matterport.com/api/models/graph, HTTP basic auth with the API token id
and secret. The GraphQL documents are the documented samples (see matterport_queries.py).

`normalize_model()` turns the API response into the Sandtable twin schema (docs/CONTRACTS.md 3.1):
building frame in metres with z up, floors, rooms, sweeps, tags and a navigation graph.
Three things the Model API does not hand over directly are approximated, and each is recorded in
`source.note` so the UI can say so:
  - room polygons: the bounding box of the room's sweep positions, padded by 1.2 m;
  - the walking graph: sweeps on the same floor closer than MATTERPORT_LINK_M (default 4.5 m) are linked;
  - stairs and doors: taken from tags or room labels that contain "stair" / "exit".
With the Showcase SDK in the browser (`Sweep.createGraph()`, `Room`, `Floor`) these can be exact.
"""
from __future__ import annotations

import math
import os
from typing import Callable

from . import matterport_queries as Q

ENDPOINT = "https://api.matterport.com/api/models/graph"


class MatterportLive:
    mode = "live"

    def __init__(self, token_id: str, token_secret: str, model_id: str, transport: Callable | None = None):
        self.token_id, self.token_secret, self.model_id = token_id, token_secret, model_id
        self._transport = transport or self._http
        self._twin: dict | None = None

    @classmethod
    def from_env(cls) -> "MatterportLive":
        missing = [k for k in ("MATTERPORT_TOKEN_ID", "MATTERPORT_TOKEN_SECRET", "MATTERPORT_MODEL_ID") if not os.environ.get(k)]
        if missing:
            from . import AdapterError
            raise AdapterError("MATTERPORT_MODE=live but these are not set: " + ", ".join(missing))
        return cls(os.environ["MATTERPORT_TOKEN_ID"], os.environ["MATTERPORT_TOKEN_SECRET"], os.environ["MATTERPORT_MODEL_ID"])

    def _http(self, query: str, variables: dict) -> dict:
        import httpx
        r = httpx.post(ENDPOINT, json={"query": query, "variables": variables}, auth=(self.token_id, self.token_secret), timeout=60.0)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"Matterport GraphQL error: {body['errors'][0].get('message')}")
        return body["data"]

    def get_model(self) -> dict:
        if self._twin is None:
            v = {"modelId": self.model_id}
            details = self._transport(Q.MODEL_DETAILS, v)["model"]
            sweeps = self._transport(Q.SWEEPS, v)["model"]
            dims = self._transport(Q.DIMENSIONS, v)["model"]
            tags = self._transport(Q.TAGS, v)["model"]
            geo = self._transport(Q.GEOCOORDINATES, v)["model"]
            self._twin = normalize_model(details, sweeps, dims, tags, geo, up_axis=os.environ.get("MATTERPORT_UP_AXIS", "z"),
                                         link_m=float(os.environ.get("MATTERPORT_LINK_M", "4.5")))
        return self._twin

    def list_tags(self) -> list[dict]:
        return self.get_model()["tags"]

    def add_tag(self, floor: str, position: list[float], label: str, description: str = "", kind: str = "observation") -> dict:
        twin = self.get_model()
        x, y, z = (list(position) + [1.2])[:3]
        mx, my, mz = _from_bldg(x, y, z, twin["_frame"])
        data = self._transport(Q.ADD_TAG, {"modelId": self.model_id, "floorId": floor, "color": "#ff3b30", "label": label[:80], "description": description,
                                           "enabled": True, "anchorPositionX": mx, "anchorPositionY": my, "anchorPositionZ": mz,
                                           "stemEnabled": True, "stemNormalX": 0.0, "stemNormalY": 0.0, "stemNormalZ": 1.0, "stemLength": 0.3})
        tag = {"id": data["addMattertag"]["id"], "kind": kind, "label": label, "floor": floor, "position": [x, y, z], "description": description,
               "status": "written to the Matterport model (Model API addMattertag)"}
        twin["tags"].append(tag)
        return tag


def _to_bldg(p: dict, up_axis: str) -> tuple[float, float, float]:
    """Model API point -> (x, y, z up). The Model API is z-up; the Showcase SDK is y-up (set MATTERPORT_UP_AXIS=y for SDK-style data)."""
    if up_axis == "y":
        return p["x"], -p["z"], p["y"]
    return p["x"], p["y"], p["z"]


def _from_bldg(x: float, y: float, z: float, frame: dict) -> tuple[float, float, float]:
    mx, my = x + frame["dx"], y + frame["dy"]
    return (mx, z, -my) if frame["up_axis"] == "y" else (mx, my, z)


def normalize_model(details: dict, sweeps: dict, dims: dict, tags: dict, geo: dict, up_axis: str = "z", link_m: float = 4.5) -> dict:
    locs = []
    for loc in sweeps.get("locations") or []:
        if not loc.get("position") or not loc.get("floor"):
            continue
        x, y, z = _to_bldg(loc["position"], up_axis)
        locs.append({"id": loc["id"], "floor": loc["floor"]["id"], "room": (loc.get("room") or {}).get("id"), "p": (x, y, z)})
    if not locs:
        raise RuntimeError("the Matterport model has no sweep locations")
    dx, dy = min(l["p"][0] for l in locs) - 2.0, min(l["p"][1] for l in locs) - 2.0     # shift so the building frame starts near (0, 0)
    for l in locs:
        l["p"] = (round(l["p"][0] - dx, 2), round(l["p"][1] - dy, 2), round(l["p"][2], 2))
    floor_ids = sorted({l["floor"] for l in locs}, key=lambda f: sum(l["p"][2] for l in locs if l["floor"] == f) / max(1, sum(1 for l in locs if l["floor"] == f)))
    labels = {f.get("id"): f.get("label") for f in dims.get("floors") or [] if f.get("id")}
    room_meta = {r["id"]: r for r in dims.get("rooms") or []}
    floors, rooms, nodes, edges, sweeps_out, stairs = [], [], [], [], [], []
    for i, fid in enumerate(floor_ids):
        fl = [l for l in locs if l["floor"] == fid]
        xs, ys = [l["p"][0] for l in fl], [l["p"][1] for l in fl]
        outline = [[min(xs) - 2, min(ys) - 2], [max(xs) + 2, min(ys) - 2], [max(xs) + 2, max(ys) + 2], [min(xs) - 2, max(ys) + 2]]
        floors.append({"id": fid, "index": i, "label": labels.get(fid) or str(i + 1), "elevation_m": round(min(l["p"][2] for l in fl) - 1.5, 2), "outline": outline, "modelled": True})
        by_room: dict[str, list] = {}
        for l in fl:
            by_room.setdefault(l["room"] or f"{fid}-unassigned", []).append(l)
        for rid, members in by_room.items():
            meta = room_meta.get(rid, {})
            label = meta.get("label") or (", ".join(meta.get("tags") or []) or "Room")
            low = label.lower()
            kind = "stair" if "stair" in low else "corridor" if any(k in low for k in ("hall", "corridor")) else "lobby" if "lobby" in low else "suite"
            rx, ry = [m["p"][0] for m in members], [m["p"][1] for m in members]
            poly = [[min(rx) - 1.2, min(ry) - 1.2], [max(rx) + 1.2, min(ry) - 1.2], [max(rx) + 1.2, max(ry) + 1.2], [min(rx) - 1.2, max(ry) + 1.2]]
            area = (meta.get("dimensions") or {}).get("areaFloor")
            rooms.append({"id": rid, "floor": fid, "kind": kind, "label": label, "suite": _suite_of(label), "polygon": poly,
                          "area_m2": round(area, 1) if isinstance(area, (int, float)) else round((max(rx) - min(rx) + 2.4) * (max(ry) - min(ry) + 2.4), 1), "height_m": (meta.get("dimensions") or {}).get("height")})
            if kind == "stair":
                stairs.append({"id": "stair" + chr(ord("A") + len(stairs)), "label": label, "room": rid, "door": None, "floors": floor_ids, "discharge": None, "standpipe": None, "roof_access": None})
        for l in fl:
            nid = f"n{len(nodes) + 1:03d}"
            l["node"] = nid
            room = l["room"] or f"{fid}-unassigned"
            kind = "stair" if any(r["id"] == room and r["kind"] == "stair" for r in rooms) else "sweep"
            nodes.append({"id": nid, "floor": fid, "at": [l["p"][0], l["p"][1]], "room": room, "kind": kind})
        for a in range(len(fl)):
            for b in range(a + 1, len(fl)):
                d = math.dist(fl[a]["p"][:2], fl[b]["p"][:2])
                if d <= link_m:
                    edges.append({"a": fl[a]["node"], "b": fl[b]["node"], "len_m": round(d, 2), "via": None})
    nbrs: dict[str, list[str]] = {}
    for e in edges:
        nbrs.setdefault(e["a"], []).append(e["b"])
        nbrs.setdefault(e["b"], []).append(e["a"])
    for l in locs:
        sweeps_out.append({"id": l["id"], "node": l["node"], "floor": l["floor"], "room": l["room"], "position": list(l["p"]),
                           "neighbors": [next(q["id"] for q in locs if q["node"] == n) for n in nbrs.get(l["node"], [])]})
    out_tags = []
    for t in tags.get("mattertags") or []:
        pos = t.get("anchorPosition") or t.get("position")
        if not pos:
            continue
        x, y, z = _to_bldg(pos, up_axis)
        low = (t.get("label") or "").lower()
        kind = next((k for k in ("standpipe", "electrical", "extinguisher", "refuge", "hazard") if k in low), "tag")
        out_tags.append({"id": t["id"], "kind": kind, "label": t.get("label"), "floor": (t.get("floor") or {}).get("id"),
                         "position": [round(x - dx, 2), round(y - dy, 2), round(z, 2)], "description": t.get("description") or ""})
    g = (geo.get("geocoordinates") or {})
    rot = g.get("rotation") or {"x": 0, "y": 0, "z": 0, "w": 1}
    yaw = math.degrees(math.atan2(2 * (rot["w"] * rot["z"] + rot["x"] * rot["y"]), 1 - 2 * (rot["y"] ** 2 + rot["z"] ** 2)))
    exercise = next((f["id"] for f in floors[1:]), floors[0]["id"])
    return {"schema": "sandtable.twin/1",
            "source": {"kind": "matterport-live", "synthetic": False, "placeholder": False,
                       "note": "Matterport Model API. Room polygons are bounding boxes of sweep positions; the walking graph links sweeps closer than "
                               f"{link_m:g} m; stairs come from room labels. Verify the georeference with one known point."},
            "model": {"id": details.get("id"), "name": details.get("name"), "address": (details.get("publication") or {}).get("address")},
            "georef": {"origin": {"lat": g.get("latitude"), "lon": g.get("longitude")}, "bearing_deg": round((90.0 - yaw) % 360.0, 2)},
            "_frame": {"dx": dx, "dy": dy, "up_axis": up_axis},
            "exercise_floor": exercise, "floors": floors, "rooms": rooms, "walls": [], "doors": [], "stairs": stairs,
            "sweeps": sweeps_out, "tags": out_tags, "graph": {"nodes": nodes, "edges": edges}}


def _suite_of(label: str) -> str | None:
    import re
    m = re.search(r"\b(?:suite|ste|unit|#)\s*([0-9]{2,4}[A-Z]?)\b", label or "", re.I)
    return m.group(1) if m else None
