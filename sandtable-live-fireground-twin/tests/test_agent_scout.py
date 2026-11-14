"""Command agent in scout mode: the recorded prompts are untouched, the scan tools work on the sample scan,
the fallback policy covers `scan` and `victim_reached`, and brief-based prompts are deterministic.

The brief is built here from fixtures/scout/sample_scan.json in the shape docs/SCOUT_CONTRACT.md 2.3 describes
(room ids and labels, victim room and node, positions to 0.5 m, confidences to 0.1, whole breaths per minute),
so these tests do not depend on the session code. The Rig of test_incident is used by composition only.
"""
import asyncio
import hashlib
import json
import re

import pytest

from sandtable import config
from sandtable.agent import CommandAgent, prompts
from sandtable.agent.llm import LLM, request_key
from sandtable.agent.tools import ACTION_TOOLS, CATALOG, SCOUT_CATALOG, ToolBox, catalog_text

from test_incident import Rig, run_to_mayday, start_operations

SAMPLE = config.FIXTURES / "scout" / "sample_scan.json"

# sha256 of every prompt text the recorded runs in fixtures/llm_cache/ were made with. If one of these
# fails, a recorded run no longer matches: undo the change (scout runs have their own prompts).
PINNED = {
    "SYSTEM_FULL": "1591b7b06fb6e07a1e5b7db1e8dbef043adefdffee28a256e30c03a9ed9ea914",
    "SYSTEM": "06bdc7fc48fa583ca5abf933c1af87e2be5764a63f88ecf1b53fa7822d0c975f",
    "CATALOG": "4bd9fe05d85c855c40bbd58d05645772556df7c2ce6fc657a08435f8cbbd4a92",
    "PREPLAN": "85f17d8d3241aef23c51f8c44734e1c9def2d6c4b5ed55817207c8ea0edf116a",
    "mayday": "29371f58fa2a50be7de7bfe2811cfcd0abbeb4f5a6d0e4d0a3315b4a93674a9e",
    "hazard": "520fb5bd4ac89d250c5144b445ca005857ce2e35258348ec4f8a07814573e892",
    "contact": "ed6751f8fd1b582dee25c551173364a23ef0bc8e7a8f8162282191e3ef6fcf85",
    "AAR": "e8eade709064b1a423e6138b079c99158a807831760bf6b766f23ee89e852294",
    "REPAIR": "9acb1514ccaf972037a6dced19560d9a67ea34039c88b40be399dd98207560e4",
    "NEED_ACTION": "bf3f465bdbb615be191ca331d2e834dac43cb4ab4421b11c35de78c6b99edb50",
}


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def run(coro):
    return asyncio.run(coro)


def half(v):
    return round(float(v) * 2) / 2


def load_sample() -> dict:
    return json.loads(SAMPLE.read_text())


def brief_from_scan(scan: dict, twin) -> dict:
    """A brief with numbers, in the terms of contract 2.3 (positions to 0.5 m, confidences to 0.1, whole bpm)."""
    victims = []
    for v in scan["victims"]:
        loc = twin.locate(v["x"], v["y"])
        victims.append({"id": v["id"], "room": loc["room"], "room_label": loc["room_label"], "node": loc["node"], "x": half(v["x"]), "y": half(v["y"]),
                        "sigma_m": half(v["sigma_m"]), "confidence": round(v["confidence"], 1), "state": v["state"], "moving": v["moving"],
                        "breathing_bpm": round(v["breathing_bpm"]) if v.get("breathing_bpm") is not None else None, "n_links": v["n_links"], "text": v["text"]})
    mism = [{"id": m["id"], "kind": m["kind"], "confidence": round(m["confidence"], 1), "at": [half(m["where"]["at"][0]), half(m["where"]["at"][1])],
             "radius_m": half(m["where"]["radius_m"]), "text": m["text"]} for m in scan["mismatches"]]
    return {"scan": scan["id"], "simulated": scan["simulated"], "sample": bool(scan.get("sample")), "floor": scan["floor"], "room": scan["room"],
            "room_label": scan["room_label"], "rung": scan["rung"], "n_pucks": scan["n_pucks"],
            "registration": {"ok": scan["registration"]["ok"], "method": scan["registration"]["method"],
                             "residual_m": round(scan["registration"]["residual_m"], 1), "ambiguity": round(scan["registration"]["ambiguity"], 1)},
            "victims": victims, "mismatches": mism, "limits": scan["limits"]}


