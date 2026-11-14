"""The Beacons object (docs/BEACON_FEED.md section 2) as outside feeds: GeoJSON (RFC 7946) and Cursor-on-Target 2.0.

Pure functions (no server, no clock of their own), tested without one:

    beacons_to_geojson(beacons, twin=None) -> dict      FeatureCollection in WGS84, [lon, lat] order
    beacons_to_cot(beacons, now, twin=None) -> str      <events> wrapping one <event version="2.0"> per item
    cot_events(beacons, now, twin=None) -> [(uid, xml, key)]   the same events one by one (the SSE stream sends these;
                                                         `key` changes only when the item's content changes)

`twin` (a Twin or a twin dict) is only needed for the room polygons of presence entries and for turning ring arcs and
zone polygons into WGS84; it defaults to the project's twin (config.TWIN_JSON).

Honesty rules enforced here, whatever the input says:
  - a phone is a Point only when its geometry is "fix" and it has x, y. A ring is a MultiLineString of the arcs the twin
    leaves possible, candidates are one Point each, a zone is the room polygon. Presence is a room polygon, never a point.
  - every feature carries properties.simulated (and properties.sample when the object is the hand-made sample).
  - CoT: how="m-s" for simulated or sample items, "m-f" otherwise (as the feed document says); a ring is published at the
    puck's position with ce = r + 2 sigma, which honestly contains the whole ring.

Cursor-on-Target choices follow Sandtable's scout feed (sandtable/scout/feeds.py, copied with credit): the XML is built
with ElementTree (always well-formed, text escaped), times are ISO 8601 UTC with a Z, unknown hae/le use 9999999.0.
Types: puck a-f-G-E-S; a phone FIX a-u-G (callsign "B1 phone (trapped)"); everything that is not a position is a map
point b-m-p-s-m with a callsign that says so: a ring "B2 RANGE ONLY" at the puck (remarks "RANGE ONLY: within N m of puck
P1, no position"), candidates "B2 CANDIDATE 1 OF 2", a zone "B4 ROOM ONLY" at the room centre, a stale phone "B1 LAST
KNOWN" at the centre of the room it was last located in, presence at the room centre. Not tested against a TAK server.
"""
from __future__ import annotations

import json
import math
import time
import xml.etree.ElementTree as ET
from functools import lru_cache

UNKNOWN = "9999999.0"
STALE_S = 30.0                      # every CoT event goes stale 30 s after its time
ARC_STEP_DEG = 5.0


# ---------------------------------------------------------------------------- geometry helpers
@lru_cache(maxsize=1)
def _default_twin():
    from . import config
    from .twin import Twin
    return Twin(config.twin_raw())


def _twin(twin):
    if twin is None:
        return _default_twin()
    if isinstance(twin, dict):
        from .twin import Twin
        return Twin(twin)
    return twin


def _ll(twin, x: float, y: float) -> tuple[float, float]:
    lat, lon = twin.to_geo(float(x), float(y))
    return round(lat, 7), round(lon, 7)


def _lonlat(twin, x: float, y: float) -> list[float]:
    lat, lon = _ll(twin, x, y)
    return [lon, lat]


def _ring_lonlat(twin, poly) -> list[list[float]]:
    ring = [_lonlat(twin, p[0], p[1]) for p in poly]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    # RFC 7946: exterior rings counter-clockwise (in lon/lat)
    area = sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1))
    if area < 0:
        ring.reverse()
    return ring


def _room_polygon(twin, room_id: str | None):
    if not room_id:
        return None
    try:
        return twin.room(room_id)["polygon"]
    except ValueError:
        return None


def _centroid(poly) -> tuple[float, float]:
    return sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)


def _bounding_radius(poly) -> float:
    cx, cy = _centroid(poly)
    return max(math.hypot(p[0] - cx, p[1] - cy) for p in poly)


def _arcs(ring: dict) -> list[list[float]]:
    arcs = ring.get("arcs") or [[0.0, 360.0]]
    out = []
    for a0, a1 in arcs:
        a0, a1 = float(a0), float(a1)
        if a1 < a0:
            a1 += 360.0
        out.append([a0, a1])
    return out


