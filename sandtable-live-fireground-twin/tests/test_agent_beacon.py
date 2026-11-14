"""Command agent in Beacon mode (occupants' phones): the phone tools respect the geometry on the hand-made sample,
the fallback policy covers `phone` and `phone_reached`, the after-action task carries the accountability hand-off,
prompts are deterministic, and the original prompts stay untouched (see test_agent_scout for the byte check).

The brief is built here from the Beacon sample in the shape the feed contract describes for GET /api/brief
(positions to 0.5 m, confidences to 0.1, no ages), so these tests do not depend on the server side.
"""
import asyncio
import copy
import hashlib
import json
import re

import pytest

from sandtable import config
from sandtable.agent import prompts
from sandtable.agent.llm import LLM, request_key
from sandtable.agent.tools import ACTION_TOOLS, CATALOG, ToolBox, catalog_text, field_catalog

from test_agent_scout import PINNED, ScoutRig, make_agent, sha
from test_incident import Rig, start_operations

SAMPLE = config.FIXTURES / "beacon" / "sample_beacons.json"
if not SAMPLE.exists():                                           # the copy kept in this tree is the server builder's; fall back to the source
    SAMPLE = config.ROOT.parent / "sandtable-beacon-victim-phones" / "fixtures" / "beacon" / "sample_beacons.json"


def run(coro):
    return asyncio.run(coro)


def half(v):
    return None if v is None else round(float(v) * 2) / 2


def load_sample() -> dict:
    if not SAMPLE.exists():
        pytest.skip("no Beacon sample in this tree or beside it")
    return json.loads(SAMPLE.read_text())


def brief_from_beacons(b: dict) -> dict:
    """A discrete brief: what GET /api/brief is specified to give (no timestamps, 0.5 m, 0.1)."""
    phones = []
    for p in b["phones"]:
        q = {"id": p["id"], "method": p["method"], "geometry": p["geometry"], "room": p.get("room"), "room_label": p.get("room_label"),
             "room_prob": None if p.get("room_prob") is None else round(p["room_prob"], 1),
             "confidence": None if p.get("confidence") is None else round(p["confidence"], 1),
             "report": {k: p["report"].get(k) for k in ("status", "people", "can_move", "text", "safe_outside", "home_suite")}, "text": p["text"], "stale": p.get("stale", False)}
        if p["geometry"] == "fix":
            q.update({"x": half(p["x"]), "y": half(p["y"]), "sigma_m": half(p["sigma_m"]), "node": p.get("node")})
        if p.get("ring"):
            r = p["ring"]
            q["ring"] = {"puck": r["puck"], "cx": half(r["cx"]), "cy": half(r["cy"]), "r_m": half(r["r_m"]), "kind": r["kind"], "arcs": r["arcs"]}
        if p.get("candidates"):
            q["candidates"] = [{"x": half(c["x"]), "y": half(c["y"]), "weight": round(c["weight"], 1), "room": c["room"], "room_label": c["room_label"]} for c in p["candidates"]]
        if p.get("zone"):
            z = p["zone"]
            q["zone"] = {k: z[k] for k in ("room", "room_label", "band", "puck")} | {"prob": round(z["prob"], 1)}
        phones.append(q)
    return {"floor": b["floor"], "room": b["room"], "room_label": b["room_label"], "simulated": b["simulated"], "sample": bool(b.get("sample")),
            "source": b["source"], "rungs": b["rungs"], "phones": phones,
            "presence": [{k: c[k] for k in ("id", "puck", "room", "room_label", "count", "count_range")} for c in b["presence"]],
            "accountability": b["accountability"], "limits": b["limits"]}