def words_brief_from_scan(scan: dict, twin) -> dict:
    """A brief in words and text with no coordinates (the shape the session builder writes at the time of writing:
    confidence words and percents, "where" text, uncertainty text, pucks as a count). Positions then come from the
    whole Scan kept on the incident."""
    word = lambda c: "high" if c >= 0.7 else "medium" if c >= 0.4 else "low"
    pct = lambda c: int(round(c * 10)) * 10
    victims = []
    for v in scan["victims"]:
        loc = twin.locate(v["x"], v["y"])
        hx, hy = half(v["x"]), half(v["y"])
        victims.append({"id": v["id"], "room": loc["room"], "room_label": loc["room_label"], "node": loc["node"], "state": v["state"], "moving": v["moving"],
                        "breathing_bpm": round(v["breathing_bpm"]), "confidence": word(v["confidence"]), "confidence_pct": pct(v["confidence"]),
                        "where": f"{hx:.1f} m from the west wall, {hy:.1f} m from the south wall", "at": f"x {hx:.1f} m, y {hy:.1f} m (twin frame)",
                        "uncertainty": f"about {max(0.5, half(v['sigma_m'])):.1f} m"})
    mism = [{"id": m["id"], "kind": m["kind"], "where": "2.5 m from the north wall", "means": "an echo where the twin shows open floor",
             "confidence": word(m["confidence"]), "confidence_pct": pct(m["confidence"])} for m in scan["mismatches"]]
    return {"scan": scan["id"], "floor": scan["floor"], "room": scan["room"], "room_label": scan["room_label"], "simulated": scan["simulated"],
            "sample": bool(scan.get("sample")), "rung": scan["rung"], "pucks": scan["n_pucks"],
            "registration": {"ok": True, "method": scan["registration"]["method"], "confidence": word(1 - scan["registration"]["ambiguity"])},
            "outline": scan["outline"]["source"], "victims": victims, "mismatches": mism, "limits": scan["limits"]}


class ScoutRig:
    """The incident Rig plus a scan, by composition: E1 at the door of the scanned room, a brief, and situations
    shaped like the ones the incident raises (data = brief, frozen snapshot carrying a compact scan).
    style "numeric": the brief carries positions, the incident knows nothing of the scan.
    style "words": the brief is words and text, the whole Scan sits on the incident as `scan` (contract 2.3)."""

    def __init__(self, scan: dict | None = None, style: str = "numeric"):
        self.rig = Rig()
        self.rig.step(1.0)
        start_operations(self.rig)
        self.scan = scan or load_sample()
        self.style = style
        if style == "words":
            self.brief = words_brief_from_scan(self.scan, self.rig.twin)
            self.rig.inc.scan = self.scan
        else:
            self.brief = brief_from_scan(self.scan, self.rig.twin)
        door = next(d for d in self.rig.twin.data["doors"] if self.scan["room"] in d["rooms"])
        self.rig.inc.assign("E1", "stage", door["id"], source="test")
        self.rig.inc.drive("E1")
        self.rig.step(30)

    def snapshot(self) -> dict:
        snap = self.rig.inc.snapshot()
        snap.setdefault("scan", {"id": self.brief["scan"], "room": self.brief["room"], "rung": self.brief["rung"],
                                 "victims": [{"id": v["id"], "room": v["room"], "state": v["state"]} for v in self.brief["victims"]],
                                 "mismatches": len(self.brief["mismatches"])})
        return snap

    def situation(self, kind: str, data: dict, unit=None) -> dict:
        return {"seq": 1, "kind": kind, "unit": unit, "data": data, "snapshot": self.snapshot()}


class CaptureLLM(LLM):
    """Replay backend over an empty cache that remembers every request it was asked for."""

    def __init__(self, cache_dir):
        super().__init__(backend="replay", record=False, cache_dir=cache_dir)
        self.requests: list[tuple[str, list[dict]]] = []

    async def complete(self, system, messages, tier="main", image_path=None, image_sha=None):
        self.requests.append((system, json.loads(json.dumps(messages))))
        return await super().complete(system, messages, tier, image_path, image_sha)


def make_agent(rig, cache_dir):
    llm = CaptureLLM(cache_dir)
    return CommandAgent(rig.provider, rig.twin, rig.inc, rig.msgs.append, llm), llm


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(config, "REPLAY_PACE_S", 0.0)


# ---------------------------------------------------------------- the recorded prompts are untouched

