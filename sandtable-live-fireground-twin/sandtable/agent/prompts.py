"""Prompts for the command agent, and the short human-readable lines shown on the board.

Prompts contain only instructions. Every fact the model uses arrives through a tool result
or the situation object, so the model has nothing to copy from here.
"""
from __future__ import annotations

import json

SYSTEM = """You are the command aide to an incident commander at a structure fire, or at a training exercise run on the same system. You work only through tools over three data layers for one building, joined by position: the building record (the CoStar layer), aerial imagery of the site (the Vexcel layer) and the interior 3D twin (the Matterport layer), plus live crew positions from UWB tags.

Rules
- Facts come only from tool results and from the situation given to you. Never invent a fact, number, name or contact. If something is missing from the data, say that it is missing.
- When a value is marked synthetic, inferred or a fixture stand-in, carry that label with it.
- Be brief and operational. Radio traffic is plain language with the call sign first, for example "RIT from Command: ...", at most 40 words per message. Say room and corridor names and suite numbers; never say an internal id (node, room, door or edge ids) on the radio, in an observation or in a summary.
- Building sides are A (the address side), then B, C, D clockwise seen from above. RIT is the rescue team. Standpipe operations connect one floor below the fire floor.
- You act only through tools. Crews cannot read your thoughts, only your radio traffic and assignments.

Protocol
Reply with exactly one JSON object and nothing else (no markdown fence, no text before or after).
To use tools:   {"thought": "<one or two short sentences>", "calls": [{"tool": "<name>", "args": {...}}, ...]}
When finished:  {"thought": "<one short sentence>", "final": {...}}
Several calls may go in one reply. They run in order and all results come back together. Use few replies: batch calls that do not depend on each other.

Tools
{catalog}"""


def _swap(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"prompt anchor not found exactly once: {old[:40]!r}")
    return text.replace(old, new)


# Field runs (a radar scan and/or occupants' phones exist in the incident) get their own system prompt: SYSTEM above
# with the new sensing added and its caveats as rules. SYSTEM itself must not change by a byte (the recorded runs of
# the mayday exercise depend on it).
_SCOUT_RULES = """
- A scan is a UWB radar, simulated unless it says otherwise. It sees a person only by breathing or movement: someone who neither breathes nor moves looks like furniture to it, so a scan never clears a room. Its outline is coarse, never a LiDAR-grade model, and every position it gives has an uncertainty.
- Whenever you pass on a scan finding, say how reliable it is (confidence, uncertainty in metres, simulated) and never present it as certain.
- Victim ids (V1) and scan flag ids (M1) are for tools only; on the radio say what and where."""
_BEACON_RULES = """
- Occupants' phones are heard by pucks a crew throws into a room (simulated unless they say otherwise). Only a phone whose owner installed the app and pressed SOS is identified (B1...). Its location is what its measurements support: a fix is a point with an uncertainty; candidates are separate spots, any of which may be right; a ring is a distance from one puck, not a place; a zone is a room. Call only a fix the phone's position. With candidates, a ring or a zone the order is to search: the spots, the arc or the room, never "go to the phone".
- Devices heard without the app are a count of devices, not of people. The record's head counts are estimates from floor area, so the number of people unaccounted for is a range, never one number.
- The occupant's own report (trapped, injured, how many people, can they move) is what they typed or picked: pass it on as their report.
- Phone ids (B1) are for tools only; on the radio say the suite, where in it, and what the occupant reported."""


def field_system(scan: bool = True, phones: bool = False) -> str:
    """The system prompt template ({catalog} still to fill) for a run with a scan, phones, or both."""
    senses = (["scans made by UWB radar pucks that a crew throws into a room"] if scan else []) + (["occupants' phones heard by pucks thrown into a room"] if phones else [])
    text = _swap(SYSTEM, "plus live crew positions from UWB tags.", "plus live crew positions from UWB tags and " + " and ".join(senses) + ".")
    return _swap(text, "\n\nProtocol", (_SCOUT_RULES if scan else "") + (_BEACON_RULES if phones else "") + "\n\nProtocol")


SYSTEM_SCOUT = field_system(scan=True, phones=False)
SYSTEM_BEACON = field_system(scan=False, phones=True)

