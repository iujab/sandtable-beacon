"""feeds.py: GeoJSON and Cursor-on-Target are valid and honest (pure functions, no server)."""
from __future__ import annotations

import copy
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from beacon import feeds

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _ring_only(sample: dict) -> dict:
    b = copy.deepcopy(sample)
    b["phones"] = [copy.deepcopy(p) for p in b["phones"] if p["geometry"] == "ring"]
    assert b["phones"], "the sample must carry a ring phone"
    return b


def test_geojson_structure(sample):
    fc = feeds.beacons_to_geojson(sample)
    assert fc["type"] == "FeatureCollection"
    assert fc["schema"] == "sandtable.beacons/1"
    assert fc["simulated"] is True
    kinds = {f["properties"]["kind"] for f in fc["features"]}
    assert {"puck", "phone", "phone-ring", "phone-candidate", "phone-zone", "presence"} <= kinds
    for f in fc["features"]:
        assert f["type"] == "Feature"
        p = f["properties"]
        for k in ("kind", "id", "simulated", "floor", "text"):
            assert k in p, (k, p)
        assert p["simulated"] is True
        g = f["geometry"]
        if g["type"] == "Point":
            lon, lat = g["coordinates"]
            assert -119 < lon < -118 and 34 < lat < 34.5
        elif g["type"] == "Polygon":
            ring = g["coordinates"][0]
            assert ring[0] == ring[-1] and len(ring) >= 4
        elif g["type"] == "MultiLineString":
            assert all(len(line) >= 2 for line in g["coordinates"])
        else:
            raise AssertionError(g["type"])


def test_geojson_kinds_match_geometry(sample):
    fc = feeds.beacons_to_geojson(sample)
    by_kind = {}
    for f in fc["features"]:
        by_kind.setdefault(f["properties"]["kind"], []).append(f)
    assert all(f["geometry"]["type"] == "Point" for f in by_kind["phone"])
    assert all(f["geometry"]["type"] == "MultiLineString" for f in by_kind["phone-ring"])
    assert all(f["geometry"]["type"] == "Polygon" for f in by_kind["phone-zone"] + by_kind["presence"])
    cands = by_kind["phone-candidate"]
    assert [f["properties"]["candidate"] for f in cands] == list(range(1, len(cands) + 1))
    assert all(f["properties"]["of"] == len(cands) for f in cands)
    for f in by_kind["presence"]:
        assert f["properties"]["opt_in"] is False
        assert "count_range" in f["properties"]
    # only fixes are Points among phones
    fix_ids = {p["id"] for p in sample["phones"] if p["geometry"] == "fix"}
    assert {f["properties"]["id"] for f in by_kind["phone"]} == fix_ids


def test_ring_phone_never_a_point(sample):
    b = _ring_only(sample)
    b["phones"][0]["x"], b["phones"][0]["y"] = 3.0, 3.0          # a buggy upstream: x/y on a ring must still not become a Point
    fc = feeds.beacons_to_geojson(b)
    phones = [f for f in fc["features"] if f["properties"]["kind"].startswith("phone")]
    assert phones and all(f["geometry"]["type"] != "Point" for f in phones)
    assert phones[0]["properties"]["kind"] == "phone-ring"
    # the arcs are sampled in 5-degree steps between the published arc ends
    arcs = b["phones"][0]["ring"]["arcs"]
    total = sum(int(round((a1 - a0) / 5.0)) + 1 for a0, a1 in arcs)
    assert sum(len(line) for line in phones[0]["geometry"]["coordinates"]) >= total - len(arcs)


def test_sample_marked_in_feeds(sample):
    fc = feeds.beacons_to_geojson(sample)
    assert fc.get("sample") is True
    assert all(f["properties"].get("sample") for f in fc["features"])