def test_existing_prompts_are_byte_identical_to_the_recorded_ones():
    legacy_system = prompts.SYSTEM.replace("{catalog}", catalog_text())
    now = {"SYSTEM_FULL": legacy_system, "SYSTEM": prompts.SYSTEM, "CATALOG": catalog_text(), "PREPLAN": prompts.PREPLAN, "AAR": prompts.AAR,
           "REPAIR": prompts.REPAIR, "NEED_ACTION": prompts.NEED_ACTION, **{k: prompts.SITUATIONS[k] for k in ("mayday", "hazard", "contact")}}
    assert {k: sha(v) for k, v in now.items()} == PINNED
    assert catalog_text(CATALOG) == catalog_text() and "get_scan" not in legacy_system

    files = sorted(config.LLM_CACHE_DIR.glob("*.json"))
    if not files:
        pytest.skip("no recorded model responses in fixtures/llm_cache yet")
    headers = {"TASK: pre-incident plan": prompts.PREPLAN, "SITUATION: MAYDAY": prompts.SITUATIONS["mayday"],
               "SITUATION: PART OF THE FLOOR IS LOST": prompts.SITUATIONS["hazard"], "SITUATION: CONTACT": prompts.SITUATIONS["contact"]}
    checked = 0
    for f in files:
        c = json.loads(f.read_text())
        first = c["request"]["messages"][0]["content"]
        if c["request"]["image_sha"]:                                   # the vision call has its own small system prompt
            continue
        if not any(first.startswith(h) for h in list(headers) + ["TASK: after-action report"]):
            continue                                                    # a scout recording (scan, victim_reached, scout after-action)
        if "SCAN BRIEF" in first or "PHONES (opt-in" in first or "SITUATION: SCAN" in first or "SITUATION: VICTIM" in first:
            continue
        assert sha(c["request"]["system"]) == PINNED["SYSTEM_FULL"], f.name
        if first.startswith("TASK: after-action report"):
            pieces = re.split(r"\{log\}|\{hazards\}|\{checks\}", prompts.AAR)
            pos = 0
            for p in pieces:
                i = first.find(p, pos)
                assert i >= pos, (f.name, p[:40])
                pos = i + len(p)
            assert first.endswith(pieces[-1])
        else:
            template = next(t for h, t in headers.items() if first.startswith(h))
            if "{situation}" in template:
                head, tail = template.split("{situation}")
                assert first.startswith(head) and first.endswith(tail), f.name
            else:
                assert first == template, f.name
        checked += 1
    assert checked >= 20


def test_mayday_exercise_runs_keep_the_original_system_prompt_and_tool_results(tmp_path):
    rig = Rig()
    rig.step(1.0)
    run_to_mayday(rig)
    agent, llm = make_agent(rig, tmp_path)
    run(agent.handle(rig.situations[-1]))
    assert llm.requests and sha(llm.requests[0][0]) == PINNED["SYSTEM_FULL"]
    head, tail = prompts.SITUATIONS["mayday"].split("{situation}")
    assert llm.requests[0][1][0]["content"].startswith(head)
    box = ToolBox(rig.provider, rig.twin, rig.inc, LLM(backend="replay"))
    box.snapshot = rig.inc.snapshot()
    r = run(box.call("route", {"src": "RIT", "dst": "E1"}))
    assert set(r) == {"ok", "length_m", "from", "to", "to_id", "via", "steps", "avoiding"}       # no scout keys without a scan
    assert box.scan_view() is None and run(box.call("get_scan", {}))["scan"] is None


# ---------------------------------------------------------------- scout tools on the sample scan

def test_scout_catalogue_extends_the_original_one():
    names = [c[0] for c in SCOUT_CATALOG]
    assert set(names) == {c[0] for c in CATALOG} | {"get_scan"} and "get_scan" not in ACTION_TOOLS
    sys_scout = prompts.SYSTEM_SCOUT.replace("{catalog}", catalog_text(SCOUT_CATALOG))
    assert "get_scan" in sys_scout and "never clears a room" in sys_scout and "V1" in sys_scout
    assert sys_scout != prompts.SYSTEM.replace("{catalog}", catalog_text())


