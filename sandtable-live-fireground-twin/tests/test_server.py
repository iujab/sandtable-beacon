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
    monkeypatch.setattr(config, "SCRIPT_PACE", 0.1)                  # the script's pauses for people watching
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
    monkeypatch.setattr(config, "SCRIPT_PACE", 0.1)                  # the script's pauses for people watching
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


def _recorded(marker: str) -> bool:
    """True when the replay cache holds a recorded turn for this kind of situation (the script has been recorded)."""
    return any(marker in p.read_text(encoding="utf-8") for p in config.LLM_CACHE_DIR.glob("*.json"))


def test_beacon_script_replays_the_recorded_model_run(monkeypatch):
    """The hands-free beacon exercise (two pucks, two phones, the search): every agent turn must hit the recording."""
    if not _recorded('\\"kind\\": \\"phone_reached\\"'):
        pytest.skip("the beacon script has not been recorded yet (scripts/record_all.sh)")
    from test_beacon_link import _beacon_run
    s, events, step, seen = _beacon_run(monkeypatch, config.LLM_CACHE_DIR)
    assert step == "done" and s.incident.phase == "terminated"
    agent = [e for e in events if e.get("type") == "agent"]
    missed = sorted({e["run"] for e in agent if e["backend"] == "reflex-fallback"})
    assert agent and not missed, f"turns missed the recording: {missed} (re-record with scripts/record_all.sh)"
    assert s.preplan["backend"] == "replay" and s.aar["backend"] == "replay"


def test_scout_script_replays_the_recorded_model_run(monkeypatch):
    """The hands-free scout exercise on the radar engine and the build-time demo seed: every agent turn must hit the recording."""
    if not _recorded('\\"kind\\": \\"victim_reached\\"'):
        pytest.skip("the scout script has not been recorded yet (scripts/record_all.sh)")
    from test_scout_session import _engine_imports, _run_scout_script
    if not _engine_imports():
        pytest.skip("radar engine does not import")
    s, events, step = _run_scout_script(monkeypatch, "sim", config.LLM_CACHE_DIR)
    assert step == "done" and s.incident.phase == "terminated" and s.scout.engine == "sim"
    agent = [e for e in events if e.get("type") == "agent"]
    missed = sorted({e["run"] for e in agent if e["backend"] == "reflex-fallback"})
    assert agent and not missed, f"turns missed the recording: {missed} (re-record with scripts/record_all.sh)"


# ---------------------------------------------------------------- resets, reconnects and races (each was a bug)

def test_a_report_after_a_reset_does_not_list_the_previous_incidents_mayday_crew():
    import sandtable.server as server

    async def scenario():
        s = server.AppState()
        inc = s.incident
        inc.start()
        inc.begin_operations()
        inc.declare_mayday("E1", "manual", "command")
        inc.resolve_mayday("E1")
        before = [p["source"] for p in s.report()["people"]]
        await s.reset()
        return before, [p["source"] for p in s.report()["people"]]
    before, after = asyncio.run(scenario())
    assert "crew" in before and "crew" not in after


class _FakeSocket:
    """A WebSocket that records what it is sent; `during(n)` runs while message n is being sent; it closes on `last`."""

    def __init__(self, during, last):
        self.sent, self.during, self.last = [], during, last

    async def accept(self):
        pass

    async def send_text(self, text):
        from fastapi import WebSocketDisconnect
        self.sent.append(json.loads(text))
        self.during(len(self.sent))
        await asyncio.sleep(0)                                   # a real send yields to the loop
        if self.sent[-1].get("entry", {}).get("text") == self.last:
            raise WebSocketDisconnect()


def test_a_board_connecting_mid_incident_gets_what_happens_while_it_catches_up_exactly_once():
    """Bug: the board joined the live traffic only after the backlog had been sent, so anything emitted meanwhile was lost."""
    import sandtable.server as server

    async def scenario():
        s = server.AppState()
        server.STATE = s
        s.emit({"type": "timeline", "entry": {"text": "before"}})

        def during(n):
            if s.backlog and n == 2:                             # while "before" (hello, then the backlog) goes out
                s.emit({"type": "timeline", "entry": {"text": "during"}})
                s.emit({"type": "timeline", "entry": {"text": "after"}})
        sock = _FakeSocket(during, "after")
        await asyncio.wait_for(server.ws(sock), 5.0)
        return sock.sent, s.sockets
    sent, sockets = asyncio.run(scenario())
    texts = [m["entry"]["text"] for m in sent if m.get("type") == "timeline"]
    assert sent[0]["type"] == "hello" and texts == ["before", "during", "after"] and not sockets