def _arc_lines(twin, ring: dict) -> list[list[list[float]]]:
    cx, cy, r = float(ring["cx"]), float(ring["cy"]), float(ring["r_m"])
    lines = []
    for a0, a1 in _arcs(ring):
        n = max(1, int(math.ceil((a1 - a0) / ARC_STEP_DEG)))
        pts = []
        for i in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * i / n)
            pts.append(_lonlat(twin, cx + r * math.cos(a), cy + r * math.sin(a)))
        lines.append(pts)
    return lines


# ---------------------------------------------------------------------------- GeoJSON
def _report_props(p: dict) -> dict:
    r = p.get("report") or {}
    return {"status": r.get("status"), "people": r.get("people"), "can_move": r.get("can_move"),
            "battery_pct": r.get("battery_pct"), "still": r.get("still"), "safe_outside": r.get("safe_outside"),
            "opt_in": True}


def _last_known(p: dict) -> dict:
    """The room a stale phone was last placed in (from `last_known`; an older producer may only have `room`)."""
    lk = p.get("last_known")
    if isinstance(lk, dict) and lk.get("room"):
        return lk
    return {"geometry": p.get("geometry"), "room": p.get("room"), "room_label": p.get("room_label"),
            "age_s": (p.get("track") or {}).get("last_seen_s")}


def beacons_to_geojson(beacons: dict, twin=None) -> dict:
    tw = _twin(twin)
    b = beacons or {}
    sim = bool(b.get("simulated", True))
    sample = bool(b.get("sample"))
    features: list[dict] = []

    def feat(geom: dict, props: dict, item: dict) -> dict:
        base = {"simulated": bool(item.get("simulated", sim)) or sim, "floor": item.get("floor") or b.get("floor"),
                "source": b.get("source")}
        if sample or item.get("sample"):
            base["sample"] = True
        if item.get("injected"):
            base["injected"] = True
        base.update(props)
        return {"type": "Feature", "geometry": geom, "properties": base}

    for pk in b.get("pucks") or []:
        if pk.get("x") is None or pk.get("y") is None:
            continue
        features.append(feat({"type": "Point", "coordinates": _lonlat(tw, pk["x"], pk["y"])},
                             {"kind": "puck", "id": pk["id"], "radios": pk.get("radios") or [], "sigma_m": pk.get("sigma_m"),
                              "text": f"Puck {pk['id']}" + (f" in {pk['room']}" if pk.get("room") else "")}, pk))

    for p in b.get("phones") or []:
        geom = p.get("geometry")
        common = {"id": p["id"], "method": p.get("method"), "geometry": geom, "text": p.get("text") or "",
                  "stale": bool(p.get("stale")), **_report_props(p)}
        if p.get("stale"):
            # a phone not heard for a while is never drawn where it was: at most the room where it was last located
            lk = _last_known(p)
            poly = _room_polygon(tw, lk.get("room"))
            if poly and not (p.get("report") or {}).get("safe_outside"):
                features.append(feat({"type": "Polygon", "coordinates": [_ring_lonlat(tw, poly)]},
                                     {"kind": "phone-last-known", **common, "room": lk.get("room"),
                                      "last_known": {k: lk.get(k) for k in ("geometry", "room", "room_label", "age_s")}}, p))
            continue
        if geom == "fix" and p.get("x") is not None and p.get("y") is not None:
            features.append(feat({"type": "Point", "coordinates": _lonlat(tw, p["x"], p["y"])},
                                 {"kind": "phone", **common, "sigma_m": p.get("sigma_m"), "confidence": p.get("confidence"),
                                  "room": p.get("room")}, p))
        elif geom == "ring" and p.get("ring"):
            ring = p["ring"]
            features.append(feat({"type": "MultiLineString", "coordinates": _arc_lines(tw, ring)},
                                 {"kind": "phone-ring", **common, "puck": ring.get("puck"), "r_m": ring.get("r_m"),
                                  "sigma_m": ring.get("sigma_m"), "ring_kind": ring.get("kind")}, p))
        elif geom == "candidates" and p.get("candidates"):
            cands = p["candidates"]
            for i, c in enumerate(cands, 1):
                features.append(feat({"type": "Point", "coordinates": _lonlat(tw, c["x"], c["y"])},
                                     {"kind": "phone-candidate", **common, "id": p["id"], "candidate": i, "of": len(cands),
                                      "weight": c.get("weight"), "sigma_m": c.get("sigma_m"), "room": c.get("room")}, p))
        elif geom == "zone" and p.get("zone"):
            z = p["zone"]
            poly = z.get("polygon") or _room_polygon(tw, z.get("room"))
            if poly:
                features.append(feat({"type": "Polygon", "coordinates": [_ring_lonlat(tw, poly)]},
                                     {"kind": "phone-zone", **common, "band": z.get("band"), "prob": z.get("prob"),
                                      "room": z.get("room"), "puck": z.get("puck")}, p))

    for c in b.get("presence") or []:
        poly = _room_polygon(tw, c.get("room"))
        if not poly:
            continue
        features.append(feat({"type": "Polygon", "coordinates": [_ring_lonlat(tw, poly)]},
                             {"kind": "presence", "id": c["id"], "puck": c.get("puck"), "room": c.get("room"),
                              "count": c.get("count"), "count_range": c.get("count_range"), "zones": c.get("zones"),
                              "opt_in": False, "text": c.get("text") or ""}, c))

    out = {"type": "FeatureCollection", "schema": b.get("schema", "sandtable.beacons/1"), "simulated": sim,
           "source": b.get("source"), "t": b.get("t"), "features": features}
    if sample:
        out["sample"] = True
    return out


