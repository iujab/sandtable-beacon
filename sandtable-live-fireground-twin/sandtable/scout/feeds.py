"""Scout scan and crew positions as outside feeds: GeoJSON (RFC 7946) and Cursor-on-Target (CoT 2.0).

Pure functions (no server, no clock of their own), so they are tested without one:

    scan_to_geojson(scan, units, twin) -> dict          FeatureCollection in WGS84, [lon, lat] order
    scan_to_cot(scan, units, twin, now) -> str          <events> wrapping one <event version="2.0"> per item
    cot_events(scan, units, twin, now) -> [(uid, xml)]  the same events one by one (the SSE stream sends these)

Every position goes through the twin's georeference (`twin.to_geo`). Every GeoJSON feature carries
`properties.simulated` and `properties.source`; the scan's features also carry `sample` when they come
from the hand-made sample. Nothing here turns a simulated position into a measured one: `simulated`
follows the scan's own flag and, for crews, the hub's `hello.simulated`.

Cursor-on-Target: the XML is built with ElementTree (so it is always well-formed) and was checked for
well-formedness and against the attributes of the public CoT base event and point schemas as published
in the ATAK-CIV repository (takcot/mitre: "CoT Base-Event Schema (PUBLIC RELEASE).xsd", "CoT Spatial
Schema (PUBLIC RELEASE).xsd", types.txt): event version, uid, type, time, start, stale, how; point lat,
lon, hae, ce, le; detail is open content. It has NOT been tested against a TAK server or an ATAK/WinTAK client.

Choices, and where they come from (types.txt / the base schema; 2525B-style atom types "a-<affiliation>-<dimension>-<function>"):
  crews              a-f-G-U-C    friendly, ground, unit, combat: the type TAK clients use for their own team members
  located person     a-u-G        unknown affiliation, ground: the radar cannot tell who it is (a civilian, or a crew without a tag)
  pucks              a-f-G-E      friendly ground equipment (the sensor sub-function "S" was not confirmed in the public list, so it is left off)
  mismatch flags     b-m-p-s-p-i  map point of interest ("b-m-p-s-p-i" is listed in types.txt)
  room outline       b-m-p-s-p-i  a point of interest at the room centre; the polygon rides in <detail><shape><polyline> (the MITRE shape
                                  extension as I know it; not checked against its schema, a client that does not know it ignores it)
  how                m-s          machine, "simulated - out of a simulation" (base schema / types.txt): every simulated or sample position
                     m-R          machine, "radio positioning system (non GPS)" (types.txt): positions measured by real UWB boards.
                                  m-g would claim GPS, which UWB is not.
  point ce / le      one-sigma radius / height error in metres (spatial schema). Unknown values use 9999999.0, the
                     widespread TAK convention (not defined by the schema itself). hae is unknown indoors (9999999.0).
  phones (Beacon)    a fix: a-u-G at the fix; a ring: a-u-G AT THE PUCK with ce = radius + 2 sigma and "RING, range only" in the
                     remarks (never a made-up point); candidates: one a-u-G per candidate (uid ...B3.c1); a zone and cold presence:
                     b-m-p-s-p-i at the room centre with ce = the room's bounding radius. (The Beacon module's own feed uses
                     a-f-G-E-S, b-m-p-s-m and ce = 2 sigma for a fix; this feed keeps the conventions above: ce is 1 sigma.)
  time/start/stale   ISO 8601 UTC with a Z ("CCYY-MM-DDThh:mm:ss.ssZ"); crews go stale after 2 minutes, scan items after 10.
"""
from __future__ import annotations

import math
import time
import xml.etree.ElementTree as ET
from typing import Callable

UNKNOWN = "9999999.0"
CREW_STALE_S = 120.0
SCAN_STALE_S = 600.0


# ---------------------------------------------------------------------------- shared

