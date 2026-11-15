"""Command agent: protocol parsing, tools, the deterministic fallback policy, and replay of the recorded real-model runs."""
import asyncio
import json
import os
import time

import pytest

from sandtable import config
from sandtable.agent import CommandAgent
from sandtable.agent.llm import LLM, ReplayMiss, extract_json, request_key
from sandtable.agent.tools import ACTION_TOOLS, CATALOG, ToolBox

from test_incident import Rig, run_to_mayday


def run(coro):
    return asyncio.run(coro)


def make_agent(rig, cache_dir=None):
    events = rig.msgs
    llm = LLM(backend="replay", record=False, cache_dir=cache_dir or config.LLM_CACHE_DIR)
    return CommandAgent(rig.provider, rig.twin, rig.inc, events.append, llm)


def test_extract_json_handles_fences_prose_and_nested_braces():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure.\n```json\n{"thought": "x {y}", "calls": [{"tool": "route", "args": {"src": "a"}}]}\n```') ["calls"][0]["tool"] == "route"
    assert extract_json('noise {not json} then {"final": {"summary": "ok"}} tail')["final"]["summary"] == "ok"
    with pytest.raises(ValueError):
        extract_json("no object here")


def test_request_key_is_stable_and_order_independent():
    m1 = [{"role": "user", "content": "x"}]
    m2 = [{"content": "x", "role": "user"}]
    assert request_key("main", "sys", m1, None) == request_key("main", "sys", m2, None)
    assert request_key("main", "sys", m1, None) != request_key("main", "sys", m1, "abc")
    assert request_key("main", "sys", m1, None) != request_key("fast", "sys", m1, None)


def test_replay_miss_is_raised_not_invented(tmp_path):
    llm = LLM(backend="replay", record=False, cache_dir=tmp_path)
    with pytest.raises(ReplayMiss):
        run(llm.complete("s", [{"role": "user", "content": "never recorded"}]))
    assert llm.calls["miss"] == 1


def test_every_tool_in_the_catalogue_runs_and_is_deterministic():
    rig = Rig()
    rig.step(1.0)
    run_to_mayday(rig)
    box = ToolBox(rig.provider, rig.twin, rig.inc, LLM(backend="replay"))
    box.snapshot = rig.inc.snapshot()
    suite = rig.inc.units["E1"]["suite"]
    args = {"get_building_record": {}, "list_tenants": {}, "list_vacant_space": {}, "outside_context": {}, "list_aerial_imagery": {},
            "measure_outside": {"from_ref": "E1", "to_ref": "hydrant:h1"}, "describe_floor": {}, "locate_unit": {"unit": "E1"},
            "route": {"src": "RIT", "dst": "E1"}, "nearest_exit": {"src": "E1"}, "hose_stretch": {"target": suite, "stair": "stairA"}, "plan_anchor_drops": {}}
    for name, a in args.items():
        r1, r2 = run(box.call(name, a)), run(box.call(name, a))
        assert "error" not in r1, (name, r1)
        assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True), name
    assert set(args) | ACTION_TOOLS | {"look_at_aerial"} == {c[0] for c in CATALOG}
    loc = run(box.call("locate_unit", {"unit": "E1"}))
    assert loc["suite"] == suite and loc["tenants_in_record"] and len(loc["exits"]) == 2          # twin + record joined through the UWB position
    assert run(box.call("route", {"src": "RIT", "dst": "nowhere"}))["error"]
    assert run(box.call("no_such_tool", {}))["error"]


def test_action_tools_act_on_the_incident_and_fixture_writes_stay_local():
    rig = Rig()
    rig.step(1.0)
    run_to_mayday(rig)
    box = ToolBox(rig.provider, rig.twin, rig.inc, LLM(backend="replay"))
    box.snapshot = rig.inc.snapshot()
    assert run(box.call("assign", {"unit": "RIT", "task": "rit_rescue", "target": "E1"}))["ok"]
    assert rig.inc.units["RIT"]["assignment"]["task"] == "rit_rescue"
    run(box.call("radio", {"to": "RIT", "text": "RIT from Command: go", "priority": "emergency"}))
    assert any(m.get("type") == "radio" and m["to"] == "RIT" for m in rig.msgs)
    obs = run(box.call("add_observation", {"text": "test note", "unit": "E1"}))
    assert obs["ok"] and "fixture mode" in obs["status"]
    corr = run(box.call("file_record_correction", {"field": "area", "current": 1, "proposed": 2, "evidence": {"why": "test"}}))
    assert corr["ok"] and "not sent to CoStar" in corr["status"]
    assert config.TAGS_OVERLAY_JSON.exists() and str(config.DATA_DIR) in str(config.TAGS_OVERLAY_JSON)


