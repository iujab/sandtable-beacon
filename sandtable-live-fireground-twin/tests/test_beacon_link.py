"""Beacon link (phones from the Beacon module, or its labelled sample): relay, incident targets, feeds, server endpoints, the beacon script."""
import asyncio
import json
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from sandtable.beacon_link import BeaconLink, load_sample
from sandtable.scout import feeds


def actionable(obj):
    """The sample's phones that are targets: opt-in, not safe outside, with a geometry or a trapped/injured report."""
    return {p["id"] for p in obj["phones"] if not (p.get("report") or {}).get("safe_outside")
            and (p["geometry"] in ("fix", "candidates", "ring", "zone") or (p.get("report") or {}).get("status") in ("trapped", "injured"))}


def test_sample_is_staged_and_labelled_and_auto_falls_back_then_switches_to_live():
    msgs = []
    link = BeaconLink(msgs.append, None, mode="sample")
    asyncio.run(link.poll_once())
    assert msgs[-1]["type"] == "beacons" and msgs[-1]["link"]["sample"] is True and msgs[-1]["phones"] == []      # no pucks in yet
    assert all(r["state"] == "unscanned" for r in msgs[-1]["accountability"]["suites"])

    async def go():
        await link.pucks_in({"n": 3})
    asyncio.run(go())
    last = msgs[-1]
    assert last["sample"] is True and len(last["phones"]) == len(load_sample()["phones"]) >= 4
    assert {"fix", "ring", "candidates", "zone"} <= {p["geometry"] for p in last["phones"]} <= {"fix", "ring", "candidates", "zone", "none"}
    for p in last["phones"]:                                         # the feed's physics rule: no x, y unless a fix
        assert (p["x"] is not None) == (p["geometry"] == "fix")
    n = len(msgs)
    asyncio.run(link.poll_once())
    assert len(msgs) == n                                            # unchanged: not re-emitted

    live = {**load_sample(), "sample": False, "seq": 77}
    live.pop("note", None)
    calls = []

    def fetch(method, url, body=None, timeout=1.5):
        calls.append(url)
        if fake_down:
            raise ConnectionRefusedError()
        return live if url.endswith("/api/beacons") else {"phones": []}
    fake_down = True
    out = []
    auto = BeaconLink(out.append, None, mode="auto", url="http://beacon.test", fetch=fetch)
    asyncio.run(auto.poll_once())
    assert auto.connected is False and auto.source == "sample" and out[-1]["link"]["connected"] is False and out[-1]["link"]["sample"] is True
    fake_down = False
    asyncio.run(auto.poll_once())
    assert auto.connected is True and auto.source == "live" and out[-1]["seq"] == 77 and out[-1]["link"]["sample"] is False
    assert auto.state_info()["phones"] == len(live["phones"]) and auto.state_info()["fixes"] == sum(p["geometry"] == "fix" for p in live["phones"])


def _rig_with_phones():
    from test_incident import Rig, start_operations
    r = Rig()
    r.step(1.0)
    start_operations(r)
    obj = load_sample()
    r.inc.on_beacons(obj)
    assert r.inc.phones == {} and "phones" not in r.inc.snapshot()   # pucks not in yet: phones are not targets
    r.inc.activate_beacon(True)
    r.inc.on_beacons(obj)
    return r, obj


def test_phones_become_targets_shaped_by_their_geometry():
    r, obj = _rig_with_phones()
    assert set(r.inc.phones) == actionable(obj) and {"B1", "B2", "B3", "B4"} <= set(r.inc.phones) and r.kinds() == ["phone"]   # one situation for the batch
    data = r.situations[-1]["data"]
    assert data["changed"] == sorted(actionable(obj)) and data["sample"] is True

    def walk(o):
        if isinstance(o, dict):
            [walk(v) for v in o.values()]
        elif isinstance(o, list):
            [walk(v) for v in o]
        else:
            assert not isinstance(o, float), o
    walk(data)
    walk(r.inc.snapshot()["phones"])
    det = {q["id"]: q["geometry_detail"] for q in data["phones"]}
    assert det["B1"]["kind"] == "fix" and det["B1"]["uncertainty"].startswith("about ")
    ring = next(d for d in det.values() if d["kind"] == "ring")
    assert ring["puck"] and isinstance(ring["radius_m"], int) and ring["rooms"]
    cand = next(d for d in det.values() if d["kind"] == "candidates")
    assert all(isinstance(c["weight_pct"], int) and c["room"] for c in cand["candidates"])
    assert data["accountability"] and all(isinstance((row["expected"] or {}).get("low", 0), int) for row in data["accountability"])
    assert r.inc.beacons is obj
    assert r.inc.phones["B1"]["match"] == "point" and r.inc.phones["B2"]["match"] == "room" and "search" in r.inc.phones["B2"]["label"]
    assert "most probable of 2 candidates" in r.inc.phones["B3"]["label"] and r.inc.phones["B4"]["room"] == "r310"
    r.inc.on_beacons(obj)
    assert r.kinds() == ["phone"]                                     # nothing changed: no new situation
    obj2 = json.loads(json.dumps(obj))
    obj2["phones"][1]["report"]["status"] = "ok" if obj2["phones"][1]["report"]["status"] != "ok" else "trapped"
    r.inc.on_beacons(obj2)
    assert r.kinds() == ["phone", "phone"] and r.situations[-1]["data"]["why"] == {"B2": "report"}