def band_text(c: dict) -> str:
    """A cold presence band in words: the band only, never "about 0", always devices, not people."""
    lo, hi = (list(c.get("count_range") or []) + [None, None])[:2]
    if lo is None or hi is None:
        lo = hi = int(c.get("count") or 0)
    lo, hi = int(lo), int(hi)
    where = f" in {c['room_label']}" if c.get("room_label") else ""
    if hi <= 0:
        return f"No device heard{where}."
    if lo == hi:
        return f"{lo} device{'s' if lo != 1 else ''} heard{where}, not {'a person' if lo == 1 else 'people'}."
    return f"{lo} to {hi} devices heard{where}: devices, not people."

def _ll(twin, x: float, y: float) -> tuple[float, float]:
    lat, lon = twin.to_geo(float(x), float(y))
    return round(lat, 7), round(lon, 7)


def _scan_source(scan: dict) -> str:
    if scan.get("sample"):
        return "hand-made sample scan (not a result)"
    if scan.get("simulated"):
        return "sandtable scout radar on simulated UWB pucks"
    return "sandtable scout radar on UWB pucks"


def _unit_source(u: dict, units_simulated: bool) -> tuple[bool, str]:
    if not u.get("physical"):
        return True, "software unit (no tag; moved by the exercise)"
    return (True, "uwb-kit hub, simulated tags") if units_simulated else (False, "uwb-kit hub, UWB tag")


def _victims(scan: dict | None, live: dict | None) -> list[dict]:
    """The scan's located people, with the incident's live status and position (a carried person moves) when given."""
    out = []
    for v in (scan or {}).get("victims") or []:
        if v.get("x") is None:
            continue
        w = dict(v)
        lv = (live or {}).get(v["id"])
        if lv:
            w["x"], w["y"], w["status"], w["rescuer"] = lv["x"], lv["y"], lv["status"], lv["rescuer"]
        out.append(w)
    return out


def _closed(ring: list) -> list:
    return ring + [ring[0]] if ring and ring[0] != ring[-1] else ring


# ---------------------------------------------------------------------------- phones (Beacon module)
def _beacon_source(b: dict) -> tuple[bool, str]:
    if b.get("sample"):
        return True, "Beacon module SAMPLE (hand-made, not a result)"
    return (True, "Beacon module, simulated") if b.get("simulated", True) else (False, "Beacon module, pucks")


def _phone_props(p: dict, b: dict, phones: dict | None) -> dict:
    sim, src = _beacon_source(b)
    rep = p.get("report") or {}
    e = (phones or {}).get(p["id"]) or {}
    out = {"id": p["id"], "simulated": sim, "source": src, "floor": p.get("floor"), "text": p.get("text"), "method": p.get("method"),
           "geometry_kind": p.get("geometry"), "opt_in": True, "status": rep.get("status"), "people": rep.get("people"), "can_move": rep.get("can_move"),
           "battery_pct": rep.get("battery_pct"), "still": rep.get("still"), "confidence": p.get("confidence"),
           "target_status": e.get("status"), "rescuer": e.get("rescuer")}
    if b.get("sample"):
        out["sample"] = True
    return out


def _arc_points(twin, ring: dict) -> list[list[list[float]]]:
    lines = []
    for a0, a1 in ring.get("arcs") or [[0.0, 360.0]]:
        pts, a = [], float(a0)
        while a < float(a1) + 1e-6:
            x = ring["cx"] + ring["r_m"] * math.cos(math.radians(a))
            y = ring["cy"] + ring["r_m"] * math.sin(math.radians(a))
            pts.append([*reversed(_ll(twin, x, y))])
            a += 5.0
        if len(pts) >= 2:
            lines.append(pts)
    return lines