class BeaconRig:
    """The incident Rig plus phones, by composition: E1 at the fire room's door, a brief, situations shaped like the
    ones the incident raises (data = the phone's brief, frozen snapshot carrying compact phones)."""

    def __init__(self, beacons: dict | None = None, data_shape: str = "phone"):
        self.rig = Rig()
        self.rig.step(1.0)
        start_operations(self.rig)
        self.beacons = beacons or load_sample()
        self.brief = brief_from_beacons(self.beacons)
        self.data_shape = data_shape
        self.rig.inc.beacons = self.beacons             # the latest whole Beacons object kept on the incident (requested from the server builder)
        door = next(d for d in self.rig.twin.data["doors"] if self.beacons["room"] in d["rooms"])
        self.rig.inc.assign("E1", "stage", door["id"], source="test")
        self.rig.inc.drive("E1")
        self.rig.step(30)

    def phone(self, pid: str) -> dict:
        return next(p for p in self.brief["phones"] if p["id"] == pid)

    def snapshot(self) -> dict:
        snap = self.rig.inc.snapshot()
        snap.setdefault("phones", [{"id": p["id"], "geometry": p["geometry"], "room": p["room"], "status": p["report"]["status"],
                                    "people": p["report"]["people"]} for p in self.brief["phones"]])
        return snap

    def situation(self, kind: str, pid: str, extra: dict | None = None) -> dict:
        if kind == "phone":
            data = self.phone(pid) if self.data_shape == "phone" else {**self.brief, "phone_id": pid}
        else:
            data = {"phone": pid, **(extra or {})}
        return {"seq": 1, "kind": kind, "unit": extra.get("rescuer") if extra else None, "data": data, "snapshot": self.snapshot()}


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(config, "REPLAY_PACE_S", 0.0)


def box_for(b: BeaconRig, with_brief=True) -> ToolBox:
    box = ToolBox(b.rig.provider, b.rig.twin, b.rig.inc, LLM(backend="replay"))
    box.snapshot = b.snapshot()
    if with_brief:
        box.beacon_brief = b.brief
    return box


# ---------------------------------------------------------------- prompts

def test_beacon_runs_have_their_own_system_prompt_and_the_original_is_untouched():
    legacy = prompts.SYSTEM.replace("{catalog}", catalog_text())
    assert sha(legacy) == PINNED["SYSTEM_FULL"] and sha(catalog_text(CATALOG)) == PINNED["CATALOG"]
    beacon = prompts.field_system(False, True).replace("{catalog}", catalog_text(field_catalog(False, True)))
    both = prompts.field_system(True, True).replace("{catalog}", catalog_text(field_catalog(True, True)))
    assert "get_phones" in beacon and "get_accountability" in beacon and "get_scan" not in beacon and "never \"go to the phone\"" in beacon
    assert "get_scan" in both and "get_phones" in both and "B1" in both and "V1" in both
    assert {c[0] for c in field_catalog(False, True)} == {c[0] for c in CATALOG} | {"get_phones", "get_accountability"}
    assert not ({"get_phones", "get_accountability"} & ACTION_TOOLS)
    for k in ("phone", "phone_reached"):
        assert "{situation}" in prompts.SITUATIONS[k] and "Budget:" in prompts.SITUATIONS[k] and "Final:" in prompts.SITUATIONS[k]
    for k in ("mayday", "hazard", "contact"):
        assert sha(prompts.SITUATIONS[k]) == PINNED[k]


# ---------------------------------------------------------------- tools on the sample

def test_get_phones_respects_the_geometry():
    b = BeaconRig()
    g = run(box_for(b).call("get_phones", {}))
    assert g["sample"] and g["simulated"]
    ph = {p["id"]: p for p in g["phones"]}
    assert set(ph) == {"B1", "B2", "B3", "B4", "B5"}
    b1 = ph["B1"]
    assert b1["geometry"] == "fix" and b1["where"] and b1["uncertainty_m"] == 0.5 and isinstance(b1["from_door_m"], int)
    assert b1["report"]["status"] == "trapped" and b1["report"]["people"] == 2 and b1["report"]["can_move"] is False
    b2 = ph["B2"]
    assert b2["geometry"] == "ring" and "where" not in b2 and "uncertainty_m" not in b2                   # a ring has no position
    assert b2["ring"]["radius_m"] == 5.0 and b2["ring"]["puck"] == "P1" and b2["ring"]["band"].startswith("thin")
    assert b2["ring"]["arc_in"] and b2["ring"]["arc_in"][0]["room_label"] == "Suite 310"
    b3 = ph["B3"]
    assert b3["geometry"] == "candidates" and [c["rank"] for c in b3["candidates"]] == [1, 2] and b3["candidates"][0]["weight"] >= b3["candidates"][1]["weight"]
    assert ph["B4"]["geometry"] == "zone" and ph["B4"]["zone"]["room_label"] == "Suite 310"
    assert ph["B5"]["report"].get("safe_outside") is True
    assert g["devices_without_app"] and all(d["note"] == "devices, not people" for d in g["devices_without_app"])
    assert any("not people" in c for c in g["cannot_tell"]) and "ring" in g["geometry_means"]
    assert "battery_pct" not in json.dumps(g) and "age_s" not in json.dumps(g)                            # drifting values stay out of prompts