def test_fallback_policy_handles_mayday_hazard_and_contact_without_a_model(tmp_path):
    rig = Rig()
    rig.step(1.0)
    run_to_mayday(rig)
    agent = make_agent(rig, tmp_path)                                  # empty cache: every model call misses
    config.REPLAY_PACE_S = 0
    out = run(agent.handle(rig.situations[-1]))
    assert out["backend"] == "reflex-fallback" and rig.inc.units["RIT"]["assignment"]["task"] == "rit_rescue"
    events = [m for m in rig.msgs if m.get("type") == "agent"]
    assert any(e["kind"] == "error" for e in events) and all(e["backend"] in ("replay", "reflex-fallback") for e in events)
    for e in events:
        for key in ("run", "phase", "step", "kind", "backend", "text", "t"):
            assert key in e
    rig.step(3)
    rit = rig.inc.units["RIT"]
    nodes = rit["route"]["nodes"]
    here = nodes.index(rit["_logical"])
    edge = next(f"{a}-{b}" for a, b in zip(nodes[here + 1:], nodes[here + 2:])
                if all(rig.twin.node(n)["kind"] == "sweep" and rig.twin.room(rig.twin.node(n)["room"])["kind"] == "corridor" for n in (a, b)))
    rig.inc.mark_hazard(edge, "collapse", "test", source="inject")
    run(agent.handle(rig.situations[-1]))
    assert rit["route"]["valid"] and rit["_follow"] is not None
    rig.step(40)
    assert rig.inc.contact_done
    run(agent.handle(rig.situations[-1]))
    assert rit["assignment"]["task"] == "exit"
    rig.step(30)
    assert rig.inc.phase == "terminated"
    aar = run(agent.run_after_action())
    assert aar["backend"] == "reflex-fallback" and aar["metrics"]["replans"] == 1 and aar["corrections"] == []
    plan = run(agent.run_preplan())
    assert plan["backend"] == "reflex-fallback" and plan["tactical"]["attack_stair"] and plan["tactical"]["hose_stretch"]["total_m"] > 0


def test_cache_files_are_real_recordings():
    files = sorted(config.LLM_CACHE_DIR.glob("*.json"))
    if not files:
        pytest.skip("no recorded model responses in fixtures/llm_cache yet")
    for f in files:
        c = json.loads(f.read_text())
        assert c["backend"] in ("claude-cli", "anthropic") and c["model"] and c["response"].strip()
        assert request_key(c["tier"], c["request"]["system"], c["request"]["messages"], c["request"]["image_sha"]) == f.stem


def test_recorded_preplan_replays_from_the_cache():
    if not list(config.LLM_CACHE_DIR.glob("*.json")):
        pytest.skip("no recorded model responses in fixtures/llm_cache yet")
    rig = Rig()
    rig.step(1.0)
    rig.inc.start()
    agent = make_agent(rig)
    config.REPLAY_PACE_S = 0
    t0 = time.time()
    plan = run(agent.run_preplan())
    assert plan["backend"] == "replay", "the pre-plan prompt no longer matches the recording: re-record (see README)"
    assert time.time() - t0 < 10
    events = [m for m in rig.msgs if m.get("type") == "agent"]
    tools = [e["tool"] for e in events if e["kind"] == "tool_call"]
    assert {"get_building_record", "list_tenants", "describe_floor", "look_at_aerial", "hose_stretch"} <= set(tools)
    assert not any(e["backend"] == "reflex-fallback" for e in events)
    assert len(plan["sections"]) >= 5 and plan["verify_on_site"] and plan["tactical"]["attack_stair"] != plan["tactical"]["evac_stair"]
    layers = {i["layer"] for s in plan["sections"] for i in s["items"]}
    assert {"costar", "vexcel", "matterport"} <= layers


@pytest.mark.skipif(os.name == "nt", reason="uses a shell script as a stand-in for the CLI")
def test_a_given_up_cli_turn_ends_the_cli_process(monkeypatch, tmp_path):
    """Bug: when a turn was given up (the agent's time limit, a reset), the claude CLI subprocess it had started kept running."""
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(fake) if name == "claude" else None)
    procs = []
    real_exec = asyncio.create_subprocess_exec

    async def spawn(*args, **kw):
        procs.append(await real_exec(*args, **kw))
        return procs[-1]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def scenario():
        llm = LLM(backend="claude-cli", record=False, cache_dir=tmp_path / "cache")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(llm.complete("system", [{"role": "user", "content": "hello"}]), 0.5)
        return await asyncio.wait_for(procs[0].wait(), 5.0)
    assert run(scenario()) is not None and procs[0].returncode is not None
