"""Scout session, feeds, incident hand-off, server endpoints and the scout exercise script (offline, fake hub, fast clock)."""
import asyncio
import json
import re
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from sandtable import config
from sandtable.adapters import build_provider
from sandtable.scout import feeds
from sandtable.scout.session import ScoutSession, fire_room
from sandtable.spatial import Twin

SCAN_KEYS = {"type", "schema", "id", "t", "floor", "room", "room_label", "simulated", "rung", "n_pucks", "pucks", "links", "registration",
             "victims", "echo_map", "outline", "mismatches", "resolution", "limits", "timing"}
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


def _engine_imports() -> bool:
    try:
        ScoutSession._import_engine()
        return True
    except Exception:
        return False


@pytest.fixture
def world():
    p = build_provider()
    return p, Twin(p.twin_raw())


def run_session(world, monkeypatch, engine="fake"):
    monkeypatch.setenv("SANDTABLE_SCOUT_ENGINE", engine)
    p, twin = world
    msgs = []
    s = ScoutSession(twin, p, msgs.append, None)
    s.pace = 0.02
    scan = asyncio.run(s.deploy())
    return s, scan, msgs


def walk_no_floats(o, where="brief"):
    if isinstance(o, dict):
        for v in o.values():
            walk_no_floats(v, where)
    elif isinstance(o, list):
        for v in o:
            walk_no_floats(v, where)
    else:
        assert not isinstance(o, float), f"float {o!r} in the {where}"


def test_fake_engine_runs_the_phases_and_marks_everything_as_sample(world, monkeypatch):
    s, scan, msgs = run_session(world, monkeypatch, "fake")
    scout = [m for m in msgs if m["type"] == "scout"]
    phases = [m["phase"] for m in scout]
    assert phases[:4] == ["thrown", "ranging", "located", "registered"] and phases[-1] == "done" and "scanning" in phases
    assert all(m["sample"] is True and m["text"] and "t" in m and m["room"] == scan["room"] for m in scout)
    assert msgs[-1] is scan and scan["type"] == "scan" and scan["sample"] is True and s.phase == "done" and s.engine == "fake"
    assert SCAN_KEYS <= set(scan) and scan["schema"] == "sandtable.scan/1"
    assert scan["room"] == fire_room(s.twin, s.provider)
    for p in scan["pucks"]:
        assert {"id", "x", "y", "sigma_m", "geo"} <= set(p) and set(p["geo"]) == {"lat", "lon"}
    v = scan["victims"][0]
    assert {"id", "x", "y", "sigma_m", "confidence", "state", "breathing_bpm", "text", "room", "node", "geo"} <= set(v)
    assert v["id"] == "V1" and v["room"] == scan["room"] and v["node"] and "wall" in v["text"]
    assert scan["outline"]["source"] in ("twin", "box-fit") and s.truth() is None


def test_brief_is_discrete_and_identical_across_runs(world, monkeypatch):
    s1, _, _ = run_session(world, monkeypatch, "fake")
    s2, _, _ = run_session(world, monkeypatch, "fake")
    b1, b2 = s1.brief(), s2.brief()
    walk_no_floats(b1)
    assert json.dumps(b1, sort_keys=True) == json.dumps(b2, sort_keys=True)
    v = b1["victims"][0]
    assert v["id"] == "V1" and isinstance(v["breathing_bpm"], int) and "m from the" in v["where"] and v["node"]
    assert not any(k in json.dumps(b1) for k in ('"t":', "timing"))


def test_sim_engine_scan_matches_the_contract_when_it_imports(world, monkeypatch):
    if not _engine_imports():
        pytest.skip("radar engine (scene/sim/dsp/register/mismatch) does not import yet")
    s, scan, msgs = run_session(world, monkeypatch, "sim")
    if s.engine != "sim":
        pytest.fail(f"the sim engine fell back to the sample: {s.fallback_reason}")
    assert SCAN_KEYS <= set(scan) and scan["simulated"] is True and not scan.get("sample") and scan["rung"] == "cir-radar"
    assert all(m["sample"] is False for m in msgs if m["type"] == "scout")
    assert scan["timing"]["compressed"] is True and s.truth() and "pucks" in s.truth()
    walk_no_floats(s.brief())
    s2, _, _ = run_session(world, monkeypatch, "sim")
    assert json.dumps(s.brief(), sort_keys=True) == json.dumps(s2.brief(), sort_keys=True)