def test_get_accountability_gives_ranges_and_priorities():
    b = BeaconRig()
    a = run(box_for(b).call("get_accountability", {}))
    rows = {r["suite"]: r for r in a["suites"]}
    assert len(rows) == 12 and rows["310"]["priority"] == 1 and rows["310"]["state"] == "people-reported"
    acc = json.loads((config.FIXTURES / "beacon" / "sample_beacons.json").read_text())["accountability"]      # numbers from the fixture, not hard-coded
    fx = {r["suite"]: r for r in acc["suites"]}
    assert rows["310"]["unaccounted"] == [fx["310"]["unaccounted"]["low"], fx["310"]["unaccounted"]["high"]] and "estimate" in rows["310"]["expected"]["basis"]
    assert rows["311"]["state"] == fx["311"]["state"] and a["priorities"][0]["target"] == acc["priorities"][0]["target"]
    assert a["totals"]["people_reported"] == acc["totals"]["people_reported"]
    assert "Not CoStar data" in a["source"]


def test_routes_and_assignments_follow_what_each_geometry_supports():
    b = BeaconRig()
    box = box_for(b)
    door = next(d for d in b.rig.twin.data["doors"] if "r310" in d["rooms"])
    r1 = run(box.call("route", {"src": "RIT", "dst": "B1"}))
    assert r1["ok"] and r1["phone"] == "B1" and r1["target"].startswith("the phone itself")
    r2 = run(box.call("route", {"src": "RIT", "dst": "B2"}))
    assert r2["ok"] and r2["to_id"] == door["id"] and "search" in r2["target"] and "range only" in r2["target"]
    r3 = run(box.call("route", {"src": "RIT", "dst": "B3"}))
    assert r3["ok"] and "candidate" in r3["target"]
    r4 = run(box.call("route", {"src": "RIT", "dst": "B4"}))
    assert r4["ok"] and r4["to_id"] == door["id"] and "room only" in r4["target"]
    assert "safe outside" in run(box.call("route", {"src": "RIT", "dst": "B5"}))["error"]
    assert run(box.call("nearest_exit", {"src": "B1"}))["ok"]

    a = run(box.call("assign", {"unit": "RIT", "task": "rescue", "target": "B2"}))           # a ring: to the door, to search
    assert a["ok"], a
    end = b.rig.inc.units["RIT"]["route"]["polyline"][-1]
    assert abs(end[0] - door["at"][0]) < 1.5 and abs(end[1] - door["at"][1]) < 1.5
    a = run(box.call("assign", {"unit": "RIT", "task": "rescue", "target": "B1"}))           # a fix: to the phone
    end = b.rig.inc.units["RIT"]["route"]["polyline"][-1]
    assert a["ok"] and abs(end[0] - 2.5) < 0.6 and abs(end[1] - 2.0) < 0.6
    assert run(box.call("add_observation", {"text": "phone B1 here", "at": "B1"}))["ok"]


# ---------------------------------------------------------------- fallback policy

