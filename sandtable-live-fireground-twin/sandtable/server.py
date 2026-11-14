"""Sandtable app server: REST + WebSocket for the command board, wiring of the hub link,
the incident (reflex layer), the command agent and the SIM storyboard director.

    uvicorn sandtable.server:app --port 1810        (or: python -m sandtable.demo)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .adapters import get_provider
from .beacon_link import BeaconLink, accountability, record_vs_phones
from .director import Director
from .hubclient import HubLink
from .incident import Incident
from .scout import feeds
from .scout.session import ScoutSession, floor_number
from .spatial import Twin

log = logging.getLogger("sandtable.server")
BACKLOG_TYPES = {"agent", "radio", "timeline", "alert", "tag"}


class AppState:
    def __init__(self):
        self.provider = get_provider()
        self._clear_local_writes()
        self.twin = Twin(self.provider.twin_raw())
        self.clients: set[asyncio.Queue] = set()
        self.backlog: list[dict] = []
        self.preplan: dict | None = None
        self.aar: dict | None = None
        self.incident = Incident(self.twin, self.provider, self.emit)
        self.hub = HubLink(self.incident)
        self.incident.teleport = self.hub.teleport
        self.incident.on_situation = self._on_situation
        self.incident.agent_busy = self.agent_busy             # crews the agent ordered hold still until its turn ends (replay determinism)
        from .agent import CommandAgent              # imported here so a broken agent cannot stop the reflex layer from loading
        self.agent = CommandAgent(self.provider, self.twin, self.incident, self.emit)
        self.scout = ScoutSession(self.twin, self.provider, self.emit, self.incident)
        self.incident.scout_session = self.scout              # so the agent's tools can reach the brief (incident.scan_brief) and the session
        self.scan_msg: dict | None = None                     # the latest Scan object (sent to new clients after hello)
        self.scout_msg: dict | None = None                    # the latest scout progress message
        self.cot_clients: set[asyncio.Queue] = set()          # Server-Sent Events subscribers of /feed/cot/stream
        self._feed_keys: dict = {}
        self.beacon = BeaconLink(self.emit, self.incident)    # the Beacon module (phones), live on BEACON_URL or the labelled sample
        self.beacons_msg: dict | None = None
        self.accountability_msg: dict | None = None
        self._acc_key: str | None = None
        self._acc_t = 0.0
        self.director = Director(self)
        self.situations: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._agent_running = False
        self._last_hello: str | None = None

    def _clear_local_writes(self) -> None:
        """Each incident starts clean: tags and corrections written by the previous one are cleared. Fixture mode: local files.
        Live Matterport in dry run: the private log of pins the agent would have written (otherwise every run's twin, and so every
        prompt, would carry the pins of the runs before it and recorded turns could never replay)."""
        mp = self.provider.matterport
        if hasattr(mp, "clear_overlay"):
            mp.clear_overlay()
        elif getattr(mp, "dry_run", False) and hasattr(mp, "_dryrun_log"):
            log_path = mp._dryrun_log()
            if log_path is not None and log_path.exists():
                log_path.unlink()
            if isinstance(getattr(mp, "_twin", None), dict):
                mp._twin["tags"] = [t for t in mp._twin.get("tags", []) if not str(t.get("id", "")).startswith("dryrun-")]
        if hasattr(self.provider.costar, "clear_corrections"):
            self.provider.costar.clear_corrections()

    # ---- broadcasting (safe to call from any sync code running on the event loop)
    def emit(self, msg: dict) -> None:
        if msg.get("type") in BACKLOG_TYPES:
            self.backlog.append(msg)
            if len(self.backlog) > 600:
                del self.backlog[:200]
        elif msg.get("type") == "preplan":
            self.preplan = msg
        elif msg.get("type") == "aar":
            self.aar = msg
        elif msg.get("type") == "scan":
            self.scan_msg = msg
            self._forward_scan_pucks(msg)
        elif msg.get("type") == "beacons":
            self.beacons_msg = msg
        elif msg.get("type") == "accountability":
            self.accountability_msg = msg
        elif msg.get("type") == "scout":
            self.scout_msg = msg
        elif msg.get("type") == "reset":
            self.backlog.clear()
            self.scan_msg, self.scout_msg = None, None
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def hello(self) -> dict:
        inc, reg = self.incident, self.incident.registration
        scale = reg.scale if reg else None
        return {"type": "hello", "simulated": inc.simulated, "drivable": inc.drivable, "hub": {**inc.hub, "connected": self.hub.alive()},
                "modes": {**self.provider.modes, "llm": self.agent.backend}, "labels": self.provider.labels(),
                "registration": reg.to_dict() if reg else None, "exercise": bool(scale and scale > 1.5), "no_motion_s": config.NO_MOTION_S}

    def state(self) -> dict:
        st = self.incident.state()
        st["agent_busy"] = self.agent_busy()
        st["storyboard"] = self.director.status()
        st["hub_connected"] = self.hub.alive()
        st["beacon"] = self.beacon.state_info()
        return st

    def agent_busy(self) -> bool:
        return self._agent_running or not self.situations.empty()

    # ---- lifecycle
    async def startup(self) -> None:
        self._tasks = [asyncio.create_task(self.hub.run()), asyncio.create_task(self._state_loop()), asyncio.create_task(self._agent_loop()),
                       asyncio.create_task(self.beacon.run())]

    async def shutdown(self) -> None:
        self.director.stop()
        for t in self._tasks:
            t.cancel()

    async def _state_loop(self) -> None:
        period = 1.0 / config.STATE_HZ
        while True:
            try:
                self.incident.tick()
                h = self.hello()
                key = json.dumps(h, sort_keys=True, default=str)
                if key != self._last_hello:
                    self._last_hello = key
                    self.emit(h)
                self.emit(self.state())
                self._feed_tick()
                self._accountability_tick()
            except Exception:
                log.exception("state loop")
            await asyncio.sleep(period)

    # ---- beacon mode
    def _forward_scan_pucks(self, scan: dict) -> None:
        """A scout scan's registered pucks are the Beacon module's pucks too: forward them (live server only; tolerant)."""
        pucks = [{"id": p["id"], "x": p["x"], "y": p["y"], "sigma_m": p.get("sigma_m")} for p in scan.get("pucks") or [] if p.get("x") is not None]
        if not pucks or self.beacon.mode != "auto" or scan.get("sample"):
            return
        try:
            asyncio.get_running_loop().create_task(self.beacon.remote("POST", "/api/pucks", {"pucks": pucks, "room": scan.get("room"), "floor": scan.get("floor")}))
        except RuntimeError:
            pass

    def accountability(self) -> dict:
        return accountability(self.beacon.beacons, self.incident, self.twin, self.provider)

    def _accountability_tick(self) -> None:
        now = time.time()
        if now - self._acc_t < 1.0:
            return
        self._acc_t = now
        acc = self.accountability()
        key = json.dumps(acc, sort_keys=True, default=str)
        if key != self._acc_key:
            self._acc_key = key
            self.emit(acc)

    def report(self) -> dict:
        """sandtable.report/1: what a command post keeps. A saved report stands alone: every person carries its drawable geometry."""
        inc, prov = self.incident, self.provider
        now = inc.clock()
        rel = (lambda t: round(t - inc.started_t, 1) if (t and inc.started_t) else None)
        prop = prov.site()["property"]
        missing = [k for k in ("sprinklers", "elevators", "owner", "property_manager", "stories", "year_built", "construction") if prop.get(k) in (None, "", {})]
        labels = prov.labels()
        b = inc.beacons or self.beacon.beacons or {}
        raw_phones = {p["id"]: p for p in b.get("phones") or []}
        progress = {"located": "located", "assigned": "assigned", "reached": "reached", "evacuating": "reached", "out": "out"}

        def phone_geometry(p: dict | None) -> dict | None:
            if not p:
                return None
            g = p.get("geometry")
            out = {"kind": g, "pruned": [{k: q.get(k) for k in ("x", "y", "reason")} for q in p.get("pruned") or []]}
            if g == "fix":
                out.update({"x": p.get("x"), "y": p.get("y"), "sigma_m": p.get("sigma_m")})
            elif g == "ring" and p.get("ring"):
                rg = p["ring"]
                out.update({"puck": rg.get("puck"), "cx": rg.get("cx"), "cy": rg.get("cy"), "r_m": rg.get("r_m"), "sigma_m": rg.get("sigma_m"), "arcs": rg.get("arcs")})
            elif g == "candidates":
                out["candidates"] = [{k: c.get(k) for k in ("x", "y", "sigma_m", "weight", "room", "room_label")} for c in p.get("candidates") or []]
            elif g == "zone" and p.get("zone"):
                out.update({k: p["zone"].get(k) for k in ("room", "room_label", "band", "prob")})
            return out
        people = []
        for e in sorted(inc.phones.values(), key=lambda q: q["id"]):
            rp = (e.get("raw") or raw_phones.get(e["id"]) or {}).get("report") or {}
            raw = e.get("raw") or raw_phones.get(e["id"])
            gd = phone_geometry(raw) or {}
            reasons = [q.get("reason") or "" for q in gd.get("pruned") or []]
            searched = [r for r in reasons if r.startswith("searched by")]
            how = (f"two candidates; a crew's search removed one ({searched[0]})" if searched else
                   f"two candidates; {e['found_by_search']} searched the first and found the person there" if e.get("found_by_search") else
                   f"two candidates; the twin removed one ({reasons[0]})" if reasons and e.get("geometry") == "fix" else
                   {"fix": f"fix ({e.get('method')})", "candidates": "two candidates, not resolved", "ring": "range only (a ring)",
                    "zone": "Bluetooth zone only"}.get(e.get("geometry"), str(e.get("geometry"))))
            people.append({"id": e["id"], "source": "phone", "status": e["status"], "state": progress.get(e["status"], e["status"]), "people": e.get("people"),
                           "how_found": how,
                           "report": {"status": e.get("report_status"), "people": e.get("people"), "can_move": e.get("can_move"), "text": rp.get("text")},
                           "where": e.get("label"), "room": e.get("room"), "room_label": e.get("room_label"), "geometry": e.get("geometry"),
                           "geometry_detail": gd, "text": e.get("text"),
                           "located_t_rel": rel(e.get("located_t")), "assigned": e.get("rescuer"), "assigned_t_rel": rel(e.get("assigned_t")),
                           "reached_t_rel": rel(e.get("reached_t")), "out_t_rel": rel(e.get("out_t")), "simulated": e.get("simulated", True), "sample": e.get("sample", False)})
        scan_v = {v["id"]: v for v in (inc.scan or {}).get("victims") or []}
        for v in sorted(inc.victims.values(), key=lambda q: q["id"]):
            sv = scan_v.get(v["id"]) or {}
            people.append({"id": v["id"], "source": "radar", "status": v["status"], "state": progress.get(v["status"], v["status"]), "people": 1,
                           "how_found": "UWB radar scan: a breathing or moving echo" + (" (hand-made sample)" if (inc.scan or {}).get("sample") else " (simulated)"),
                           "report": {"status": None, "people": None, "can_move": None, "text": None},
                           "where": v.get("label"), "room": v.get("room"), "room_label": v.get("room_label"), "geometry": "fix",
                           "geometry_detail": {"kind": "fix", "x": sv.get("x"), "y": sv.get("y"), "sigma_m": sv.get("sigma_m"), "pruned": []},
                           "radar": {"state": v.get("state"), "breathing_bpm": sv.get("breathing_bpm"), "confidence": sv.get("confidence")}, "text": sv.get("text") or v.get("what"),
                           "located_t_rel": rel(v.get("located_t")), "assigned": v.get("rescuer"), "assigned_t_rel": rel(v.get("assigned_t")),
                           "reached_t_rel": rel(v.get("reached_t")), "out_t_rel": rel(v.get("out_t")),
                           "simulated": bool((inc.scan or {}).get("simulated", True)), "sample": bool((inc.scan or {}).get("sample"))})
        md = inc.mayday or getattr(inc, "_last_mayday", None)
        if md:
            u = inc.units[md["unit"]]
            people.append({"id": md["unit"], "source": "crew", "status": u["status"], "how_found": f"tagged crew: {md.get('reason')} mayday", "state": "out" if u["status"] == "out" else "reached" if inc.contact_done else "located",
                           "people": 1, "report": {"status": "mayday", "people": 1, "can_move": None, "text": f"mayday ({md.get('reason')})"},
                           "where": md.get("room_label"), "room": md.get("room"), "room_label": md.get("room_label"), "geometry": "fix",
                           "geometry_detail": {"kind": "fix", "x": u.get("x"), "y": u.get("y"), "sigma_m": u.get("sigma_m"), "pruned": []}, "text": f"mayday ({md.get('reason')})",
                           "located_t_rel": rel(md.get("t")), "assigned": None, "assigned_t_rel": None, "reached_t_rel": None, "out_t_rel": rel(md.get("resolved_t")),
                           "simulated": inc.simulated, "sample": False})
        crews = [{**{k: u.get(k) for k in ("id", "label", "kind", "physical", "status", "room", "room_label", "time_in_s", "air_pct", "x", "y", "sigma_m")},
                  "assignment": u.get("assignment"), "path": list(u.get("_trail") or [])} for u in inc.units.values()]
        pucks = [{**{k: p.get(k) for k in ("id", "x", "y", "sigma_m", "room", "placed")}, "source": "beacon"} for p in b.get("pucks") or []]
        pucks += [{**{k: p.get(k) for k in ("id", "x", "y", "sigma_m")}, "source": "scout", "placed": "self-located"} for p in (inc.scan or {}).get("pucks") or []]
        acc = self.accountability()
        ended = next((e["t"] for e in reversed(inc.timeline) if e["kind"] == "par"), None) if inc.phase == "terminated" else None
        elapsed = round((ended or now) - inc.started_t, 1) if inc.started_t else 0.0
        differences = [{"id": m.get("id"), "kind": "twin", "text": m.get("text"), "source": "scout radar (" + ("sample" if (inc.scan or {}).get("sample") else "simulated") + ")",
                        "at": (m.get("where") or {}).get("at"), "room": (inc.scan or {}).get("room")} for m in (inc.scan or {}).get("mismatches") or []]
        try:                                            # the twin's georeference against the record's property point: flag, never snap
            out_l = self.twin.floor()["outline"]
            glat, glon = self.twin.to_geo(sum(p[0] for p in out_l) / len(out_l), sum(p[1] for p in out_l) / len(out_l))
            off = math.hypot((glat - float(prop["lat"])) * 111320.0, (glon - float(prop["lon"])) * 111320.0 * math.cos(math.radians(glat)))
            if off > 50.0:
                differences.append({"id": "georef", "kind": "twin", "source": "cross-check of matterport and costar",
                                    "text": f"The twin's floor sits about {off:.0f} m from the building record's property point: check on the aerial "
                                            "whether the record's point is the parcel or the tower (not corrected automatically)."})
        except (KeyError, TypeError, ValueError):
            pass
        differences += [{"id": c.get("id"), "kind": "record", "text": c.get("text"), "source": "cross-check of " + " and ".join(c.get("layers") or []),
                         "room": next((r["id"] for r in self.twin.rooms("suite") if c.get("suite") and r.get("suite") == c.get("suite")), None)}
                        for c in prov.site().get("cross_checks") or []]
        if record_vs_phones(acc):                       # a live record that says "empty" where phones report people (contract section 4)
            differences.append(record_vs_phones(acc))
        word = {"fixture": "fixture", "live": "real"}
        bsrc = self.beacon.source
        beacon_word = "sample" if bsrc == "sample" else "replay" if bsrc == "replay" else ("simulated" if b.get("simulated", True) else "real") if b else "none"
        scout_word = None if not inc.scan else "sample" if inc.scan.get("sample") else "simulated"
        aar_backend = (self.aar or {}).get("backend")
        pre_backend = (self.preplan or {}).get("backend")

        def agent_label(backend):
            return {"replay": "replay of a recorded real model run", "reflex-fallback": "deterministic fallback policy (no model)",
                    "anthropic": "live model (Anthropic API)", "claude-cli": "live model (Claude CLI)"}.get(backend, backend)
        out_people = [p for p in people if p["state"] == "out"]
        left = [p for p in people if p["state"] != "out" and p["source"] != "crew"]
        rows = acc["rows"]
        summary = (f"{'Simulated exercise' if inc.simulated else 'Incident'} at {prop.get('address')}, {prop.get('city')}: phase {inc.phase}, {elapsed:.0f} s. "
                   f"{len([p for p in people if p['source'] != 'crew'])} located ({', '.join(p['id'] for p in people if p['source'] != 'crew') or 'none'}); "
                   f"{len(out_people)} brought out ({', '.join(p['id'] for p in out_people) or 'none'}); "
                   f"{len(left)} still inside or not reached ({', '.join(p['id'] for p in left) or 'none'}). "
                   f"Unaccounted on the floor: {acc['totals'].get('unaccounted_low')} to {acc['totals'].get('unaccounted_high')} people (estimate). "
                   f"Sources: Beacon {beacon_word}, UWB {'simulated' if inc.simulated else 'real'}" + (f", scout radar {scout_word}" if scout_word else "") + ".")
        return {"type": "report", "schema": "sandtable.report/1", "generated_t": round(time.time(), 2),
                "simulated": bool(inc.simulated) or bool(b.get("simulated", True)),
                "summary": summary,
                "incident": {"id": f"inc-{int(inc.started_t)}" if inc.started_t else None, "address": f"{prop.get('address')}, {prop.get('city')}",
                             "started_t": round(inc.started_t, 2) if inc.started_t else None, "ended_t": round(ended, 2) if ended else None,
                             "floor": self.twin.exercise_floor, "floor_label": self.twin.floor()["label"], "phase": inc.phase,
                             "elapsed_s": elapsed, "storyboard": self.director.status()},
                "building": {"address": prop.get("address"), "city": prop.get("city"), "property_type": prop.get("property_type"), "stories": prop.get("stories"),
                             "year_built": prop.get("year_built"), "construction": prop.get("construction"), "rba_sf": prop.get("rba_sf"), "missing": missing,
                             "life_hazards": [{"suite": t.get("suite"), "text": t["life_hazard"]} for t in prov.costar.list_tenants(floor_number(self.twin)) if t.get("life_hazard")]},
                "provenance": {"costar": {**(labels.get("costar") or {}), "word": word.get(prov.modes.get("costar"), prov.modes.get("costar"))},
                               "matterport": {**(labels.get("matterport") or {}), "word": word.get(prov.modes.get("matterport"), prov.modes.get("matterport"))},
                               "vexcel": {**(labels.get("vexcel") or {}), "word": word.get(prov.modes.get("vexcel"), prov.modes.get("vexcel"))},
                               "uwb": {"word": "simulated" if inc.simulated else "real", "source": inc.hub.get("source"),
                                       "text": "Crew positions from the uwb-kit simulator (no board attached)" if inc.simulated else "Crew positions from real UWB boards"},
                               "beacon": {**self.beacon.link_info(), "word": beacon_word, "simulated": bool(b.get("simulated", True)), "sample": bool(b.get("sample")),
                                          "text": {"sample": "Phones: hand-made sample, not a result", "replay": "Phones: replay of a recorded run of the simulated Beacon module",
                                                   "simulated": "Phones: the Beacon module's simulator", "real": "Phones: the Beacon module on real pucks",
                                                   "none": "Phones: no Beacon data"}.get(beacon_word)},
                               "scout": {"word": scout_word, "engine": self.scout.engine,
                                         "text": "Radar: hand-made sample scan, not a result" if scout_word == "sample" else "Radar: simulated pucks (no hardware)"} if inc.scan else None,
                               "agent": {"word": "recorded" if pre_backend == "replay" else "fallback" if pre_backend == "reflex-fallback" else "live" if pre_backend else None,
                                         "backend": self.agent.backend, "preplan": agent_label(pre_backend), "aar": agent_label(aar_backend)}},
                "people": people, "accountability": acc, "crews": crews, "pucks": pucks,
                "hazards": [{k: h[k] for k in ("id", "ref", "kind", "label", "note")} for h in inc.hazards],
                "differences": differences,
                "timeline": [{"t_rel": e["t_rel"], "kind": e["kind"], "text": e["text"]} for e in inc.timeline if e["kind"] != "unit"],
                "agent": {"preplan_summary": (self.preplan or {}).get("summary"), "preplan_source": agent_label(pre_backend),
                          "aar": {k: (self.aar or {}).get(k) for k in ("summary", "went_well", "improve", "follow_up", "backend")} if self.aar else None,
                          "aar_source": agent_label(aar_backend)},
                "limits": ["Exercise in simulation: no puck, phone or tag hardware was in the loop" if inc.simulated else "Positions from real UWB boards",
                           "Phones: a ring is a range, two candidates are two candidates; only opt-in phones are identified; devices are not people",
                           "Expected head counts are estimates; unaccounted is a range",
                           "Building record, twin and aerial are fixtures unless the provenance says real"]}

    # ---- outside feeds (GeoJSON, Cursor-on-Target)
    def feed_units(self) -> list[dict]:
        return list(self.incident.units.values())

    def cot_now(self) -> list[tuple[str, str]]:
        inc = self.incident
        return feeds.cot_events(inc.scan, self.feed_units(), self.twin, time.time(), units_simulated=inc.simulated, victims=inc.victims,
                                beacons=self.beacon.beacons, phones=inc.phones)

    def _feed_tick(self) -> None:
        """Pushes CoT events to the SSE subscribers when a unit changes room, a scan lands or a located person's status changes."""
        inc = self.incident
        keys = {f"unit.{u['id']}": u["room"] for u in inc.units.values() if u["room"]}
        keys.update({f"victim.{v['id']}": v["status"] for v in inc.victims.values()})
        b = self.beacon.beacons or {}
        keys.update({f"phone.{p['id']}": (p.get("geometry"), (p.get("report") or {}).get("status"), (inc.phones.get(p["id"]) or {}).get("status"))
                     for p in b.get("phones") or []})
        keys["scan"] = (inc.scan or {}).get("id")
        changed = {k for k, v in keys.items() if self._feed_keys.get(k) != v}
        self._feed_keys = keys
        if not changed or not self.cot_clients:
            return
        sid = keys["scan"]
        want = set()
        if "scan" in changed and sid:
            want.add(f"sandtable.{sid}.")
        for k in changed:
            if k.startswith("unit."):
                want.add(f"sandtable.unit.{k[5:]}")
            elif k.startswith("victim.") and sid:
                want.add(f"sandtable.{sid}.{k[7:]}")
            elif k.startswith("phone."):
                want.add(f"sandtable.beacon.{k[6:]}")
                want.add(f"sandtable.beacon.{k[6:]}.")
        for uid, xml in self.cot_now():
            if any(uid == w or (w.endswith(".") and uid.startswith(w)) for w in want):
                for q in list(self.cot_clients):
                    try:
                        q.put_nowait(xml)
                    except asyncio.QueueFull:
                        pass

    def _on_situation(self, situation: dict) -> None:
        self.situations.put_nowait(situation)

    async def _agent_loop(self) -> None:
        while True:
            s = await self.situations.get()
            self._agent_running = True
            try:
                if s["kind"] == "all_out":
                    await self.agent.run_after_action()
                else:
                    await self.agent.handle(s)
            except Exception:
                log.exception("agent failed on %s", s.get("kind"))
            finally:
                self._agent_running = False

    # ---- operations
    async def alarm(self) -> None:
        if self.incident.phase != "standby":
            return
        self.incident.start()

        async def run():
            self._agent_running = True
            try:
                await self.agent.run_preplan()
            except Exception:
                log.exception("pre-plan failed")
            finally:
                self._agent_running = False
                self.incident.begin_operations()
        self._tasks.append(asyncio.create_task(run()))

    async def reset(self) -> None:
        while not self.situations.empty():
            self.situations.get_nowait()
        self.preplan, self.aar = None, None
        self._clear_local_writes()
        self.twin.data["tags"] = [t for t in self.twin.data.get("tags", []) if not str(t.get("id", "")).startswith(("obs-", "dryrun-"))]
        self.agent.reset()
        self.scout.reset()
        await self.beacon.reset()
        self.incident.reset()