def test_a_reset_ends_the_old_incidents_pre_plan():
    """Bug: a pre-plan still running at a reset finished later on the new incident: a stale plan, and operations begun before
    the new incident's own pre-plan was ready."""
    import sandtable.server as server

    async def scenario():
        s = server.AppState()
        calls = []

        async def preplan():
            calls.append(1)
            await asyncio.sleep(0.3 if len(calls) == 1 else 2.0)       # the old pre-plan would finish while the new one still runs
            s.emit({"type": "preplan", "address": "old" if len(calls) == 1 else "new"})
        s.agent.run_preplan = preplan
        await s.alarm()
        await asyncio.sleep(0.05)
        await s.reset()
        await s.alarm()                                                # the new incident's pre-plan
        await asyncio.sleep(0.5)
        out = (s.incident.phase, s.preplan, s.agent_busy())
        await s.reset()
        return out
    phase, preplan, busy = asyncio.run(scenario())
    assert phase == "preplan" and preplan is None and busy


def test_a_reset_ends_the_agents_turn_on_the_old_incident():
    """Bug: an agent turn still running at a reset went on and gave its orders to the new incident's crews."""
    import sandtable.server as server

    async def scenario():
        s = server.AppState()
        acted = []

        async def handle(situation):
            await asyncio.sleep(0.3)
            acted.append(situation["kind"])
            s.incident.assign("RIT", "stage", "stairB", source="agent")
        s.agent.handle = handle
        loop_task = asyncio.create_task(s._agent_loop())
        s.incident.start()
        s.incident.begin_operations()
        s._on_situation({"seq": 1, "kind": "mayday", "unit": "E1", "data": {}, "snapshot": s.incident.snapshot()})
        await asyncio.sleep(0.05)
        busy = s.agent_busy()
        await s.reset()
        await asyncio.sleep(0.5)
        loop_task.cancel()
        return busy, acted, s.incident.units["RIT"]["assignment"], s.agent_busy()
    busy, acted, rit, busy_after = asyncio.run(scenario())
    assert busy and acted == [] and rit is None and not busy_after


def test_the_exercise_script_starts_once_and_a_stop_then_start_is_not_undone_by_the_old_run():
    """Bugs: two starts before the first script ran started two scripts; the ending of a stopped run cleared the flags of a run
    started right after it."""
    from sandtable.director import Director

    class Stub:
        def __init__(self):
            self.resets, self.incident = 0, None
            self.agent = type("A", (), {"backend": "replay"})()

        async def reset(self):
            self.resets += 1
            await asyncio.sleep(10)

    async def scenario():
        d = Director(Stub())
        d.start(script="mayday")
        first = d._task
        d.start(script="mayday")
        same = d._task is first
        await asyncio.sleep(0.05)
        d.stop()
        d.start(script="mayday")                                 # at once, before the stopped run has wound down
        await asyncio.sleep(0.05)
        status = d.status()
        d.stop()
        await asyncio.sleep(0.05)
        return same, d.s.resets, status, d.status()
    same, resets, during, after = asyncio.run(scenario())
    assert same and resets == 2
    assert during["running"] is True and during["step"] == "reset"
    assert after["running"] is False and after["step"] == "stopped"


def test_a_teleport_requested_while_one_fails_is_still_sent():
    """Bug: when a teleport to the hub failed, the newest target asked for in the meantime (a crew's last step, a staging
    spot) was dropped until some later teleport."""
    import threading
    from sandtable.hubclient import HubLink

    class Kit:
        def __init__(self):
            self.sent, self.release = [], threading.Event()

        def teleport(self, tag, x, y):
            if (x, y) == (1.0, 1.0):
                self.release.wait(2.0)
                raise OSError("hub hiccup")
            self.sent.append((tag, x, y))

    async def scenario():
        link = HubLink(incident=None, url="ws://127.0.0.1:9/stream")
        link.kit, link._loop = Kit(), asyncio.get_running_loop()
        link.teleport("T1", 1.0, 1.0)                            # in flight, and it will fail
        await asyncio.sleep(0.05)
        link.teleport("T1", 2.0, 2.0)                            # superseded before it is sent
        link.teleport("T1", 3.0, 3.0)                            # the newest target
        link.kit.release.set()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if link.kit.sent:
                break
        await asyncio.sleep(0.1)
        return link.kit.sent
    assert asyncio.run(scenario()) == [("T1", 3.0, 3.0)]
