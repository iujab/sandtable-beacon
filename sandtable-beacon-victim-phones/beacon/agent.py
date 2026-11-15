"""The Beacon command agent: the phones against the building record, turned into orders.

One turn = plan, then act: a tool-using loop (at most 8 model replies) over the occupants' phones,
the building record's suites and the twin's routes, which ends in an AgentPlan: a summary, radio
orders, search priorities, where to throw the next puck, corrections to the record, and the limits
it acknowledges (devices are not people, a ring is not a position, head counts are estimates).

Tool calling is prompted JSON, identical on every backend, so one recording replays anywhere
(beacon/llm.py). Every fact in the prompt comes from the frozen discrete brief (beacon/tools.py).
When the model is unavailable (a replay miss with no live backend, a time-out, any error) a small
deterministic policy built from the roster's priorities acts instead, and every message it emits
is labelled `backend: "reflex-fallback"`.

Loop, event shapes, prompt style and the fallback idea copied from Sandtable's command agent
(`sandtable-live-fireground-twin/sandtable/agent/__init__.py` and `prompts.py`, sibling project,
lane L01), with credit, so its board can show these messages unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Callable

from . import config
from .llm import LLM, LLMError, ReplayMiss, extract_json
from .tools import ACTION_TOOLS, STANDING_LIMITS, ToolBox, brief_of, catalog_text, devices_band
from .twin import Twin

__all__ = ["BeaconAgent", "SYSTEM", "TASK", "situation"]
log = logging.getLogger("beacon.agent")

MAX_STEPS = 8                                                  # model replies per turn
PACE_S = float(os.environ.get("BEACON_AGENT_PACE_S", "0.6"))   # replay: pause between steps so people can follow
REPLAY_TIMEOUT_S = 90.0
LIVE_TIMEOUT_S = float(os.environ.get("BEACON_AGENT_TIMEOUT_S", "480"))
FALLBACK = "reflex-fallback"

# The system prompt: Sandtable's command-aide prompt (prompts.SYSTEM + its Beacon rules), narrowed to the three layers
# this module joins, with the goal and the honesty constraints stated as goals.
SYSTEM = """You are the command aide to an incident commander at a structure fire, or at a training exercise run on the same system. You work only through tools over three data layers for one building, joined by position: the building record (the CoStar layer: tenants by suite, kind of business, area, vacancy), the interior twin (the Matterport layer: rooms, doors, stairs, walking routes) and occupants' phones heard by pucks a crew throws into a room (the Beacon layer).

Goal
The record says who should be on the fire floor; the phones say who is still inside, where, and who got out. Turn the difference into action: who goes where first and by which route, what each crew hears on the radio, where the next puck is thrown, and which entries of the record must be checked.

Rules
- Facts come only from tool results and from the situation given to you. Never invent a fact, number, name or contact. If something is missing from the data, say that it is missing.
- When a value is marked simulated, sample, estimate or fixture, carry that label with it.
- Be brief and operational. Radio traffic is plain language with the call sign first, for example "RIT from Command: ...", at most 40 words per message. Say suite numbers, room and corridor names and stairs; never say an internal id (room, door or node ids) on the radio, in a summary or in a priority.
- Only a phone whose owner installed the app and pressed SOS is identified (B1...). Its location is what its measurements support: a fix is a point with an uncertainty; candidates are separate spots, any of which may be right; a ring is a distance from one puck, not a place; a zone is a room. Call only a fix the phone's position. Two pucks give two candidate spots, mirror images of each other: that is never a position; send the crew to check both, or ask for a puck to be moved to decide. With candidates, a ring or a zone the order is to search: the spots, the arc or the room, never "go to the phone". A fix that rests on a statement (rests_on: the occupant's stated suite, or command's search room) is only as good as that statement: say so.
- Devices heard without the app are a count of devices, not of people. The record's expected head counts are estimates from floor area and building-code load factors, so the number of people unaccounted for is a range, never one number.
- The occupant's own report (trapped, injured, how many people, whether they can move) is what they typed or picked: pass it on as their report. An occupant who pressed "I am out" is accounted for, not searched for.
- A suite the record lists as vacant where a phone, a device or a registered app user turns up is a disagreement to verify, not proof either way.
- Phone ids (B1) are for tools and for the target fields of the final object; on the radio say the suite, where in it, and what the occupant reported.
- You act only through tools. Crews hear only your radio traffic and see only your assignments. Units: RIT (rescue team) and E1 (Engine 1); ALL reaches every unit.

