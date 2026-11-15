"""Regression tests for bugs found in the code-quality sweep (report history, agent plan, tool room ids, CoT, story)."""
from __future__ import annotations

import asyncio
import copy
import json
import time
import xml.etree.ElementTree as ET

import pytest

from beacon import config, feeds, report
from beacon.agent import BeaconAgent
from beacon.llm import LLM
from beacon.tools import ToolBox, brief_of
from beacon.twin import Twin


@pytest.fixture(scope="module")
def sample() -> dict:
    return json.loads(config.SAMPLE_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- report history
class _Source:
    """Stands in for the server's state: beacons() returns the same object until the next step, plus an event list."""

    def __init__(self, b: dict):
        self.b, self.events = b, []

    def beacons(self) -> dict:
        return self.b


def test_report_from_a_session_at_one_instant_claims_no_history(sample):
    rec = report.Recorder()
    src = _Source(copy.deepcopy(sample))
    r = report.build(src, None, rec)
    assert r["history"]["observations"] == 1
    assert r["history"]["source"].startswith("latest state only")
    report.build(src, None, rec)                      # asking again, same state: still one observation
    rec.pull(src)                                     # the routes' poller, same state
    assert rec.observations == 1


def test_recorder_counts_each_new_state_once(sample):
    rec = report.Recorder()
    a, b = copy.deepcopy(sample), copy.deepcopy(sample)
    b["t"] = (a.get("t") or 0) + 5
    for x in (a, a, b, b):
        rec.feed(x)
    assert rec.observations == 2


def test_recorder_event_dedup_memory_is_bounded():
    rec = report.Recorder()
    n = report.MAX_EVENT_KEYS + 50
    for i in range(n):
        rec.feed({"type": "beacon_event", "t": 1000.0 + i, "name": "phone_new", "id": f"B{i}", "text": "x"})
    assert len(rec.events) == report.MAX_EVENTS
    assert len(rec._event_keys) == report.MAX_EVENT_KEYS
    rec.feed({"type": "beacon_event", "t": 1000.0 + n - 1, "name": "phone_new", "id": f"B{n - 1}", "text": "x"})   # a repeat
    assert len(rec.events) == report.MAX_EVENTS and rec.events[-1]["id"] == f"B{n - 1}"
    assert [e["id"] for e in rec.events].count(f"B{n - 1}") == 1


# ---------------------------------------------------------------------------- the agent's plan from untrusted model output
class _Scripted(LLM):
    """Returns fixed replies (as if recorded): exercises the plan builder on model output a real model could give."""

    def __init__(self, replies, cache_dir):
        super().__init__("replay", cache_dir=cache_dir)
        self.replies = list(replies)

    async def complete(self, system, messages, tier="main"):
        return self.replies.pop(0), {"source": "replay", "key": "scripted"}


def test_plan_survives_a_list_target_and_a_string_of_limits(sample, tmp_path):
    final = {"summary": "RIT to Suite 310.",
             "priorities": [{"rank": 1, "suite": "310", "target": ["B1", "B3"], "action": "rescue", "why": "two phones"},
                            {"rank": 2, "suite": "Suite 302", "target": {"id": "B9"}, "action": "search", "why": "x"}],
             "next_puck": None, "limits_acknowledged": "Devices are not people."}
    llm = _Scripted([json.dumps({"thought": "done", "final": final})], tmp_path / "empty")
    agent = BeaconAgent(sample, None, None, None, llm=llm, record_path=tmp_path / "rc.json", pace_s=0)
    plan = asyncio.run(agent.run("test"))
    assert plan["backend"] == "replay"
    assert [(p["suite"], p["target"], p["action"]) for p in plan["priorities"]] == [("310", None, "rescue"), ("302", None, "search")]
    assert plan["limits_acknowledged"] == ["Devices are not people."]


# ---------------------------------------------------------------------------- tools: rooms by id, not by label
def test_ring_room_is_resolved_by_id_when_labels_repeat(sample, tmp_path):
    """A scanned suite often has several rooms with one label ("Office"). The room a ring mostly crosses must be the
    room itself, not the first room carrying the same label (here: a suite the record lists as vacant)."""
    raw = copy.deepcopy(config.twin_raw())
    for r in raw["rooms"]:
        if r["id"] in ("r309", "r310"):
            r["label"] = "Office"
    twin = Twin(raw)
    brief = brief_of(copy.deepcopy(sample))
    ring = next(p for p in brief["phones"] if p["geometry"] == "ring")
    box = ToolBox(brief, twin, config.site_raw(), record_path=tmp_path / "rc.json")
    view = next(p for p in box.call("get_phones", {})["phones"] if p["id"] == ring["id"])
    assert view["ring"]["arc_crosses"][0] == {"room_label": "Office", "share_pct": 100}
    assert box._target(ring["id"])[2] == "r310"
    ring_checks = [e for d in box.disagreements() for e in d["evidence"] if "range ring" in e]
    assert ring_checks == []                             # r310 is occupied: no record check against vacant Suite 309


# ---------------------------------------------------------------------------- CoT: the same position test as GeoJSON
def test_cot_skips_items_with_half_a_position(sample):
    b = copy.deepcopy(sample)
    b["pucks"][0]["y"] = None
    fixp = next(p for p in b["phones"] if p["geometry"] == "fix")
    fixp["y"] = None
    root = ET.fromstring(feeds.beacons_to_cot(b, time.time()).split("\n", 1)[1])
    uids = {e.attrib["uid"] for e in root.findall("event")}
    assert f"sandtable.beacon.{b['pucks'][0]['id']}" not in uids
    assert f"sandtable.beacon.{fixp['id']}" not in uids
    gj = {f["properties"]["id"] for f in feeds.beacons_to_geojson(b)["features"]}
    assert b["pucks"][0]["id"] not in gj and fixp["id"] not in gj


# ---------------------------------------------------------------------------- the walk-through restarted while it runs
def test_story_restart_keeps_running_flag():
    from beacon.story import Story

    async def scenario():
        s = Story.__new__(Story)                         # no scene needed: the run is held inside its first step
        s.state, s.task, s.running, s.done, s.error, s.t0, s.log = None, None, False, False, None, None, []
        s._careless_spot = lambda: time.sleep(0.3)
        s.start()
        await asyncio.sleep(0.05)
        first = s.task
        s.start()                                        # restart: the first run is cancelled, the second runs
        await asyncio.sleep(0.05)
        assert first.cancelled() and not s.task.done()
        assert s.running is True
        s.stop()
        await asyncio.gather(s.task, return_exceptions=True)
        assert s.running is False

    asyncio.run(scenario())