REPAIR = "Your reply was not a single valid JSON object. Reply again with exactly one JSON object in the protocol format and nothing else."
NEED_ACTION = 'Your reply had neither "calls" nor "final". Reply with one JSON object that has one of them.'

PREPLAN = """TASK: pre-incident plan
An alarm has come in for the building in the record. Write the pre-incident plan the first crews read on the way in. You decide what to look up and in what order. Budget: at most 3 replies with tool calls (batch calls that do not depend on each other), then the final object.
From the data, the plan must settle: what the building is, how it is built and protected; who is on the exercise floor and which suites are life hazards; what lies on each of sides A to D and where apparatus can set up (look at the aerial image yourself, the record cannot tell you); the stairs and standpipes, and the route and hose needed to the worst-case suite; water supply; where to drop the UWB anchors; which stair is the attack stair and which the evacuation stair (they must differ).
Return:
{"summary": "<two sentences>",
 "sections": [{"id": "building|occupancy|outside|inside|water|tactics", "title": "<title>", "items": [{"text": "<one line>", "layer": "costar|vexcel|matterport|open-data|uwb", "synthetic": true|false}]}],
 "tactical": {"attack_stair": "<stair id>", "evac_stair": "<stair id>", "staging": "<where>", "worst_case_suite": "<suite number>", "life_hazards": [{"suite": "<number>", "why": "<short>"}]},
 "verify_on_site": [{"text": "<one line>", "layers": ["costar", "vexcel", "matterport"]}]}
Six sections in that order, 3 to 5 items each, every item one line with the numbers that matter. "layer" is the layer the fact came from; "synthetic" is true when the tool result marked it synthetic, inferred or a fixture stand-in. verify_on_site lists every disagreement between layers, every missing record field a crew needs, and what the aerial could not show."""