def test_rescue_of_a_phone_with_a_fix_then_out_ends_through_all_out():
    r, _ = _rig_with_phones()
    out = r.inc.assign("RIT", "rescue", "B1")
    assert out["ok"] and r.inc.units["RIT"]["assignment"]["target"] == "B1" and r.inc.phase == "rescue"
    assert r.inc.units["RIT"]["route"]["polyline"][-1] == pytest.approx([2.4, 2.1])
    r.step(40)
    assert r.inc.phones["B1"]["status"] == "reached" and r.kinds()[-1] == "phone_reached" and r.situations[-1]["data"]["people"] == 2
    r.inc.assign("RIT", "exit", "exit")
    r.step(40)
    assert r.inc.phones["B1"]["status"] == "out" and r.inc.phase != "terminated"          # other phones still inside: not over
    for pid in sorted(r.inc.phones):
        if pid != "B1":
            r.inc.hand_over(pid, "test")
    r.step(1)
    assert r.inc.phase == "terminated" and r.kinds()[-1] == "all_out"
    par = next(e["text"] for e in r.inc.timeline if e["kind"] == "par")
    assert "Handed over" in par and "B2" in par


def test_feeds_draw_phones_as_what_they_are():
    from sandtable.spatial import Twin
    from sandtable.adapters import build_provider
    twin = Twin(build_provider().twin_raw())
    obj = load_sample()
    fc = feeds.scan_to_geojson(None, [], twin, beacons=obj)
    kinds = [f["properties"]["kind"] for f in fc["features"]]
    by = {g: sum(p["geometry"] == g for p in obj["phones"]) for g in ("fix", "ring", "zone")}
    assert kinds.count("phone") == by["fix"] and kinds.count("phone-ring") == by["ring"] and kinds.count("phone-zone") == by["zone"]
    assert kinds.count("phone-candidate") == sum(len(p.get("candidates") or []) for p in obj["phones"] if p["geometry"] == "candidates") and "presence" in kinds
    assert not any(f["properties"]["id"] == p["id"] and f["properties"]["kind"] == "phone" for p in obj["phones"] if p["geometry"] != "fix" for f in fc["features"])
    ring = next(f for f in fc["features"] if f["properties"]["kind"] == "phone-ring")
    assert ring["geometry"]["type"] == "MultiLineString" and all(f["properties"]["simulated"] and f["properties"]["source"] for f in fc["features"])
    root = ET.fromstring(feeds.scan_to_cot(None, [], twin, time.time(), beacons=obj).split("\n", 1)[1])
    ev = {e.get("uid"): e for e in root.findall("event")}
    b2 = ev["sandtable.beacon.B2"]
    puck = next(p for p in obj["pucks"] if p["id"] == obj["phones"][1]["ring"]["puck"])
    lat, lon = twin.to_geo(puck["x"], puck["y"])
    assert float(b2.find("point").get("lat")) == pytest.approx(lat, abs=1e-6) and float(b2.find("point").get("ce")) > obj["phones"][1]["ring"]["r_m"]
    assert "RING" in b2.find("detail/remarks").text and "sandtable.beacon.B3.c2" in ev and ev["sandtable.beacon.B1"].get("how") == "m-s"

    some = feeds.cot_events(None, [], twin, time.time(), beacons=obj, only=lambda uid: uid.startswith("sandtable.beacon.B3."))
    every = dict(feeds.cot_events(None, [], twin, time.time(), beacons=obj))
    assert some and {u for u, _x in some} == {u for u in every if u.startswith("sandtable.beacon.B3.")}


