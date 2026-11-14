"""The HTTP, WebSocket and SSE surface of docs/BEACON_FEED.md sections 3 and 4 (TestClient, offline)."""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET

import pytest

from beacon import config
from conftest import HAVE_LOCATOR, HAVE_SIM

needs_sim = pytest.mark.skipif(not (HAVE_LOCATOR and HAVE_SIM), reason="beacon.sim / beacon.locate not available")


def _wait(fn, timeout=6.0, every=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


# ---------------------------------------------------------------------------- the fake (sample) engine
def test_fake_engine_marks_everything_sample(make_client):
    c = make_client("fake")
    h = c.get("/healthz").json()
    assert h["ok"] is True and h["simulated"] is True and h["source"] == "fake" and h["sample"] is True
    for k in ("phones", "pucks"):
        assert isinstance(h[k], int)
    b = c.get("/api/beacons").json()
    assert b["type"] == "beacons" and b["sample"] is True and b["simulated"] is True
    assert b["phase"] in ("cold", "band", "ring", "candidates", "fix")
    for coll in ("pucks", "phones", "presence"):
        for item in b[coll]:
            assert item["sample"] is True and item["simulated"] is True
    assert any(x.startswith("SAMPLE") for x in b["limits"])
    fc = c.get("/feed/beacons.geojson").json()
    assert fc["sample"] is True and all(f["properties"]["sample"] for f in fc["features"])
    assert "SAMPLE" in c.get("/feed/cot.xml").text


def test_fake_engine_phases(sample):
    from beacon.server import FakeEngine
    from beacon.twin import Twin
    fe = FakeEngine(Twin(config.twin_raw()), sample)
    seen = []
    for k, ph in enumerate(FakeEngine.PHASES):
        b = fe.frame(fe.t0 + k * FakeEngine.PHASE_S + 0.5)
        assert b["phase"] == ph
        geoms = [p["geometry"] for p in b["phones"]]
        seen.append(geoms[0] if geoms else None)
        for p in b["phones"]:
            if p["geometry"] != "fix":
                assert p["x"] is None and p["y"] is None
    assert seen == [None, "zone", "ring", "candidates", "fix"]


def test_fake_engine_refuses_changes(make_client):
    c = make_client("fake")
    assert c.post("/api/sim/phone", json={"key": "k", "x": 1, "y": 1}).status_code == 409
    assert c.post("/api/sim/story", json={"action": "start"}).status_code == 409
    assert c.get("/api/sim/truth").status_code == 409
    assert c.post("/api/pucks", json={"n": 2}).status_code == 409
    assert c.post("/api/ingest", json={"kind": "cold", "puck": "P1"}).status_code == 409
    assert c.post("/api/reset").json() == {"ok": True}


def test_websocket_hello_then_beacons_and_pong(make_client):
    c = make_client("fake")
    with c.websocket_connect("/ws") as ws:
        h = ws.receive_json()
        assert h["type"] == "hello" and h["module"] == "sandtable-beacon" and h["v"] == 1
        for k in ("simulated", "source", "rungs", "labels", "noise", "rig"):
            assert k in h
        b = ws.receive_json()
        assert b["type"] == "beacons"
        ws.send_text("ping")
        for _ in range(60):
            m = ws.receive_json()
            if m.get("type") == "pong":
                break
        else:
            raise AssertionError("no pong")


def test_sse_events_once(make_client):
    c = make_client("fake")
    r = c.get("/events?once=1")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    msgs = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert [m["type"] for m in msgs[:2]] == ["hello", "beacons"]


def test_cot_stream_once(make_client):
    c = make_client("fake")
    r = c.get("/feed/cot/stream?once=1")
    assert r.status_code == 200
    frames = [blk for blk in r.text.split("\n\n") if blk.startswith("event: cot")]
    assert frames
    xml = "\n".join(line[6:] for line in frames[0].splitlines() if line.startswith("data: "))
    assert ET.fromstring(xml).tag == "event"


def test_bundle_and_static(make_client):
    c = make_client("fake")
    b = c.get("/api/bundle").json()
    assert set(b) >= {"twin", "site", "labels", "config"}
    assert b["labels"]["costar"]["real_sponsor_data"] is False
    assert "Not a Matterport scan" in b["labels"]["matterport"]["text"]
    assert c.get("/fixtures/beacon/sample_beacons.json").status_code == 200
    r = c.options("/api/beacons", headers={"Origin": "http://localhost:1810", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") in ("*", "http://localhost:1810")


def test_brief_accountability_directory_shapes(make_client):
    c = make_client("fake")
    br = c.get("/api/brief").json()
    assert br["schema"] == "sandtable.beacons.brief/1" and "phones" in br and "limits" in br
    acc = c.get("/api/accountability")
    if "accountability" in c.get("/api/beacons").json():
        assert acc.status_code == 200 and "suites" in acc.json()
    d = c.get("/api/directory")
    assert d.status_code == 200 and isinstance(d.json(), list)


def test_ingest_example(make_client):
    c = make_client("fake")
    ex = c.get("/api/ingest/example").json()
    assert ex == json.loads(config.INGEST_EXAMPLE_JSON.read_text(encoding="utf-8"))
    assert ex["device"]["eid"] and ex["obs"]


# ---------------------------------------------------------------------------- the simulator engine
@needs_sim
def test_sim_routes(make_client):
    c = make_client("sim")
    h = c.get("/healthz").json()
    assert h["source"] == "sim" and h["simulated"] is True and h["sample"] is False
    r = c.post("/api/pucks", json={"n": 3, "room": "r310", "seed": 310})
    assert r.status_code == 200 and len(r.json()["pucks"]) == 3
    assert c.post("/api/pucks", json={"n": 9}).status_code == 400
    assert c.post("/api/pucks", json={"n": 2, "room": "nowhere"}).status_code == 400
    r = c.post("/api/sim/phone", json={"key": "victim", "x": 2.4, "y": 2.1, "opt_in": True, "uwb": True,
                                        "report": {"status": "trapped", "people": 2, "can_move": False}})
    assert r.status_code == 200, r.text
    # three random pucks: a fix, or candidates when the throw leaves the mirror spot unexplained (the locator decides)
    got = _wait(lambda: (c.get("/api/sim/phone/victim").json().get("phone") or {}).get("geometry") in ("fix", "candidates"), 8.0)
    v = c.get("/api/sim/phone/victim").json()
    assert set(v) >= {"key", "truth", "phone", "ack"}
    assert v["truth"]["x"] == 2.4 and set(v["ack"]) >= {"heard_by", "geometry", "sigma_m", "text"}
    assert got, v
    assert v["ack"]["heard_by"] and v["ack"]["geometry"] == v["phone"]["geometry"]
    if v["phone"]["geometry"] != "fix":
        assert v["phone"]["x"] is None and v["ack"]["sigma_m"] is None
    assert c.get("/api/sim/phone/nobody").status_code == 404
    assert c.post("/api/sim/phone", json={"key": "", "x": 1, "y": 1}).status_code == 400
    assert c.post("/api/sim/phone", json={"key": "bad", "x": "a", "y": 1}).status_code == 400
    truth = c.get("/api/sim/truth").json()
    assert truth["simulated"] is True and truth["phones"]
    b = c.get("/api/beacons").json()
    pid = b["phones"][0]["id"]
    heat = c.get(f"/api/debug/heat/{pid}")
    assert heat.status_code in (200, 404)
    if heat.status_code == 200:
        assert {"origin", "cell_m", "nx", "ny", "values"} <= set(heat.json())
    assert c.get("/api/debug/heat/B99").status_code == 404
    for path in ("/api/brief", "/api/accountability", "/api/directory", "/feed/beacons.geojson", "/feed/cot.xml"):
        assert c.get(path).status_code == 200, path
    assert c.post("/api/sim/phone", json={"key": "victim", "remove": True}).json()["ok"] is True
    assert c.post("/api/reset").json()["ok"] is True
    b = _wait(lambda: (lambda b: b if not b["pucks"] else None)(c.get("/api/beacons").json()), 3.0)
    assert b and b["pucks"] == [] and b["phones"] == []


@needs_sim
def test_sim_scenario_and_story_start_stop(make_client):
    c = make_client("sim")
    r = c.post("/api/sim/scenario", json={"name": "suite-310"})
    assert r.status_code == 200 and r.json()["room"] == "r310"
    assert c.post("/api/sim/scenario", json={"name": "../../etc/passwd"}).status_code == 400
    b = _wait(lambda: (lambda b: b if b["phones"] else None)(c.get("/api/beacons").json()), 6.0)
    assert b and b["pucks"]
    for p in b["phones"]:
        if p["geometry"] != "fix":
            assert p["x"] is None and p["y"] is None
    r = c.post("/api/sim/story", json={"action": "start", "script": "suite-310"})
    assert r.status_code == 200 and r.json()["story"]["running"] is True
    st = _wait(lambda: (c.get("/api/sim/story").json()["story"] or {}).get("step", -1) >= 1, 6.0)
    assert st
    assert c.post("/api/sim/story", json={"action": "stop"}).json()["story"]["running"] is False
    assert c.post("/api/sim/story", json={"action": "start", "script": "no-such-scene"}).status_code == 400


@needs_sim
def test_sim_ingest_marks_injected(make_client):
    c = make_client("sim")
    c.post("/api/pucks", json={"pucks": [{"id": "P1", "x": 7.9, "y": 6.7}], "room": "r310"})
    t = time.time()
    r = c.post("/api/ingest", json=[{"kind": "uwb", "t": t, "puck": "P1", "eid": "0a0b0c0d", "d_m": 4.0},
                                    {"kind": "report", "t": t, "eid": "0a0b0c0d", "report": {"status": "trapped"}}])
    assert r.status_code == 200 and r.json()["accepted"] == 2
    ph = _wait(lambda: next((p for p in c.get("/api/beacons").json()["phones"] if p["eid"] == "0a0b0c0d"), None), 3.0)
    assert ph and ph["injected"] is True


# ---------------------------------------------------------------------------- live mode (iPhones through POST /api/ingest)
def test_live_mode_ingest_flow(make_client):
    c = make_client("live")
    h = c.get("/healthz").json()
    assert h["simulated"] is False and h["source"] == "live"
    assert c.post("/api/sim/phone", json={"key": "k", "x": 1, "y": 1}).status_code == 409
    assert c.post("/api/pucks", json={"n": 2}).status_code == 409
    with c.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["simulated"] is False and hello["rig"] == {"scale": 1.0}
    ex = c.get("/api/ingest/example").json()
    acc = ex["obs"][0]["accessory"]
    r = c.post("/api/ingest", json=ex)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["accepted"] == len(ex["obs"]) and body["unknown_accessories"] == [acc]
    assert set(body["ack"]) >= {"heard_by", "geometry", "sigma_m", "text"}
    b = _wait(lambda: (lambda b: b if b["unplaced_pucks"] else None)(c.get("/api/beacons").json()), 3.0)
    assert b["unplaced_pucks"][0]["accessory"] == acc and b["pucks"] == []
    r = c.post("/api/pucks", json={"pucks": [{"id": "P1", "x": 7.9, "y": 6.7, "accessory": acc}], "room": "r310"})
    assert r.status_code == 200
    r = c.post("/api/pucks", json={"pucks": [{"id": "P2", "x": 5.0, "y": 5.0, "accessory": "DWM3001CDK#9F8E"}], "merge": True})
    assert [p["id"] for p in r.json()["pucks"]] == ["P1", "P2"]
    for i in range(4):
        r = c.post("/api/ingest", json=ex)
        assert r.json()["unknown_accessories"] == []
    ph = _wait(lambda: next((p for p in c.get("/api/beacons").json()["phones"] if p["geometry"] != "none"), None), 4.0)
    assert ph and ph["simulated"] is False and ph["report"]["model"] == "iPhone 14"
    assert ph["geometry"] in ("ring", "zone", "fix", "candidates")
    if ph["geometry"] != "fix":
        assert ph["x"] is None
    root = ET.fromstring(c.get("/feed/cot.xml").text.split("\n", 1)[1])
    assert {ev.attrib["how"] for ev in root.findall("event")} == {"m-f"}
    fc = c.get("/feed/beacons.geojson").json()
    assert fc["simulated"] is False
    ack = c.post("/api/ingest", json=ex).json()["ack"]
    assert ack["heard_by"] == ["P1"]
    bye = c.post("/api/ingest", json={"device": {"eid": ex["device"]["eid"]}, "obs": [{"kind": "bye", "t": time.time()}]})
    assert bye.status_code == 200
    assert _wait(lambda: not c.get("/api/beacons").json()["phones"], 3.0)


def test_live_garbage_rejected_and_stream_survives(make_client):
    c = make_client("live")
    bad = [
        {"device": {"eid": "a3f19c02"}, "obs": [{"kind": "uwb", "accessory": "X#0001", "d_m": -2}]},
        {"device": {"eid": "a3f19c02"}, "obs": [{"kind": "uwb", "accessory": "X#0001", "d_m": 1e9}]},
        {"device": {"eid": "a3f19c02"}, "obs": [{"kind": "sos", "accessory": "X#0001", "rssi": 99}]},
        {"device": {"eid": "nothex!!"}, "obs": [{"kind": "uwb", "accessory": "X#0001", "d_m": 2}]},
        {"device": {"eid": "a3f19c02"}, "obs": "everything"},
        [{"kind": "uwb", "puck": "P1", "d_m": 2}],
        42,
    ]
    for body in bad:
        r = c.post("/api/ingest", json=body)
        assert r.status_code == 400, (body, r.text)
    r = c.post("/api/ingest", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert c.get("/healthz").json()["ok"] is True
    b1 = c.get("/api/beacons").json()["seq"]
    assert _wait(lambda: c.get("/api/beacons").json()["seq"] > b1, 3.0)


def test_live_rig_routes(make_client):
    c = make_client("live")
    assert c.get("/api/rig").json()["scale"] == 1.0
    r = c.post("/api/rig", json={"scale": 3.0})
    assert r.status_code == 200 and r.json()["scale"] == 3.0 and r.json()["applies"] is True
    assert c.post("/api/rig", json={"scale": -1}).status_code == 400
    assert c.post("/api/rig", json={"scale": "big"}).status_code == 400
    b = _wait(lambda: (lambda b: b if b["rig"]["scale"] == 3.0 else None)(c.get("/api/beacons").json()), 3.0)
    assert b and b["rig"] == {"scale": 3.0}
    assert config.PUCKS_LIVE_JSON.is_file()                                # persisted (tmp_path in tests)


def test_live_host_kinds(make_client):
    c = make_client("live")
    c.post("/api/pucks", json={"pucks": [{"id": "P1", "x": 7.9, "y": 6.7}], "room": "r310"})
    t = time.time()
    r = c.post("/api/ingest", json=[{"kind": "host", "t": t, "puck": "P1", "host": "uno-q", "uptime_s": 312},
                                    {"kind": "uwb_puck", "t": t, "puck": "P1", "d_m": 2.29},
                                    {"kind": "cold", "t": t, "puck": "P1", "window_s": 10.0, "near": 2, "mid": 1, "far": 4}])
    assert r.status_code == 200 and r.json()["accepted"] == 3
    b = _wait(lambda: (lambda b: b if b["presence"] else None)(c.get("/api/beacons").json()), 3.0)
    assert b["pucks"][0]["host"] == "uno-q" and b["presence"][0]["count"] == 3
    assert b["phones"] == []                                               # a puck-side range with no phone is ignored


def test_agent_routes_answer(make_client):
    c = make_client("fake")
    r = c.get("/api/agent")
    assert r.status_code in (200, 409, 503)
    if r.status_code == 200:
        assert "busy" in r.json()


# ---------------------------------------------------------------------------- two-puck additions (BEACON_FEED A8)
def test_search_room_routes(make_client):
    c = make_client("live")
    assert c.get("/api/search_room").json() == {"search_room": None}
    r = c.post("/api/search_room", json={"room": "r310"})
    assert r.status_code == 200 and r.json()["search_room"] == {"room": "r310", "room_label": "Suite 310"}
    assert c.post("/api/search_room", json={"room": "nowhere"}).status_code == 400
    b = _wait(lambda: (lambda b: b if b.get("search_room") else None)(c.get("/api/beacons").json()), 3.0)
    assert b["search_room"]["room"] == "r310"
    with c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["search_room"] == {"room": "r310", "room_label": "Suite 310"}
    assert c.post("/api/search_room", json={"room": None}).json()["search_room"] is None


def test_layout_route(make_client):
    pytest.importorskip("beacon.layout")
    c = make_client("fake")
    r = c.get("/api/layout?room=r310&n=2&strategy=door-wall")
    assert r.status_code == 200
    lay = r.json()
    assert len(lay["pucks"]) == 2 and {"wall", "why", "mirror_falls"} <= set(lay)
    assert c.get("/api/layout").status_code == 200                      # default room
    assert c.get("/api/layout?room=nowhere").status_code == 400
    assert c.get("/api/layout?strategy=wild").status_code == 400


@needs_sim
def test_two_pucks_placed_by_layout(make_client):
    pytest.importorskip("beacon.layout")
    c = make_client("sim")
    r = c.post("/api/pucks", json={"n": 2, "room": "r310"})
    assert r.status_code == 200 and r.json()["strategy"] == "door-wall" and len(r.json()["pucks"]) == 2
    lay = c.get("/api/layout?room=r310").json()
    for got, want in zip(r.json()["pucks"], lay["pucks"]):
        assert abs(got["x"] - want["x"]) < 0.5 and abs(got["y"] - want["y"]) < 0.5      # believed = true + position noise
    assert c.post("/api/pucks", json={"n": 3, "strategy": "door-wall"}).status_code == 400


def test_app_link(make_client):
    c = make_client("fake")
    j = c.get("/api/app_link?url=http://192.0.2.10:1885").json()
    assert j == {"link": "sandtablebeacon://server?url=http%3A%2F%2F192.0.2.10%3A1885", "server": "http://192.0.2.10:1885"}
    own = c.get("/api/app_link").json()
    assert own["server"].startswith("http://") and own["link"].startswith("sandtablebeacon://server?url=")
    svg = c.get("/api/app_link.svg?url=http://192.0.2.10:1885")
    if svg.status_code == 501:
        pytest.skip("segno not installed")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml") and "<svg" in svg.text
    assert c.get("/api/app_link?url=javascript:alert(1)").status_code == 400


# ---------------------------------------------------------------------------- round 2: judge findings
def _phone_ids(c):
    return [p["id"] for p in c.get("/api/beacons").json()["phones"]]


@needs_sim
def test_unticking_the_app_drops_the_phone_at_once(make_client):
    c = make_client("sim")
    c.post("/api/pucks", json={"n": 2, "room": "r310"})
    c.post("/api/sim/phone", json={"key": "tab", "x": 2.1, "y": 1.8, "opt_in": True, "uwb": True, "report": {"status": "trapped"}})
    assert _wait(lambda: _phone_ids(c), 6.0)
    r = c.post("/api/sim/phone", json={"key": "tab", "x": 2.1, "y": 1.8, "opt_in": False})
    assert r.status_code == 200
    assert _wait(lambda: not _phone_ids(c), 1.5), "the phone must leave the board as soon as the app is switched off"
    truth = c.get("/api/sim/truth").json()
    dev = next(p for p in truth["phones"] if p["key"] == "tab")
    assert dev["opt_in"] is False                                       # still a device in the simulator (cold count)
    c.post("/api/sim/phone", json={"key": "tab2", "x": 3.0, "y": 5.0, "opt_in": True, "uwb": True})
    assert _wait(lambda: _phone_ids(c), 6.0)
    assert c.post("/api/sim/phone", json={"key": "tab2", "remove": True}).json()["ok"] is True
    assert _wait(lambda: not _phone_ids(c), 1.5)


@needs_sim
def test_page_phones_expire_scenario_phones_do_not(make_client, monkeypatch):
    from beacon import server
    monkeypatch.setattr(server, "SIM_PHONE_TTL_S", 1.5)
    c = make_client("sim")
    assert c.post("/api/sim/scenario", json={"name": "suite-310"}).status_code == 200
    scene = {p["key"] for p in c.get("/api/sim/truth").json()["phones"]}
    c.post("/api/sim/phone", json={"key": "closed-tab", "x": 3.0, "y": 5.0, "opt_in": True, "uwb": True})
    c.post("/api/sim/phone", json={"key": "open-tab", "x": 4.0, "y": 3.0, "opt_in": True, "uwb": True})
    t0 = time.time()
    while time.time() - t0 < 4.0:                                       # only one page keeps polling
        assert c.get("/api/sim/phone/open-tab").status_code == 200
        time.sleep(0.3)
    keys = {p["key"] for p in c.get("/api/sim/truth").json()["phones"]}
    assert "closed-tab" not in keys and "open-tab" in keys and scene <= keys
    assert c.get("/api/sim/phone/closed-tab").status_code == 404


@needs_sim
def test_agent_summary_marks_old_orders(make_client):
    from beacon import server
    c = make_client("sim")
    st = server.S()
    if st.session.agent_status is None:
        pytest.skip("no command agent wired")

    class _Agent:
        busy, backend, last = False, "replay", {"summary": "RIT to Suite 310"}
    st.agent = _Agent()
    c.post("/api/pucks", json={"n": 2, "room": "r310"})
    b = _wait(lambda: (lambda b: b if b.get("agent") else None)(c.get("/api/beacons").json()), 3.0)
    assert b["agent"]["summary"] == "RIT to Suite 310" and b["agent"]["as_of_t"] and b["agent"]["stale"] is False
    c.post("/api/pucks", json={"n": 3, "room": "r310", "seed": 1})        # the puck set changed since the plan
    assert _wait(lambda: c.get("/api/beacons").json()["agent"]["stale"] is True, 3.0)


@needs_sim
def test_hello_carries_sim_model_and_story_length(make_client):
    c = make_client("sim")
    with c.websocket_connect("/ws") as ws:
        h = ws.receive_json()
    assert isinstance(h.get("sim_model"), dict)
    st = c.post("/api/sim/story", json={"action": "start"}).json()["story"]
    assert st["expected_s"] > 0 and st["expected_basis"]
    c.post("/api/sim/story", json={"action": "stop"})


def test_report_in_fake_mode_is_the_sample(make_client):
    c = make_client("fake")
    r = c.get("/report.html")
    if r.status_code == 404:
        pytest.skip("report routes not wired")
    assert r.status_code == 200 and "SAMPLE" in r.text