@pytest.mark.parametrize("data_shape", ["phone", "brief"])
@pytest.mark.parametrize("pid", ["B1", "B2"])
def test_fallback_policy_for_phone_and_phone_reached(tmp_path, pid, data_shape):
    b = BeaconRig(data_shape=data_shape)
    agent, llm = make_agent(b.rig, tmp_path)
    out = run(agent.handle(b.situation("phone", pid)))
    assert out["backend"] == "reflex-fallback"
    assert llm.requests[0][0] == agent.system_beacon and llm.requests[0][1][0]["content"].startswith("SITUATION: PHONE")
    rit = b.rig.inc.units["RIT"]
    assert rit["assignment"]["task"] == "rescue" and rit["route"]["valid"]
    radio = [m for m in b.rig.msgs if m.get("type") == "radio"]
    to_rit = next(m for m in radio if m["to"] == "RIT")["text"]
    assert ("search" in to_rit) == (pid == "B2") and ("trapped" in to_rit or "injured" in to_rit) and "None" not in to_rit
    assert ("ring around puck P1" in to_rit) == (pid == "B2") and ("within" in to_rit) == (pid == "B1")
    assert any(m["to"] == "ALL" and "not people" in m["text"] for m in radio)
    assert all(len(m["text"].split()) <= 45 for m in radio) and not any(re.search(r"\b(B\d|n\d{3}|r\d{3}|d\d{3})\b", m["text"]) for m in radio)

    b.rig.inc.move_unit("RIT", *rit["route"]["polyline"][-1])
    b.rig.step(1.0)
    out2 = run(agent.handle(b.situation("phone_reached", pid, {"rescuer": "RIT", "room": "r310"})))
    assert out2["backend"] == "reflex-fallback" and rit["assignment"]["task"] == "exit"
    assert [a["tool"] for a in out2["actions"]] == ["assign", "radio", "request_resource"]
    assert any(e["kind"] == "resource" and "EMS" in e["text"] for e in b.rig.inc.timeline)


def test_a_second_phone_does_not_pull_the_rescue_team_off_the_first(tmp_path):
    b = BeaconRig()
    agent, _ = make_agent(b.rig, tmp_path)
    run(agent.handle(b.situation("phone", "B1")))
    assert b.rig.inc.units["RIT"]["assignment"]["task"] == "rescue"
    first = b.rig.inc.units["RIT"]["route"]["polyline"][-1]
    out = run(agent.handle(b.situation("phone", "B2")))
    assert [a["tool"] for a in out["actions"]] == ["radio"] and b.rig.inc.units["RIT"]["route"]["polyline"][-1] == first


# ---------------------------------------------------------------- after-action hand-off

def test_beacon_after_action_has_the_accountability_hand_off_and_the_mayday_one_is_unchanged(tmp_path):
    b = BeaconRig()
    agent, llm = make_agent(b.rig, tmp_path)
    run(agent.handle(b.situation("phone", "B1")))
    llm.requests.clear()
    aar = run(agent.run_after_action())
    system, messages = llm.requests[0]
    user = messages[0]["content"]
    assert system == agent.system_beacon and "PHONES (opt-in" in user and "ACCOUNTABILITY" in user and '"handoff"' in user and "SCAN BRIEF" not in user
    assert aar["backend"] == "reflex-fallback" and aar["handoff"]["unaccounted"]["high"] >= aar["handoff"]["unaccounted"]["low"]
    assert aar["handoff"]["reported"] and aar["handoff"]["search_next"] and len(aar["accountability"]["suites"]) == 12

    agent.reset()
    b.rig.inc.reset()
    llm.requests.clear()
    aar2 = run(agent.run_after_action())
    system, messages = llm.requests[0]
    assert sha(system) == PINNED["SYSTEM_FULL"] and "PHONES" not in messages[0]["content"] and "handoff" not in aar2


def test_scan_and_phones_together_get_one_prompt_with_both(tmp_path):
    s = ScoutRig(style="numeric")
    b = load_sample()
    agent, llm = make_agent(s.rig, tmp_path)
    run(agent.handle(s.situation("scan", s.brief)))
    agent.tools.beacon_brief = brief_from_beacons(b)
    llm.requests.clear()
    run(agent.run_after_action())
    system, messages = llm.requests[0]
    assert system == agent.system_for(True, True) and "SCAN BRIEF" in messages[0]["content"] and "PHONES (opt-in" in messages[0]["content"]


# ---------------------------------------------------------------- determinism