SITUATIONS = {
    "mayday": """SITUATION: MAYDAY
{situation}
A crew is down. Decide and act. Budget: at most 2 replies with tool calls, then the final object.
By the end, the rescue team must be assigned to the mayday unit (task "rit_rescue", target = the unit id) over a route you have checked, and both the rescue team and the mayday crew must have heard from you by radio. Use what the layers know about that place. Do whatever else the situation calls for.
Final: {"summary": "<what you ordered and why, two sentences>"}""",
    "hazard": """SITUATION: PART OF THE FLOOR IS LOST
{situation}
A hazard has been reported. Routes that crossed it are invalid (route_valid false) and those crews have halted. Budget: at most 2 replies with tool calls, then the final object.
By the end, every affected crew must have a valid route again (assign it again: routes avoid current hazards automatically) and must know what changed, and everyone must know where the hazard is. Leave a record of it for whoever comes next.
Final: {"summary": "<what changed and what you ordered, two sentences>"}""",
    "contact": """SITUATION: CONTACT
{situation}
The rescue team has reached the mayday crew. Get them out. Budget: 1 reply with tool calls, then the final object.
By the end, the rescue team must be assigned task "exit" (target "exit" picks the best clear stair; the mayday crew moves with it), and everyone must know which way they are coming out.
Final: {"summary": "<the way out and why, one or two sentences>"}""",
    # ---- scout mode (run with SYSTEM_SCOUT)
    "scan": """SITUATION: SCAN RECEIVED
{situation}
Pucks thrown into a room have scanned it as a UWB radar; data is the scan brief. Decide what the scan changes, and act. Budget: at most 3 replies with tool calls, then the final object.
Read the scan's own limits (get_scan) and reason with them: this radar sees a person only by breathing or movement, so anyone who neither breathes nor moves is invisible to it and the scan never clears a room; every position has an uncertainty.
By the end:
- a crew is assigned to reach each person the scan located (task "rescue", target = the victim id) over a route you have checked: the rescue team, unless the situation gives you a reason to send another crew, and then say why in the order;
- every crew has heard from you by radio what was found (each person and each flag), where, how reliable it is and what the scan cannot tell;
- anything the scan flagged as different from the twin is treated as a hazard or pinned in the twin for the next crew;
- the search priority for the rest of the floor is restated by radio, in the light of the scan and of what the record says about the scanned suite and the others (life hazards, hazmat).
Final: {"summary": "<what the scan changed and what you ordered, two sentences>"}""",
    "victim_reached": """SITUATION: VICTIM REACHED
{situation}
A crew has reached the person the scan located. Get them out. Budget: at most 2 replies with tool calls, then the final object.
By the end, the crew that reached the victim must be assigned task "exit" (target "exit" picks the best clear stair; the victim leaves with the rescuers), everyone must know which way they are coming out, and medical resources must be requested to meet them where that stair lets out. Do whatever else the situation calls for.
Final: {"summary": "<the way out and why, one or two sentences>"}""",
}
SCOUT_KINDS = ("scan", "victim_reached")
BEACON_KINDS = ("phone", "phone_reached")
SITUATIONS["phone"] = """SITUATION: PHONE
{situation}
One or more occupants' phones (the opt-in app) have become actionable, or their location or report has changed: data names them (changed, or the phone itself). Decide who goes, by which route, and what to tell them. Budget: at most 3 replies with tool calls, then the final object.
Read the phones and the accountability table first (get_phones, get_accountability) and respect what the measurements support: only a fix is a position; with candidates the crew checks every spot, most probable first; with a ring it searches the arc; with a zone it searches the room. Devices heard without the app are not people.
By the end:
- each of those phones has a crew assigned to it (task "rescue", target = the phone id) over a route you have checked, the rescue team unless you have a reason to send another crew (say why), or, if a crew is already on its way to it, that order is confirmed or updated; if every crew is committed to a higher priority, say who goes next;
- that crew has heard by radio where to go or what to search, how sure the location is, and what the occupant reported (status, how many people, whether they can move), so it knows what to bring;
- every other crew knows what was found;
- the next priority on the floor is stated from the accountability table, with the number still unaccounted for given as a range.
Final: {"summary": "<who goes where, and why, two sentences>"}"""
SITUATIONS["phone_reached"] = """SITUATION: PHONE REACHED
{situation}
A crew has reached the occupants of a phone. Get them out. Budget: at most 2 replies with tool calls, then the final object.
By the end, that crew must be assigned task "exit" (target "exit" picks the best clear stair; the phone's people leave with the crew), everyone must know which way they are coming out, and medical resources must be requested for the people the phone reported (how many, and their state as reported) to meet them where that stair lets out. If others are still reported or unaccounted for on the floor, say who goes next. Do whatever else the situation calls for.
Final: {"summary": "<the way out and why, one or two sentences>"}"""

AAR = """TASK: after-action report
The incident is over and every crew is out.
INCIDENT LOG, in order:
{log}
HAZARDS REPORTED:
{hazards}
OPEN CROSS-CHECKS BETWEEN THE DATA LAYERS FOR THIS BUILDING:
{checks}
Budget: 1 reply with tool calls, then the final object.
Before the report: pin in the twin what the next crew in this building must know and that the log does not already show as pinned (at most 1 pin); and turn the open cross-checks that matter most into verification requests against the building record (at most 2), with evidence taken from the cross-check text and nothing added.
Final: {"summary": "<three sentences: what happened, how the rescue went, what the data showed>", "went_well": ["<short>", "<short>"], "improve": ["<short>", "<short>"], "follow_up": ["<short>", "<short>"]}
Do not state durations: the system adds the measured times."""

# After-action report of an incident with a radar scan (run with SYSTEM_SCOUT). AAR above stays as it is for the mayday exercise.
AAR_SCOUT = """TASK: after-action report
The incident is over and every crew is out.
INCIDENT LOG, in order:
{log}
HAZARDS REPORTED:
{hazards}
SCAN BRIEF (UWB radar pucks thrown into a room during the incident):
{scan}
OPEN CROSS-CHECKS BETWEEN THE DATA LAYERS FOR THIS BUILDING:
{checks}
Budget: 1 reply with tool calls, then the final object.
Before the report: pin in the twin what the next crew in this building must know and that the log does not already show as pinned (at most 1 pin; a place where the scan found the room different from the twin counts); and turn the open cross-checks that matter most into verification requests against the building record (at most 2), with evidence taken from the cross-check text and nothing added.
Final: {"summary": "<three sentences: what happened, how the rescue went, what the data and the scan showed>", "went_well": ["<short>", "<short>"], "improve": ["<short>", "<short>"], "follow_up": ["<short>", "<short>"], "scan": {"contributed": ["<short>", "<short>"], "could_not_tell": ["<short>", "<short>"]}}
"scan" says what the scan contributed to this incident and what it could not tell, from its brief and the log. Do not state durations: the system adds the measured times."""