def _units(twin):
    return [{"id": "E1", "label": "Engine 1", "kind": "engine", "physical": True, "status": "interior", "x": 12.0, "y": 7.2,
             "room": "rcW", "room_label": "West corridor", "sigma_m": 0.6, "assignment": {"task": "stage", "target": "n025", "target_label": "West corridor"}},
            {"id": "RIT", "label": "Rescue team", "kind": "rit", "physical": False, "status": "staged", "x": None, "y": None}]


def test_geojson_is_valid_wgs84_with_every_feature_labelled(world, monkeypatch):
    s, scan, _ = run_session(world, monkeypatch, "fake")
    fc = feeds.scan_to_geojson(scan, _units(s.twin), s.twin)
    assert fc["type"] == "FeatureCollection"
    kinds = [f["properties"]["kind"] for f in fc["features"]]
    assert {"puck", "victim", "mismatch", "room_outline", "crew"} <= set(kinds) and kinds.count("puck") == 5 and kinds.count("crew") == 1
    for f in fc["features"]:
        assert f["type"] == "Feature" and "simulated" in f["properties"] and f["properties"]["source"]
        g = f["geometry"]
        assert g["type"] in ("Point", "Polygon", "LineString")
    victim = next(f for f in fc["features"] if f["properties"]["kind"] == "victim")
    lat, lon = s.twin.to_geo(scan["victims"][0]["x"], scan["victims"][0]["y"])
    assert victim["geometry"]["coordinates"] == pytest.approx([lon, lat], abs=1e-6)              # [lon, lat] order
    assert -180 <= victim["geometry"]["coordinates"][0] <= 180 and -90 <= victim["geometry"]["coordinates"][1] <= 90
    pr = victim["properties"]
    assert pr["uncertainty_m"] == 0.4 and pr["breathing_bpm"] == 14 and pr["state"] == "breathing" and pr["confidence"] == 0.86 and pr["sample"] is True
    ring = next(f for f in fc["features"] if f["properties"]["kind"] == "room_outline")["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) >= 4
    json.dumps(fc)
    empty = feeds.scan_to_geojson(None, _units(s.twin), s.twin)
    assert [f["properties"]["kind"] for f in empty["features"]] == ["crew"]


def test_cot_is_well_formed_and_every_event_has_the_required_fields(world, monkeypatch):
    s, scan, _ = run_session(world, monkeypatch, "fake")
    xml = feeds.scan_to_cot(scan, _units(s.twin), s.twin, 1_800_000_000.25)
    root = ET.fromstring(xml.split("\n", 1)[1])
    assert root.tag == "events"
    events = root.findall("event")
    assert len(events) == 5 + 1 + 1 + 1 + 1                    # pucks, victim, mismatch, room, one placed crew
    types = {}
    for ev in events:
        for k in ("version", "uid", "type", "time", "start", "stale", "how"):
            assert ev.get(k), f"event lacks {k}"
        assert ev.get("version") == "2.0" and ev.get("how") == "m-s"
        assert all(ISO.match(ev.get(k)) for k in ("time", "start", "stale")) and ev.get("stale") > ev.get("time")
        pt = ev.find("point")
        assert pt is not None and all(pt.get(k) for k in ("lat", "lon", "hae", "ce", "le"))
        assert 34 < float(pt.get("lat")) < 35 and -119 < float(pt.get("lon")) < -118
        assert ev.find("detail/contact").get("callsign") and ev.find("detail/remarks").text
        types[ev.get("uid")] = ev
    v = types["sandtable.scan-sample.V1"]
    assert v.get("type") == "a-u-G" and float(v.find("point").get("ce")) == 0.4
    assert "14 per minute" in v.find("detail/remarks").text and "confidence 0.86" in v.find("detail/remarks").text
    assert types["sandtable.unit.E1"].get("type") == "a-f-G-U-C"
    real = feeds.cot_events(None, _units(s.twin), s.twin, time.time(), units_simulated=False)
    assert ET.fromstring(real[0][1]).get("how") == "m-R"


# ---------------------------------------------------------------------------- incident hand-off
def test_snapshot_without_a_scan_keeps_todays_keys_and_with_one_adds_a_discrete_scan(monkeypatch):
    from test_incident import Rig, start_operations
    r = Rig()
    r.step(1.0)
    start_operations(r)
    snap = r.inc.snapshot()
    assert list(snap) == ["phase", "units", "hazards", "mayday"]
    assert list(snap["units"][0]) == ["id", "label", "kind", "physical", "status", "floor", "room", "room_label", "suite", "node", "assignment", "route_valid"]
    sample = json.loads((config.FIXTURES / "scout" / "sample_scan.json").read_text())
    s = ScoutSession(r.twin, r.provider, r.msgs.append, r.inc)
    s.scan = s._decorate(sample)
    r.inc.set_scan(s.scan, s.brief())
    snap2 = r.inc.snapshot()
    assert list(snap2)[:4] == ["phase", "units", "hazards", "mayday"] and "scan" in snap2
    walk_no_floats(snap2, "snapshot")
    assert snap2["scan"]["victims"][0] == {"id": "V1", "room": "r310", "room_label": "Suite 310", "node": "n044", "state": "breathing", "status": "located", "rescuer": None}
    assert r.kinds()[-1] == "scan" and r.situations[-1]["data"] == s.brief()
    assert r.inc.state()["scout"]["victims"][0]["status"] == "located"
    r.inc.reset()
    assert r.inc.scan is None and r.inc.victims == {} and "scan" not in r.inc.snapshot()


def test_rescue_to_a_located_person_then_out_ends_through_all_out():
    from test_incident import Rig, start_operations
    r = Rig()
    r.step(1.0)
    start_operations(r)
    sample = json.loads((config.FIXTURES / "scout" / "sample_scan.json").read_text())
    s = ScoutSession(r.twin, r.provider, r.msgs.append, r.inc)
    s.scan = s._decorate(sample)
    r.inc.set_scan(s.scan, s.brief())
    out = r.inc.assign("RIT", "rescue", "V1")
    rit = r.inc.units["RIT"]
    assert out["ok"] and rit["assignment"]["target"] == "V1" and r.inc.phase == "rescue"
    assert rit["route"]["polyline"][-1] == pytest.approx([2.4, 2.1]) and r.inc.victims["V1"]["status"] == "assigned"
    r.step(40)
    assert r.inc.victims["V1"]["status"] == "reached" and r.kinds()[-1] == "victim_reached" and r.inc.phase == "egress"
    assert r.situations[-1]["data"]["victim"] == "V1" and r.situations[-1]["unit"] == "RIT"
    r.inc.assign("RIT", "exit", "exit")
    assert r.inc.victims["V1"]["status"] == "evacuating"
    r.step(40)
    assert r.inc.victims["V1"]["status"] == "out" and r.inc.phase == "terminated" and r.kinds()[-1] == "all_out"
    assert any("with V1" in e["text"] for e in r.inc.timeline if e["kind"] == "par")


# ---------------------------------------------------------------------------- server
def test_scout_endpoints_feeds_and_websocket(monkeypatch):
    monkeypatch.setenv("SANDTABLE_SCOUT_ENGINE", "fake")
    monkeypatch.setenv("SANDTABLE_SCOUT_PACE", "0.05")
    monkeypatch.setenv("SANDTABLE_BEACON", "sample")
    import sandtable.server as server
    with TestClient(server.app) as client:
        assert client.post("/api/scout/deploy", json={}).status_code == 409             # no incident yet
        assert client.get("/api/scout").json() == {"phase": "idle", "room": None, "engine": None, "scan": None}
        assert client.get("/feed/scan.geojson").json()["type"] == "FeatureCollection"
        inc = server.STATE.incident
        inc.start()
        inc.begin_operations()
        assert client.post("/api/scout/deploy", json={"room": "nowhere"}).status_code == 400
        assert client.post("/api/scout/deploy").status_code == 200                      # no body at all is fine
        assert client.post("/api/scout/deploy", json={}).status_code == 409             # one is running
        for _ in range(100):
            if client.get("/api/scout").json()["phase"] == "done":
                break
            time.sleep(0.05)
        st = client.get("/api/scout").json()
        assert st["phase"] == "done" and st["scan"]["type"] == "scan" and st["engine"] == "fake"
        assert client.get("/api/scout/truth").status_code == 404                        # the sample has no ground truth
        assert client.get("/api/state").json()["scout"]["phase"] == "done"
        gj = client.get("/feed/scan.geojson")
        assert gj.headers["content-type"].startswith("application/geo+json") and any(f["properties"]["kind"] == "victim" for f in gj.json()["features"])
        cot = client.get("/feed/cot.xml")
        assert cot.headers["content-type"].startswith("application/xml") and ET.fromstring(cot.text.split("\n", 1)[1]).findall("event")
        sse = client.get("/feed/cot/stream?once=1")
        assert sse.headers["content-type"].startswith("text/event-stream")
        datas = [ln[6:] for ln in sse.text.splitlines() if ln.startswith("data: ")]
        assert datas and all(ET.fromstring(d).tag == "event" for d in datas)
        with client.websocket_connect("/ws") as ws:
            types = [json.loads(ws.receive_text())["type"] for _ in range(3)]
            assert types[0] == "hello" and "scan" in types                              # the scan is replayed to a new client
        assert any(e["kind"] == "scan" for e in inc.timeline)
        client.post("/api/reset", json={})
        assert client.get("/api/scout").json()["scan"] is None and server.STATE.scan_msg is None and inc.scan is None
        assert client.post("/api/storyboard", json={"action": "start", "script": "nope"}).status_code == 400


# ---------------------------------------------------------------------------- the scout exercise script
def _run_scout_script(monkeypatch, engine, cache_dir):
    from test_server import FakeHub
    import sandtable.server as server
    monkeypatch.setenv("SANDTABLE_SCOUT_ENGINE", engine)
    monkeypatch.setenv("SANDTABLE_SCOUT_PACE", "0.1")
    monkeypatch.setenv("SANDTABLE_BEACON", "sample")                 # tests never talk to a Beacon server that may be running on 1885
    monkeypatch.setattr(config, "REPLAY_PACE_S", 0.0)
    monkeypatch.setattr(config, "STATE_HZ", 40.0)
    monkeypatch.setattr(config, "AGENT_FALLBACK_AFTER_S", 3.0)
    monkeypatch.setattr(config, "LLM_CACHE_DIR", cache_dir)

    async def scenario():
        s = server.AppState()
        s.agent.llm.cache_dir = cache_dir
        t0 = time.time()
        clock = lambda: t0 + (time.time() - t0) * 12.0
        s.incident.clock = clock
        s.hub = FakeHub(s.incident, clock)
        s.incident.teleport = s.hub.teleport
        events, q = [], asyncio.Queue(maxsize=100000)
        s.clients.add(q)
        await s.startup()
        try:
            await asyncio.sleep(0.3)
            s.director.start("auto", "scout")
            for _ in range(900):
                await asyncio.sleep(0.1)
                if s.director.step in ("done", "failed") and not s.director.running:
                    break
            while not q.empty():
                events.append(q.get_nowait())
            return s, events, s.director.step
        finally:
            await s.shutdown()
    return asyncio.run(scenario())


def test_scout_script_reaches_terminated_on_the_fake_engine(monkeypatch, tmp_path):
    s, events, step = _run_scout_script(monkeypatch, "fake", tmp_path)
    inc = s.incident
    assert step == "done" and inc.phase == "terminated", s.director.steps
    phases = [e["phase"] for e in events if e.get("type") == "scout"]
    assert phases[0] == "thrown" and phases[-1] == "done"
    assert any(e.get("type") == "scan" for e in events) and inc.victims["V1"]["status"] == "out"
    kinds = [e["kind"] for e in inc.timeline]
    assert "scan" in kinds and "victim" in kinds and "par" in kinds and s.aar is not None
    assert all(u["status"] == "out" for u in inc.units.values() if u["_was_interior"]) and inc.scout["smoke"] > 0.5
    assert s.director.script == "scout" and any(st.startswith("smoke") for st in s.director.steps)


def test_scout_script_on_the_sim_engine_when_it_imports(monkeypatch, tmp_path):
    if not _engine_imports():
        pytest.skip("radar engine does not import yet")
    s, events, step = _run_scout_script(monkeypatch, "sim", tmp_path)
    assert step == "done" and s.incident.phase == "terminated", s.director.steps
    assert s.scout.engine == "sim" and s.scout.truth() is not None


def test_crews_ordered_by_the_agent_hold_while_it_is_busy_but_the_director_does_not():
    """Replay determinism: a crew the agent ordered starts walking only when the agent's turn ends."""
    from test_incident import Rig, start_operations
    r = Rig()
    r.step(1.0)
    start_operations(r)
    sample = json.loads((config.FIXTURES / "scout" / "sample_scan.json").read_text())
    s = ScoutSession(r.twin, r.provider, r.msgs.append, r.inc)
    s.scan = s._decorate(sample)
    r.inc.set_scan(s.scan, s.brief())
    busy = [True]
    r.inc.agent_busy = lambda: busy[0]
    r.inc.assign("RIT", "rescue", "V1")                                  # the agent's order (source "agent")
    r.inc.assign("E1", "stage", "rcW", source="director")
    r.step(3)
    assert r.inc.units["RIT"]["_follow"]["s"] == 0.0 and r.inc.units["E1"]["_follow"]["s"] > 0
    busy[0] = False
    r.step(1)
    assert r.inc.units["RIT"]["_follow"]["s"] > 0