def test_phone_prompts_are_deterministic(tmp_path):
    keys, views = [], []
    for jitter in (0.0, 0.04):
        bb = copy.deepcopy(load_sample())
        for p in bb["phones"]:                                         # centimetre noise underneath must not reach the prompt
            if p.get("x") is not None:
                p["x"], p["y"] = p["x"] + jitter, p["y"] - jitter
            if p.get("ring"):
                p["ring"]["r_m"] += jitter
            for c in p.get("candidates") or []:
                c["x"] += jitter
            p["report"]["battery_pct"] = (p["report"].get("battery_pct") or 50) - int(jitter * 100)   # batteries drain
            p["report"]["age_s"] = 1.0 + jitter * 50
        b = BeaconRig(bb)
        agent, llm = make_agent(b.rig, tmp_path)
        run(agent.handle(b.situation("phone", "B2")))
        system, messages = llm.requests[0]
        keys.append(request_key("main", system, messages, None))
        box = box_for(b, with_brief=False)                             # straight from the raw Beacons object on the incident: rounding happens in the tool
        views.append(json.dumps(run(box.call("get_phones", {})), sort_keys=True))
    assert keys[0] == keys[1] and views[0] == views[1]
    nums = [float(x) for x in re.findall(r'"(?:uncertainty_m|radius_m|weight|confidence|room_prob|prob)": ([0-9.]+)', views[0])]
    assert nums and all(abs(n * 10 - round(n * 10)) < 1e-9 for n in nums)


def test_a_phone_situation_raised_by_the_incident_itself(tmp_path):
    """With the server side's own `on_beacons` (when present): its brief and snapshot shapes reach the agent intact."""
    b = BeaconRig()
    inc = b.rig.inc
    if not hasattr(inc, "on_beacons"):
        pytest.skip("the incident does not relay Beacon objects yet")
    inc.beacons = None                                                 # only what the incident itself keeps
    inc.activate_beacon(True)
    inc.on_beacons(load_sample())
    sit = b.rig.situations[-1]
    assert sit["kind"] == "phone" and "phones" in sit["snapshot"]
    agent, llm = make_agent(b.rig, tmp_path)
    out = run(agent.handle(sit))
    assert llm.requests[0][0] == agent.system_beacon and out["backend"] == "reflex-fallback"
    first = sit["data"]["changed"][0]
    assert b.rig.inc.units["RIT"]["assignment"]["task"] == "rescue" and b.rig.inc.units["RIT"]["assignment"]["target"] == first
    g = run(agent.tools.call("get_phones", {}))
    ph = {p["id"]: p for p in g["phones"]}
    assert ph["B2"]["geometry"] == "ring" and "search" in ph["B2"]["crew_goes_to"] and ph["B1"]["report"]["people"] == 2
    radio = [m["text"] for m in b.rig.msgs if m.get("type") == "radio"]
    assert radio and not any("None" in t for t in radio)


def test_fallback_wording_says_what_each_geometry_is_and_never_a_missing_number():
    from sandtable.agent import CommandAgent
    w = CommandAgent._where_words
    assert w({"geometry": "fix", "uncertainty_m": 0.5, "where": "about 2.5 m from the west wall"}, "Suite 310") == "Located about 2.5 m from the west wall, within about 0.5 m"
    assert "within 0.5 m" in w({"geometry": "fix", "text": "Phone B1: UWB fix in Suite 310, within 0.5 m"}, "Suite 310")
    assert "None" not in w({"geometry": "fix"}, "Suite 310")
    assert w({"geometry": "candidates", "candidates": [{}, {}]}, "Suite 310").startswith("At one of 2 possible points in Suite 310")
    assert w({"geometry": "ring", "ring": {"puck": "P1"}}, "Suite 310").startswith("Somewhere on a ring around puck P1")
    assert w({"geometry": "zone"}, "Suite 310").startswith("In Suite 310, not yet pinned down")


def test_calls_next_to_a_null_final_are_run_not_dropped(tmp_path):
    """A live model once answered {"thought", "calls": [...], "final": null}: the calls must run and the loop go on."""
    b = BeaconRig()

    class Scripted(LLM):
        def __init__(self):
            super().__init__(backend="replay", record=False, cache_dir=tmp_path)
            self.replies = [json.dumps({"thought": "out", "calls": [{"tool": "assign", "args": {"unit": "RIT", "task": "exit", "target": "exit"}}], "final": None}),
                            json.dumps({"thought": "done", "final": {"summary": "RIT out by Stair B."}})]

        async def complete(self, system, messages, tier="main", image_path=None, image_sha=None):
            return self.replies.pop(0), {"source": "replay", "key": "x"}

    from sandtable.agent import CommandAgent
    agent = CommandAgent(b.rig.provider, b.rig.twin, b.rig.inc, b.rig.msgs.append, Scripted())
    out = run(agent.handle(b.situation("phone_reached", "B1", {"rescuer": "RIT", "room": "r310"})))
    assert out["backend"] == "replay" and [a["tool"] for a in out["actions"]] == ["assign"] and b.rig.inc.units["RIT"]["assignment"]["task"] == "exit"