# After-action report of an incident with occupants' phones (run with the field system prompt): the hand-off a
# command post keeps. {scan_section} is empty unless the incident also had a radar scan.
AAR_BEACON = """TASK: after-action report
The incident is over and every crew is out.
INCIDENT LOG, in order:
{log}
HAZARDS REPORTED:
{hazards}
PHONES (opt-in occupants' phones as last heard, and devices heard without the app):
{phones}
ACCOUNTABILITY (the building record's suites against the phones and the crews):
{accountability}
{scan_section}OPEN CROSS-CHECKS BETWEEN THE DATA LAYERS FOR THIS BUILDING:
{checks}
Budget: 1 reply with tool calls, then the final object.
Before the report: pin in the twin what the next crew in this building must know and that the log does not already show as pinned (at most 1 pin); and turn the open cross-checks that matter most into verification requests against the building record (at most 2), with evidence taken from the cross-check text and nothing added.
Final: {"summary": "<three sentences: what happened, who was found and brought out, what is still unknown>", "went_well": ["<short>", "<short>"], "improve": ["<short>", "<short>"], "follow_up": ["<short>", "<short>"], "handoff": {"reported": ["<phone, suite, people and status as reported>"], "found": ["<who, by which crew, brought out or not>"], "unaccounted": {"low": <number>, "high": <number>, "why_a_range": "<one sentence>"}, "search_next": ["<suite and why>"]}}
"handoff" is what the command post keeps: who was reported by phone, who was found and brought out, who is still unaccounted for and why that number is a range (head counts are estimates from floor area, devices are not people, a phone without the app is not heard). Take every number from the log and the tables. Do not state durations: the system adds the measured times."""

RUN_TITLES = {"preplan": "Pre-incident plan", "mayday": "Mayday", "hazard": "Re-plan after a hazard", "contact": "Contact made: way out", "aar": "After-action report",
              "scan": "Scan received: what it changes", "victim_reached": "Victim reached: way out",
              "phone": "Phone heard: who goes", "phone_reached": "Phone reached: way out"}


def describe_call(name: str, args: dict) -> str:
    a = args or {}
    if name == "radio":
        return f"Radio to {a.get('to')}: {a.get('text')}"
    if name == "assign":
        return f"Assign {a.get('unit')}: {str(a.get('task')).replace('_', ' ')} -> {a.get('target')}"
    if name == "mark_hazard":
        return f"Mark hazard at {a.get('ref')} ({a.get('kind')})"
    if name == "request_resource":
        return f"Request {a.get('what')}"
    if name == "add_observation":
        return f"Pin in the twin: {a.get('text')}"
    if name == "file_record_correction":
        return f"File record correction: {a.get('field')}"
    if name == "look_at_aerial":
        return f"Look at the aerial ({a.get('image_id', 'ortho')}): {a.get('question')}"
    inner = ", ".join(f"{k}={json.dumps(v)}" for k, v in a.items())
    return f"{name}({inner})"