STATE: AppState | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global STATE
    STATE = AppState()
    await STATE.startup()
    yield
    await STATE.shutdown()


app = FastAPI(title="Sandtable", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def S() -> AppState:
    assert STATE is not None
    return STATE


@app.get("/healthz")
async def healthz():
    return {"ok": True, "hub_connected": S().hub.alive(), "simulated": S().incident.simulated, "phase": S().incident.phase}


@app.get("/api/bundle")
async def bundle():
    s = S()
    return {"site": s.provider.site(), "twin": s.provider.twin_raw(), "aerial": s.provider.aerial(), "labels": s.provider.labels(),
            "config": {"no_motion_s": config.NO_MOTION_S, "state_hz": config.STATE_HZ, "llm_backend": s.agent.backend,
                       "exercise": bool(s.incident.registration and s.incident.registration.scale > 1.5)}}


@app.get("/api/state")
async def state():
    return S().state()


@app.get("/api/preplan")
async def preplan():
    return S().preplan


@app.get("/api/vision/{image_id}.png")
async def vision_image(image_id: str):
    """The exact picture the agent's vision tool sent to the model (aerial + footprint, side letters, hydrants, engine)."""
    from fastapi.responses import FileResponse
    if not image_id.replace("_", "").isalnum():
        raise HTTPException(400, "bad image id")
    path = config.DATA_DIR / f"annotated_{image_id}.png"
    if not path.is_file():
        try:
            S().agent.tools.annotate_aerial(image_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
    return FileResponse(path, media_type="image/png")


@app.get("/api/live/config")
async def live_config():
    """What the browser needs for the live Matterport Showcase embed. The SDK key is domain-restricted to localhost and must reach
    the browser; it is returned only when MATTERPORT_MODE is live (nulls in fixture mode) and is never logged."""
    mode = S().provider.modes.get("matterport", "fixture")
    live = mode == "live"
    return {"matterport": {"mode": mode, "sdk_key": config.env("MATTERPORT_SDK_KEY") or None if live else None,
                           "model_id": (getattr(S().provider.matterport, "model_id", None) or getattr(getattr(S().provider.matterport, "client", None), "model_id", None)
                                        or config.env("MATTERPORT_MODEL_ID") or None) if live else None}}


@app.get("/api/aar")
async def aar():
    return S().aar


@app.post("/api/alarm")
async def alarm():
    await S().alarm()
    return {"ok": True, "phase": S().incident.phase}


@app.post("/api/storyboard")
async def storyboard(body: dict):
    s = S()
    mode = body.get("mode") or ("auto" if s.incident.drivable else "assist")
    script = body.get("script") or "beacon"                      # the HTTP default is the Beacon exercise; "scout" and "mayday" stay
    if script not in ("beacon", "scout", "mayday"):
        raise HTTPException(400, "script must be 'beacon', 'scout' or 'mayday'")
    if body.get("action") == "stop":
        s.director.stop()
    elif mode == "auto" and not s.incident.drivable:
        raise HTTPException(409, "the full autopilot only runs on a simulated hub; use mode 'assist' with real boards")
    else:
        beacon = body.get("beacon") or "story"                   # beacon script: replay the recorded simulated run, or "live"
        if beacon not in ("story", "live"):
            raise HTTPException(400, "beacon must be 'story' or 'live'")
        s.director.start(mode, script, beacon)
    return {"ok": True, "storyboard": s.director.status()}


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@app.post("/api/scout/deploy")
async def scout_deploy(request: Request):
    """Throw the pucks: starts a scan of a room (default: the exercise's fire room)."""
    s, body = S(), await _json_body(request)
    if s.incident.phase in ("standby", "terminated"):
        raise HTTPException(409, "no incident is active: dispatch the alarm first")
    if s.scout.running:
        raise HTTPException(409, "a scan is already running")
    room = body.get("room")
    try:
        if room:
            s.twin.room(room)
        n = int(body.get("n") or 5)
        seed = int(body["seed"]) if body.get("seed") is not None else None      # None: the build-time demo seed
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    if not 1 <= n <= 8:
        raise HTTPException(400, "n must be between 1 and 8 pucks")
    s.scout.start(room, n, seed)
    return {"ok": True, "phase": s.scout.phase, "room": room, "n": n}


@app.get("/api/scout")
async def scout_status():
    sc = S().scout
    return {"phase": sc.phase, "room": sc.room, "engine": sc.engine, "scan": sc.scan}


@app.get("/api/scout/truth")
async def scout_truth():
    """Simulation ground truth, for evaluation scripts only. There is none on real boards or for the sample engine."""
    s = S()
    if not s.incident.simulated:
        raise HTTPException(404, "no ground truth on real boards")
    truth = s.scout.truth()
    if truth is None:
        raise HTTPException(404, "no simulated scan yet (the sample engine has no ground truth)")
    return truth


@app.get("/api/beacons")
async def beacons():
    """The latest Beacons object relayed from the Beacon module (or the labelled sample), with the link status; null when off."""
    s = S()
    return {**s.beacon.beacons, "link": s.beacon.link_info()} if s.beacon.beacons else None


@app.post("/api/beacon/pucks")
async def beacon_pucks(request: Request):
    """Pucks are in: forwarded to the Beacon server's POST /api/pucks ({"pucks": [...], "room"?} or {"n": 3, "room"?});
    from now on its opt-in phones are targets in this incident."""
    s, body = S(), await _json_body(request)
    if s.incident.phase in ("standby", "terminated"):
        raise HTTPException(409, "no incident is active: dispatch the alarm first")
    if not body.get("pucks") and not body.get("n"):
        body = {"n": 3, **body}
    out = await s.beacon.pucks_in(body)
    return {"ok": True, "forwarded": out, "link": s.beacon.link_info()}


@app.get("/api/accountability")
async def accountability_view():
    return S().accountability()


@app.get("/api/report")
async def report():
    return S().report()


@app.get("/api/report.md")
async def report_md():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(report_markdown(S().report()), media_type="text/markdown; charset=utf-8")


def report_markdown(r: dict) -> str:
    """sandtable.report/1 as Markdown, for a command post's records."""
    def esc(v):
        return "" if v is None else str(v).replace("|", "/").replace("\n", " ")
    inc, bld, prov = r["incident"], r["building"], r["provenance"]
    lines = [f"# Incident report: {inc['address']}", "", r.get("summary") or "", "",
             f"*{'SIMULATED exercise' if r['simulated'] else 'Live incident'}. Phase: {inc['phase']}. Elapsed: {inc['elapsed_s']} s. "
             f"Generated {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(r['generated_t']))}.*", "",
             "## Data provenance", ""]
    for k in ("costar", "matterport", "vexcel"):
        if prov.get(k):
            lines.append(f"- {k}: {prov[k].get('text')}")
    bl = prov.get("beacon") or {}
    lines.append(f"- beacon: {'connected to ' + bl.get('url', '') if bl.get('connected') else 'not connected'}; source {bl.get('source')}"
                 + ("; SAMPLE (hand-made, not a result)" if bl.get("sample") else "; simulated" if bl.get("simulated") else ""))
    lines.append(f"- uwb: {prov['uwb']['word']} ({prov['uwb'].get('source')}); agent: pre-plan {prov['agent'].get('preplan')}, after-action {prov['agent'].get('aar')}")
    if prov.get("scout"):
        lines.append(f"- scout radar: {prov['scout']['word']}: {prov['scout']['text']}")
    lines += ["", "## Building", "", f"{esc(bld.get('property_type'))}, {esc(bld.get('stories'))} floors, built {esc(bld.get('year_built'))}, {esc(bld.get('construction'))}.",
              f"Not in the record: {', '.join(bld.get('missing') or []) or 'nothing listed'}.", ""]
    lines += ["## People", "", "| Id | Source | People | Report | Where | Geometry | How found | State | Crew | Located (s) | Assigned (s) | Reached (s) | Out (s) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in r["people"]:
        rep = p.get("report") or {}
        lines.append(f"| {p['id']} | {p['source']}{' (sample)' if p.get('sample') else ''} | {esc(p.get('people'))} | {esc(rep.get('status'))} | {esc(p.get('where'))} | "
                     f"{esc(p.get('geometry'))} | {esc(p.get('how_found'))} | {p['state']} | {esc(p.get('assigned'))} | {esc(p.get('located_t_rel'))} | "
                     f"{esc(p.get('assigned_t_rel'))} | {esc(p.get('reached_t_rel'))} | {esc(p.get('out_t_rel'))} |")
    acc = r["accountability"]
    lines += ["", "## Accountability", "", "| Suite | Tenant | Life hazard | Expected | Phones | Reported | Located | Devices | Unaccounted | State | Crew | Reached |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in acc["rows"]:
        exp, un = row.get("expected") or {}, row.get("unaccounted") if isinstance(row.get("unaccounted"), dict) else {}
        lines.append(f"| {esc(row.get('suite'))} | {esc(row.get('tenant'))} | {esc(row.get('life_hazard'))} | "
                     f"{esc(exp.get('people'))} ({esc(exp.get('low'))}-{esc(exp.get('high'))}) | {esc(row.get('phones_opt_in'))} | {esc(row.get('people_reported'))} | "
                     f"{esc(row.get('located'))} | {esc(row.get('cold_devices'))} | {esc(un.get('low'))}-{esc(un.get('high'))} | {esc(row.get('state'))} | "
                     f"{esc(row.get('assigned'))} | {'yes' if row.get('reached') else ''} |")
    lines += ["", "## Differences found", ""] + ([f"- {d['kind']} {esc(d.get('id'))}: {esc(d.get('text'))} ({esc(d.get('source'))})" for d in r.get("differences") or []] or ["- none"])
    lines += ["", "## Crews", ""] + [f"- {c['label']}: {c['status']}, {esc(c.get('room_label'))}, air {esc(c.get('air_pct'))}%" for c in r["crews"]]
    lines += ["", "## Hazards", ""] + ([f"- {h['label']}: {h['kind']} {esc(h.get('note'))}" for h in r["hazards"]] or ["- none"])
    lines += ["", "## Timeline", ""] + [f"- {e['t_rel']:>6} s  {e['kind']}: {esc(e['text'])}" for e in r["timeline"]]
    ag = r.get("agent") or {}
    lines += ["", "## Command agent", "", f"Pre-plan: {esc(ag.get('preplan_summary'))}", ""]
    if ag.get("aar"):
        lines.append(f"After action ({esc(ag['aar'].get('backend'))}): {esc(ag['aar'].get('summary'))}")
    lines += ["", "## Limits", ""] + [f"- {x}" for x in r["limits"] + acc.get("limits", [])]
    return "\n".join(lines) + "\n"


@app.get("/feed/scan.geojson")
async def feed_geojson():
    s = S()
    fc = feeds.scan_to_geojson(s.incident.scan, s.feed_units(), s.twin, units_simulated=s.incident.simulated, victims=s.incident.victims,
                               beacons=s.beacon.beacons, phones=s.incident.phones)
    return JSONResponse(fc, media_type="application/geo+json", headers={"Cache-Control": "no-store"})


@app.get("/feed/cot.xml")
async def feed_cot():
    s = S()
    xml = feeds.scan_to_cot(s.incident.scan, s.feed_units(), s.twin, time.time(), units_simulated=s.incident.simulated, victims=s.incident.victims,
                            beacons=s.beacon.beacons, phones=s.incident.phones)
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "no-store"})


@app.get("/feed/cot/stream")
async def feed_cot_stream(once: int = 0):
    """Server-Sent Events, one CoT <event> per message: everything current on connect, then an event whenever a unit
    changes room, a scan lands or a located person's status changes. `?once=1` sends the current picture and closes."""
    s = S()
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)

    def frame(xml: str) -> str:
        return "event: cot\ndata: " + xml.replace("\n", "\ndata: ") + "\n\n"

    async def gen():
        try:
            yield ": sandtable cursor-on-target stream (how=\"m-s\" marks simulated positions; not tested against a TAK server)\n\n"
            for _uid, xml in s.cot_now():
                yield frame(xml)
            if once:
                return
            s.cot_clients.add(q)
            while True:
                try:
                    xml = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield frame(xml)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            s.cot_clients.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/inject")
async def inject(body: dict):
    try:
        hz = S().incident.mark_hazard(body["ref"], body.get("kind", "blocked"), body.get("note", ""), source="inject")
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "hazard": hz}