def test_the_sample_before_pucks_is_made_once_and_the_sample_itself_is_untouched():
    link = BeaconLink(lambda m: None, None, mode="sample")
    before = json.dumps(link._sample, sort_keys=True)
    a = link._before_pucks(link._sample)
    assert a is link._before_pucks(link._sample) and a["phones"] == [] and a["pucks"] == []    # polled every 0.5 s: not re-copied each time
    assert json.dumps(link._sample, sort_keys=True) == before and link._sample["phones"]
    other = load_sample()
    assert link._before_pucks(other) is not a                                                  # a new source (a story) gets its own copy

def test_beacon_endpoints_accountability_and_report(monkeypatch):
    monkeypatch.setenv("SANDTABLE_BEACON", "sample")
    import sandtable.server as server
    with TestClient(server.app) as client:
        for _ in range(40):
            if client.get("/api/beacons").json():
                break
            time.sleep(0.05)
        b = client.get("/api/beacons").json()
        assert b["type"] == "beacons" and b["link"]["sample"] is True and b["phones"] == []
        assert client.post("/api/beacon/pucks", json={"n": 3}).status_code == 409          # no incident yet
        assert client.get("/api/state").json()["beacon"]["source"] == "sample"
        inc = server.STATE.incident
        inc.start()
        inc.begin_operations()
        out = client.post("/api/beacon/pucks", json={"n": 3}).json()
        assert out["ok"] and out["link"]["sample"] is True
        obj = client.get("/api/beacons").json()
        assert obj["phones"] and set(inc.phones) == actionable(obj)
        acc = client.get("/api/accountability").json()
        row = next(r for r in acc["rows"] if r["suite"] == "310")
        assert acc["schema"] == "sandtable.accountability/1" and row["state"] == "people-reported" and {"B1", "B3", "B4"} <= set(row["targets"])
        rep = client.get("/api/report").json()
        assert rep["schema"] == "sandtable.report/1" and {p["id"] for p in rep["people"]} >= {"B1", "B2"} and rep["provenance"]["beacon"]["sample"] is True
        md = client.get("/api/report.md")
        assert md.status_code == 200 and md.text.startswith("# Incident report") and "| B1 | phone (sample)" in md.text
        gj = client.get("/feed/scan.geojson").json()
        assert any(f["properties"]["kind"] == "phone-ring" for f in gj["features"])
        with client.websocket_connect("/ws") as ws:
            types = [json.loads(ws.receive_text())["type"] for _ in range(3)]
            assert "beacons" in types
        client.post("/api/reset", json={})
        assert inc.phones == {} and client.get("/api/beacons").json() is not None


def _beacon_run(monkeypatch, tmp_path, beacon_env="sample"):
    from test_scout_session import _run_scout_script
    import sandtable.server as server
    monkeypatch.setenv("SANDTABLE_BEACON", beacon_env)
    import sandtable.director as director
    real_start = director.Director.start
    monkeypatch.setattr(director.Director, "start", lambda self, mode="auto", script="mayday", beacon="story": real_start(self, mode, "beacon", beacon))
    seen = []
    real_on = server.AppState._on_situation
    monkeypatch.setattr(server.AppState, "_on_situation", lambda self, sit: (seen.append(json.loads(json.dumps(sit))), real_on(self, sit))[1])
    s, events, step = _run_scout_script(monkeypatch, "fake", tmp_path)
    return s, events, step, seen