@pytest.mark.parametrize("source", ["numeric_brief", "words_brief", "full_scan"])
def test_get_scan_route_assign_and_pins_on_the_sample(source):
    s = ScoutRig(style="words" if source == "words_brief" else "numeric")
    box = ToolBox(s.rig.provider, s.rig.twin, s.rig.inc, LLM(backend="replay"))
    if source == "full_scan":                                           # only the whole Scan object is known
        box.snapshot = {**s.rig.inc.snapshot(), "scan": s.scan}
    else:
        box.scan_brief, box.snapshot = s.brief, s.snapshot()
    g = run(box.call("get_scan", {}))
    assert g["room_label"] == "Suite 310" and g["rung"] == "cir-radar" and g["pucks"] == 5 and g["simulated"] and g["sample"]
    assert g["registration"]["method"] == "twin-echo-match" and g["registration"].get("ambiguity", 0.2) == 0.2
    v = g["victims"][0]
    assert v["id"] == "V1" and v["breathing_bpm"] == 14 and v["confidence"] == 0.9 and v["uncertainty_m"] in (0.5, "about 0.5 m") and v["state"] == "breathing"
    assert v["where"] and isinstance(v["from_door_m"], int)
    assert len(g["flags"]) == 1 and g["flags"][0]["id"] == "M1" and g["flags"][0]["confidence"] == 0.7 and g["flags"][0]["means"]
    assert s.scan["limits"][1] in g["limits"]
    assert any("never clears a room" in c for c in g["cannot_tell"]) and any("hand-made sample" in c for c in g["cannot_tell"])
    assert "breathing rate" in " ".join(g["can_tell"])

    r = run(box.call("route", {"src": "RIT", "dst": "V1"}))
    assert r["ok"] and r["victim"] == "V1" and r["to"] == "Suite 310" and isinstance(r["passes_flags"], list)
    assert run(box.call("route", {"src": "RIT", "dst": "M1"}))["passes_flags"] == ["M1"]
    ex = run(box.call("nearest_exit", {"src": "V1"}))
    assert ex["ok"] and ex["to_id"] in {e["id"] for e in s.rig.twin.exits()}
    a = run(box.call("assign", {"unit": "RIT", "task": "rescue", "target": "V1"}))
    assert a["ok"], a
    rit = s.rig.inc.units["RIT"]
    end = rit["route"]["polyline"][-1]
    assert rit["assignment"]["task"] == "rescue" and abs(end[0] - s.scan["victims"][0]["x"]) < 0.6 and abs(end[1] - s.scan["victims"][0]["y"]) < 0.6
    for at in ("M1", "V1"):
        o = run(box.call("add_observation", {"text": f"test pin at {at}", "at": at}))
        assert o["ok"]
    tags = {t["label"]: t for t in s.rig.provider.matterport.list_tags() if str(t.get("id", "")).startswith("obs-")}
    pos = tags["test pin at M1"]["position"]
    assert abs(pos[0] - 3.5) < 0.6 and abs(pos[1] - 6.0) < 0.6
    assert run(box.call("route", {"src": "RIT", "dst": "V9"}))["error"]


def test_a_two_puck_scan_says_it_cannot_place_anyone():
    scan = load_sample()
    scan["n_pucks"] = 2
    scan["victims"] = []
    s = ScoutRig(scan)
    box = ToolBox(s.rig.provider, s.rig.twin, s.rig.inc, LLM(backend="replay"))
    box.scan_brief = s.brief
    g = run(box.call("get_scan", {}))
    assert g["victims"] == [] and any("range only" in c for c in g["cannot_tell"])


# ---------------------------------------------------------------- fallback policy