# ---------------------------------------------------------------- the record against the phones (contract section 4), invented record

class _InventedCostar:
    mode = "live"

    def __init__(self, tenants, spaces):
        self.tenants, self.spaces = tenants, spaces

    def list_tenants(self, floor=None):
        return [t for t in self.tenants if floor is None or t.get("floor") == floor]

    def list_available_space(self, floor=None):
        return [a for a in self.spaces if floor is None or a.get("floor") == floor]


class _InventedProvider:
    """A live-mode record made up for the test: no real names, nothing read from a sponsor."""
    modes = {"costar": "live", "matterport": "fixture", "vexcel": "fixture"}

    def __init__(self):
        self.costar = _InventedCostar(
            tenants=[{"suite": "301", "floor": 3, "name": "Invented Clinic A", "employees": 10, "sf": 900, "life_hazard": "patients with limited mobility"},
                     {"suite": "999", "floor": 3, "name": "Invented Firm B", "employees": 20, "sf": 4000}],
            spaces=[{"suite": "310", "floor": 3, "sf": 880, "status": "for lease"}])

    def labels(self):
        return {"costar": {"mode": "live", "real_sponsor_data": True, "text": "Building record: CoStar (live)."}}


def test_live_record_against_phones_gives_against_record_headline_and_first_priority():
    from sandtable.beacon_link import accountability, record_vs_phones
    b = BeaconRig()
    acc = accountability(b.beacons, b.rig.inc, b.rig.twin, _InventedProvider())
    rows = {r["suite"]: r for r in acc["rows"] if r["suite"]}
    r310 = rows["310"]
    assert r310["scope"] == "scan" and r310["record"]["status"] == "available" and r310["state"] == "against-record" and r310["covered"]
    assert r310["phones_opt_in"] == 4 and r310["people_reported"] == 5 and r310["located"] == 1          # the ring counts in its puck's room; safe-outside does not
    assert "available space with no tenant" in r310["why"] and "5 people" in r310["why"] and r310["expected"]["basis"] == "no current tenant on the record"
    assert acc["priorities"][0]["suite"] == "310" and r310["priority"] == 1
    assert "Suite 310" in acc["headline"] and "5 people are in it" in acc["headline"]
    far = rows["999"]
    assert far["scope"] == "storey" and far["state"] == "not-covered" and far["room"] is None and far["covered"] is False
    assert far["unaccounted"] == {"low": 10, "high": 24} and far["expected"]["basis"] == "employees in the building record"
    assert rows["301"]["state"] == "not-covered" and rows["301"]["record"]["status"] == "tenant" and rows["301"]["record"]["employees"] == 10
    assert rows["303"]["record"]["status"] == "vacant"
    assert acc["storey"] == {"label": "3", "tenancies": 2, "in_scan": 1, "not_covered": 1, "expected_low": 10, "expected_high": 24}     # the "same storey, no pucks" group
    assert "2 tenancies on storey 3 have no puck: about 15 to 36 people" in acc["headline"]                                       # every uncovered tenancy
    assert acc["totals"]["against_record_people"] == 5 and acc["totals"]["not_covered_suites"] == 2
    assert acc["source"]["real_sponsor_data"] is True and acc["source"]["text"] == "Building record: CoStar (live)."
    d = record_vs_phones(acc)
    assert d == {"id": "record_vs_phones", "kind": "record_vs_phones", "source": ["costar", "beacon"], "text": acc["headline"]}


