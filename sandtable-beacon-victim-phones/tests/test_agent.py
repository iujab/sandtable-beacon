"""The command agent (beacon/agent.py, tools.py, llm.py): replay of the recorded real runs, the labelled fallback,
discrete tool results, no coordinates in the plan, and a clean cache. Offline: replay backend only."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest

from beacon import config
from beacon.agent import BeaconAgent, situation
from beacon.llm import CACHE_DIR, LLM, LLMError, request_key
from beacon.tools import ACTION_TOOLS, CATALOG, ToolBox, brief_of
from beacon.twin import Twin

ROOT = Path(__file__).resolve().parent.parent
HAVE_SIM = importlib.util.find_spec("beacon.sim") is not None and importlib.util.find_spec("beacon.locate") is not None
INDEX = json.loads((CACHE_DIR / "RUNS.json").read_text(encoding="utf-8"))


def _script():
    spec = importlib.util.spec_from_file_location("record_agent", ROOT / "scripts" / "record_agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def twin() -> Twin:
    return Twin(config.twin_raw())


@pytest.fixture(scope="module")
def sample() -> dict:
    return json.loads(config.SAMPLE_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scene():
    if not HAVE_SIM:
        pytest.skip("simulator or locator not available")
    return _script().scene_session("suite-310")


def run(source, llm, tmp_path, twin=None):
    msgs: list[dict] = []
    agent = BeaconAgent(source, twin, None, msgs.append, llm=llm, record_path=tmp_path / "data" / "record_checks.json", pace_s=0)
    plan = asyncio.run(agent.run("test"))
    return plan, msgs, agent


def comparable(plan: dict) -> dict:
    return {k: v for k, v in plan.items() if k != "backend"}


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
            yield (f"{path}.{k}", k, None)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield (path, None, obj)


ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


# ---------------------------------------------------------------------------- replay of the recorded real runs
def test_replay_hit_reproduces_the_recorded_sample_plan(sample, tmp_path):
    rec = INDEX["runs"]["sample"]
    assert rec["recorded_with"] in ("claude-cli", "anthropic") and rec["models"]
    plan, msgs, agent = run(sample, LLM("replay"), tmp_path)
    assert plan["backend"] == "replay" and agent.backend == "replay"
    assert comparable(plan) == rec["plan"]
    assert agent.llm.calls == {"replay": rec["steps"], "live": 0, "miss": 0}
    assert {m["backend"] for m in msgs if m["type"] == "agent"} == {"replay"}
    assert msgs[-1]["type"] == "agent_plan" and msgs[-1] == plan
    assert plan["orders"] and plan["priorities"] and plan["limits_acknowledged"]


@pytest.mark.parametrize("name", ["suite-310", "suite-310+309"])
def test_replay_hit_reproduces_the_recorded_scene_plans(name, tmp_path):
    if not HAVE_SIM:
        pytest.skip("simulator or locator not available")
    rec = INDEX["runs"][name]
    plan, _msgs, agent = run(_script().situation_source(name), LLM("replay"), tmp_path)
    if plan["backend"] != "replay":
        pytest.fail(f"the recorded {name} run no longer replays: the scenario, simulator or locator changed; "
                    f"re-record with `LLM_BACKEND=claude-cli .venv/bin/python scripts/record_agent.py --situation {name}`")
    assert comparable(plan) == rec["plan"]
    if name == "suite-310+309":                                # the record disagrees with the phones: the model filed it
        assert [c["suite"] for c in plan["record_checks"]] == ["309"]
        assert plan["record_checks"][0]["status"] == "filed locally (fixture mode)"


def test_every_recorded_file_is_one_real_reply_of_an_indexed_run():
    keys = {k for r in INDEX["runs"].values() for k in r["keys"]}
    files = {f.stem for f in CACHE_DIR.glob("*.json") if f.name != "RUNS.json"}
    assert keys == files
    for f in CACHE_DIR.glob("*.json"):
        if f.name == "RUNS.json":
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        assert set(d) == {"key", "backend", "model", "tier", "recorded_at", "note", "request", "response"}
        assert d["backend"] in ("claude-cli", "anthropic") and d["model"]
        assert request_key(d["tier"], d["request"]["system"], d["request"]["messages"]) == d["key"] == f.stem


def test_cache_carries_no_machine_or_personal_data():
    bad = re.compile(r"/home/|/Users/|C:\\\\|session_id|tailscale|\.ts\.net|@[a-z0-9-]+\.[a-z]{2,}", re.I)
    for f in CACHE_DIR.glob("*.json"):
        text = f.read_text(encoding="utf-8")
        assert not bad.search(text), f.name
        assert "Two of us in the back office" not in text      # occupants' typed notes never reach a prompt


# ---------------------------------------------------------------------------- the fallback
def test_replay_miss_gives_the_labelled_fallback(sample, tmp_path):
    plan, msgs, agent = run(sample, LLM("replay", cache_dir=tmp_path / "empty"), tmp_path)
    assert plan["backend"] == "reflex-fallback" and agent.backend == "reflex-fallback"
    agent_msgs = [m for m in msgs if m["type"] == "agent"]
    err = next(i for i, m in enumerate(agent_msgs) if m["kind"] == "error")
    assert "no recorded model response" in agent_msgs[err]["text"]
    assert all(m["backend"] == "reflex-fallback" for m in agent_msgs[err:])
    assert plan["orders"][0]["to"] == "RIT" and plan["orders"][0]["priority"] == "emergency"
    assert plan["priorities"][0]["suite"] == "310" and plan["priorities"][0]["action"] == "rescue"
    assert plan["next_puck"] and plan["next_puck"]["suite"] == sample["accountability"]["next_puck"]["suite"]
    assert any(o["to"] == "ALL" and "not people" in o["text"] for o in plan["orders"])
    assert plan["limits_acknowledged"]


def test_llm_error_gives_the_labelled_fallback(sample, tmp_path):
    class Broken(LLM):
        async def complete(self, system, messages, tier="main"):
            raise LLMError("simulated outage")
    plan, msgs, _agent = run(sample, Broken("replay", cache_dir=tmp_path / "empty"), tmp_path)
    assert plan["backend"] == "reflex-fallback"
    assert any(m.get("kind") == "error" and "simulated outage" in m["text"] for m in msgs)


def test_fallback_files_the_record_disagreements(tmp_path, twin):
    from beacon import roster
    b = json.loads(config.SAMPLE_JSON.read_text(encoding="utf-8"))
    for p in b["phones"]:
        if p["id"] == "B4":
            p["report"]["home_suite"] = "309"                  # a user registered to a suite the record lists as vacant
    b["accountability"] = roster.accountability(config.site_raw(), twin, b["floor"], b["phones"], b["presence"], b["pucks"],
                                                labels=config.labels(), focus_room=b["room"])
    plan, _msgs, _agent = run(b, LLM("replay", cache_dir=tmp_path / "empty"), tmp_path)
    assert [c["suite"] for c in plan["record_checks"]] == ["309"]
    assert plan["record_checks"][0]["status"] == "filed locally (fixture mode)"
    filed = json.loads((tmp_path / "data" / "record_checks.json").read_text(encoding="utf-8"))
    assert filed[-1]["suite"] == "309" and filed[-1]["status"] == "filed locally (fixture mode)"


def test_busy_agent_refuses_a_second_run(sample, tmp_path):
    agent = BeaconAgent(sample, None, None, None, llm=LLM("replay"), record_path=tmp_path / "rc.json", pace_s=0)
    agent.busy = True
    with pytest.raises(RuntimeError):
        asyncio.run(agent.run())


# ---------------------------------------------------------------------------- discrete tool results and plans
def _all_tool_results(brief, twin, tmp_path) -> list[tuple[str, dict]]:
    box = ToolBox(brief, twin, config.site_raw(), record_path=tmp_path / "rc.json")
    out = []
    for name, *_ in CATALOG:
        if name in ACTION_TOOLS or name == "route_to":
            continue
        out.append((name, box.call(name, {})))
    for row in out[1][1]["suites"]:
        out.append(("suite_accountability", box.call("suite_accountability", {"suite": row["suite"]})))
    targets = [p["id"] for p in brief["phones"]] + [r["suite"] for r in out[1][1]["suites"]] + ["stairA", "nowhere"]
    for t in targets:
        out.append(("route_to", box.call("route_to", {"target": t})))
    out.append(("assign", box.call("assign", {"unit": "RIT", "task": "rescue", "target": brief["phones"][0]["id"]})))
    out.append(("assign", box.call("assign", {"unit": "E1", "task": "scan", "target": "301"})))
    out.append(("radio", box.call("radio", {"to": "ALL", "text": "test", "priority": "urgent"})))
    out.append(("file_record_check", box.call("file_record_check", {"suite": "309", "text": "test"})))
    return out


@pytest.mark.parametrize("which", ["sample", "scene"])
def test_tool_results_are_discrete(which, sample, twin, tmp_path, request):
    src = sample if which == "sample" else request.getfixturevalue("scene")
    brief = brief_of(src)
    results = _all_tool_results(brief, twin, tmp_path)
    assert any("error" in r for _n, r in results)              # the unknown target is refused, not invented
    for name, r in results:
        json.dumps(r)
        for path, key, val in walk(r):
            assert key not in ("x", "y", "cx", "cy", "lat", "lon", "geo", "t", "time", "timestamp", "age_s", "eid"), f"{name}{path}"
            if isinstance(val, float):
                assert round(val, 1) == val, f"{name}{path} = {val}"
            if isinstance(val, str):
                assert not ISO_TIME.search(val), f"{name}{path}"
                assert not re.search(r"\b1[0-9]{9}\b", val), f"{name}{path}"


def test_same_situation_same_prompt(sample, twin):
    a = json.dumps(situation(brief_of(sample), twin), sort_keys=True)
    b = json.dumps(situation(brief_of(json.loads(json.dumps(sample))), twin), sort_keys=True)
    assert a == b and '"x"' not in a


def test_candidates_are_two_spots_never_a_position(sample, twin, tmp_path):
    box = ToolBox(brief_of(sample), twin, config.site_raw(), record_path=tmp_path / "rc.json")
    ph = next(p for p in box.call("get_phones", {})["phones"] if p["geometry"] == "candidates")
    assert len(ph["candidates"]) == 2 and "not a position" in ph["where"] and "both" in ph["where"]
    r = box.call("route_to", {"target": ph["id"]})
    assert "candidate spots" in r["leads_to"]
    ring = next(p for p in box.call("get_phones", {})["phones"] if p["geometry"] == "ring")
    assert "no position" in ring["where"]
    assert "room only" in box.call("route_to", {"target": next(p["id"] for p in box.call("get_phones", {})["phones"] if p["geometry"] == "zone")})["leads_to"]
    assert "error" in box.call("route_to", {"target": next(p["id"] for p in box.call("get_phones", {})["phones"] if p.get("safe_outside"))})


def test_stated_evidence_is_shown(sample, twin, tmp_path):
    b = json.loads(json.dumps(sample))
    fixp = next(p for p in b["phones"] if p["geometry"] == "fix")
    fixp["assumptions"] = [{"kind": "occupant-statement", "room": "r310", "text": "Mirror candidate dismissed because the occupant says Suite 310."}]
    box = ToolBox(brief_of(b), twin, config.site_raw(), record_path=tmp_path / "rc.json")
    v = next(p for p in box.call("get_phones", {})["phones"] if p["id"] == fixp["id"])
    assert v["rests_on"][0]["room_label"] == "Suite 310" and "occupant says" in v["rests_on"][0]["text"]


@pytest.mark.parametrize("cache", ["recorded", "empty"])
def test_plan_never_contains_coordinates(sample, tmp_path, cache):
    llm = LLM("replay") if cache == "recorded" else LLM("replay", cache_dir=tmp_path / "empty")
    plan, _msgs, _agent = run(sample, llm, tmp_path)
    for path, key, _val in walk(plan):
        assert key not in ("x", "y", "cx", "cy", "lat", "lon", "geo"), path
    assert set(plan) >= {"type", "run", "backend", "summary", "orders", "priorities", "next_puck", "record_checks", "limits_acknowledged"}
    for o in plan["orders"]:
        assert set(o) == {"to", "text", "priority", "target"} and o["priority"] in ("routine", "urgent", "emergency")
    for q in plan["priorities"]:
        assert q["action"] in ("rescue", "search", "scan")


def test_bad_tool_calls_are_refused_not_invented(sample, twin, tmp_path):
    box = ToolBox(brief_of(sample), twin, config.site_raw(), record_path=tmp_path / "rc.json")
    assert "error" in box.call("nonexistent", {})
    assert "error" in box.call("radio", {"to": "LADDER9", "text": "x"})
    assert "error" in box.call("assign", {"unit": "RIT", "task": "dance", "target": "310"})
    assert "error" in box.call("file_record_check", {"suite": "999", "text": "x"})
    assert "error" in box.call("suite_accountability", {"suite": "999"})
    assert box.orders == [] and box.record_checks == []