# ---------------------------------------------------------------------------- Cursor-on-Target
def _half_up(v: float) -> float:
    return max(0.5, math.ceil(float(v) * 2.0 - 1e-9) / 2.0)


def _iso(t: float) -> str:
    t = float(t)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def _event(uid: str, typ: str, how: str, lat: float, lon: float, ce, now: float, callsign: str, remarks: str) -> ET.Element:
    ev = ET.Element("event", {"version": "2.0", "uid": uid, "type": typ, "time": _iso(now), "start": _iso(now),
                              "stale": _iso(now + STALE_S), "how": how})
    ET.SubElement(ev, "point", {"lat": f"{lat:.7f}", "lon": f"{lon:.7f}", "hae": UNKNOWN,
                                "ce": f"{float(ce):.2f}" if ce is not None else UNKNOWN, "le": UNKNOWN})
    detail = ET.SubElement(ev, "detail")
    ET.SubElement(detail, "contact", {"callsign": callsign})
    ET.SubElement(detail, "remarks").text = remarks
    return ev


def _elements(beacons: dict, now: float, twin) -> list[tuple[str, ET.Element, str]]:
    tw = _twin(twin)
    b = beacons or {}
    sample = bool(b.get("sample"))
    sim = bool(b.get("simulated", True)) or sample
    how = "m-s" if sim else "m-f"
    tag = "SAMPLE (hand-made, not a result). " if sample else ("SIMULATED. " if sim else "")
    out: list[tuple[str, ET.Element, str]] = []

    def add(uid, typ, x, y, ce, callsign, remarks):
        lat, lon = _ll(tw, x, y)
        key = json.dumps([typ, round(lat, 6), round(lon, 6), None if ce is None else round(float(ce), 1), remarks])
        out.append((uid, _event(uid, typ, how, lat, lon, ce, now, callsign, remarks), key))

    for pk in b.get("pucks") or []:
        if pk.get("x") is None:
            continue
        add(f"sandtable.beacon.{pk['id']}", "a-f-G-E-S", pk["x"], pk["y"], pk.get("sigma_m"), f"Puck {pk['id']}",
            f"{tag}Beacon puck {pk['id']} ({', '.join(pk.get('radios') or [])})" + (f" in {pk['room']}" if pk.get("room") else "") + ".")

    for p in b.get("phones") or []:
        geom = p.get("geometry")
        text = p.get("text") or ""
        rep = p.get("report") or {}
        status = rep.get("status") or "unknown"
        cs = f"{p['id']} phone ({status})"
        uid = f"sandtable.beacon.{p['id']}"
        if p.get("stale"):
            lk = _last_known(p)
            poly = _room_polygon(tw, lk.get("room"))
            if poly and not rep.get("safe_outside"):
                cx, cy = _centroid(poly)
                age = (p.get("track") or {}).get("last_seen_s")
                heard = f"not heard for {int(age)} s" if age is not None else "not heard recently"
                room = lk.get("room_label") or lk.get("room")
                where = (f"last heard by puck {lk['puck']} in {room} (range only)" if lk.get("geometry") == "ring" and lk.get("puck")
                         else f"last located in {room}")
                add(uid, "b-m-p-s-m", cx, cy, _bounding_radius(poly), f"{p['id']} LAST KNOWN",
                    f"LAST KNOWN: {heard}; {where}. No current position: the point is the room's centre. {tag}{text}.")
            continue
        if geom == "fix" and p.get("x") is not None:
            sig = p.get("sigma_m")
            add(f"sandtable.beacon.{p['id']}", "a-u-G", p["x"], p["y"], 2 * float(sig) if sig is not None else None, cs,
                f"{tag}{text}. Method {p.get('method')}; ce is 2 sigma.")
        elif geom == "ring" and p.get("ring"):
            # A ring is not a position: a map-point marker at the PUCK, with a circular error that contains the whole ring.
            r = p["ring"]
            ce = float(r["r_m"]) + 2 * float(r.get("sigma_m") or 0.0)
            add(uid, "b-m-p-s-m", r["cx"], r["cy"], ce, f"{p['id']} RANGE ONLY",
                f"RANGE ONLY: within {_half_up(ce):g} m of puck {r.get('puck')}, no position. The point is the puck; the phone is "
                f"somewhere on a ring of radius {float(r['r_m']):.1f} m around it (ce = radius + 2 sigma). {tag}{text}.")
        elif geom == "candidates" and p.get("candidates"):
            cands = p["candidates"]
            n = len(cands)
            for i, c in enumerate(cands, 1):
                sig = c.get("sigma_m")
                add(f"{uid}.c{i}", "b-m-p-s-m", c["x"], c["y"], 2 * float(sig) if sig is not None else None,
                    f"{p['id']} CANDIDATE {i} OF {n}",
                    f"CANDIDATE {i} OF {n}: one of {n} possible spots (weight {float(c.get('weight') or 0):.2f}), not a position; "
                    f"the phone is at one of them, the board cannot choose. {tag}{text}.")
        elif geom == "zone" and p.get("zone"):
            z = p["zone"]
            poly = z.get("polygon") or _room_polygon(tw, z.get("room"))
            if poly:
                cx, cy = _centroid(poly)
                add(uid, "b-m-p-s-m", cx, cy, _bounding_radius(poly), f"{p['id']} ROOM ONLY",
                    f"ROOM ONLY: in {z.get('room_label') or z.get('room')} (probability {float(z.get('prob') or 0):.1f}, "
                    f"{z.get('band')} band of puck {z.get('puck')}), no position; the point is the room's centre. {tag}{text}.")

    for c in b.get("presence") or []:
        poly = _room_polygon(tw, c.get("room"))
        if not poly:
            continue
        cx, cy = _centroid(poly)
        lo, hi = (c.get("count_range") or [None, None])[:2]
        add(f"sandtable.beacon.{c['id']}", "b-m-p-s-m", cx, cy, _bounding_radius(poly),
            f"{c.get('room_label') or c.get('room')}: {c.get('count')} devices",
            f"{tag}Cold presence heard by puck {c.get('puck')}: about {c.get('count')} devices (band {lo} to {hi}). "
            f"Devices, not people; no identities kept.")
    return out


def cot_events(beacons: dict, now: float, twin=None) -> list[tuple[str, str, str]]:
    """Each CoT event as a one-line XML string, with its uid and a content key (no times in it)."""
    return [(uid, ET.tostring(ev, encoding="unicode"), key) for uid, ev, key in _elements(beacons, now, twin)]


def beacons_to_cot(beacons: dict, now: float, twin=None) -> str:
    root = ET.Element("events")
    root.append(ET.Comment(' Sandtable Beacon feed: Cursor-on-Target 2.0 events. how="m-s" marks simulated or sample positions. '
                           "A ring is published at its puck with ce = radius + 2 sigma. Well-formed; NOT tested against a TAK server. "))
    for _uid, ev, _key in _elements(beacons, now, twin):
        root.append(ev)
    ET.indent(root, space=" ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
