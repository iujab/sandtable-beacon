"""The command agent.

Two tiers. The reflex layer (sandtable.incident) is deterministic and raises the alarm in
milliseconds. This agent deliberates on top of it: a tool-using loop over the building record,
the aerial imagery, the twin and the live crew positions, which plans, acts through the
incident (radio, assignments, hazards, tags, record corrections) and re-plans when the
situation changes.

Tool calling is prompted JSON, identical on every backend, so one recording replays anywhere.
When the model is unavailable (replay miss with no live backend, time-out, error) a small
deterministic policy acts instead and every event it emits is labelled `reflex-fallback`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Callable

from .. import config
from .llm import LLM, LLMError, ReplayMiss, extract_json
from .tools import ACTION_TOOLS, ToolBox, catalog_text, field_catalog
from . import prompts

__all__ = ["CommandAgent"]
log = logging.getLogger("sandtable.agent")


class CommandAgent:
    def __init__(self, provider, twin, incident, emit: Callable[[dict], None], llm: LLM | None = None):
        self.provider, self.twin, self.incident, self.emit = provider, twin, incident, emit
        self.llm = llm or LLM()
        self.tools = ToolBox(provider, twin, incident, self.llm)
        self.backend = self.llm.backend
        self.busy = False
        self._runs = 0
        # System prompts chosen by run: the original one (pre-plan, mayday exercise; its recordings depend on every
        # byte of it), and a field one for any run in an incident with a radar scan and/or occupants' phones, whose
        # rules and tool catalogue cover exactly what the incident has.
        self.system = prompts.SYSTEM.replace("{catalog}", catalog_text())
        self.system_scout = self.system_for(scan=True, phones=False)
        self.system_beacon = self.system_for(scan=False, phones=True)
        self.last_sources: list[str] = []

    def system_for(self, scan: bool, phones: bool) -> str:
        if not scan and not phones:
            return self.system
        return prompts.field_system(scan, phones).replace("{catalog}", catalog_text(field_catalog(scan, phones)))

    def reset(self) -> None:
        self._runs = 0
        self.tools.scan_brief = None
        self.tools.beacon_brief = None

    # ------------------------------------------------------------------ events
    def _event(self, run: str, phase: str, step: int, kind: str, backend: str, text: str, **extra) -> None:
        self.emit({"type": "agent", "run": run, "phase": phase, "step": step, "kind": kind, "backend": backend, "text": text, "t": round(time.time(), 2), **extra})

    # ------------------------------------------------------------------ the loop
    async def _loop(self, run: str, phase: str, user: str, max_steps: int | None = None, system: str | None = None) -> tuple[dict, list[dict]]:
        """Runs the model with tools until it returns `final`. Returns (final, trace of tool calls)."""
        messages = [{"role": "user", "content": user}]
        trace: list[dict] = []
        max_steps = max_steps or config.AGENT_MAX_STEPS
        system = system or self.system
        repaired = False
        step = 0
        while step < max_steps + 1:
            step += 1
            text, meta = await self.llm.complete(system, messages, tier="main")
            source = meta["source"]
            self.last_sources.append(source)
            try:
                obj = extract_json(text)
            except ValueError:
                if repaired:
                    raise LLMError("the model did not return JSON twice")
                repaired = True
                messages += [{"role": "assistant", "content": text}, {"role": "user", "content": prompts.REPAIR}]
                continue
            if source == "replay":
                await asyncio.sleep(config.REPLAY_PACE_S)
            thought = str(obj.get("thought") or "").strip()
            if thought:
                self._event(run, phase, step, "thought", source, thought)
            calls = obj.get("calls")
            if not calls and obj.get("tool"):
                calls = [{"tool": obj["tool"], "args": obj.get("args") or {}}]
            final = obj.get("final")
            if final is not None and not calls:
                return (final if isinstance(final, dict) else {"summary": str(final)}), trace
            if not calls:                            # neither, or calls missing next to "final": null (seen in a live run)
                messages += [{"role": "assistant", "content": json.dumps(obj, sort_keys=True)}, {"role": "user", "content": prompts.NEED_ACTION}]
                continue
            results = []
            for c in calls[:12]:
                name, args = str(c.get("tool")), c.get("args") or {}
                is_action = name in ACTION_TOOLS
                self._event(run, phase, step, "action" if is_action else "tool_call", source, prompts.describe_call(name, args), tool=name, args=args)
                result = await self.tools.call(name, args)
                if name == "look_at_aerial" and self.tools.vision_meta:
                    self.last_sources.append(self.tools.vision_meta["source"])
                trace.append({"tool": name, "args": args, "result": result})
                results.append({"tool": name, "result": result})
                self._event(run, phase, step, "tool_result", source, prompts.describe_result(name, result), tool=name, result=prompts.small(result))
                if source == "replay":
                    await asyncio.sleep(config.REPLAY_PACE_S * 0.4)
            last = step >= max_steps
            messages += [{"role": "assistant", "content": json.dumps(obj, sort_keys=True)},
                         {"role": "user", "content": "RESULTS\n" + json.dumps(results, sort_keys=True) + ("\n\nYou have used your replies. Return the final object now." if last else "")}]
        raise LLMError("the model did not finish within the step limit")

    async def _guarded(self, run: str, phase: str, user: str, fallback, max_steps: int | None = None, timeout: float | None = None, system: str | None = None):
        """Model first; deterministic policy when the model is unavailable or too slow."""
        self.busy = True
        self.last_sources = []
        self._event(run, phase, 0, "start", self.backend, prompts.RUN_TITLES.get(phase if phase != "command" else run.split("#")[0], run))
        try:
            final, trace = await asyncio.wait_for(self._loop(run, phase, user, max_steps, system), timeout)
            backend = "replay" if self.last_sources and all(s == "replay" for s in self.last_sources) else (self.backend if self.backend != "replay" else "replay")
            return final, trace, backend
        except ReplayMiss:
            why = "no recorded model response for this exact situation and no live model configured"
        except asyncio.TimeoutError:
            why = f"the model did not answer within {timeout:.0f} s"
        except LLMError as exc:
            why = f"model unavailable ({exc})"
        except Exception as exc:                                   # never let the agent take the incident down
            log.exception("agent loop failed")
            why = f"agent error ({type(exc).__name__})"
        finally:
            self.busy = False
        log.warning("reflex-fallback on %s: %s", run, why)          # scripts/record_all.sh counts these lines
        self._event(run, phase, 99, "error", "reflex-fallback", f"Deterministic fallback policy takes over: {why}.")
        final, trace = await fallback(run, phase)
        return final, trace, "reflex-fallback"

    # ------------------------------------------------------------------ entry points
    async def run_preplan(self) -> dict:
        self.tools.snapshot = self.incident.snapshot()
        final, trace, backend = await self._guarded("preplan", "preplan", prompts.PREPLAN, self._fallback_preplan, timeout=900)
        plan = self._finish_preplan(final, backend)
        plan["vision"] = [{"image": t["args"].get("image_id", "ortho"), "question": t["args"].get("question"), "answer": t["result"].get("answer"), "source": t["result"].get("source")}
                          for t in trace if t["tool"] == "look_at_aerial" and isinstance(t["result"], dict) and t["result"].get("answer")]
        self._event("preplan", "preplan", 100, "final", backend, plan.get("summary") or "Pre-plan ready.")
        self.emit(plan)
        return plan

    def _finish_preplan(self, final: dict, backend: str) -> dict:
        prop = self.provider.site()["property"]
        tactical = dict(final.get("tactical") or {})
        stairs = [s["id"] for s in self.twin.exits()]
        attack = tactical.get("attack_stair") if tactical.get("attack_stair") in stairs else (stairs[0] if stairs else None)
        evac = tactical.get("evac_stair") if tactical.get("evac_stair") in stairs else next((s for s in stairs if s != attack), attack)
        tactical["attack_stair"], tactical["evac_stair"] = attack, evac
        suites = {r["suite"]: r["id"] for r in self.twin.rooms("suite")}
        hazards = []
        for h in tactical.get("life_hazards") or []:
            suite = str(h.get("suite", "")).replace("Suite ", "").strip()
            if suite in suites:
                hazards.append({"suite": suite, "room": suites[suite], "why": h.get("why", "")})
        tactical["life_hazards"] = hazards
        worst = str(tactical.get("worst_case_suite") or "").replace("Suite ", "").strip()
        if worst not in suites:                                      # farthest life-hazard suite from the attack stair
            cand = [h["suite"] for h in hazards] or list(suites)
            worst = max(cand, key=lambda s: self.twin.route(attack, suites[s])["length_m"]) if attack and cand else None
        tactical["worst_case_suite"] = worst
        if attack and worst:
            from ..spatial import hose_stretch, plan_anchor_drops
            tactical["hose_stretch"] = {**hose_stretch(self.twin, self.provider.site(), suites[worst], attack), "target": f"Suite {worst}", "stair": attack}
            drops = plan_anchor_drops(self.twin)
            tactical["anchor_drops"] = drops["anchors"]
            tactical["anchor_coverage_pct"] = drops["coverage_pct"]
            tactical["anchor_line_of_sight_pct"] = drops["line_of_sight_pct"]
        return {"type": "preplan", "address": f"{prop.get('address')}, {prop.get('city')}", "backend": backend,
                "summary": final.get("summary"), "sections": final.get("sections") or [], "tactical": tactical,
                "verify_on_site": final.get("verify_on_site") or []}

    async def handle(self, situation: dict) -> dict:
        self._runs += 1
        kind = situation["kind"]
        run = f"{kind}#{self._runs}"
        self.tools.snapshot = situation["snapshot"]
        if kind == "scan" and isinstance(situation.get("data"), dict):
            self.tools.scan_brief = situation["data"]
        if kind == "phone" and isinstance(situation.get("data"), dict):
            self.tools.beacon_brief = situation["data"]
        body = json.dumps({"kind": kind, "unit": situation.get("unit"), "data": situation.get("data"), "snapshot": situation["snapshot"]}, sort_keys=True)
        task = prompts.SITUATIONS.get(kind)
        if task is None:
            return {"actions": [], "backend": None}
        user = task.replace("{situation}", body)
        # Field runs: the scan and phone situations, and any run once the incident has a scan or phones (decided from
        # the frozen snapshot only). Everything else keeps the original system prompt: the mayday recordings stay valid.
        scan = kind in prompts.SCOUT_KINDS or "scan" in situation["snapshot"]
        phones = kind in prompts.BEACON_KINDS or "phones" in situation["snapshot"]
        field = scan or phones
        live_timeout = 360 if field else 240

        async def fb(run_, phase_):
            return await self._fallback_situation(run_, situation)
        final, trace, backend = await self._guarded(run, "command", user, fb, max_steps=5, timeout=config.AGENT_FALLBACK_AFTER_S if self.backend == "replay" else live_timeout,
                                                    system=self.system_for(scan, phones) if field else None)
        self._event(run, "command", 100, "final", backend, final.get("summary") or "Done.")
        return {"actions": [t for t in trace if t["tool"] in ACTION_TOOLS], "backend": backend}

    async def run_after_action(self) -> dict:
        self.tools.snapshot = self.incident.snapshot()
        log_lines = [f"{e['kind']}: {e['text']}" for e in self.incident.timeline if e["kind"] not in ("radio", "unit", "system", "correction", "twin")]
        site = self.provider.site()
        scan = self.tools.scan_view()                 # both None in the mayday exercise: then the prompt is exactly the recorded one
        phones = self.tools.phones_view()
        accountability = self.tools.accountability_view() if phones is not None else None
        if phones is not None or scan is not None:
            # Field runs: an order's route length and way depend on where the crew stood at that instant, so on timing. The
            # after-action input must be the same for the same incident at any speed, so those two details are left out here
            # (the order itself stays). The mayday input is exactly as recorded.
            log_lines = [re.sub(r", \d+ m via .*$", "", line) if line.startswith("assign: ") else line for line in log_lines]
        if phones is not None:
            template = prompts.AAR_BEACON.replace("{phones}", json.dumps(phones, sort_keys=True)).replace(
                "{accountability}", json.dumps(accountability, sort_keys=True)).replace(
                "{scan_section}", ("" if scan is None else "SCAN BRIEF (UWB radar pucks thrown into a room during the incident):\n" + json.dumps(scan, sort_keys=True) + "\n")
                + (f"THE RECORD AGAINST THE PHONES (live building record): {accountability['headline']}\n" if (accountability or {}).get("headline") else ""))
        elif scan is not None:
            template = prompts.AAR_SCOUT.replace("{scan}", json.dumps(scan, sort_keys=True))
        else:
            template = prompts.AAR
        user = template.replace("{log}", json.dumps(log_lines, indent=0)).replace(
            "{checks}", json.dumps([{"id": c["id"], "text": c["text"], "layers": c["layers"]} for c in site.get("cross_checks", [])], indent=0)).replace(
            "{hazards}", json.dumps([{k: h[k] for k in ("ref", "kind", "label", "note")} for h in self.incident.hazards]))
        final, trace, backend = await self._guarded("aar", "aar", user, self._fallback_aar, max_steps=3, timeout=600,
                                                    system=self.system_for(scan is not None, phones is not None) if (scan or phones) else None)
        aar = {"type": "aar", "backend": backend, "summary": final.get("summary"), "went_well": final.get("went_well") or [], "improve": final.get("improve") or [],
               "follow_up": final.get("follow_up") or [],
               "timeline": [{"t_rel": e["t_rel"], "kind": e["kind"], "text": e["text"]} for e in self.incident.timeline if e["kind"] != "unit"],
               "observations": [t for t in self.provider.matterport.list_tags() if str(t.get("id", "")).startswith("obs-")],
               "corrections": [{**t["args"], **{k: v for k, v in t["result"].items() if k != "ok"}} for t in trace if t["tool"] == "file_record_correction" and t["result"].get("ok")],
               "metrics": self.incident.metrics()}
        if scan is not None:
            fs = final.get("scan") if isinstance(final.get("scan"), dict) else {}
            aar["scan"] = {"brief": scan, "contributed": fs.get("contributed") or [], "could_not_tell": fs.get("could_not_tell") or []}
        if phones is not None:
            aar["handoff"] = final.get("handoff") if isinstance(final.get("handoff"), dict) else {}
            aar["phones"], aar["accountability"] = phones, accountability
        self._event("aar", "aar", 100, "final", backend, aar["summary"] or "After-action report ready.")
        self.emit(aar)
        return aar

    # ------------------------------------------------------------------ deterministic fallback policy
    async def _act(self, run: str, phase: str, name: str, args: dict, trace: list[dict]) -> dict:
        self._event(run, phase, len(trace) + 1, "action" if name in ACTION_TOOLS else "tool_call", "reflex-fallback", prompts.describe_call(name, args), tool=name, args=args)
        result = await self.tools.call(name, args)
        trace.append({"tool": name, "args": args, "result": result})
        self._event(run, phase, len(trace), "tool_result", "reflex-fallback", prompts.describe_result(name, result), tool=name, result=prompts.small(result))
        return result

    async def _fallback_situation(self, run: str, s: dict) -> tuple[dict, list[dict]]:
        trace: list[dict] = []
        snap, kind = s["snapshot"], s["kind"]
        md = snap.get("mayday")
        rescuers = [u["id"] for u in snap["units"] if u["kind"] == "rit"] or [u["id"] for u in snap["units"] if not md or u["id"] != md["unit"]]
        if kind == "mayday" and md and rescuers:
            r = await self._act(run, "command", "assign", {"unit": rescuers[0], "task": "rit_rescue", "target": md["unit"]}, trace)
            await self._act(run, "command", "radio", {"to": rescuers[0], "priority": "emergency",
                            "text": f"{rescuers[0]} from Command: mayday, {md['unit']} down in {md['room_label']}. Go now" + (f", {r['route']['length_m']} metres." if r.get("ok") else ".")}, trace)
            return {"summary": f"Policy sent {rescuers[0]} to {md['unit']} by the shortest unblocked route."}, trace
        if kind == "hazard":
            for u in snap["units"]:
                live = self.incident.units[u["id"]]
                if live["assignment"] and live["route"] and not live["route"].get("valid", True):
                    a = live["assignment"]
                    await self._act(run, "command", "assign", {"unit": u["id"], "task": a["task"], "target": a["target"]}, trace)
            await self._act(run, "command", "radio", {"to": "ALL", "priority": "urgent", "text": f"All units from Command: {s['data'].get('label')} is lost. Routes updated."}, trace)
            return {"summary": "Policy re-routed every unit whose route crossed the hazard."}, trace
        if kind == "contact" and md:
            rescuer = s["data"].get("rescuer") or rescuers[0]
            await self._act(run, "command", "assign", {"unit": rescuer, "task": "exit", "target": "exit"}, trace)
            await self._act(run, "command", "radio", {"to": rescuer, "priority": "urgent", "text": f"{rescuer} from Command: bring {md['unit']} out by the nearest clear stair."}, trace)
            return {"summary": "Policy ordered the rescue team and the mayday crew out by the nearest clear stair."}, trace
        if kind == "scan":
            return await self._fallback_scan(run, rescuers, trace)
        if kind == "victim_reached":
            return await self._fallback_victim_reached(run, s, rescuers, trace)
        if kind == "phone":
            return await self._fallback_phone(run, s, rescuers, trace)
        if kind == "phone_reached":
            return await self._fallback_phone_reached(run, s, rescuers, trace)
        return {"summary": "No policy for this situation."}, trace

    async def _fallback_scan(self, run: str, rescuers: list[str], trace: list[dict]) -> tuple[dict, list[dict]]:
        """Send the rescue team to each located person, say what the scan found and what it cannot tell, pin every flag."""
        scan = self.tools.scan_view() or {}
        room = scan.get("room_label") or "the scanned room"
        sim = "Sample scan, not a radar result," if scan.get("sample") else "Simulated scan" if scan.get("simulated", True) else "Scan"
        sent = []
        for v in scan.get("victims") or []:
            if not rescuers:
                break
            r = await self._act(run, "command", "assign", {"unit": rescuers[0], "task": "rescue", "target": v["id"]}, trace)
            if r.get("ok"):
                sent.append(v)
                await self._act(run, "command", "radio", {"to": rescuers[0], "priority": "urgent",
                                "text": f"{rescuers[0]} from Command: person located in {room}, {v.get('where') or 'position uncertain'}. Go now" +
                                        (f", {r['route']['length_m']} metres." if r.get("route") else ".")}, trace)
                break                                             # one rescue team, one person at a time
        if sent:
            v = sent[0]
            u = v.get("uncertainty_m")
            found = (f"{sim} of {room}: one person, {v.get('state') or 'detected'}" + (f" about {v['breathing_bpm']} a minute" if v.get("breathing_bpm") else "") +
                     (f", within about {u:g} metres" if isinstance(u, (int, float)) else f", {u}" if u else "") + f". {rescuers[0]} assigned.")
        else:
            found = f"{sim} of {room}: no breathing or moving person located."
        await self._act(run, "command", "radio", {"to": "ALL", "priority": "urgent",
                        "text": f"All units from Command: {found} Radar cannot see anyone not breathing or moving: room not cleared."}, trace)
        for m in scan.get("flags") or []:
            await self._act(run, "command", "add_observation", {"text": f"Scan flag: {m.get('text') or m.get('kind')} (confidence {m.get('confidence')}, simulated scan)", "at": m["id"]}, trace)
        return {"summary": (f"Policy sent {rescuers[0]} to the person the scan located in {room} and told all units the scan does not clear the room." if sent
                            else f"Policy told all units the scan of {room} located nobody and does not clear the room.")}, trace

    async def _fallback_victim_reached(self, run: str, s: dict, rescuers: list[str], trace: list[dict]) -> tuple[dict, list[dict]]:
        rescuer = (s.get("data") or {}).get("rescuer") or (rescuers[0] if rescuers else None)
        if rescuer is None:
            return {"summary": "No crew to bring the victim out."}, trace
        r = await self._act(run, "command", "assign", {"unit": rescuer, "task": "exit", "target": "exit"}, trace)
        stair = next((x for x in reversed((r.get("route") or {}).get("rooms") or []) if x.lower().startswith("stair")), "the nearest clear stair")
        discharge = next((e["discharge"] for e in self.twin.exits() if e["label"] == stair and e.get("discharge")), None)
        await self._act(run, "command", "radio", {"to": "ALL", "priority": "urgent",
                        "text": f"All units from Command: {rescuer} has the victim and is coming out by {stair}."}, trace)
        await self._act(run, "command", "request_resource", {"what": "EMS unit for one victim removed from the fire floor",
                        "reason": f"victim located by the scan, coming out by {stair}" + (f", meet at {discharge}" if discharge else "")}, trace)
        return {"summary": f"Policy ordered {rescuer} out with the victim by {stair} and requested EMS."}, trace

    def _situation_phone(self, s: dict) -> str | None:
        """The phone a situation is about: data may be the phone itself, name it, or carry it under "phone"."""
        d = s.get("data") or {}
        ph = d.get("phone")
        changed = d.get("changed") if isinstance(d.get("changed"), list) else []
        cands = [d.get("phone_id"), *changed, ph if isinstance(ph, str) else (ph or {}).get("id") if isinstance(ph, dict) else None,
                 d.get("id") if d.get("geometry") else None, d.get("target"), s.get("unit")]
        known = self.tools._phone_entries()
        return next((c for c in cands if isinstance(c, str) and c in known), None)

    @staticmethod
    def _where_words(ph: dict, room: str) -> str:
        """Where a phone is, in the words its geometry allows. Never a missing number, never a point for a ring."""
        geom, u = ph.get("geometry"), ph.get("uncertainty_m")
        if geom == "fix":
            m = re.search(r"within (?:about )?([0-9.]+ m)", ph.get("text") or "")         # the feed's own words when the brief has no number
            within = f"within about {u:g} m" if isinstance(u, (int, float)) else f"within {m.group(1)}" if m else "uncertainty not given"
            return "Located" + (f" {ph['where']}" if ph.get("where") else f" in {room}") + f", {within}"
        if geom == "candidates":
            n = len(ph.get("candidates") or []) or 2
            return f"At one of {n} possible points in {room}: check them all"
        if geom == "ring":
            puck = (ph.get("ring") or {}).get("puck")
            return f"Somewhere on a ring around puck {puck}: search along it" if puck else "Somewhere on a ring around a puck: search along it"
        if geom == "zone":
            return f"In {room}, not yet pinned down: search the room"
        return "Heard, not located"

    async def _fallback_phone(self, run: str, s: dict, rescuers: list[str], trace: list[dict]) -> tuple[dict, list[dict]]:
        """Send the rescue team to the phone (a fix: to it; otherwise to search the spots, the arc or the room) unless it is
        already on a rescue; tell it the occupant's report and how sure the location is; tell everyone."""
        pid = self._situation_phone(s)
        view = self.tools.phones_view() or {"phones": []}
        ph = next((q for q in view["phones"] if q["id"] == pid), None)
        if ph is None:
            return {"summary": "Policy: no actionable phone in the situation."}, trace
        rep = ph.get("report") or {}
        who = f"{rep.get('people') or 'unknown number of'} {'person' if rep.get('people') == 1 else 'people'}, {rep.get('status') or 'status unknown'}" + (
            ", cannot move" if rep.get("can_move") is False else "")
        room = ph.get("room_label") or "the scanned room"
        geom = ph.get("geometry")
        how = self._where_words(ph, room)
        tag = "Sample" if view.get("sample") else "Simulated" if view.get("simulated") else "Phone"
        snap = s["snapshot"]
        busy = {u["id"] for u in snap["units"] if (u.get("assignment") or {}).get("task") in ("rescue", "rit_rescue") and (u["assignment"] or {}).get("target") != pid}
        free = [r for r in rescuers if r not in busy]
        if free:
            rescuer = free[0]
            r = await self._act(run, "command", "assign", {"unit": rescuer, "task": "rescue", "target": pid}, trace)
            if r.get("ok"):
                await self._act(run, "command", "radio", {"to": rescuer, "priority": "urgent",
                                "text": f"{rescuer} from Command: phone in {room} reports {who}. {how}. Go now" + (f", {r['route']['length_m']} metres." if r.get("route") else ".")}, trace)
            await self._act(run, "command", "radio", {"to": "ALL", "priority": "urgent",
                            "text": f"All units from Command: {tag} phone in {room}: {who}. {rescuer} assigned. Devices heard are not people."}, trace)
            return {"summary": f"Policy sent {rescuer} to the phone in {room} ({geom}) and told all units."}, trace
        await self._act(run, "command", "radio", {"to": "ALL", "priority": "urgent",
                        "text": f"All units from Command: {tag} phone in {room}: {who}. Rescue team committed; next free crew goes."}, trace)
        return {"summary": f"Policy logged the phone in {room}; every rescue crew is committed."}, trace

    async def _fallback_phone_reached(self, run: str, s: dict, rescuers: list[str], trace: list[dict]) -> tuple[dict, list[dict]]:
        d = s.get("data") or {}
        cand = d.get("rescuer") or s.get("unit")
        rescuer = cand if cand in self.incident.units else (rescuers[0] if rescuers else None)
        if rescuer is None:
            return {"summary": "No crew to bring the occupants out."}, trace
        pid = self._situation_phone(s) or d.get("phone")
        ph = next((q for q in (self.tools.phones_view() or {"phones": []})["phones"] if q["id"] == pid), None)
        rep = (ph or {}).get("report") or {}
        n = rep.get("people")
        r = await self._act(run, "command", "assign", {"unit": rescuer, "task": "exit", "target": "exit"}, trace)
        stair = next((x for x in reversed((r.get("route") or {}).get("rooms") or []) if x.lower().startswith("stair")), "the nearest clear stair")
        discharge = next((e["discharge"] for e in self.twin.exits() if e["label"] == stair and e.get("discharge")), None)
        await self._act(run, "command", "radio", {"to": "ALL", "priority": "urgent",
                        "text": f"All units from Command: {rescuer} has the occupants of the phone and is coming out by {stair}."}, trace)
        await self._act(run, "command", "request_resource", {"what": f"EMS for {n if n else 'the'} occupant{'s' if n != 1 else ''} reported by phone",
                        "reason": f"reported {rep.get('status') or 'status unknown'}" + (", cannot move" if rep.get("can_move") is False else "") +
                                  f"; coming out by {stair}" + (f", meet at {discharge}" if discharge else "")}, trace)
        return {"summary": f"Policy ordered {rescuer} out with the phone's occupants by {stair} and requested EMS."}, trace

    async def _fallback_preplan(self, run: str, phase: str) -> tuple[dict, list[dict]]:
        trace: list[dict] = []
        rec = await self._act(run, phase, "get_building_record", {}, trace)
        ten = await self._act(run, phase, "list_tenants", {}, trace)
        out = await self._act(run, phase, "outside_context", {}, trace)
        floor = await self._act(run, phase, "describe_floor", {}, trace)
        r = rec["record"]
        fx = self.provider.modes["costar"] == "fixture"
        hz = [t for t in ten["tenants"] if t["life_hazard"]]
        sections = [
            {"id": "building", "title": "Building", "items": [
                {"text": f"{r.get('property_type')}, {r.get('stories')} floors, {r.get('rba_sf')} sq ft, built {r.get('year_built')}", "layer": "costar", "synthetic": False},
                {"text": f"Construction: {r.get('construction')}", "layer": "costar", "synthetic": False},
                {"text": "Not in the record: " + ", ".join(rec["missing_fields"]), "layer": "costar", "synthetic": False}]},
            {"id": "occupancy", "title": "Occupancy and life hazards", "items": [
                {"text": f"Suite {t['suite']}: {t['category']}. {t['life_hazard']}" + (f" Hazmat: {t['hazmat']}." if t["hazmat"] else ""), "layer": "costar", "synthetic": False} for t in hz[:6]]},
            {"id": "outside", "title": "Outside", "items": [
                {"text": f"Side {k} faces {v['faces_bearing_deg']} degrees" + (f" ({v['street']})" if v.get("street") else ""), "layer": "vexcel", "synthetic": False} for k, v in out["sides"].items()] + ([
                {"text": f"Nearest hydrant {out['hydrants'][0]['id']}: {out['hydrants'][0]['to_engine_m']} m from Engine 1", "layer": "open-data", "synthetic": False}] if out["hydrants"] else [])},
            {"id": "inside", "title": "Inside", "items": [
                {"text": f"Floor {floor['floor']}: {floor['size_m'][0]} x {floor['size_m'][1]} m, {len(floor['suites'])} suites, ring corridor", "layer": "matterport", "synthetic": floor["synthetic"]}] + [
                {"text": f"{s['label']}: discharge {s['discharge']}, standpipe {'yes' if s['standpipe'] else 'no'}", "layer": "matterport", "synthetic": floor["synthetic"]} for s in floor["stairs"]]},
        ]
        stairs = [s["id"] for s in floor["stairs"]]
        final = {"summary": "Pre-plan assembled by the fallback policy from the three layers, without model prose.", "sections": sections,
                 "tactical": {"attack_stair": stairs[0] if stairs else None, "evac_stair": stairs[1] if len(stairs) > 1 else None, "staging": "one floor below the fire floor",
                              "life_hazards": [{"suite": t["suite"], "why": t["life_hazard"]} for t in hz]},
                 "verify_on_site": [{"text": c["text"], "layers": c["layers"]} for c in rec["cross_checks"]] +
                                   ([{"text": "Sprinklers, elevators and key-holder contacts are not in the fixture record.", "layers": ["costar"]}] if fx else [])}
        return final, trace

    async def _fallback_aar(self, run: str, phase: str) -> tuple[dict, list[dict]]:
        m = self.incident.metrics()
        parts = [f"{k.replace('_', ' ')}: {v}" for k, v in m.items()]
        final = {"summary": "After-action report assembled by the fallback policy from the incident log. " + "; ".join(parts) + ".",
                 "went_well": [], "improve": [], "follow_up": []}
        acc = self.tools.accountability_view() if self.tools.has_phones() else None
        if acc is not None:                           # the hand-off from the tables only, no judgement
            lows = [r["unaccounted"][0] for r in acc["suites"] if r.get("unaccounted") and r["unaccounted"][0] is not None]
            highs = [r["unaccounted"][1] for r in acc["suites"] if r.get("unaccounted") and r["unaccounted"][1] is not None]
            def people(n):
                return f"{n} {'person' if n == 1 else 'people'}" if isinstance(n, int) else "number not reported"
            final["handoff"] = {"reported": [f"{p['id']}: {p.get('room_label')}, {people((p.get('report') or {}).get('people'))}, {(p.get('report') or {}).get('status')}"
                                             for p in (self.tools.phones_view() or {}).get("phones", [])],
                                "found": [e["text"] for e in self.incident.timeline
                                          if e["kind"] in ("victim", "phone", "contact") and ("has reached" in e["text"] or "out of the floor" in e["text"])],
                                "unaccounted": {"low": sum(lows), "high": sum(highs), "why_a_range": "head counts are estimates from floor area; devices are not people; phones without the app are not heard"},
                                "search_next": [q.get("text") for q in acc.get("priorities") or [] if q.get("text")]}
        scan = self.tools.scan_view()
        if scan is not None:                          # facts from the brief only, no judgement
            final["scan"] = {"contributed": [f"{v['id']}: {v.get('state')} person in {v.get('room_label')}, {v.get('where')}" for v in scan["victims"]] +
                                            [f"{f['id']}: {f.get('text') or f.get('kind')}" for f in scan["flags"]],
                             "could_not_tell": scan["cannot_tell"]}
        return final, []