@app.post("/api/mayday")
async def mayday(body: dict):
    s = S()
    if s.incident.phase in ("standby", "terminated"):
        raise HTTPException(409, "no incident is running")
    s.incident.declare_mayday(body.get("unit", "E1"), "manual", "command")
    return {"ok": True}


@app.post("/api/unit/move")
async def unit_move(body: dict):
    try:
        S().incident.move_unit(body["unit"], float(body["x"]), float(body["y"]))
    except PermissionError as exc:
        raise HTTPException(409, str(exc))
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/unit/assign")
async def unit_assign(body: dict):
    """Manual order from the board (the incident commander can always override the agent)."""
    try:
        return S().incident.assign(body["unit"], body.get("task", "search"), body.get("target"), source="command")
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/registration")
async def registration(body: dict):
    try:
        reg = S().incident.set_registration(body.get("pairs"), body.get("scale"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "registration": reg}


@app.post("/api/reset")
async def reset():
    S().director.stop()
    await S().reset()
    return {"ok": True}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    s = S()
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    try:
        await sock.send_text(json.dumps(s.hello()))
        for obj in (s.preplan, s.aar, s.scout_msg, s.scan_msg, s.beacons_msg, s.accountability_msg):
            if obj:
                await sock.send_text(json.dumps(obj))
        for msg in list(s.backlog):
            await sock.send_text(json.dumps(msg))
        s.clients.add(q)
        while True:
            msg = await q.get()
            await sock.send_text(json.dumps(msg))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        s.clients.discard(q)


app.mount("/fixtures", StaticFiles(directory=str(config.FIXTURES)), name="fixtures")
LIVE_VEXCEL = getattr(config, "LIVE_DIR", config.DATA_DIR / "live") / "vexcel"   # live Vexcel images (private: under data/live/), served read-only
LIVE_VEXCEL.mkdir(parents=True, exist_ok=True)
app.mount("/live/vexcel", StaticFiles(directory=str(LIVE_VEXCEL)), name="live-vexcel")
if (config.WEB_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
else:
    @app.get("/")
    async def placeholder():
        return HTMLResponse("<h1>Sandtable</h1><p>The command board (web/) is not built yet. The API is up: see /api/state.</p>")