Protocol
Reply with exactly one JSON object and nothing else (no markdown fence, no text before or after).
To use tools:   {"thought": "<one or two short sentences>", "calls": [{"tool": "<name>", "args": {...}}, ...]}
When finished:  {"thought": "<one short sentence>", "final": {...}}
Several calls may go in one reply. They run in order and all results come back together. Use few replies: batch calls that do not depend on each other.

Tools
{catalog}"""

TASK = """SITUATION: PHONES ON THE FIRE FLOOR
{situation}
Pucks thrown into the fire room hear occupants' phones; the building record says who should be on the floor. Decide and act. Budget: at most 4 replies with tool calls, then the final object.
Read the phones and the record first (get_phones, suite_accountability) and respect what the measurements support.
By the end:
- the most urgent occupants reported by phone have a crew assigned (task "rescue" with target = the phone id, or "search" with target = a suite when there is no position) over a route you have checked (route_to); the rescue team first, unless you say why another crew;
- each assigned crew has heard by radio where to go or what to search, how sure the location is, and what the occupant reported (status, how many people, whether they can move), so it knows what to bring;
- every unit has heard what was found and how many people are still unaccounted for on the floor, as a range;
- you have decided where the next puck is thrown (next_puck_plan), and a crew has been told;
- every disagreement between the record and the phones is filed with file_record_check, with its evidence.
Final: {"summary": "<who goes where first and why, two or three sentences>", "priorities": [{"rank": 1, "suite": "<number>", "target": "<phone id or null>", "action": "rescue|search|scan", "why": "<short>"}], "next_puck": {"suite": "<number>", "why": "<short>"} or null, "limits_acknowledged": ["<short>", "..."]}
"limits_acknowledged" lists what this plan cannot know (for example that devices are not people, that a ring is not a position, that expected head counts are estimates)."""

REPAIR = "Your reply was not a single valid JSON object. Reply again with exactly one JSON object in the protocol format and nothing else."
NEED_ACTION = 'Your reply had neither "calls" nor "final". Reply with one JSON object that has one of them.'


def data_label(brief: dict) -> str:
    if brief.get("sample"):
        return "SAMPLE (hand-made sample of the feed, not a measurement)"
    if brief.get("simulated"):
        return "SIMULATED (phones, pucks and devices from the simulator, not a measurement)"
    return "LIVE (opt-in phones reporting through the app)"


def situation(brief: dict, twin: Twin) -> dict:
    """The situation line of the prompt: discrete facts only (the model reads the details through tools)."""
    phones = brief.get("phones") or []
    inside = [p for p in phones if not (p.get("report") or {}).get("safe_outside")]
    acc = brief.get("accountability") or {}
    floor = brief.get("floor") or twin.exercise_floor
    try:
        floor_label = str(twin.floor(floor).get("label") or floor)
    except KeyError:
        floor_label = floor
    return {"data": data_label(brief), "floor": floor_label, "fire_room": brief.get("room_label"),
            "pucks_placed": len(brief.get("pucks") or []),
            "opt_in_phones_inside": [{"id": p["id"], "geometry": p.get("geometry"), "status": (p.get("report") or {}).get("status"),
                                      "people": (p.get("report") or {}).get("people"), "can_move": (p.get("report") or {}).get("can_move")}
                                     for p in inside],
            "opt_in_phones_safe_outside": len(phones) - len(inside),
            "devices_heard_without_app": devices_band(((acc.get("totals") or {}).get("cold_devices")) or 0) if acc else None,   # "some" | "none"
            "building_record": "available" if acc.get("suites") else "not available"}


# ---------------------------------------------------------------------------- readable lines for the board
def describe_call(name: str, args: dict) -> str:
    a = args or {}
    if name == "radio":
        return f"Radio to {a.get('to')}: {a.get('text')}"
    if name == "assign":
        return f"Assign {a.get('unit')}: {a.get('task')} -> {a.get('target')}"
    if name == "file_record_check":
        return f"Record check, Suite {str(a.get('suite', '')).replace('Suite', '').strip()}: {a.get('text')}"
    inner = ", ".join(f"{k}={json.dumps(v)}" for k, v in a.items())
    return f"{name}({inner})"


def describe_result(name: str, r: dict) -> str:
    if not isinstance(r, dict):
        return str(r)[:160]
    if r.get("error"):
        return f"error: {r['error']}"
    if name == "get_phones":
        parts = []
        for p in r.get("phones") or []:
            rep = p.get("report") or {}
            if p.get("safe_outside"):
                parts.append(f"{p['id']}: safe outside")
                continue
            where = p.get("room_label") or ((p.get("ring") or {}).get("arc_crosses") or [{}])[0].get("room_label") or "?"
            parts.append(f"{p['id']}: {p.get('geometry')} ({where}), {rep.get('people')} {rep.get('status')}" + (", cannot move" if rep.get("can_move") is False else ""))
        tag = "SAMPLE " if r.get("sample") else "SIMULATED " if r.get("simulated") else ""
        return (tag + "; ".join(parts)) if parts else (r.get("reason") or "no phones")
    if name == "suite_accountability":
        if r.get("suite"):
            s = r["suite"]
            return f"Suite {s['suite']}: {s.get('state')}, expected {s['expected'].get('people')} (estimate), unaccounted {s.get('unaccounted')}"
        t = r.get("totals") or {}
        un = t.get("unaccounted") or [None, None]
        return (f"{len(r.get('suites') or [])} suites; expected {t.get('expected_people')} (estimate, up to {t.get('expected_high')}); "
                f"{t.get('people_reported')} reported by phone, {t.get('located')} located, {t.get('safe_outside')} safe outside; "
                f"unaccounted {un[0]} to {un[1]}; {len(r.get('disagreements') or [])} record disagreement(s)")
    if name == "next_puck_plan":
        s = (r.get("suites") or [None])[0]
        return (f"next: Suite {s['suite']} (door {s.get('door')}, {s.get('walk_from_fire_room_m')} m from the fire room); {r.get('pucks_left_in_kit')} pucks left"
                if s else "no unscanned occupied suite")
    if name == "route_to":
        return (f"{r['length_m']} m from {r['attack_stair']} via {', '.join(r.get('via') or []) or 'direct'} to {r.get('to')}" if r.get("ok")
                else f"no route: {r.get('reason')}")
    if name == "assign":
        rt = r.get("route")
        return (f"route {rt['length_m']} m from {rt['attack_stair']} to {rt['to']}" if r.get("ok") and rt else ("ok" if r.get("ok") else f"refused: {r.get('reason')}"))
    if name == "radio":
        return "sent" if r.get("ok") else "not sent"
    if name == "file_record_check":
        return r.get("status") or "ok"
    return "ok" if r.get("ok") else json.dumps(r)[:160]


def small(result) -> dict:
    """A trimmed copy of a tool result for the stream (the full result goes to the model)."""
    text = json.dumps(result, sort_keys=True)
    return result if len(text) <= 1500 else {"truncated": True, "preview": text[:1500]}


def _who(view: dict) -> str:
    rep = view.get("report") or {}
    n = rep.get("people")
    who = f"{n if n is not None else 'unknown number of'} {'person' if n == 1 else 'people'}"
    if rep.get("status") and rep["status"] != "unknown":
        who += f", {rep['status']}"
    if rep.get("can_move") is False:
        who += ", cannot move"
    return who


def _where(view: dict) -> str:
    g = view.get("geometry")
    if g == "fix":
        rests = "; ".join(a.get("means") or "" for a in view.get("rests_on") or [] if a.get("means"))
        return f"Located {view.get('where')}" + (f" ({rests})" if rests else "")
    if g == "candidates":
        n = len(view.get("candidates") or [])
        return f"{'Two' if n == 2 else n} possible spots in {view.get('room_label') or 'the room'}, not a position: check {'both' if n == 2 else 'all'}"
    if g == "ring":
        top = ((view.get("ring") or {}).get("arc_crosses") or [{}])[0].get("room_label")
        return "Range only, no position: search along the arc" + (f", mostly in {top}" if top else "")
    if g == "zone":
        return f"In {view.get('room_label') or 'the room'} by Bluetooth only: search the room"
    return "Heard, not located"


# ---------------------------------------------------------------------------- the agent
class BeaconAgent:
    def __init__(self, session, twin=None, site: dict | None = None, emit: Callable[[dict], None] | None = None,
                 llm: LLM | None = None, record_path=None, pace_s: float | None = None):
        """session: a BeaconSession, anything with beacons() (and optionally brief()), or a zero-argument callable returning one."""
        self.session = session
        self.twin = twin if isinstance(twin, Twin) else Twin(twin if isinstance(twin, dict) else config.twin_raw())
        self.site = site if site is not None else config.site_raw()
        self._emit = emit
        self.llm = llm or LLM()
        self.backend = self.llm.backend
        self.busy = False
        self.last: dict | None = None
        self.log: list[dict] = []
        self.record_path = record_path
        self.pace_s = PACE_S if pace_s is None else float(pace_s)
        self.system = SYSTEM.replace("{catalog}", catalog_text())
        self._runs = 0

    # ---- state for the routes and the Beacons object
    def status(self) -> dict:
        return {"busy": self.busy, "backend": self.backend, "plan": self.last, "log": list(self.log)}

    def summary(self) -> dict | None:
        """{"busy", "backend", "summary"} for the Beacons object's `agent` field (None before the first plan)."""
        if self.last is None and not self.busy:
            return None
        return {"busy": self.busy, "backend": self.backend, "summary": (self.last or {}).get("summary")}

    def reset(self) -> None:
        if not self.busy:
            self.last, self.log = None, []

    def emit(self, msg: dict) -> None:
        if self._emit is None:
            return
        try:
            self._emit(msg)
        except Exception:
            log.exception("agent emit")

    def _event(self, run: str, step: int, kind: str, backend: str, text: str, **extra) -> None:
        msg = {"type": "agent", "run": run, "step": step, "kind": kind, "backend": backend, "text": text, "t": round(time.time(), 2), **extra}
        self.log.append(msg)
        self.emit(msg)

    # ---- one turn
    async def run(self, reason: str = "manual") -> dict:
        if self.busy:
            raise RuntimeError("the command agent is already running")
        self.busy = True
        try:
            self._runs += 1
            run = f"phones#{self._runs}"
            self.log = []
            brief = brief_of(self.session)                      # frozen for the whole turn
            box = ToolBox(brief, self.twin, self.site, record_path=self.record_path, run=run)
            user = TASK.replace("{situation}", json.dumps(situation(brief, self.twin), sort_keys=True))
            self._event(run, 0, "start", self.backend, f"Phones against the building record: who goes where ({reason})")
            timeout = REPLAY_TIMEOUT_S if self.llm.backend == "replay" else LIVE_TIMEOUT_S
            why = None
            try:
                final, backend = await asyncio.wait_for(self._loop(run, user, box), timeout)
            except ReplayMiss:
                why = "no recorded model response for this exact situation and no live model configured"
            except asyncio.TimeoutError:
                why = f"the model did not finish within {timeout:.0f} s"
            except LLMError as exc:
                why = f"model unavailable ({exc})"
            except Exception as exc:                              # never let the agent take the board down
                log.exception("agent loop failed")
                why = f"agent error ({type(exc).__name__})"
            if why is not None:
                log.warning("reflex-fallback on %s: %s", run, why)
                self._event(run, 99, "error", FALLBACK, f"Deterministic fallback policy takes over: {why}."
                            + (" Actions taken by the model before it failed are discarded." if box.calls else ""))
                box = ToolBox(brief, self.twin, self.site, record_path=self.record_path, run=run)
                final = await self._fallback(run, brief, box)
                backend = FALLBACK
            plan = self._plan(run, backend, final, box, brief)
            self._event(run, 100, "final", backend, plan["summary"])
            self.emit(plan)
            self.last, self.backend = plan, backend
            return plan
        finally:
            self.busy = False

    async def _loop(self, run: str, user: str, box: ToolBox) -> tuple[dict, str]:
        messages = [{"role": "user", "content": user}]
        sources: list[str] = []
        repaired = False
        step = 0
        while step < MAX_STEPS:
            step += 1
            text, meta = await self.llm.complete(self.system, messages, tier="main")
            src = meta["source"]
            sources.append(src)
            try:
                obj = extract_json(text)
            except ValueError:
                if repaired:
                    raise LLMError("the model did not return JSON twice")
                repaired = True
                messages += [{"role": "assistant", "content": text}, {"role": "user", "content": REPAIR}]
                continue
            if src == "replay":
                await asyncio.sleep(self.pace_s)
            thought = str(obj.get("thought") or "").strip()
            if thought:
                self._event(run, step, "thought", src, thought)
            calls = obj.get("calls")
            if not calls and obj.get("tool"):
                calls = [{"tool": obj["tool"], "args": obj.get("args") or {}}]
            final = obj.get("final")
            if final is not None and not calls:
                backend = "replay" if all(s == "replay" for s in sources) else self.llm.backend
                return (final if isinstance(final, dict) else {"summary": str(final)}), backend
            if not calls or not isinstance(calls, list):
                messages += [{"role": "assistant", "content": json.dumps(obj, sort_keys=True)}, {"role": "user", "content": NEED_ACTION}]
                continue
            results = []
            for c in calls[:12]:
                c = c if isinstance(c, dict) else {}
                name, args = str(c.get("tool")), c.get("args") or {}
                self._event(run, step, "action" if name in ACTION_TOOLS else "tool_call", src, describe_call(name, args), tool=name, args=args)
                result = box.call(name, args)
                results.append({"tool": name, "result": result})
                self._event(run, step, "tool_result", src, describe_result(name, result), tool=name, result=small(result))
                if src == "replay":
                    await asyncio.sleep(self.pace_s * 0.4)
            last = step >= MAX_STEPS - 1
            messages += [{"role": "assistant", "content": json.dumps(obj, sort_keys=True)},
                         {"role": "user", "content": "RESULTS\n" + json.dumps(results, sort_keys=True)
                          + ("\n\nYou have used your replies. Return the final object now." if last else "")}]
        raise LLMError("the model did not finish within the step limit")

    # ---- the AgentPlan
    def _plan(self, run: str, backend: str, final: dict, box: ToolBox, brief: dict) -> dict:
        acc = brief.get("accountability") or {}
        phone_ids = {p["id"] for p in brief.get("phones") or []}
        suites = self.twin._suite_room
        summary = " ".join(str(final.get("summary") or "").split()) or "No summary was returned."
        priorities, src = [], "agent"
        stated_pri = final.get("priorities")
        for p in stated_pri if isinstance(stated_pri, list) else []:
            if not isinstance(p, dict):
                continue
            suite = str(p.get("suite") or "").replace("Suite", "").strip()
            if suite not in suites:
                continue
            target = p.get("target")                      # model output: may be a list or an object, never trusted
            target = target if isinstance(target, str) and target in phone_ids else None
            action = p.get("action") if p.get("action") in ("rescue", "search", "scan") else ("rescue" if target else "search")
            priorities.append({"rank": len(priorities) + 1, "suite": suite, "target": target, "action": action,
                               "why": " ".join(str(p.get("why") or "").split())[:300]})
        if not priorities and acc.get("priorities"):
            src = "roster"
            priorities = [{"rank": q.get("rank"), "suite": q.get("suite"), "target": q.get("target"), "action": q.get("action"),
                           "why": q.get("text")} for q in acc["priorities"]]
        np = final.get("next_puck")
        next_puck = None
        if isinstance(np, dict) and str(np.get("suite") or "").replace("Suite", "").strip() in suites:
            s = str(np["suite"]).replace("Suite", "").strip()
            next_puck = {"suite": s, "room": suites[s], "why": " ".join(str(np.get("why") or "").split())[:300]}
        elif np is None and acc.get("next_puck") and src == "roster":
            r = acc["next_puck"]
            next_puck = {"suite": r.get("suite"), "room": r.get("room"), "why": r.get("text")}
        stated = final.get("limits_acknowledged") or []
        stated = [stated] if isinstance(stated, str) else stated if isinstance(stated, list) else []
        limits = [" ".join(str(x).split())[:240] for x in stated if str(x).strip()][:10]
        return {"type": "agent_plan", "run": run, "backend": backend, "data": data_label(brief),
                "simulated": bool(brief.get("simulated")), "sample": bool(brief.get("sample")),
                "summary": summary,
                "orders": [dict(o) for o in box.orders],
                "assignments": [dict(a) for a in box.assignments.values()],
                "priorities": priorities, "priorities_source": src,
                "next_puck": next_puck,
                "record_checks": [dict(c) for c in box.record_checks],
                "limits_acknowledged": limits,
                "limits_standing": list(STANDING_LIMITS)}

    # ---- the deterministic fallback policy
    async def _fallback(self, run: str, brief: dict, box: ToolBox) -> dict:
        """Rescue order by the roster's priorities, a radio order per priority, the next puck, the record checks."""
        step = 0

        async def act(name: str, args: dict) -> dict:
            nonlocal step
            step += 1
            self._event(run, step, "action" if name in ACTION_TOOLS else "tool_call", FALLBACK, describe_call(name, args), tool=name, args=args)
            r = box.call(name, args)
            self._event(run, step, "tool_result", FALLBACK, describe_result(name, r), tool=name, result=small(r))
            if self.pace_s:
                await asyncio.sleep(self.pace_s * 0.4)
            return r

        phones = await act("get_phones", {})
        acc = await act("suite_accountability", {})
        views = {p["id"]: p for p in phones.get("phones") or []}
        rows = {r["suite"]: r for r in acc.get("suites") or []}
        pri = (brief.get("accountability") or {}).get("priorities") or []
        free = ["RIT", "E1"]
        plan_pri, next_puck, sent = [], None, []
        for p in pri:
            action, suite, target = p.get("action"), p.get("suite"), p.get("target")
            row = rows.get(suite) or {}
            plan_pri.append({"rank": p.get("rank"), "suite": suite, "target": target, "action": action, "why": row.get("why") or p.get("text")})
            if action in ("rescue", "search"):
                view = views.get(target) if target else None
                if not free:
                    await act("radio", {"to": "ALL", "priority": "urgent", "target": target or suite,
                                        "text": f"All units from Command: Suite {suite} is next: {row.get('why') or action}. Next free crew goes."})
                    continue
                unit = free.pop(0)
                if action == "rescue" and view is not None and view.get("geometry") in ("fix", "candidates"):
                    r = await act("assign", {"unit": unit, "task": "rescue", "target": target})
                else:
                    r = await act("assign", {"unit": unit, "task": "search", "target": suite})
                rt = r.get("route") if r.get("ok") else None
                go = f" Go now, {rt['length_m']} metres from {rt['attack_stair']}." if rt else " Go now."
                if view is not None:
                    text = f"{unit} from Command: Suite {suite}, phone reports {_who(view)}. {_where(view)}.{go}"
                else:
                    text = f"{unit} from Command: search Suite {suite}: {row.get('why') or 'see the table'}.{go}"
                cannot = view is not None and (view.get("report") or {}).get("can_move") is False
                await act("radio", {"to": unit, "priority": "emergency" if cannot else "urgent", "target": target or suite, "text": text})
                sent.append(f"{unit} to Suite {suite}")
            elif action == "scan" and next_puck is None:
                plan = await act("next_puck_plan", {})
                first = (plan.get("suites") or [{}])[0]
                s = first.get("suite") or suite
                next_puck = {"suite": s, "why": first.get("why") or p.get("text")}
                unit = free.pop(0) if free else None
                if unit:
                    await act("assign", {"unit": unit, "task": "scan", "target": s})
                exp = (first.get("expected") or {})
                door = f" through the door {first['door']}" if first.get("door") else ""
                await act("radio", {"to": unit or "ALL", "priority": "routine", "target": s,
                                    "text": f"{unit or 'All units'} from Command: {'carry' if unit else 'next free crew carries'} the next puck to Suite {s}{door}. "
                                            f"Record expects about {exp.get('people')} people there, an estimate."})
                sent.append(f"next puck to Suite {s}")
        t = acc.get("totals") or {}
        un = t.get("unaccounted") or [None, None]
        floor = phones.get("floor") or "the fire floor"
        await act("radio", {"to": "ALL", "priority": "urgent",
                            "text": f"All units from Command: {t.get('phones_inside')} phones inside report {t.get('people_reported')} people, "
                                    f"{t.get('located')} located, {t.get('safe_outside')} safe outside. Unaccounted for on floor {floor}: "
                                    f"{un[0]} to {un[1]}, an estimate. Devices heard are not people."})
        for d in acc.get("disagreements") or []:
            await act("file_record_check", {"suite": d["suite"], "text": "; ".join(d["evidence"]) + ": verify the record."})
        first = plan_pri[0] if plan_pri else None
        summary = ("Fallback policy, no model: " + ("; ".join(sent) if sent else "no crew assigned") + ". "
                   + (f"First priority Suite {first['suite']} ({first['action']}). " if first else "")
                   + f"Unaccounted for on the floor: {un[0]} to {un[1]} (estimate).")
        return {"summary": summary, "priorities": plan_pri, "next_puck": next_puck, "limits_acknowledged": list(STANDING_LIMITS)}