def test_beacon_script_replays_the_recorded_two_puck_run_identically(monkeypatch, tmp_path):
    """The hands-free beacon exercise: two pucks placed by hand, phone 1 a fix (its mirror pruned outside the building), phone 2 two
    candidates; the agent's situations must be byte-identical across runs so recorded model turns replay."""
    runs, aar = [], []
    for i in range(2):                      # the second run has a slow agent (1 s inside every turn, like a live model): same situations
        with monkeypatch.context() as m:
            if i == 1:
                _slow_agent(m, 1.0)
            s, events, step, seen = _beacon_run(m, tmp_path / str(i))
            inc = s.incident
            assert step == "done" and inc.phase == "terminated", s.director.steps
            assert inc.phones["B1"]["status"] == "out" and inc.phones["B1"]["geometry"] == "fix"
            assert inc.phones["B2"]["status"] == "out" and inc.phones["B2"]["geometry"] == "fix" and inc.phones["B2"]["searched"][0]["by"] == "Engine 1"
            assert inc.phones["B1"]["rescuer"] == "RIT" and inc.phones["B2"]["rescuer"] == "E1"
            assert any(e["kind"] == "search" and "nobody there" in e["text"] for e in inc.timeline)
            b2 = next(p for e in events if e.get("type") == "beacons" for p in e.get("phones") or [] if p["id"] == "B2" and p["geometry"] == "fix")
            assert any(q["reason"] == "searched by Engine 1: nobody there" for q in b2["pruned"]) and b2["annotated"]
            rep = s.report()
            how = {p["id"]: p["how_found"] for p in rep["people"]}
            assert "outside the building" in how["B1"] and "searched by Engine 1" in how["B2"] and all(p["out_t_rel"] for p in rep["people"])
            relayed = [e for e in events if e.get("type") == "beacons" and e.get("phones")]
            assert relayed and all(e["link"]["source"] == "replay" and e["replay"] is True and e["sample"] is False for e in relayed)
            assert all(p.get("placed") == "by hand" and p.get("source") == "hand" for p in relayed[-1]["pucks"]) and len(relayed[-1]["pucks"]) == 2
            assert s.beacon.state_info()["connected"] is False and s.beacon.state_info()["source"] == "replay"
            b1 = next(p for p in relayed[0]["phones"] if p["id"] == "B1")
            assert b1["pruned"] and "outside the building" in b1["pruned"][0]["reason"]
            par = next(e["text"] for e in inc.timeline if e["kind"] == "par")
            assert par.startswith("All crews") and "3 of 3" in par and "ACCOUNTABILITY COMPLETE" not in par.upper()   # suites stay unsearched
            runs.append([x for x in seen if x["kind"] in ("phone", "phone_reached", "all_out")])
            aar.append(_aar_lines(inc))
    kinds = [x["kind"] for x in runs[0]]
    assert kinds.count("phone") >= 3 and kinds.count("phone_reached") == 2 and kinds[-1] == "all_out"
    assert any(x["kind"] == "phone" and x["data"]["why"] == {"B2": "fix"} for x in runs[0])
    assert len(runs[0]) == len(runs[1])
    for a, b in zip(runs[0], runs[1]):
        a, b = dict(a), dict(b)
        a.pop("seq"), b.pop("seq")
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert aar[0] == aar[1]


def test_an_assigned_rescue_follows_the_phone_when_its_geometry_improves():
    r, obj = _rig_with_phones()
    zone = json.loads(json.dumps(obj))
    b4 = next(p for p in zone["phones"] if p["geometry"] == "zone")
    r.inc.assign("RIT", "rescue", b4["id"])
    first = list(r.inc.units["RIT"]["route"]["polyline"][-1])
    assert r.inc.phones[b4["id"]]["match"] == "room" and r.inc.units["RIT"]["assignment"]["target"] == b4["id"]
    b4.update({"geometry": "fix", "x": 2.0, "y": 6.5, "sigma_m": 0.3, "room": "r310", "room_label": "Suite 310", "zone": None})
    n = len(r.situations)
    r.inc.on_beacons(zone)
    e = r.inc.phones[b4["id"]]
    assert e["match"] == "point" and r.inc.units["RIT"]["route"]["polyline"][-1] == pytest.approx([2.0, 6.5]) and first != [2.0, 6.5]
    assert r.situations[n]["kind"] == "phone" and r.situations[n]["data"]["why"] == {b4["id"]: "fix"}


def _aar_lines(inc):
    return [f"{e['kind']}: {e['text']}" for e in inc.timeline if e["kind"] not in ("radio", "unit", "system", "correction", "twin")]


def _slow_agent(monkeypatch, delay_s):
    """Test hook: every agent turn takes `delay_s` longer (inside the turn, while the agent is busy), like a live model."""
    import sandtable.agent as agent_mod
    real = agent_mod.CommandAgent.handle

    async def handle(self, situation):
        await asyncio.sleep(delay_s)
        return await real(self, situation)
    monkeypatch.setattr(agent_mod.CommandAgent, "handle", handle)


