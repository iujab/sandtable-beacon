"""Server: REST and WebSocket shapes, and the whole storyboard in replay mode against a fake hub with a fast clock."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from sandtable import config


def test_rest_and_websocket_shapes():
    import sandtable.server as server
    with TestClient(server.app) as client:
        assert client.get("/healthz").json()["ok"] is True
        b = client.get("/api/bundle").json()
        assert set(b) >= {"site", "twin", "aerial", "labels", "config"} and b["twin"]["schema"] == "sandtable.twin/1"
        st = client.get("/api/state").json()
        assert st["type"] == "state" and st["phase"] == "standby" and {"units", "hazards", "mayday", "anchors", "agent_busy", "storyboard"} <= set(st)
        assert client.get("/api/preplan").json() is None and client.get("/api/aar").json() is None
        assert client.get("/").status_code == 200 and "Sandtable" in client.get("/").text
        assert client.get("/fixtures/aerial/ortho.png").status_code == 200
        assert client.get("/crew.html").status_code == 200
        assert client.post("/api/inject", json={"ref": "nowhere"}).status_code == 400
        assert client.post("/api/mayday", json={"unit": "E1"}).status_code == 409           # no incident running
        assert client.post("/api/registration", json={"auto": True}).status_code == 400     # no anchors known without a hub
        corridor = next(r["id"] for r in b["twin"]["rooms"] if r["kind"] == "corridor")
        hz = client.post("/api/inject", json={"ref": corridor, "kind": "blocked", "note": "test"}).json()["hazard"]
        assert hz["geometry"]["kind"] == "room" and client.get("/api/state").json()["hazards"][0]["ref"] == corridor
        with client.websocket_connect("/ws") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["type"] == "hello" and set(hello["modes"]) == {"costar", "matterport", "vexcel", "llm"} and hello["modes"]["llm"] == "replay"
            assert hello["labels"]["matterport"]["real_sponsor_data"] is False
            seen = set()
            for _ in range(40):
                seen.add(json.loads(ws.receive_text())["type"])
                if {"state", "timeline"} <= seen:
                    break
            assert {"state", "timeline"} <= seen                                             # live state + backlog of the hazard entry
        assert client.post("/api/reset", json={}).json()["ok"] is True
        assert client.get("/api/state").json()["hazards"] == []


class FakeHub:
    """Stands in for the uwb-kit hub: teleports set a tag's rig position, positions are fed back at 20 Hz with a little noise."""

    def __init__(self, incident, clock):
        self.incident, self.clock, self.site, self.i = incident, clock, {}, 0

    def teleport(self, tag, x, y):
        self.site[tag] = (x, y)

    def alive(self):
        return True

    async def run(self):
        inc = self.incident
        inc.on_hello({"type": "hello", "simulated": True, "source": "sim:test-fake-hub", "profile": "3A2T",
                      "nodes": {"A1": {"role": "anchor"}, "A2": {"role": "anchor"}, "A3": {"role": "anchor"}, "T1": {"role": "tag"}, "T2": {"role": "tag"}}})
        inc.on_anchors({"type": "anchors", "anchors": {"A1": {"x": 0, "y": 0}, "A2": {"x": 3.6, "y": 0}, "A3": {"x": 1.8, "y": 4.4}}})
        while True:
            self.i += 1
            j = 0.02 * (1 if self.i % 2 else -1)
            for tag, (x, y) in list(self.site.items()):
                inc.on_pos({"type": "pos", "id": tag, "t": self.clock(), "x": x + j, "y": y - j, "cov": [0.004, 0.0, 0.004], "q": 0.9})
            await asyncio.sleep(0.02)