def test_live_accountability_tool_gives_headline_storey_and_counts_only():
    b = BeaconRig()
    box = ToolBox(b.rig.provider, b.rig.twin, b.rig.inc, LLM(backend="replay"))
    box.provider = _InventedProvider()
    box.snapshot, box.beacon_brief = b.snapshot(), b.brief
    a = run(box.call("get_accountability", {}))
    assert a["headline"].startswith("The record lists Suite 310") and a["storey"]["not_covered"] == 1
    text = json.dumps(a)
    assert "Invented Clinic A" not in text and "Invented Firm B" not in text                          # counts only, no tenant names
    assert {r["suite"]: r["state"] for r in a["suites"] if r["suite"]}["310"] == "against-record"


def test_fixture_accountability_has_no_live_keys_and_is_never_below_the_incident():
    from sandtable.beacon_link import accountability, closing_line, record_vs_phones
    b = BeaconRig()
    lagging = copy.deepcopy(b.beacons)
    row = next(r for r in lagging["accountability"]["suites"] if r["suite"] == "310")
    row.update({"phones_opt_in": 1, "people_reported": 1, "located": 0})                              # a story's frozen table, behind the phones
    inc = b.rig.inc
    inc.activate_beacon(True)
    inc.on_beacons(lagging)
    acc = accountability(lagging, inc, b.rig.twin, b.rig.provider)
    assert "headline" not in acc and "storey" not in acc and all("scope" not in r for r in acc["rows"]) and record_vs_phones(acc) is None
    r310 = next(r for r in acc["rows"] if r["suite"] == "310")
    held = [q for q in inc.phones.values() if q.get("room") == "r310"]
    assert r310["phones_opt_in"] >= len(held) and r310["people_reported"] >= sum(int(q.get("people") or 1) for q in held) == 5 and r310["located"] >= 1
    assert acc["totals"]["people_reported"] >= 5
    line = closing_line(inc)
    assert line.startswith("All crews out. Reported people out: 0 of 5. Not searched: ") and "Unaccounted on the record:" in line and "complete" not in line.lower()


def test_tools_ignore_unknown_arguments_with_one_fixed_phrase():
    b = BeaconRig()
    box = box_for(b)
    r = run(box.call("radio", {"to": "ALL", "text": "All units from Command: test.", "priority": "routine", "priority_note": "secret-value"}))
    assert r["ok"] and r["note"] == "unrecognised arguments were ignored" and "secret-value" not in json.dumps(r) and "priority_note" not in json.dumps(r)
    assert "error" in run(box.call("radio", {"to": "ALL"}))                                            # a missing argument is still an error


def test_cold_band_text_says_the_band_only():
    from sandtable.scout.feeds import band_text
    assert band_text({"count": 0, "count_range": [1, 1], "room_label": "Suite 310"}) == "1 device heard in Suite 310, not a person."
    assert "about 0" not in band_text({"count": 0, "count_range": [0, 2]}).lower()


def test_live_record_with_an_unnamed_occupancy_says_it_does_not_know_who_and_is_not_against_record():
    from sandtable.beacon_link import accountability, record_vs_phones
    prov = _InventedProvider()
    prov.costar.spaces = []
    prov.costar.tenants.append({"suite": "310", "floor": 3, "sf": 880})                     # an occupancy entry: no name, no head count
    b = BeaconRig()
    acc = accountability(b.beacons, b.rig.inc, b.rig.twin, prov)
    r310 = next(r for r in acc["rows"] if r["suite"] == "310")
    assert r310["record"]["status"] == "unnamed" and r310["record"]["tenants"] == 0
    assert r310["record"]["text"] == "1 occupancy entry on record, no tenant named, no head count"
    assert r310["expected"] == {"people": None, "low": None, "high": None, "basis": "an occupancy entry on record with no tenant named and no head count"}
    assert r310["state"] == "people-reported" and r310["priority"] == 1 and acc["priorities"][0]["suite"] == "310"
    assert r310["why"] == "the record has an occupancy entry for this suite but names no tenant and gives no head count; 4 phones report 5 people in it"
    assert acc["headline"].startswith("The record does not say who is in Suite 310 (an occupancy entry with no tenant named and no head count); the phones say 5 people are in it.")
    assert acc["totals"]["against_record_people"] == 0 and not any(r["state"] == "against-record" for r in acc["rows"])
    assert record_vs_phones(acc)["text"] == acc["headline"]