def test_the_fallback_policy_finishes_the_beacon_script_when_the_first_phone_has_two_candidates(monkeypatch, tmp_path):
    """No stuck exercise: empty recording cache (every agent turn is the labelled fallback policy) and the priority phone is the one
    with two candidates (the live-twin case that once hung at "reached: the agent plans the way out")."""
    import sandtable.beacon_link as bl
    story = json.loads(bl.STORY.read_text())
    swap = {"B1": "B2", "B2": "B1"}
    for f in story["frames"]:
        for p in f["beacons"].get("phones") or []:
            p["id"] = swap.get(p["id"], p["id"])
        f["beacons"]["phones"] = sorted(f["beacons"].get("phones") or [], key=lambda p: p["id"])
    path = tmp_path / "story_swapped.json"
    path.write_text(json.dumps(story))
    monkeypatch.setattr(bl, "STORY", path)
    s, events, step, seen = _beacon_run(monkeypatch, tmp_path / "cache")
    inc = s.incident
    assert step == "done" and inc.phase == "terminated", s.director.steps
    assert all(v["status"] == "out" for v in inc.phones.values())
    agent = [e for e in events if e.get("type") == "agent" and e["kind"] != "start"]
    assert agent and all(e["backend"] == "reflex-fallback" for e in agent)       # every turn labelled as the fallback policy
    assert any(e["kind"] == "fallback" for e in inc.timeline)
    assert any(e["kind"] == "search" and "phone B1" in e["text"] for e in inc.timeline)          # the priority phone needed the search


def test_pucks_in_forwards_how_each_puck_was_placed_and_its_accessory_name():
    """A puck snapped to the measured spacing must reach the Beacon module as placed_by "baseline", and the Bluetooth
    name a phone reports must reach it too; the relay used to keep only id, x, y and sigma_m."""
    sent = []

    def fetch(method, url, body=None, timeout=1.5):
        sent.append((method, url, body))
        if url.endswith("/api/beacons"):
            return {**load_sample(), "sample": False, "seq": 1}
        return {"ok": True}

    link = BeaconLink(lambda m: None, None, url="http://beacon.test", mode="auto", fetch=fetch)
    asyncio.run(link.pucks_in({"room": "r310", "pucks": [
        {"id": "P1", "x": 9.15, "y": 6.7, "placed_by": "hand", "accessory": "DWM3001CDK (AAAA0001)", "floor": "f3"},
        {"id": "P2", "x": 9.15, "y": 2.73, "placed_by": "baseline", "sigma_m": 0.04}]}))
    posts = [b for m, u, b in sent if m == "POST" and u.endswith("/api/pucks")]
    assert len(posts) == 1 and posts[0]["room"] == "r310"
    by_id = {p["id"]: p for p in posts[0]["pucks"]}
    assert by_id["P1"]["placed_by"] == "hand" and by_id["P1"]["accessory"] == "DWM3001CDK (AAAA0001)" and by_id["P1"]["floor"] == "f3"
    assert by_id["P2"]["placed_by"] == "baseline" and by_id["P2"]["sigma_m"] == 0.04 and "accessory" not in by_id["P2"]


def test_puck_restart_is_relayed_to_a_live_beacon_server_and_refused_otherwise(monkeypatch):
    """The board's Restart button: POST /api/beacon/puck_restart forwards to the Beacon server's /api/pucks/restart,
    passes its reason through, and refuses while no live Beacon server is connected (sample or a recorded run)."""
    import io
    import urllib.error
    monkeypatch.setenv("SANDTABLE_BEACON", "sample")
    import sandtable.server as server
    with TestClient(server.app) as client:
        assert client.post("/api/beacon/puck_restart", json={"puck": "P2"}).status_code == 409      # the sample: nothing to restart
        assert client.post("/api/beacon/puck_restart", json={}).status_code == 400
        link, sent = server.STATE.beacon, []

        def fetch(method, url, body=None, timeout=1.5):
            sent.append((method, url, body, timeout))
            if body and body.get("puck") == "P9":
                raise urllib.error.HTTPError(url, 409, "conflict", {}, io.BytesIO(b'{"detail": "P9 has no board name tied to it"}'))
            return {"ok": True, "puck": body["puck"], "dry_run": body["dry_run"], "address": "1BDB5E43", "jlink": "000760226574"}

        monkeypatch.setattr(link, "mode", "auto")
        monkeypatch.setattr(link, "connected", True)
        monkeypatch.setattr(link, "story", None)
        monkeypatch.setattr(link, "fetch", fetch)
        r = client.post("/api/beacon/puck_restart", json={"puck": "P2", "dry_run": True})
        assert r.status_code == 200 and r.json()["jlink"] == "000760226574"
        assert sent[-1][0] == "POST" and sent[-1][1].endswith("/api/pucks/restart") and sent[-1][2] == {"puck": "P2", "dry_run": True}
        assert sent[-1][3] >= 30                                                                   # a reset takes seconds
        bad = client.post("/api/beacon/puck_restart", json={"puck": "P9"})
        assert bad.status_code == 409 and "no board name" in bad.json()["detail"]