def test_storyboard_replays_the_recorded_model_run_end_to_end(monkeypatch):
    """The two-minute storyboard, compressed: the incident clock runs 12x so it finishes in seconds.
    Every model call must hit the recorded cache (no reflex fallback), which proves the prompts are deterministic."""
    if not list(config.LLM_CACHE_DIR.glob("*.json")):
        pytest.skip("no recorded model responses in fixtures/llm_cache yet")
    import sandtable.server as server
    monkeypatch.setattr(config, "REPLAY_PACE_S", 0.0)
    monkeypatch.setattr(config, "STATE_HZ", 40.0)

    async def scenario():
        s = server.AppState()
        t0 = time.time()
        clock = lambda: t0 + (time.time() - t0) * 12.0
        s.incident.clock = clock
        s.hub = FakeHub(s.incident, clock)
        s.incident.teleport = s.hub.teleport
        events = []
        q = asyncio.Queue(maxsize=100000)
        s.clients.add(q)
        await s.startup()
        try:
            await asyncio.sleep(0.3)
            s.director.start()
            for _ in range(600):
                await asyncio.sleep(0.1)
                if s.director.step in ("done", "failed") and not s.director.running:
                    break
            while not q.empty():
                events.append(q.get_nowait())
            return s, events, s.director.step
        finally:
            await s.shutdown()

    s, events, final_step = asyncio.run(scenario())
    assert final_step == "done" and s.incident.phase == "terminated"
    agent_events = [e for e in events if e.get("type") == "agent"]
    assert agent_events and not [e for e in agent_events if e["backend"] == "reflex-fallback"], "a model call missed the recording: re-record (see README)"
    assert s.preplan["backend"] == "replay" and s.aar["backend"] == "replay"
    runs = {e["run"].split("#")[0] for e in agent_events}
    assert {"preplan", "mayday", "hazard", "contact", "aar"} <= runs
    actions = [e["tool"] for e in agent_events if e["kind"] == "action"]
    assert actions.count("assign") >= 3 and "radio" in actions and "add_observation" in actions
    assert len(s.incident.hazards) == 1 and s.incident.stats["replans"] == 1
    assert s.aar["metrics"]["mayday_to_contact_s"] > 0 and s.aar["observations"]
    radio = [e for e in events if e.get("type") == "radio"]
    assert len(radio) >= 5 and all(len(m["text"].split()) <= 60 for m in radio)
    assert all(u["status"] == "out" for u in s.incident.units.values())


def _run_exercise(monkeypatch, mode, nodes, cache_dir=None, human=False):
    import sandtable.server as server
    monkeypatch.setattr(config, "REPLAY_PACE_S", 0.0)
    monkeypatch.setattr(config, "STATE_HZ", 40.0)
    if cache_dir is not None:
        monkeypatch.setattr(config, "LLM_CACHE_DIR", cache_dir)

    async def scenario():
        s = server.AppState()
        if cache_dir is not None:
            s.agent.llm.cache_dir = cache_dir
        t0 = time.time()
        clock = lambda: t0 + (time.time() - t0) * 12.0
        s.incident.clock = clock
        hub = FakeHub(s.incident, clock)
        hub_nodes = nodes

        async def run():
            inc = s.incident
            inc.on_hello({"type": "hello", "simulated": True, "source": "sim:test-fake-hub", "profile": "test", "nodes": hub_nodes})
            inc.on_anchors({"type": "anchors", "anchors": {"A1": {"x": 0, "y": 0}, "A2": {"x": 3.6, "y": 0}, "A3": {"x": 1.8, "y": 4.4}}})
            while True:
                hub.i += 1
                j = 0.02 * (1 if hub.i % 2 else -1)
                for tag, (x, y) in list(hub.site.items()):
                    inc.on_pos({"type": "pos", "id": tag, "t": clock(), "x": x + j, "y": y - j, "cov": [0.004, 0.0, 0.004], "q": 0.9})
                await asyncio.sleep(0.02)
        hub.run = run
        s.hub = hub
        s.incident.teleport = hub.teleport
        events, q = [], asyncio.Queue(maxsize=100000)
        s.clients.add(q)
        await s.startup()
        try:
            await asyncio.sleep(0.3)
            s.director.start(mode)
            moved = False
            for _ in range(700):
                await asyncio.sleep(0.1)
                e1 = s.incident.units["E1"]
                if human and not moved and s.director.step.startswith("YOUR MOVE") and e1["route"]:
                    moved = True                                   # the test plays the person who carries the Engine 1 token
                    for x, y in e1["route"]["polyline"]:
                        s.incident.move_unit("E1", x, y)
                        await asyncio.sleep(0.12)
                if s.director.step in ("done", "failed") and not s.director.running:
                    break
            while not q.empty():
                events.append(q.get_nowait())
            return s, events, s.director.step
        finally:
            await s.shutdown()
    return asyncio.run(scenario())