def test_cot_parses_and_times(sample):
    now = 1794679200.25
    xml = feeds.beacons_to_cot(sample, now)
    root = ET.fromstring(xml.split("\n", 1)[1])
    assert root.tag == "events"
    evs = root.findall("event")
    assert evs
    for ev in evs:
        a = ev.attrib
        assert a["version"] == "2.0"
        assert a["uid"].startswith("sandtable.beacon.")
        for k in ("time", "start", "stale"):
            assert ISO.match(a[k]), a[k]
        t = datetime.strptime(a["time"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
        st = datetime.strptime(a["stale"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
        assert abs((st - t) - 30.0) < 0.01
        assert a["how"] == "m-s"
        pt = ev.find("point")
        for k in ("lat", "lon", "hae", "ce", "le"):
            assert k in pt.attrib
        assert ev.find("detail/contact").attrib["callsign"]
        assert ev.find("detail/remarks").text


def test_cot_items(sample):
    now = time.time()
    evs = {ev.attrib["uid"]: ev for ev in ET.fromstring(feeds.beacons_to_cot(sample, now).split("\n", 1)[1]).findall("event")}
    pucks = {p["id"]: p for p in sample["pucks"]}
    for p in sample["phones"]:
        uid = f"sandtable.beacon.{p['id']}"
        if p["geometry"] == "fix":
            assert evs[uid].attrib["type"] == "a-u-G"
            assert abs(float(evs[uid].find("point").attrib["ce"]) - 2 * p["sigma_m"]) < 0.01
        elif p["geometry"] == "ring":
            ev = evs[uid]
            r = p["ring"]
            pk = pucks[r["puck"]]
            assert abs(float(ev.find("point").attrib["ce"]) - (r["r_m"] + 2 * r["sigma_m"])) < 0.01
            # the point is the puck's position, not a phone position, and nothing about it says "a person here"
            assert abs(float(ev.find("point").attrib["lat"]) - pk["geo"]["lat"]) < 1e-6
            assert ev.attrib["type"] == "b-m-p-s-m"
            assert ev.find("detail/contact").attrib["callsign"] == f"{p['id']} RANGE ONLY"
            assert ev.find("detail/remarks").text.startswith("RANGE ONLY: within ")
            assert f"of puck {r['puck']}, no position" in ev.find("detail/remarks").text
        elif p["geometry"] == "candidates":
            n = len(p["candidates"])
            for i in range(1, n + 1):
                ev = evs[f"{uid}.c{i}"]
                assert ev.attrib["type"] == "b-m-p-s-m"
                assert ev.find("detail/contact").attrib["callsign"] == f"{p['id']} CANDIDATE {i} OF {n}"
                assert ev.find("detail/remarks").text.startswith(f"CANDIDATE {i} OF {n}: ")
            assert uid not in evs
        elif p["geometry"] == "zone":
            assert evs[uid].attrib["type"] == "b-m-p-s-m"
            assert evs[uid].find("detail/contact").attrib["callsign"] == f"{p['id']} ROOM ONLY"
            assert evs[uid].find("detail/remarks").text.startswith("ROOM ONLY: ")
        else:
            assert uid not in evs
    for pk in sample["pucks"]:
        assert evs[f"sandtable.beacon.{pk['id']}"].attrib["type"] == "a-f-G-E-S"
    for c in sample["presence"]:
        assert evs[f"sandtable.beacon.{c['id']}"].attrib["type"] == "b-m-p-s-m"


def test_cot_escapes_text_and_marks_real(sample):
    b = _ring_only(sample)
    b["simulated"] = False
    b.pop("sample", None)
    b["phones"][0]["text"] = 'Phone <B2> & "friends" </remarks><evil/>'
    xml = feeds.beacons_to_cot(b, time.time())
    root = ET.fromstring(xml.split("\n", 1)[1])
    ev = next(e for e in root.findall("event") if e.attrib["uid"].endswith(b["phones"][0]["id"]))
    assert "<evil/>" in ev.find("detail/remarks").text
    assert ev.attrib["how"] == "m-f"


def test_cot_event_keys_stable(sample):
    a = feeds.cot_events(sample, 1000.0)
    b = feeds.cot_events(sample, 2000.0)
    assert [(u, k) for u, _x, k in a] == [(u, k) for u, _x, k in b]
    assert a[0][1] != b[0][1]                       # the XML carries the time; the key does not


def test_empty_beacons():
    fc = feeds.beacons_to_geojson({"simulated": True, "pucks": [], "phones": [], "presence": []})
    assert fc["features"] == []
    xml = feeds.beacons_to_cot({"simulated": True}, time.time())
    assert ET.fromstring(xml.split("\n", 1)[1]).tag == "events"


def test_stale_phone_is_never_a_dot(sample):
    """Even if an upstream marked a stale phone `fix` with coordinates, the feeds publish only its last room."""
    b = copy.deepcopy(sample)
    fixp = next(p for p in b["phones"] if p["geometry"] == "fix")
    fixp["stale"] = True
    fixp["sigma_m"] = 45.0
    fixp["track"]["last_seen_s"] = 41.0
    fixp["last_known"] = {"geometry": "fix", "room": fixp["room"], "room_label": fixp["room_label"], "age_s": 41.0}
    fc = feeds.beacons_to_geojson(b)
    mine = [f for f in fc["features"] if f["properties"]["id"] == fixp["id"]]
    assert [f["properties"]["kind"] for f in mine] == ["phone-last-known"]
    assert mine[0]["geometry"]["type"] == "Polygon" and mine[0]["properties"]["stale"] is True
    evs = {e.attrib["uid"]: e for e in ET.fromstring(feeds.beacons_to_cot(b, time.time()).split("\n", 1)[1]).findall("event")}
    ev = evs[f"sandtable.beacon.{fixp['id']}"]
    assert ev.attrib["type"] == "b-m-p-s-m"
    assert ev.find("detail/contact").attrib["callsign"] == f"{fixp['id']} LAST KNOWN"
    rem = ev.find("detail/remarks").text
    assert rem.startswith("LAST KNOWN: not heard for 41 s; last located in Suite 310")
    assert "45" not in ev.find("point").attrib["ce"]