def _phone_features(b: dict | None, twin, phones: dict | None) -> list[dict]:
    """A phone is drawn as what the measurements support: a fix as a point, a ring as its arcs, candidates as candidates,
    a zone as the room. Never a made-up point."""
    if not b:
        return []
    feats = []
    sim, src = _beacon_source(b)
    for pk in b.get("pucks") or []:
        if pk.get("x") is None:
            continue
        lat, lon = _ll(twin, pk["x"], pk["y"])
        feats.append({"type": "Feature", "id": f"beacon-{pk['id']}", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"kind": "beacon-puck", "id": pk["id"], "radios": pk.get("radios"), "uncertainty_m": pk.get("sigma_m"),
                                     "simulated": sim, "source": src, "floor": pk.get("floor")}})
    for p in b.get("phones") or []:
        props, geom = _phone_props(p, b, phones), p.get("geometry")
        if geom == "fix" and p.get("x") is not None:
            lat, lon = _ll(twin, p["x"], p["y"])
            feats.append({"type": "Feature", "id": f"phone-{p['id']}", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": {**props, "kind": "phone", "uncertainty_m": p.get("sigma_m"), "room": p.get("room"), "room_label": p.get("room_label")}})
        elif geom == "ring" and p.get("ring"):
            rg = p["ring"]
            lines = _arc_points(twin, rg)
            if lines:
                feats.append({"type": "Feature", "id": f"phone-{p['id']}-ring", "geometry": {"type": "MultiLineString", "coordinates": lines},
                              "properties": {**props, "kind": "phone-ring", "puck": rg.get("puck"), "r_m": rg.get("r_m"), "uncertainty_m": rg.get("sigma_m"),
                                             "ring_kind": rg.get("kind")}})
        elif geom == "candidates":
            cs = sorted(p.get("candidates") or [], key=lambda c: -float(c.get("weight") or 0))
            for i, c in enumerate(cs):
                lat, lon = _ll(twin, c["x"], c["y"])
                feats.append({"type": "Feature", "id": f"phone-{p['id']}-c{i + 1}", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                              "properties": {**props, "kind": "phone-candidate", "candidate": i + 1, "of": len(cs), "weight": c.get("weight"),
                                             "uncertainty_m": c.get("sigma_m"), "room": c.get("room"), "room_label": c.get("room_label")}})
        elif geom == "zone" and (p.get("zone") or {}).get("polygon"):
            z = p["zone"]
            ring = _closed([[*reversed(_ll(twin, x, y))] for x, y in z["polygon"]])
            feats.append({"type": "Feature", "id": f"phone-{p['id']}-zone", "geometry": {"type": "Polygon", "coordinates": [ring]},
                          "properties": {**props, "kind": "phone-zone", "room": z.get("room"), "room_label": z.get("room_label"), "band": z.get("band"), "prob": z.get("prob")}})
    for c in b.get("presence") or []:
        try:
            poly = twin.room(c["room"])["polygon"]
        except (KeyError, ValueError):
            continue
        ring = _closed([[*reversed(_ll(twin, x, y))] for x, y in poly])
        feats.append({"type": "Feature", "id": f"presence-{c['id']}", "geometry": {"type": "Polygon", "coordinates": [ring]},
                      "properties": {"kind": "presence", "id": c["id"], "count": c.get("count"), "count_range": c.get("count_range"), "zones": c.get("zones"),
                                     "opt_in": False, "room": c.get("room"), "text": band_text(c), "simulated": sim, "source": src,
                                     "note": "devices, not people; no identities"}})
    return feats


# ---------------------------------------------------------------------------- GeoJSON
def scan_to_geojson(scan: dict | None, units: list[dict], twin, *, units_simulated: bool = True, victims: dict | None = None,
                    beacons: dict | None = None, phones: dict | None = None) -> dict:
    feats: list[dict] = _phone_features(beacons, twin, phones)
    if scan:
        src, sim, sample = _scan_source(scan), bool(scan.get("simulated")), bool(scan.get("sample"))
        base = {"simulated": sim, "source": src, "scan": scan.get("id"), "room": scan.get("room"), "room_label": scan.get("room_label")}
        if sample:
            base["sample"] = True
        outline = scan.get("outline") or {}
        if outline.get("polygon"):
            ring = _closed([[*reversed(_ll(twin, x, y))] for x, y in outline["polygon"]])
            feats.append({"type": "Feature", "id": f"{scan.get('id')}-room", "geometry": {"type": "Polygon", "coordinates": [ring]},
                          "properties": {**base, "kind": "room_outline", "outline_source": outline.get("source"), "confidence": outline.get("confidence"),
                                         "source": f"{src}; outline from the twin" if outline.get("source") == "twin" else f"{src}; outline fitted from wall echoes (coarse)"}})
        for p in scan.get("pucks") or []:
            if p.get("x") is None:
                continue
            lat, lon = _ll(twin, p["x"], p["y"])
            feats.append({"type": "Feature", "id": f"{scan.get('id')}-{p['id']}", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": {**base, "kind": "puck", "id": p["id"], "uncertainty_m": p.get("sigma_m")}})
        for v in _victims(scan, victims):
            lat, lon = _ll(twin, v["x"], v["y"])
            feats.append({"type": "Feature", "id": f"{scan.get('id')}-{v['id']}", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": {**base, "kind": "victim", "id": v["id"], "uncertainty_m": v.get("sigma_m"), "breathing_bpm": v.get("breathing_bpm"),
                                         "state": v.get("state"), "moving": bool(v.get("moving")), "confidence": v.get("confidence"),
                                         "status": v.get("status", "located"), "rescuer": v.get("rescuer"), "text": v.get("text"),
                                         "node": v.get("node")}})
        for m in scan.get("mismatches") or []:
            w = m.get("where") or {}
            if w.get("kind") == "point" and w.get("at"):
                lat, lon = _ll(twin, *w["at"][:2])
                geom = {"type": "Point", "coordinates": [lon, lat]}
            elif w.get("kind") == "segment" and w.get("a") and w.get("b"):
                geom = {"type": "LineString", "coordinates": [[*reversed(_ll(twin, *w["a"][:2]))], [*reversed(_ll(twin, *w["b"][:2]))]]}
            else:
                continue
            feats.append({"type": "Feature", "id": f"{scan.get('id')}-{m.get('id')}", "geometry": geom,
                          "properties": {**base, "kind": "mismatch", "id": m.get("id"), "mismatch": m.get("kind"), "confidence": m.get("confidence"),
                                         "radius_m": w.get("radius_m"), "text": m.get("text")}})
    for u in units:
        if u.get("x") is None:
            continue
        lat, lon = _ll(twin, u["x"], u["y"])
        sim, src = _unit_source(u, units_simulated)
        a = u.get("assignment") or {}
        feats.append({"type": "Feature", "id": f"unit-{u['id']}", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"kind": "crew", "id": u["id"], "label": u.get("label"), "unit_kind": u.get("kind"), "status": u.get("status"),
                                     "room": u.get("room"), "room_label": u.get("room_label"), "uncertainty_m": u.get("sigma_m"),
                                     "task": a.get("task"), "target": a.get("target"), "target_label": a.get("target_label"),
                                     "simulated": sim, "source": src}})
    return {"type": "FeatureCollection", "features": feats,
            "metadata": {"generator": "sandtable", "crs": "WGS84 (EPSG:4326), [lon, lat]", "scan": (scan or {}).get("id"),
                         "simulated": bool((scan or {}).get("simulated", True)), "sample": bool((scan or {}).get("sample")),
                         "beacon": None if not beacons else {"seq": beacons.get("seq"), "simulated": bool(beacons.get("simulated", True)), "sample": bool(beacons.get("sample"))}}}


# ---------------------------------------------------------------------------- Cursor-on-Target
def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def _event(uid: str, typ: str, how: str, lat: float, lon: float, ce, now: float, stale_s: float, callsign: str, remarks: str) -> ET.Element:
    ev = ET.Element("event", {"version": "2.0", "uid": uid, "type": typ, "time": _iso(now), "start": _iso(now), "stale": _iso(now + stale_s), "how": how})
    ET.SubElement(ev, "point", {"lat": f"{lat:.7f}", "lon": f"{lon:.7f}", "hae": UNKNOWN,
                                "ce": f"{float(ce):.2f}" if ce is not None else UNKNOWN, "le": UNKNOWN})
    detail = ET.SubElement(ev, "detail")
    ET.SubElement(detail, "contact", {"callsign": callsign})
    ET.SubElement(detail, "remarks").text = remarks
    return ev


def _phone_cot(b: dict | None, twin, now: float, phones: dict | None) -> list[tuple[str, ET.Element]]:
    """Phones as CoT: a fix at the fix; a ring as an event AT THE PUCK whose ce contains the ring (radius + 2 sigma), remarks "ring, range only";
    candidates one event each; a zone at the room centre. Never a made-up point."""
    if not b:
        return []
    out = []
    sim, _src = _beacon_source(b)
    how = "m-s" if sim else "m-R"
    tag = "SAMPLE (hand-made, not a result). " if b.get("sample") else "SIMULATED. " if sim else ""
    pucks = {pk["id"]: pk for pk in b.get("pucks") or []}
    for p in b.get("phones") or []:
        rep = p.get("report") or {}
        e = (phones or {}).get(p["id"]) or {}
        who = (f"{rep.get('people')} {'person' if rep.get('people') == 1 else 'people'}" if rep.get("people") else "occupant") + f", {rep.get('status') or 'unknown'}"
        tail = f" Report: {rep.get('text') or 'none'}. Battery {rep.get('battery_pct')}%." + (f" Crew {e['rescuer']}: {e['status']}." if e.get("rescuer") else "")
        uid, geom = f"sandtable.beacon.{p['id']}", p.get("geometry")
        if geom == "fix" and p.get("x") is not None:
            lat, lon = _ll(twin, p["x"], p["y"])
            out.append((uid, _event(uid, "a-u-G", how, lat, lon, p.get("sigma_m"), now, SCAN_STALE_S, f"Phone {p['id']}",
                                    f"{tag}Phone {p['id']} ({p.get('method')}): {who}. Fix in {p.get('room_label')}, uncertainty {p.get('sigma_m')} m (1 sigma).{tail}")))
        elif geom == "ring" and p.get("ring"):
            rg = p["ring"]
            ce = float(rg["r_m"]) + 2 * float(rg.get("sigma_m") or 0)
            lat, lon = _ll(twin, rg["cx"], rg["cy"])
            out.append((uid, _event(uid, "a-u-G", how, lat, lon, ce, now, SCAN_STALE_S, f"Phone {p['id']} (ring)",
                                    f"{tag}Phone {p['id']} ({p.get('method')}): {who}. RING, range only: about {float(rg['r_m']):.1f} m from puck {rg.get('puck')}; "
                                    f"the point is the puck and ce contains the ring, not a position.{tail}")))
        elif geom == "candidates":
            cs = sorted(p.get("candidates") or [], key=lambda c: -float(c.get("weight") or 0))
            for i, c in enumerate(cs):
                cu = f"{uid}.c{i + 1}"
                lat, lon = _ll(twin, c["x"], c["y"])
                out.append((cu, _event(cu, "a-u-G", how, lat, lon, c.get("sigma_m"), now, SCAN_STALE_S, f"Phone {p['id']} candidate {i + 1}",
                                       f"{tag}Phone {p['id']}: {who}. Candidate {i + 1} of {len(cs)} (weight {round(float(c.get('weight') or 0) * 100)}%), not a fix.{tail}")))
        elif geom == "zone" and p.get("zone"):
            z = p["zone"]
            try:
                poly = twin.room(z["room"])["polygon"]
            except (KeyError, ValueError):
                continue
            cx, cy = sum(q[0] for q in poly) / len(poly), sum(q[1] for q in poly) / len(poly)
            ce = max(((q[0] - cx) ** 2 + (q[1] - cy) ** 2) ** 0.5 for q in poly)
            lat, lon = _ll(twin, cx, cy)
            out.append((uid, _event(uid, "b-m-p-s-p-i", how, lat, lon, ce, now, SCAN_STALE_S, f"Phone {p['id']} (zone)",
                                    f"{tag}Phone {p['id']} ({p.get('method')}): {who}. Bluetooth ZONE only: probably in {z.get('room_label')} ({z.get('band')} range), no position.{tail}")))
    for c in b.get("presence") or []:
        try:
            poly = twin.room(c["room"])["polygon"]
        except (KeyError, ValueError):
            continue
        cx, cy = sum(q[0] for q in poly) / len(poly), sum(q[1] for q in poly) / len(poly)
        lat, lon = _ll(twin, cx, cy)
        uid = f"sandtable.beacon.{c['id']}"
        out.append((uid, _event(uid, "b-m-p-s-p-i", how, lat, lon, max(((q[0] - cx) ** 2 + (q[1] - cy) ** 2) ** 0.5 for q in poly), now, SCAN_STALE_S,
                                f"Devices {c.get('room_label')}", f"{tag}{band_text(c)}")))
    for pk in pucks.values():
        if pk.get("x") is None:
            continue
        lat, lon = _ll(twin, pk["x"], pk["y"])
        uid = f"sandtable.beacon.{pk['id']}"
        out.append((uid, _event(uid, "a-f-G-E", how, lat, lon, pk.get("sigma_m"), now, SCAN_STALE_S, f"Beacon puck {pk['id']}", f"{tag}Beacon puck {pk['id']}.")))
    return out


def _cot_elements(scan, units, twin, now, units_simulated, victims, beacons=None, phones=None) -> list[tuple[str, ET.Element]]:
    out: list[tuple[str, ET.Element]] = _phone_cot(beacons, twin, now, phones)
    if scan:
        sim = bool(scan.get("simulated")) or bool(scan.get("sample"))
        how = "m-s" if sim else "m-R"
        tag = "SAMPLE (hand-made, not a result). " if scan.get("sample") else "SIMULATED. " if scan.get("simulated") else ""
        sid = scan.get("id")
        for v in _victims(scan, victims):
            lat, lon = _ll(twin, v["x"], v["y"])
            bpm = v.get("breathing_bpm")
            rem = (f"{tag}Located by UWB radar in {v.get('room_label')}: {v.get('text') or v.get('state')}. "
                   f"Breathing {round(float(bpm)) if bpm else 'not seen'}" + (" per minute" if bpm else "") +
                   f"; confidence {float(v.get('confidence') or 0):.2f}; uncertainty {v.get('sigma_m')} m (1 sigma). Status: {v.get('status', 'located')}"
                   + (f", rescuer {v['rescuer']}" if v.get("rescuer") else "") + ".")
            uid = f"sandtable.{sid}.{v['id']}"
            out.append((uid, _event(uid, "a-u-G", how, lat, lon, v.get("sigma_m"), now, SCAN_STALE_S, f"{v['id']} located person", rem)))
        for p in scan.get("pucks") or []:
            if p.get("x") is None:
                continue
            lat, lon = _ll(twin, p["x"], p["y"])
            uid = f"sandtable.{sid}.{p['id']}"
            out.append((uid, _event(uid, "a-f-G-E", how, lat, lon, p.get("sigma_m"), now, SCAN_STALE_S, f"Puck {p['id']}",
                                    f"{tag}UWB scout puck in {scan.get('room_label')}, self-located and registered into the twin.")))
        for m in scan.get("mismatches") or []:
            w = m.get("where") or {}
            if w.get("kind") == "point" and w.get("at"):
                x, y, ce = w["at"][0], w["at"][1], w.get("radius_m")
            elif w.get("kind") == "segment" and w.get("a") and w.get("b"):
                x, y = (w["a"][0] + w["b"][0]) / 2, (w["a"][1] + w["b"][1]) / 2
                ce = ((w["a"][0] - w["b"][0]) ** 2 + (w["a"][1] - w["b"][1]) ** 2) ** 0.5 / 2
            else:
                continue
            lat, lon = _ll(twin, x, y)
            uid = f"sandtable.{sid}.{m.get('id')}"
            out.append((uid, _event(uid, "b-m-p-s-p-i", how, lat, lon, ce, now, SCAN_STALE_S, f"Twin mismatch {m.get('id')}",
                                    f"{tag}{m.get('kind')}: {m.get('text')} (confidence {float(m.get('confidence') or 0):.2f})")))
        outline = scan.get("outline") or {}
        if outline.get("polygon"):
            poly = outline["polygon"]
            cx, cy = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
            lat, lon = _ll(twin, cx, cy)
            uid = f"sandtable.{sid}.room"
            ev = _event(uid, "b-m-p-s-p-i", how, lat, lon, None, now, SCAN_STALE_S, f"{scan.get('room_label')} (scanned room)",
                        f"{tag}Room outline from the " + ("twin" if outline.get("source") == "twin" else "wall echoes (coarse box fit)") + ".")
            shape = ET.SubElement(ev.find("detail"), "shape")
            pl = ET.SubElement(shape, "polyline", {"closed": "true"})
            for x, y in poly:
                vlat, vlon = _ll(twin, x, y)
                ET.SubElement(pl, "vertex", {"lat": f"{vlat:.7f}", "lon": f"{vlon:.7f}", "hae": UNKNOWN})
            out.append((uid, ev))
    for u in units:
        if u.get("x") is None:
            continue
        lat, lon = _ll(twin, u["x"], u["y"])
        sim, src = _unit_source(u, units_simulated)
        a = u.get("assignment") or {}
        rem = (("SIMULATED. " if sim else "") + f"{u.get('label')}: {u.get('status')} in {u.get('room_label') or 'unknown room'}"
               + (f"; task {a.get('task')} to {a.get('target_label')}" if a.get("task") else "") + f". Source: {src}.")
        uid = f"sandtable.unit.{u['id']}"
        out.append((uid, _event(uid, "a-f-G-U-C", "m-s" if sim else "m-R", lat, lon, u.get("sigma_m"), now, CREW_STALE_S, u.get("label") or u["id"], rem)))
    return out


def cot_events(scan: dict | None, units: list[dict], twin, now: float, *, units_simulated: bool = True, victims: dict | None = None,
               beacons: dict | None = None, phones: dict | None = None, only: Callable[[str], bool] | None = None) -> list[tuple[str, str]]:
    """Each CoT event as a one-line XML string, with its uid. `only`: keep the events whose uid it accepts (the rest are not serialised)."""
    return [(uid, ET.tostring(ev, encoding="unicode")) for uid, ev in _cot_elements(scan, units, twin, now, units_simulated, victims, beacons, phones)
            if only is None or only(uid)]


def scan_to_cot(scan: dict | None, units: list[dict], twin, now: float, *, units_simulated: bool = True, victims: dict | None = None,
                beacons: dict | None = None, phones: dict | None = None) -> str:
    root = ET.Element("events")
    root.append(ET.Comment(" Sandtable feed (scout scan, phones, crews): Cursor-on-Target 2.0 events. how=\"m-s\" marks simulated positions. "
                           "Well-formed and checked against the public CoT base event schema fields; NOT tested against a TAK server. "))
    for _uid, ev in _cot_elements(scan, units, twin, now, units_simulated, victims, beacons, phones):
        root.append(ev)
    ET.indent(root, space=" ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