@pytest.mark.parametrize("style", ["numeric", "words"])
def test_fallback_policy_for_scan_and_victim_reached(tmp_path, style):
    s = ScoutRig(style=style)
    agent, llm = make_agent(s.rig, tmp_path)                            # empty cache: every model call misses
    out = run(agent.handle(s.situation("scan", s.brief, unit="E1")))
    assert out["backend"] == "reflex-fallback"
    assert llm.requests and llm.requests[0][0] == agent.system_scout and llm.requests[0][1][0]["content"].startswith("SITUATION: SCAN RECEIVED")
    rit = s.rig.inc.units["RIT"]
    assert rit["assignment"]["task"] == "rescue" and rit["route"]["valid"]
    tools = [a["tool"] for a in out["actions"]]
    assert tools[0] == "assign" and "radio" in tools and "add_observation" in tools
    radio = [m for m in s.rig.msgs if m.get("type") == "radio"]
    to_all = [m for m in radio if m["to"] == "ALL"]
    assert to_all and "not cleared" in to_all[0]["text"] and "Sample scan, not a radar result" in to_all[0]["text"]      # the sample is labelled as such
    assert all(len(m["text"].split()) <= 45 for m in radio) and not any(re.search(r"\b(V1|M1|n\d{3}|r\d{3})\b", m["text"]) for m in radio)
    events = [m for m in s.rig.msgs if m.get("type") == "agent" and m["run"].startswith("scan")]
    assert any(e["kind"] == "error" and e["backend"] == "reflex-fallback" for e in events)
    assert all(e["backend"] == "reflex-fallback" for e in events if e["kind"] in ("action", "tool_result", "final"))

    s.rig.inc.move_unit("RIT", *rit["route"]["polyline"][-1])         # the rescue team has reached the victim
    s.rig.step(1.0)
    out2 = run(agent.handle(s.situation("victim_reached", {"rescuer": "RIT", "victim": "V1", "room": "r310"}, unit="RIT")))
    assert out2["backend"] == "reflex-fallback"
    assert rit["assignment"]["task"] == "exit" and rit["route"]["valid"]
    tools2 = [a["tool"] for a in out2["actions"]]
    assert tools2 == ["assign", "radio", "request_resource"]
    assert any(e["kind"] == "resource" and "EMS" in e["text"] for e in s.rig.inc.timeline)
    stair = rit["route"]["rooms"][-1]
    assert any(m.get("type") == "radio" and m["to"] == "ALL" and s.rig.twin.room(stair)["label"] in m["text"] for m in s.rig.msgs)


def test_scout_after_action_report_has_its_own_prompt_and_the_mayday_one_is_unchanged(tmp_path):
    s = ScoutRig()
    agent, llm = make_agent(s.rig, tmp_path)
    run(agent.handle(s.situation("scan", s.brief)))
    llm.requests.clear()
    aar = run(agent.run_after_action())
    system, messages = llm.requests[0]
    assert system == agent.system_scout and "SCAN BRIEF" in messages[0]["content"] and '"rung": "cir-radar"' in messages[0]["content"]
    assert aar["backend"] == "reflex-fallback" and aar["scan"]["brief"]["victims"][0]["id"] == "V1"
    assert aar["scan"]["contributed"] and any("never clears a room" in c for c in aar["scan"]["could_not_tell"])

    agent.reset()                                                       # a new incident without a scan: the recorded prompt, byte for byte
    s.rig.inc.reset()
    llm.requests.clear()
    aar2 = run(agent.run_after_action())
    system, messages = llm.requests[0]
    assert sha(system) == PINNED["SYSTEM_FULL"] and "SCAN BRIEF" not in messages[0]["content"] and "scan" not in aar2
    assert messages[0]["content"].startswith(prompts.AAR.split("{log}")[0])


# ---------------------------------------------------------------- determinism

def test_brief_based_prompts_are_deterministic(tmp_path):
    keys, views = [], []
    for jitter, style in ((0.0, "words"), (0.03, "words"), (0.0, "numeric"), (0.03, "numeric")):
        scan = load_sample()
        for v in scan["victims"]:                                       # centimetre noise underneath must not reach the prompt
            v["x"], v["y"], v["confidence"] = v["x"] + jitter, v["y"] - jitter, v["confidence"] + jitter / 10
        for m in scan["mismatches"]:
            m["where"]["at"] = [m["where"]["at"][0] + jitter, m["where"]["at"][1] + jitter]
        s = ScoutRig(scan, style)
        agent, llm = make_agent(s.rig, tmp_path)
        run(agent.handle(s.situation("scan", s.brief, unit="E1")))
        system, messages = llm.requests[0]
        keys.append(request_key("main", system, messages, None))
        box = ToolBox(s.rig.provider, s.rig.twin, s.rig.inc, LLM(backend="replay"))
        box.snapshot = {**s.rig.inc.snapshot(), "scan": scan}       # straight from the raw scan: rounding happens in the tool
        views.append(json.dumps(run(box.call("get_scan", {})), sort_keys=True))
    assert keys[0] == keys[1] and keys[2] == keys[3] and views[0] == views[1] == views[2] == views[3]

    def floats(o):
        if isinstance(o, float):
            yield o
        elif isinstance(o, dict):
            for x in o.values():
                yield from floats(x)
        elif isinstance(o, list):
            for x in o:
                yield from floats(x)
    assert all(abs(f * 10 - round(f * 10)) < 1e-9 for f in floats(json.loads(views[0])))     # tenths and half metres only