TWO_TAGS = {"A1": {"role": "anchor"}, "A2": {"role": "anchor"}, "A3": {"role": "anchor"}, "T1": {"role": "tag"}, "T2": {"role": "tag"}}
ONE_TAG = {"A1": {"role": "anchor"}, "A2": {"role": "anchor"}, "A3": {"role": "anchor"}, "A4": {"role": "anchor"}, "T1": {"role": "tag"}}


def test_guided_exercise_with_one_tag_replays_the_recording(monkeypatch):
    """Guided mode, four anchors and one tag: a person moves Engine 1, the rescue team is a software unit. This is the hardware demo path."""
    if not list(config.LLM_CACHE_DIR.glob("*.json")):
        pytest.skip("no recorded model responses in fixtures/llm_cache yet")
    s, events, final_step = _run_exercise(monkeypatch, "assist", ONE_TAG, human=True)
    assert final_step == "done" and s.incident.phase == "terminated"
    assert s.incident.units["RIT"]["physical"] is False
    agent_events = [e for e in events if e.get("type") == "agent"]
    assert not [e for e in agent_events if e["backend"] == "reflex-fallback"], "a model call missed the recording: re-record with scripts/record_all.sh"
    assert s.preplan["backend"] == "replay" and s.aar["backend"] == "replay"


def test_exercise_completes_on_the_fallback_policy_when_no_model_is_available(monkeypatch, tmp_path):
    """Empty cache, no live model: every turn must fall back to the deterministic policy, be labelled so, and the exercise must still finish."""
    monkeypatch.setattr(config, "AGENT_FALLBACK_AFTER_S", 3.0)
    s, events, final_step = _run_exercise(monkeypatch, "auto", TWO_TAGS, cache_dir=tmp_path)
    assert final_step == "done" and s.incident.phase == "terminated"
    agent_events = [e for e in events if e.get("type") == "agent" and e["kind"] != "start"]
    assert agent_events and all(e["backend"] == "reflex-fallback" for e in agent_events)
    assert s.preplan["backend"] == "reflex-fallback" and s.aar["backend"] == "reflex-fallback" and s.aar["corrections"] == []
    assert all(u["status"] == "out" for u in s.incident.units.values())


def test_autopilot_is_refused_on_real_boards_but_guided_mode_is_allowed():
    import sandtable.server as server
    with TestClient(server.app) as client:
        server.STATE.incident.on_hello({"type": "hello", "simulated": False, "source": "serial:test", "profile": "4A1T",
                                        "nodes": {"A1": {"role": "anchor"}, "T1": {"role": "tag"}}})
        assert client.post("/api/storyboard", json={"action": "start", "mode": "auto"}).status_code == 409
        assert client.post("/api/unit/move", json={"unit": "E1", "x": 5, "y": 5}).status_code == 409        # a real tag is never moved by software
        ok = client.post("/api/storyboard", json={"action": "start"}).json()
        assert ok["ok"] and ok["storyboard"]["mode"] == "assist"
        client.post("/api/storyboard", json={"action": "stop"})
        r = client.get("/api/vision/ortho.png")
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