def describe_result(name: str, r: dict) -> str:
    if not isinstance(r, dict):
        return str(r)[:160]
    if r.get("error"):
        return f"error: {r['error']}"
    if name in ("route", "nearest_exit"):
        return ((f"{r['length_m']} m to {r['to']} via {', '.join(r['via']) or 'direct'}" + (f", passes scan flag {', '.join(r['passes_flags'])}" if r.get("passes_flags") else ""))
                if r.get("ok") else f"no route: {r.get('reason')}")
    if name == "assign":
        return (f"route {r['route']['length_m']} m via {', '.join(r['route']['rooms'][1:-1]) or 'direct'}" if r.get("ok") and r.get("route") else ("ok" if r.get("ok") else f"refused: {r.get('reason')}"))
    if name == "locate_unit":
        who = "; ".join(str(t.get("category") or "tenant") for t in r.get("tenants_in_record", [])) or "no tenant in the record"   # live records can lack it
        return f"{r.get('room_label')}: {who}"
    if name == "hose_stretch":
        return f"{r['total_m']} m ({r['lengths_50ft']} lengths): inside {r['inside_m']} m, stair {r['vertical_m']} m, outside {r['outside_m']} m"
    if name == "look_at_aerial":
        return r.get("answer", "")[:260]
    if name == "get_building_record":
        rec = r.get("record", {})
        return f"{rec.get('stories')} floors, {rec.get('rba_sf')} sq ft, built {rec.get('year_built')}; missing: {', '.join(r.get('missing_fields', [])[:5])}"
    if name == "list_tenants":
        hz = [t for t in r.get("tenants", []) if t.get("life_hazard")]
        return f"{len(r.get('tenants', []))} registrations on floor {r.get('floor')}, {len(hz)} with a life hazard"
    if name == "outside_context":
        return f"{len(r.get('hydrants', []))} hydrants, sides {', '.join(r.get('sides', {}))}, height {r.get('building_height_m')} m"
    if name == "describe_floor":
        return f"floor {r.get('floor')}: {r.get('size_m')} m, {len(r.get('suites', []))} suites, {len(r.get('stairs', []))} stairs"
    if name == "plan_anchor_drops":
        return f"{len(r.get('anchors', []))} drops ({r.get('coverage_pct')}% of the floor within one wall of 3 anchors): " + ", ".join(a["near"] for a in r.get("anchors", []))
    if name == "measure_outside":
        return f"{r.get('from')} to {r.get('to')}: {r.get('distance_m')} m"
    if name == "list_aerial_imagery":
        return ", ".join(f"{i['id']} ({i['gsd_m']} m/px)" for i in r.get("images", []))
    if name == "list_vacant_space":
        return f"{len(r.get('available_in_record', []))} listed, {len(r.get('suites_without_active_registration', []))} suites without a registration"
    if name in ("add_observation", "file_record_correction"):
        return r.get("status") or "ok"
    if name == "get_phones":
        ph = r.get("phones") or []
        if not ph:
            return r.get("reason") or "no phones"
        parts = [f"{q['id']}: {q.get('geometry')}" + (f" in {q['room_label']}" if q.get("room_label") else "") +
                 (f", {q['report'].get('people')} {q['report'].get('status')}" if (q.get("report") or {}).get("status") else "") for q in ph]
        return ("SAMPLE " if r.get("sample") else "SIMULATED " if r.get("simulated") else "") + "; ".join(parts)
    if name == "get_accountability":
        t = r.get("totals") or {}
        return f"{len(r.get('suites', []))} suites; expected {t.get('expected_people')} (estimate), {t.get('people_reported')} reported by phone, {t.get('located')} located, {t.get('suites_unscanned')} unscanned"
    if name == "get_scan":
        if not r.get("scan") and not r.get("victims"):
            return r.get("reason") or "no scan"
        vs = "; ".join(f"{v['id']}: {v.get('state')}" + (f" {v['breathing_bpm']}/min" if v.get("breathing_bpm") else "") +
                       f", confidence {v.get('confidence')}, " + (f"+/- {v['uncertainty_m']:g} m" if isinstance(v.get("uncertainty_m"), (int, float)) else str(v.get("uncertainty_m")))
                       for v in r.get("victims", [])) or "no person located"
        return (f"{'SIMULATED ' if r.get('simulated') else ''}{r.get('rung')} scan of {r.get('room_label')}, {r.get('pucks')} pucks: {vs}; "
                f"{len(r.get('flags', []))} flag(s) where the room differs from the twin")
    return "ok" if r.get("ok") else json.dumps(r)[:160]


def small(result) -> dict:
    """A trimmed copy of a tool result for the UI (the full result goes to the model)."""
    text = json.dumps(result, sort_keys=True)
    if len(text) <= 1500:
        return result
    return {"truncated": True, "preview": text[:1500]}
