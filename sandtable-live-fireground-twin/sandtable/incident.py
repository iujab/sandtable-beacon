"""Incident state and the deterministic reflex layer.

No web framework and no LLM in here: positions come in (`on_hello`, `on_anchors`,
`on_pos`, `on_event`), `tick()` runs the reflexes (no-motion mayday, followers,
contact, all-out), and the action methods are what the command agent calls.
Everything that happens is appended to the timeline and broadcast through `emit`.
"""
from __future__ import annotations

import math
import time
from typing import Callable

from . import config
from .spatial import Registration, Twin, default_registration, fit_similarity

AUTO_TASKS = {"rit_rescue", "exit", "search", "stage", "rescue"}        # tasks a simulated crew walks by itself
HELD_WHILE_AGENT_BUSY = ("agent", "reflex")   # followers started by these sources hold still while the agent handles a situation
RESCUE_TASKS = ("rescue", "rit_rescue", "search")                        # with a located person (V1, B1) as target: a rescue
PHASES = ("standby", "preplan", "operations", "mayday", "rescue", "egress", "terminated")


class Incident:
    def __init__(self, twin: Twin, provider, emit: Callable[[dict], None], clock: Callable[[], float] = time.time):
        self.twin, self.provider, self.emit, self.clock = twin, provider, emit, clock
        self.simulated = True
        self.hub = {"connected": False, "source": None, "profile": None}
        self.anchors_site: dict = {}
        self.registration: Registration | None = None
        self.teleport: Callable[[str, float, float], None] | None = None     # set by the server (sim only)
        self.on_situation: Callable[[dict], None] | None = None               # set by the server
        self.scout_session = None                                             # set by the server (sandtable.scout.session.ScoutSession)
        self.agent_busy: Callable[[], bool] | None = None                     # set by the server: True while the agent handles a situation
        self.units: dict[str, dict] = {}
        self._tags: list[str] = []
        self._seq = 0
        self._hz = 0
        self._last_tick: float | None = None
        self._reset_state()
        self._make_units([])

    @property
    def drivable(self) -> bool:
        """True only for the kit's own simulator, where software may move a tag (POST /sim/teleport).
        Real boards and the kit's fake-board emulator are never driven, whatever hello.simulated says."""
        return bool(self.simulated) and str(self.hub.get("source") or "").startswith("sim")

    # ------------------------------------------------------------------ set-up
    def _reset_state(self) -> None:
        self.phase = "standby"
        self.started_t: float | None = None
        self.hazards: list[dict] = []
        self.mayday: dict | None = None
        self.timeline: list[dict] = []
        self.contact_done = False
        self.stats = {"replans": 0}
        # scout mode: the latest scan, the agent's brief of it, the people it located, and the board's scout strip
        self.scan: dict | None = None
        self.scan_brief: dict | None = None
        self.victims: dict[str, dict] = {}
        self.scout = {"phase": "idle", "room": None, "smoke": 0.0}
        # beacon mode: opt-in phones relayed from the Beacon module, once pucks are in for this incident
        self.beacon_active = False
        self.phones: dict[str, dict] = {}
        self.beacons: dict | None = None                    # the latest Beacons object, once pucks are in for this incident
        self.script_hold = False                            # the exercise script holds agent-ordered crews while a recorded picture forms
        self.held_units: set[str] = set()                   # the exercise script holds these crews whatever ordered them (one crew in the room at a time)

    def _make_units(self, tags: list[str]) -> None:
        self._tags = tags
        spec = [("E1", "Engine 1", "engine"), ("RIT", "Rescue team", "rit")]
        old = self.units
        self.units = {}
        for i, (uid, label, kind) in enumerate(spec):
            tag = tags[i] if i < len(tags) else None
            u = {"id": uid, "label": label, "kind": kind, "physical": tag is not None, "tag": tag, "status": "staged",
                 "x": None, "y": None, "floor": self.twin.exercise_floor, "room": None, "room_label": None, "suite": None, "node": None, "sweep": None,
                 "heading": None, "speed": 0.0, "q": None, "sigma_m": None, "still_s": 0.0, "air_pct": 100.0, "time_in_s": 0.0,
                 "assignment": None, "route": None, "geo": None, "site": None,
                 "_still_ref": None, "_still_since": None, "_entered_t": None, "_cand": None, "_cand_n": 0, "_follow": None, "_logical": None, "_was_interior": False}
            if uid in old and old[uid]["tag"] == tag:
                for k in ("x", "y", "room", "room_label", "suite", "node", "sweep", "geo", "q", "sigma_m"):
                    u[k] = old[uid][k]
            self.units[uid] = u
        self.stage_units()

    def stage_units(self) -> None:
        """Park units at the attack stair. Simulated tags are teleported there; virtual units are placed."""
        try:
            base = self.twin.resolve("stairA")["at"]
        except ValueError:                               # a twin without a labelled Stair A: any stair, else the first sweep
            stair_rooms = self.twin.rooms("stair")
            base = self.twin.room_centre(stair_rooms[0]["id"]) if stair_rooms else self.twin.data["graph"]["nodes"][0]["at"]
        for i, u in enumerate(self.units.values()):
            at = (base[0] - 1.2 + 2.4 * i, base[1])
            u["assignment"], u["route"], u["_follow"], u["_logical"], u["status"] = None, None, None, None, "staged"
            u["_still_ref"], u["_still_since"], u["_entered_t"], u["_was_interior"] = None, None, None, False
            u["still_s"], u["time_in_s"], u["air_pct"] = 0.0, 0.0, 100.0
            u["_trail"] = []
            if not u["physical"]:
                self._set_position(u, at[0], at[1], self.clock(), None, None)
            elif self.drivable and self.registration and self.teleport:
                self.teleport(u["tag"], *self.registration.invert(*at))

    def reset(self) -> None:
        self._reset_state()
        self.stage_units()
        self.emit({"type": "reset"})

    # ------------------------------------------------------------------ hub input
    def on_hello(self, hello: dict) -> None:
        self.simulated = bool(hello.get("simulated"))
        self.hub = {"connected": True, "source": hello.get("source"), "profile": hello.get("profile")}
        tags = sorted(nid for nid, n in (hello.get("nodes") or {}).items() if n.get("role") == "tag")
        if tags != self._tags:
            self._make_units(tags)

    def on_anchors(self, msg: dict) -> None:
        self.anchors_site = msg.get("anchors") or {}
        if self.anchors_site and (self.registration is None or self.registration.method.startswith("default")):
            scale = float(config.EXERCISE_SCALE) if config.EXERCISE_SCALE else None
            self.registration = default_registration(self.anchors_site, self.twin, scale)
            if self.phase == "standby":
                self.stage_units()

    def set_registration(self, pairs: list[dict] | None = None, scale: float | None = None) -> dict:
        if pairs:
            self.registration = fit_similarity([p["site"] for p in pairs], [p["bldg"] for p in pairs], allow_mirror=len(pairs) >= 3)
            self.registration.method = f"fit from {len(pairs)} reference points"
        elif self.anchors_site:
            self.registration = default_registration(self.anchors_site, self.twin, scale)
        else:
            raise ValueError("no anchors known yet and no point pairs given")
        self._log("system", f"Registration set: scale 1:{self.registration.scale:.1f} ({self.registration.method})")
        return self.registration.to_dict()

    def on_pos(self, msg: dict) -> None:
        u = next((q for q in self.units.values() if q["tag"] == msg.get("id")), None)
        if u is None or self.registration is None:
            return
        u["site"] = [round(msg["x"], 3), round(msg["y"], 3)]
        X, Y = self.registration.apply(msg["x"], msg["y"])
        if u["x"] is not None:                       # light smoothing on top of the kit's filter
            X, Y = 0.5 * u["x"] + 0.5 * X, 0.5 * u["y"] + 0.5 * Y
        cov = msg.get("cov") or [0.0, 0.0, 0.0]
        sigma = math.sqrt(max(cov[0], cov[2], 0.0)) * self.registration.scale
        self._set_position(u, X, Y, msg.get("t") or self.clock(), sigma, msg.get("q"))

    def on_event(self, msg: dict) -> None:
        u = next((q for q in self.units.values() if q["tag"] == msg.get("id")), None)
        if u and msg.get("name") == "button" and self.phase not in ("standby", "terminated"):
            self.declare_mayday(u["id"], "button", "crew")

    def _set_position(self, u: dict, X: float, Y: float, t: float, sigma: float | None, q) -> None:
        prev = (u["x"], u["y"])
        loc = self.twin.locate(X, Y)
        if u["room"] is None or loc["room"] == u["room"]:
            u["_cand"], u["_cand_n"] = None, 0
            accept = True
        else:                                        # hysteresis: a new room must persist before it is believed
            if u["_cand"] == loc["room"]:
                u["_cand_n"] += 1
            else:
                u["_cand"], u["_cand_n"] = loc["room"], 1
            accept = u["_cand_n"] >= (4 if u["physical"] else 1)
        if accept and loc["room"] != u["room"]:
            old = u["room"]
            u["room"], u["room_label"], u["suite"] = loc["room"], loc["room_label"], loc["suite"]
            u["_cand"], u["_cand_n"] = None, 0
            if old and u["physical"] and not self._adjacent(old, loc["room"]) and self.phase not in ("standby",):
                self._log("twin", f"{u['label']} went from {self.twin.room(old)['label']} to {loc['room_label']} with no door between them in the twin: the twin may be stale here, or the fix is poor", u["id"])
        if u["room"] == loc["room"]:
            u["node"], u["sweep"] = loc["node"], loc["sweep"]
        if prev[0] is not None:
            d = math.hypot(X - prev[0], Y - prev[1])
            if d > 0.05:
                u["heading"] = round(math.atan2(Y - prev[1], X - prev[0]), 3)
        u["x"], u["y"], u["q"] = round(X, 2), round(Y, 2), q
        trail = u.setdefault("_trail", [])                 # the path walked, for the report (a point every metre or so)
        if self.started_t and (not trail or math.dist(trail[-1][1:], (X, Y)) >= 1.0):
            trail.append([round(t - self.started_t, 1), round(X, 2), round(Y, 2)])
            if len(trail) > 600:
                del trail[:100]
        u["sigma_m"] = round(sigma, 2) if sigma is not None else None
        lat, lon = self.twin.to_geo(X, Y)
        u["geo"] = {"lat": round(lat, 7), "lon": round(lon, 7)}
        ref = u["_still_ref"]
        radius = max(config.NO_MOTION_RADIUS_M, 2.5 * (u["sigma_m"] or 0.0))     # noise must not look like movement
        if ref is None:
            u["_still_ref"], u["_still_since"], u["_still_out"] = (X, Y), t, 0
        elif math.hypot(X - ref[0], Y - ref[1]) > radius:
            u["_still_out"] = u.get("_still_out", 0) + 1         # one outlier fix is not movement: three in a row are
            if u["_still_out"] >= (3 if u["physical"] else 1):
                u["_still_ref"], u["_still_since"], u["_still_out"] = (X, Y), t, 0
        else:
            u["_still_out"] = 0
        u["still_s"] = round(t - (u["_still_since"] or t), 1)

    def _adjacent(self, a: str, b: str) -> bool:
        """Rooms a crew can pass between: joined by a door or opening, or two parts of the open corridor."""
        if self.twin.room(a)["kind"] == "corridor" and self.twin.room(b)["kind"] == "corridor":
            return True
        return any({a, b} == set(d["rooms"]) for d in self.twin.data["doors"])

    # ------------------------------------------------------------------ reflex loop
    def tick(self) -> None:
        now = self.clock()
        dt = 0.0 if self._last_tick is None else min(1.0, now - self._last_tick)
        self._last_tick = now
        active = self.phase in ("operations", "mayday", "rescue", "egress")
        for u in self.units.values():
            self._advance(u, dt, now)
            in_stair = bool(u["room"]) and self.twin.room(u["room"])["kind"] == "stair"
            if active and u["x"] is not None:
                if not in_stair and u["status"] in ("staged", "out"):
                    u["status"], u["_was_interior"] = "interior", True
                    if u["_entered_t"] is None:
                        u["_entered_t"] = now
                        self._log("unit", f"{u['label']} is on the floor ({u['room_label']})", u["id"])
                if in_stair and u["status"] == "exiting":
                    u["status"] = "out"
                    self._log("unit", f"{u['label']} is out of the floor at {u['room_label']}", u["id"])
            rt = u["route"]
            if rt and not rt.get("done") and rt.get("valid") and u["_follow"] is None and u["x"] is not None:
                end, dst = rt["polyline"][-1], rt.get("dst") or {}      # a crew moved by a person has arrived when it is at the destination
                if math.hypot(u["x"] - end[0], u["y"] - end[1]) <= config.CONTACT_M or (dst.get("kind") == "room" and u["room"] == dst.get("id")):
                    rt["done"] = True
            if u["_entered_t"] is not None and u["status"] != "out" and self.phase != "terminated":
                u["time_in_s"] = round(now - u["_entered_t"], 1)
                u["air_pct"] = round(max(0.0, 100.0 * (1 - u["time_in_s"] / config.AIR_WORK_TIME_S)), 1)
            if (self.phase == "operations" and u["physical"] and u["status"] == "interior" and not in_stair and u["_follow"] is None
                    and (u["assignment"] or {}).get("task") not in ("hold", "stage") and u["still_s"] >= config.NO_MOTION_S):
                self.declare_mayday(u["id"], "no_motion", "reflex")
        if self.victims or self.phones:
            self._victims_tick()
        if self.mayday and not self.contact_done:
            down = self.units[self.mayday["unit"]]
            for r in self.units.values():
                if r["id"] == down["id"] or (r["assignment"] or {}).get("task") != "rit_rescue" or r["x"] is None:
                    continue
                if r["_follow"] is not None:                 # a software-driven crew: contact when it has walked its route
                    continue
                arrived = bool(r["route"] and r["route"].get("done"))
                near = math.hypot(r["x"] - down["x"], r["y"] - down["y"]) <= config.CONTACT_M
                if arrived or near:
                    self.contact_done = True
                    self.phase = "egress"
                    self._log("contact", f"{r['label']} has reached {down['label']} in {down['room_label']}", r["id"])
                    self._situation("contact", down["id"], {"rescuer": r["id"], "room": down["room"], "room_label": down["room_label"]})
        if self.phase == "egress" and self.mayday:
            down = self.units[self.mayday["unit"]]
            others = [r for r in self.units.values() if r["_was_interior"]]
            if down["status"] == "out" and all(r["status"] == "out" for r in others):
                self.resolve_mayday(down["id"])
                self.phase = "terminated"
                self._log("par", "All crews are out of the floor. Accountability complete.")
                self._situation("all_out", None, {})
        elif self.phase == "egress" and not self.mayday and (self.victims or self.phones):
            rescued = [v for v in self._people() if v["rescuer"] and v["status"] in ("reached", "evacuating", "out")]
            others = [r for r in self.units.values() if r["_was_interior"]]
            phones_done = all(v["status"] in ("out", "handed_over") for v in self.phones.values())    # every phone that reported people inside
            if rescued and all(v["status"] == "out" for v in rescued) and phones_done and all(r["status"] == "out" for r in others):
                self.phase = "terminated"
                names = ", ".join(v["id"] for v in rescued)
                left = [v for v in self._people() if v["status"] not in ("out",)]
                rest = (f" Handed over, still inside and not reached: {', '.join(v['id'] for v in left)}" +
                        (lambda n: f" ({n} {'person' if n == 1 else 'people'} reported)")(sum(int(v.get('people') or 1) for v in left)) + ".") if left else ""
                self._log("par", f"All crews are out of the floor with {names}." + (rest or " Accountability complete."))
                self._situation("all_out", None, {})

    # ------------------------------------------------------------------ scout mode: located people
    def _victims_tick(self) -> None:
        """A rescuer that has walked its route to a located person raises `victim_reached` (same arrival test as `contact`);
        a person being brought out moves with the rescuer and is out when the rescuer is."""
        for v in self._people():
            r = self.units.get(v["rescuer"]) if v["rescuer"] else None
            if r is None:
                continue
            if v["status"] == "assigned":
                # the assigned rescuer, or any other crew sent to this person (the first to arrive reaches them)
                crews = [r] + [u for u in self.units.values() if u is not r and (u["assignment"] or {}).get("target") == v["id"]]
                for r in crews:
                    a = r["assignment"] or {}
                    if a.get("target") != v["id"] or r["x"] is None or r["_follow"] is not None:
                        continue
                    if v.get("kind") == "phone" and v.get("geometry") == "candidates":
                        # at a candidate point: whether the person is there is the crew's observation (report_search), not the arrival
                        if r["route"] and r["route"].get("done") and not v.get("at_candidate"):
                            v["at_candidate"] = {"x": v["x"], "y": v["y"], "by": r["id"]}
                        continue
                    arrived = bool(r["route"] and r["route"].get("done"))
                    if v.get("match") == "room":                      # a ring or a zone: reached when the crew is in that room
                        near = r["room"] == v["room"]
                        arrived = arrived and near
                    else:
                        near = math.hypot(r["x"] - v["x"], r["y"] - v["y"]) <= config.CONTACT_M
                    if arrived or near:
                        v["status"], v["reached_t"], v["rescuer"] = "reached", self.clock(), r["id"]
                        if self.phase in ("operations", "rescue"):
                            self.phase = "egress"
                        if v.get("kind") == "phone":
                            self._log("phone", f"{r['label']} has reached phone {v['id']} in {v['room_label']}: {v['what']}", r["id"])
                            self._situation("phone_reached", r["id"], {"phone": v["id"], "rescuer": r["id"], "geometry": v["geometry"], "room": v["room"],
                                                                       "room_label": v["room_label"], "node": v["node"], "people": v["people"]})
                        else:
                            self._log("victim", f"{r['label']} has reached {v['id']} in {v['room_label']}: {v['what']}", r["id"])
                            self._situation("victim_reached", r["id"], {"victim": v["id"], "rescuer": r["id"], "room": v["room"], "room_label": v["room_label"], "node": v["node"]})
                        break
            elif v["status"] == "evacuating" and r["x"] is not None:
                v["x"], v["y"] = round(r["x"] - 0.8, 2), round(r["y"], 2)
                if r["status"] == "out":
                    v["status"], v["out_t"] = "out", self.clock()
                    self._log("phone" if v.get("kind") == "phone" else "victim", f"{v['id']} is out of the floor with {r['label']}" +
                              (f" ({v['people']} {'person' if v['people'] == 1 else 'people'})" if v.get("kind") == "phone" and v.get("people") else ""), r["id"])

    def set_scan(self, scan: dict, brief: dict) -> None:
        """A scout scan has landed: keep it, put the people it located on the board as route targets, tell the agent."""
        self.scan, self.scan_brief = scan, brief
        self.victims = {}
        for v in scan.get("victims") or []:
            if v.get("x") is None:
                continue
            bpm = v.get("breathing_bpm")
            what = ("moving" if v.get("moving") else f"breathing about {round(float(bpm))} per minute" if v.get("state") == "breathing" and bpm
                    else "breathing" if v.get("state") == "breathing" else "no breathing seen")
            self.victims[v["id"]] = {"id": v["id"], "kind": "radar", "x": float(v["x"]), "y": float(v["y"]), "room": v.get("room"), "room_label": v.get("room_label"),
                                     "node": v.get("node"), "state": v.get("state"), "what": what, "status": "located", "rescuer": None,
                                     "dst": [float(v["x"]), float(v["y"])], "match": "point", "label": f"located person {v['id']} in {v.get('room_label')}",
                                     "located_t": self.clock(), "reached_t": None, "out_t": None}
        self.scout.update({"phase": "done", "room": scan.get("room")})
        n_v, n_m = len(self.victims), len(scan.get("mismatches") or [])
        tag = "hand-made sample" if scan.get("sample") else "simulated" if scan.get("simulated") else "measured"
        where = {b["id"]: b.get("where") for b in (brief or {}).get("victims") or []}       # discrete (0.5 m) text: the timeline feeds the after-action prompt
        people = "; ".join(f"{v['id']} {v['what']}" + (f", {where[v['id']]}" if where.get(v["id"]) else "")
                           for v in self.victims.values()) or "nobody located"
        self._log("scan", f"Scout scan of {scan.get('room_label')} [{tag}]: {people}. {n_m} mismatch flag{'s' if n_m != 1 else ''} against the twin.")
        if self.phase not in ("standby", "terminated"):
            self._situation("scan", None, brief)

    def report_search(self, pid: str, unit_id: str, found: bool) -> None:
        """A crew's observation at a phone's candidate point: found (the phone is reached) or nobody there (that candidate is pruned:
        the Beacon link relays it as pruned "searched by <crew>: nobody there", and with one candidate left the phone becomes a fix)."""
        v, u = self.phones.get(pid), self.units.get(unit_id)
        if v is None or u is None:
            return
        at = v.get("at_candidate") or {"x": v["x"], "y": v["y"]}
        v["at_candidate"] = None
        if found:
            v["status"], v["reached_t"] = "reached", self.clock()
            if self.phase in ("operations", "rescue"):
                self.phase = "egress"
            self._log("phone", f"{u['label']} has reached phone {pid} in {v['room_label']}: {v['what']}", unit_id)
            self._situation("phone_reached", unit_id, {"phone": pid, "rescuer": unit_id, "geometry": v["geometry"], "room": v["room"],
                                                       "room_label": v["room_label"], "node": v["node"], "people": v["people"]})
            return
        v.setdefault("searched", []).append({"x": round(float(at["x"]), 2), "y": round(float(at["y"]), 2), "by": u["label"]})
        self._log("search", f"{u['label']} searched the candidate point of phone {pid} in {v['room_label']}: nobody there", unit_id)

    def phone_searches(self) -> dict:
        return {k: list(v["searched"]) for k, v in self.phones.items() if v.get("searched")}

    def hand_over(self, pid: str, why: str) -> None:
        """A located phone nobody reached is handed over explicitly (the incident does not end as if it were accounted for)."""
        v = self.victim_target(pid)
        if v is not None and v["status"] not in ("out", "handed_over"):
            v["status"] = "handed_over"
            self._log("handover", f"{pid} handed over, still inside and not reached ({v.get('what')} in {v.get('room_label')}): {why}")

    def log_scout(self, text: str, unit: str | None = None) -> dict:
        return self._log("scout", text, unit)

    def victim_target(self, ref) -> dict | None:
        """The located person a reference names (a radar victim "V1" or an opt-in phone "B1"), or None.
        For tools that accept a person as a destination."""
        if not isinstance(ref, str):
            return None
        return self.victims.get(ref) or self.phones.get(ref)

    def _people(self) -> list[dict]:
        return list(self.victims.values()) + list(self.phones.values())

    # ------------------------------------------------------------------ beacon mode: opt-in phones
    def activate_beacon(self, on: bool = True) -> None:
        """Pucks are in for this incident: from now on the Beacon module's phones become targets (until reset)."""
        if on and not self.beacon_active:
            self.beacon_active = True
        elif not on:
            self.beacon_active = False

    def _phone_target(self, p: dict, pucks_room: str | None = None) -> dict | None:
        """Where a crew goes for a phone, as the geometry allows: a fix is a point; candidates: the most probable one (and it says so);
        a ring or a zone: into the most probable room, to search it. Never a made-up point."""
        geom, pid = p.get("geometry"), p["id"]
        if geom == "fix" and p.get("x") is not None:
            loc = self.twin.locate(p["x"], p["y"])
            room = p.get("room") or loc["room"]
            label = self.twin.room(room)["label"] if room in self.twin._rooms else loc["room_label"]
            return {"x": float(p["x"]), "y": float(p["y"]), "room": room, "room_label": label, "node": p.get("node") or loc["node"],
                    "dst": [float(p["x"]), float(p["y"])], "match": "point", "label": f"phone {pid} (fix) in {label}"}
        if geom == "candidates" and p.get("candidates"):
            from .scout.session import room_door
            doors = [d["at"] for d in (room_door(self.twin, c.get("room") or self.twin.locate(c["x"], c["y"])["room"]) for c in p["candidates"]) if d]

            def near_door(c):
                return min((math.dist((c["x"], c["y"]), d) for d in doors), default=0.0)
            cs = sorted(p["candidates"], key=lambda c: (-round(float(c.get("weight") or 0), 1), round(near_door(c), 1)))   # a tie: nearer the door first
            c = cs[0]
            loc = self.twin.locate(c["x"], c["y"])
            room = c.get("room") or loc["room"]
            label = self.twin.room(room)["label"] if room in self.twin._rooms else loc["room_label"]
            pct = int(round(float(c.get("weight") or 0) * 100))
            return {"x": float(c["x"]), "y": float(c["y"]), "room": room, "room_label": label, "node": loc["node"], "dst": [float(c["x"]), float(c["y"])],
                    "match": "point", "label": f"phone {pid}, the most probable of {len(cs)} candidates ({pct}%), in {label}"}
        room, ring_rooms = None, []
        if geom == "zone" and p.get("zone"):
            room = p["zone"].get("room")
        elif geom == "ring" and p.get("ring"):
            rg, votes = p["ring"], {}
            arcs = rg.get("arcs") or [[0.0, 360.0]]
            for a0, a1 in arcs:
                a = float(a0)
                while a <= float(a1) + 1e-6:
                    x = rg["cx"] + rg["r_m"] * math.cos(math.radians(a))
                    y = rg["cy"] + rg["r_m"] * math.sin(math.radians(a))
                    loc = self.twin.locate(x, y)
                    if loc["off_map_m"] == 0 and loc["room"]:
                        votes[loc["room"]] = votes.get(loc["room"], 0) + 1
                    a += 5.0
            if votes:                                       # the room the pucks were thrown into counts double: a crew searches there first
                score = {k: v * (2 if k == pucks_room else 1) for k, v in votes.items()}
                ranked = sorted(score, key=lambda k: (self.twin.room(k)["kind"] != "corridor", score[k], k), reverse=True)
                room = ranked[0]
                ring_rooms = [k for k in ranked if self.twin.room(k)["kind"] != "corridor"] + [k for k in ranked if self.twin.room(k)["kind"] == "corridor"]
        elif p.get("room"):
            room = p["room"]
        if not room or room not in self.twin._rooms:
            return None
        r = self.twin.resolve(room)
        why = "range only: a ring around " + str((p.get("ring") or {}).get("puck")) if geom == "ring" else "Bluetooth zone only" if geom == "zone" else str(geom)
        out = {"x": float(r["at"][0]), "y": float(r["at"][1]), "room": room, "room_label": r["label"], "node": r["node"], "dst": room, "match": "room",
               "label": f"search {r['label']} for phone {pid} ({why})"}
        if geom == "ring":
            out["ring_rooms"] = ring_rooms
        return out

    def on_beacons(self, obj: dict) -> None:
        """The Beacon module's latest object. Phones become targets once pucks are in for this incident; situation `phone`
        when a phone first becomes actionable, when it improves to a fix, or when its occupant's report changes."""
        if not self.beacon_active or self.phase in ("standby", "terminated"):
            return
        self.beacons = obj
        changed, why = [], {}
        for p in obj.get("phones") or []:
            rep = p.get("report") or {}
            if not p.get("id") or rep.get("safe_outside"):
                continue
            geom, status = p.get("geometry"), rep.get("status") or "unknown"
            if geom not in ("fix", "candidates", "ring", "zone") and status not in ("trapped", "injured"):
                continue
            tgt = self._phone_target(p, obj.get("room"))
            if tgt is None:
                continue
            people = rep.get("people")
            what = (f"{people} {'person' if people == 1 else 'people'}" if people else "occupant") + f", {status}" + (", cannot move" if rep.get("can_move") is False else "")
            e = self.phones.get(p["id"])
            if e is None:
                e = {"id": p["id"], "kind": "phone", "status": "located", "rescuer": None, "located_t": self.clock(), "reached_t": None, "out_t": None,
                     "geometry": None, "report_status": None}
                self.phones[p["id"]] = e
                why[p["id"]] = "new"
            elif geom == "fix" and e["geometry"] != "fix":
                why[p["id"]] = "fix"
            elif status != e["report_status"]:
                why[p["id"]] = "report"
            elif e["status"] in ("located", "assigned") and tgt["room"] != e.get("room"):
                why[p["id"]] = "moved"
            moved = e["status"] == "assigned" and e.get("dst") is not None and (
                tgt["room"] != e.get("room") or tgt["match"] != e.get("match") or
                (tgt["match"] == "point" and math.dist((tgt["x"], tgt["y"]), (e["x"], e["y"])) > 1.5))
            if e["status"] in ("located", "assigned"):
                e.update(tgt)
                if geom != "candidates":
                    e["at_candidate"] = None
            if moved and e["rescuer"] in self.units:           # the rescue team follows the phone's best geometry (reflex, logged by assign)
                task = (self.units[e["rescuer"]]["assignment"] or {}).get("task") or "rescue"
                self.assign(e["rescuer"], task, p["id"], source="reflex")
            e["detail"] = self._phone_detail(p, tgt)
            e.update({"geometry": geom, "method": p.get("method"), "report_status": status, "people": people, "can_move": rep.get("can_move"),
                      "what": what, "text": p.get("text"), "state": status, "confidence": p.get("confidence"), "simulated": bool(obj.get("simulated", True)),
                      "sample": bool(obj.get("sample"))})
            if p["id"] in why:
                changed.append(p["id"])
        if changed:
            for pid in changed:
                e = self.phones[pid]
                tag = "sample" if e["sample"] else "simulated" if e["simulated"] else "measured"
                self._log("phone", f"Phone {pid} [{tag}]: {e['what']}; {e['geometry']} in {e['room_label']}" +
                          {"new": "", "fix": " (now a fix)", "report": " (report changed)", "moved": " (most probable room changed)"}[why[pid]])
            self._situation("phone", None, self.phones_brief(obj, changed, why))

    def _phone_detail(self, p: dict, tgt: dict) -> dict:
        """The geometry in discrete words and whole numbers (agent prompts must replay): a fix's uncertainty in half metres,
        a ring's puck, radius in whole metres and the rooms its allowed arc crosses, candidates' rooms and weights in whole percents."""
        geom = p.get("geometry")

        def label(room):
            return self.twin.room(room)["label"] if room in self.twin._rooms else None
        if geom == "fix":
            sig = float(p.get("sigma_m") or 0.5)
            return {"kind": "fix", "uncertainty": f"about {max(0.5, round(sig * 2) / 2):.1f} m", "room": tgt["room"], "room_label": tgt["room_label"]}
        if geom == "ring" and p.get("ring"):
            rg = p["ring"]
            return {"kind": "ring", "puck": rg.get("puck"), "radius_m": int(round(float(rg.get("r_m") or 0))), "ring_kind": rg.get("kind"),
                    "rooms": [{"room": k, "room_label": label(k)} for k in tgt.get("ring_rooms", [])]}
        if geom == "candidates":
            cs = sorted(p.get("candidates") or [], key=lambda c: -float(c.get("weight") or 0))
            out = []
            for i, c in enumerate(cs):
                room = c.get("room") or self.twin.locate(c["x"], c["y"])["room"]
                out.append({"rank": i + 1, "room": room, "room_label": label(room), "weight_pct": int(round(float(c.get("weight") or 0) * 100))})
            return {"kind": "candidates", "candidates": out, "pruned": len(p.get("pruned") or [])}
        if geom == "zone" and p.get("zone"):
            z = p["zone"]
            return {"kind": "zone", "room": z.get("room"), "room_label": z.get("room_label") or label(z.get("room")), "band": z.get("band")}
        return {"kind": geom}

    def phones_brief(self, obj: dict | None = None, changed: list | None = None, why: dict | None = None) -> dict:
        """DISCRETE summary of the phones for the agent: ids, geometry words, rooms, nodes, whole numbers, the occupants' reports."""
        out = []
        for e in sorted(self.phones.values(), key=lambda q: q["id"]):
            b = {"id": e["id"], "geometry": e["geometry"], "method": e.get("method"), "room": e["room"], "room_label": e["room_label"],
                 "node": e["node"] if e.get("match") == "point" else None, "target": e["label"], "status": e["status"], "rescuer": e["rescuer"],
                 "report": {"status": e["report_status"], "people": e.get("people"), "can_move": e.get("can_move")},
                 "confidence_pct": int(round(float(e["confidence"]) * 10)) * 10 if e.get("confidence") is not None else None, "text": e.get("text"),
                 "geometry_detail": e.get("detail")}
            out.append(b)
        acc = (obj or {}).get("accountability") or {}

        def whole(v):
            return int(round(float(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) else v

        def band(d):
            return {k: whole(v) for k, v in (d or {}).items() if k in ("people", "low", "high")} or None
        rows = [{"suite": r.get("suite"), "room": r.get("room"), "room_label": r.get("room_label"), "tenant": r.get("tenant"), "life_hazard": r.get("life_hazard"),
                 "state": r.get("state"), "priority": whole(r.get("priority")), "expected": band(r.get("expected")), "unaccounted": band(r.get("unaccounted")),
                 "phones_opt_in": whole(r.get("phones_opt_in")), "people_reported": whole(r.get("people_reported")), "located": whole(r.get("located")),
                 "cold_devices": whole(r.get("cold_devices")), "safe_outside": whole(r.get("safe_outside"))} for r in acc.get("suites") or []]
        return {"changed": list(changed or []), "why": dict(why or {}), "phones": out, "accountability": rows,
                "room": (obj or {}).get("room"), "room_label": (obj or {}).get("room_label"),
                "simulated": bool((obj or {}).get("simulated", True)), "sample": bool((obj or {}).get("sample")),
                "priorities": [{k: q.get(k) for k in ("rank", "suite", "room", "target", "text")} for q in acc.get("priorities") or []],
                "limits": [str(x) for x in (obj or {}).get("limits") or []]}

    def _advance(self, u: dict, dt: float, now: float) -> None:
        f = u["_follow"]
        if not f:
            return
        if u["id"] in self.held_units or (f.get("by") in HELD_WHILE_AGENT_BUSY and (self.script_hold or (self.agent_busy is not None and self.agent_busy()))):
            return                     # replay determinism: a crew ordered by the agent starts walking when the agent's turn ends
        f["s"] = min(f["total"], f["s"] + config.WALK_SPEED_MPS * dt)
        pts, acc = f["pts"], 0.0
        pos = pts[-1]
        for i in range(len(pts) - 1):
            seg = math.dist(pts[i], pts[i + 1])
            if acc + seg >= f["s"]:
                k = 0.0 if seg == 0 else (f["s"] - acc) / seg
                pos = (pts[i][0] + k * (pts[i + 1][0] - pts[i][0]), pts[i][1] + k * (pts[i + 1][1] - pts[i][1]))
                break
            acc += seg
        while f["i"] + 1 < len(f["cum"]) and f["cum"][f["i"] + 1] <= f["s"] + 1e-6:
            f["i"] += 1
        if f["nodes"]:
            u["_logical"] = f["nodes"][min(f["i"], len(f["nodes"]) - 1)]
        if u["physical"]:
            if self.drivable and self.teleport and self.registration:
                self.teleport(u["tag"], *self.registration.invert(*pos))
        else:
            self._set_position(u, pos[0], pos[1], now, None, None)
        for other_id in f.get("carry", []):                    # a rescued crew walks out with its rescuers
            o = self.units[other_id]
            if o["physical"] and self.drivable and self.teleport and self.registration:
                self.teleport(o["tag"], *self.registration.invert(pos[0] - 0.8, pos[1]))      # on real boards a person walks the token out
            elif not o["physical"]:
                self._set_position(o, pos[0] - 0.8, pos[1], now, None, None)
            if not o["physical"] or self.drivable:
                o["_logical"] = u["_logical"]
        if f["s"] >= f["total"] - 1e-6:
            u["_follow"] = None
            if u["route"]:
                u["route"]["done"] = True

    def _start_follow(self, u: dict, route: dict, carry: list[str] | None = None, by: str = "agent") -> None:
        pts = [tuple(p) for p in route["polyline"]]
        if u["x"] is not None and pts and math.dist((u["x"], u["y"]), pts[0]) > 0.3:
            pts.insert(0, (u["x"], u["y"]))
            lead = 1
        else:
            lead = 0
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + math.dist(a, b))
        nodes = ([route["nodes"][0]] * lead + route["nodes"]) if route.get("nodes") else []
        u["_follow"] = {"pts": pts, "cum": cum, "total": cum[-1], "s": 0.0, "i": 0, "nodes": nodes, "carry": carry or [], "by": by}

    def drive(self, unit_id: str, on: bool = True) -> None:
        """Director / UI hook: make a unit walk its current route even if its task is not an auto task."""
        u = self.units[unit_id]
        if on and u["route"] and u["route"].get("valid"):
            self._start_follow(u, u["route"], by="director")
        elif not on:
            u["_follow"] = None

    def move_unit(self, unit_id: str, x: float, y: float) -> None:
        """Sim / virtual only: put a unit somewhere (UI drag)."""
        u = self.units[unit_id]
        u["_follow"] = None
        if not u["physical"]:
            self._set_position(u, x, y, self.clock(), None, None)
        elif self.drivable and self.teleport and self.registration:
            self.teleport(u["tag"], *self.registration.invert(x, y))
        else:
            raise PermissionError("a physical tag on real hardware cannot be moved by software")

    # ------------------------------------------------------------------ helpers
    def _log(self, kind: str, text: str, unit: str | None = None) -> dict:
        now = self.clock()
        entry = {"t": round(now, 2), "t_rel": round(now - self.started_t, 1) if self.started_t else 0.0, "kind": kind, "text": text, "unit": unit}
        self.timeline.append(entry)
        self.emit({"type": "timeline", "entry": entry})
        return entry

    def _situation(self, kind: str, unit: str | None, data: dict) -> None:
        self._seq += 1
        s = {"seq": self._seq, "kind": kind, "unit": unit, "data": data, "snapshot": self.snapshot()}
        if self.on_situation:
            self.on_situation(s)

    def blocked_refs(self) -> set[str]:
        return {h["ref"] for h in self.hazards}

    def _unit_node(self, u: dict) -> str | None:
        return u["_logical"] if (u["_logical"] and (u["_follow"] is not None or not u["physical"] or self.drivable)) else u["node"]

    def _hazard_geometry(self, ref: str) -> tuple[str, dict]:
        tw = self.twin
        if ref in tw._rooms:
            return tw.room(ref)["label"], {"kind": "room", "polygon": tw.room(ref)["polygon"]}
        if ref in tw._stairs:
            room = tw.room(tw.stair(ref)["room"])
            return tw.stair(ref)["label"], {"kind": "room", "polygon": room["polygon"]}
        if ref in tw._doors:
            return tw.resolve(ref)["label"], {"kind": "point", "at": tw.door(ref)["at"]}
        if ref in tw._nodes:
            return f"{tw.room(tw.node(ref)['room'])['label']} (point)", {"kind": "point", "at": tw.node(ref)["at"]}
        a, b = ref.split("-", 1)
        na, nb = tw.node(a), tw.node(b)
        label = tw.room(na["room"])["label"]
        near = self._nearest_suite(((na["at"][0] + nb["at"][0]) / 2, (na["at"][1] + nb["at"][1]) / 2))
        return (f"{label} near {near}" if near else label), {"kind": "edge", "polyline": [na["at"], nb["at"]]}

    def _nearest_suite(self, p) -> str | None:
        best = None
        for d in self.twin.data["doors"]:
            r = self.twin.room(d["rooms"][0])
            if r["kind"] != "suite":
                continue
            dist = math.dist(p, d["at"])
            if best is None or dist < best[0]:
                best = (dist, r["label"])
        return best[1] if best else None

    def _route_blocked(self, route: dict) -> bool:
        bnodes, bedges = self.twin._blocked_sets(self.blocked_refs())
        nodes = route.get("nodes") or []
        inner = set(nodes[1:-1])
        return bool(inner & bnodes) or any(frozenset(p) in bedges for p in zip(nodes, nodes[1:]))

    # ------------------------------------------------------------------ actions
    def start(self) -> None:
        if self.phase != "standby":
            return
        self.started_t = self.clock()
        self.phase = "preplan"
        prop = self.provider.site()["property"]
        self._log("alarm", f"Alarm: {prop.get('address')}, {prop.get('city')}. Command agent is building the pre-plan.")

    def begin_operations(self) -> None:
        if self.phase == "preplan":
            self.phase = "operations"
            for u in self.units.values():
                u["_still_ref"], u["_still_since"] = None, None
            self._log("phase", "Pre-plan complete. Crews cleared to enter.")

    def radio(self, sender: str, to: str, text: str, priority: str = "routine") -> dict:
        msg = {"type": "radio", "t": round(self.clock(), 2), "from": sender, "to": to, "text": text, "priority": priority}
        self.emit(msg)
        self._log("radio", f"{sender} to {to}: {text}")
        return {"ok": True}

    def assign(self, unit_id: str, task: str, target=None, via: list[str] | None = None, source: str = "agent") -> dict:
        if unit_id not in self.units:
            raise ValueError(f"unknown unit {unit_id!r}")
        u = self.units[unit_id]
        carry: list[str] = []
        blocked = self.blocked_refs()
        start = self._unit_node(u) or ([u["x"], u["y"]] if u["x"] is not None else "stairA")
        if task in ("hold", "stage") and target is None:
            u["assignment"], u["route"], u["_follow"] = {"task": task, "target": None, "target_label": None}, None, None
            self._log("assign", f"{u['label']}: {task}", unit_id)
            return {"ok": True, "route": None}
        victim = self.victim_target(target)
        if target in self.units:
            other = self.units[target]
            dst = self._unit_node(other) or [other["x"], other["y"]]
            label = f"{other['label']} in {other['room_label']}"
        elif victim is not None:                                    # a person the scan or a phone located: the graph node nearest them, or their room
            dst = victim["dst"]
            label = victim["label"]
        elif target in (None, "exit"):
            dst, label = "exit", "nearest clear stair"
        else:
            dst, label = target, None
        legs = [start] + list(via or []) + [dst]
        route = None
        for a, b in zip(legs, legs[1:]):
            leg = self.twin.route(a, b, blocked)
            if not leg["ok"]:
                self._log("assign", f"{u['label']}: no route for {task} ({leg['reason']})", unit_id)
                return {"ok": False, "reason": leg["reason"]}
            if route is None:
                route = leg
            else:
                route = {**leg, "nodes": route["nodes"] + leg["nodes"][1:], "polyline": route["polyline"] + leg["polyline"][1:],
                         "length_m": round(route["length_m"] + leg["length_m"], 1), "rooms": route["rooms"] + leg["rooms"][1:],
                         "doors": route["doors"] + leg["doors"], "steps": route["steps"][:-1] + leg["steps"][1:], "src_resolved": route["src_resolved"]}
        label = label or route["dst_resolved"]["label"]
        if victim is not None and victim.get("match") == "point":   # last few steps from that node to the person, when no wall is in the way
            end = route["polyline"][-1]
            gap = math.dist(end, (victim["x"], victim["y"]))
            if gap > 0.3 and not self.twin.crosses_wall(end, (victim["x"], victim["y"])):
                route = {**route, "polyline": route["polyline"] + [[victim["x"], victim["y"]]], "length_m": round(route["length_m"] + gap, 1)}
        if u["assignment"] and u["route"] and task == u["assignment"].get("task") and not u["route"].get("valid", True):
            self.stats["replans"] += 1
        u["assignment"] = {"task": task, "target": route["dst_resolved"]["id"] if (target not in self.units and victim is None) else target, "target_label": label}
        u["route"] = {"polyline": route["polyline"], "length_m": route["length_m"], "valid": True, "done": False, "nodes": route["nodes"],
                      "rooms": route["rooms"], "steps": route["steps"], "dst": route["dst_resolved"]}
        if victim is not None and task in RESCUE_TASKS and victim["status"] in ("located", "assigned"):
            victim["rescuer"], victim["status"], victim["assigned_t"] = unit_id, "assigned", self.clock()
        if task == "exit":
            u["status"] = "exiting" if u["status"] != "mayday" else "mayday"
            if self.mayday and unit_id != self.mayday["unit"] and self.contact_done:
                carry = [self.mayday["unit"]]
                self.units[self.mayday["unit"]]["status"] = "exiting"
            for v in self._people():                               # a reached person leaves with the rescuer
                if v["rescuer"] == unit_id and v["status"] == "reached":
                    v["status"] = "evacuating"
        self._log("assign", f"{u['label']}: {task.replace('_', ' ')} to {label}, {round(route['length_m'])} m via {self._via_text(route)}", unit_id)
        if not u["physical"] or (self.drivable and task in AUTO_TASKS):
            self._start_follow(u, u["route"], carry, by=source)
        if self.mayday and task == "rit_rescue" and self.phase == "mayday":
            self.phase = "rescue"
        elif victim is not None and task in RESCUE_TASKS and self.phase == "operations":
            self.phase = "rescue"
        return {"ok": True, "route": {"length_m": round(route["length_m"]), "rooms": [self.twin.room(r)["label"] for r in route["rooms"]],
                                      "steps": [s["text"] for s in route["steps"]]}}

    def _via_text(self, route: dict) -> str:
        labels = [self.twin.room(r)["label"] for r in route["rooms"][1:-1]]
        return ", ".join(labels) if labels else "direct"

    def mark_hazard(self, ref: str, kind: str = "blocked", note: str = "", source: str = "agent") -> dict:
        self.twin._blocked_sets([ref])                      # validates the reference
        existing = next((h for h in self.hazards if h["ref"] == ref), None)
        if existing:
            return existing
        label, geometry = self._hazard_geometry(ref)
        hz = {"id": f"hz{len(self.hazards) + 1}", "ref": ref, "kind": kind, "label": label, "note": note, "source": source, "geometry": geometry}
        self.hazards.append(hz)
        self._log("hazard", f"Hazard ({kind}) at {label}" + (f": {note}" if note else "") + f" [reported by {source}]")
        affected = []
        for u in self.units.values():
            if u["route"] and u["route"].get("valid") and not u["route"].get("done") and self._route_blocked(u["route"]):
                u["route"]["valid"] = False
                u["_follow"] = None
                affected.append(u["id"])
        self.emit({"type": "alert", "level": "warn", "text": f"{label} is lost ({kind})", "unit": None})
        if source != "agent" and (affected or self.mayday):
            self._situation("hazard", None, {"hazard": hz["id"], "ref": ref, "kind": kind, "label": label, "note": note, "affected_units": affected})
        return hz

    def clear_hazard(self, hazard_id: str) -> None:
        self.hazards = [h for h in self.hazards if h["id"] != hazard_id]

    def request_resource(self, what: str, reason: str) -> dict:
        self._log("resource", f"Requested: {what} ({reason})")
        return {"ok": True, "status": "request logged (exercise: no real dispatch is contacted)"}

    def declare_mayday(self, unit_id: str, reason: str, source: str = "reflex") -> None:
        if self.mayday or unit_id not in self.units or self.phase in ("standby", "terminated"):
            return
        u = self.units[unit_id]
        u["status"], u["_follow"] = "mayday", None
        self.phase = "mayday"
        self.contact_done = False
        self.mayday = {"unit": unit_id, "room": u["room"], "room_label": u["room_label"], "reason": reason, "t": self.clock()}
        why = {"no_motion": f"no movement for {config.NO_MOTION_S:g} s", "button": "crew pressed the tag button", "manual": "declared by command"}.get(reason, reason)
        self._log("mayday", f"MAYDAY: {u['label']} in {u['room_label']} ({why})", unit_id)
        self.emit({"type": "alert", "level": "mayday", "text": f"MAYDAY: {u['label']} in {u['room_label']} ({why})", "unit": unit_id})
        self._situation("mayday", unit_id, {"reason": reason, "room": u["room"], "room_label": u["room_label"]})

    def resolve_mayday(self, unit_id: str) -> None:
        if self.mayday and self.mayday["unit"] == unit_id:
            self.mayday["resolved_t"] = self.clock()
            self._log("mayday", f"Mayday cleared: {self.units[unit_id]['label']}", unit_id)
            self._last_mayday, self.mayday = self.mayday, None

    def add_observation(self, text: str, unit_id: str | None = None, position: list[float] | None = None, kind: str = "observation") -> dict:
        u = self.units.get(unit_id) if unit_id else None
        if position is None and u is not None and u["x"] is not None:
            position = [u["x"], u["y"], 1.2]
        if position is None:
            c = self.twin.room_centre(self.twin.rooms("corridor")[0]["id"])
            position = [c[0], c[1], 1.2]
        if len(position) == 2:
            position = [position[0], position[1], 1.2]
        where = self.twin.locate(position[0], position[1])["room_label"]
        tag = self.provider.matterport.add_tag(self.twin.exercise_floor, position, text[:80], f"{text} (logged during the incident at {where})", kind)
        self._log("observation", f"Pinned in the twin at {where}: {text}", unit_id)
        self.emit({"type": "tag", "tag": tag})
        return tag

    # ------------------------------------------------------------------ views
    def snapshot(self) -> dict:
        """Discrete, deterministic state for agent prompts: ids and labels only, no clock times, no coordinates."""
        units = []
        for u in self.units.values():
            node = self._unit_node(u)
            room = self.twin.node(node)["room"] if (node and u["_logical"] and node == u["_logical"]) else u["room"]
            r = self.twin.room(room) if room else None
            units.append({"id": u["id"], "label": u["label"], "kind": u["kind"], "physical": u["physical"], "status": u["status"], "floor": u["floor"],
                          "room": room, "room_label": r["label"] if r else None, "suite": r.get("suite") if r else None, "node": node,
                          "assignment": dict(u["assignment"]) if u["assignment"] else None,
                          "route_valid": bool(u["route"] and u["route"].get("valid")) if u["route"] else None})
        md = {k: self.mayday[k] for k in ("unit", "room", "room_label", "reason")} if self.mayday else None
        snap = {"phase": self.phase, "units": units,
                "hazards": [{k: h[k] for k in ("id", "ref", "kind", "label", "note")} for h in self.hazards], "mayday": md}
        if self.scan:                                       # only when a scan exists: without one the snapshot stays byte-identical
            snap["scan"] = self._scan_snapshot()
        if self.phones:                                     # only when phones exist (same rule)
            snap["phones"] = [{"id": e["id"], "geometry": e["geometry"], "room": e["room"], "room_label": e["room_label"],
                               "node": e["node"] if e.get("match") == "point" else None, "report": e["report_status"], "people": e.get("people"),
                               "status": e["status"], "rescuer": e["rescuer"]} for e in sorted(self.phones.values(), key=lambda q: q["id"])]
        return snap

    def _scan_snapshot(self) -> dict:
        sc = self.scan
        victims = [{"id": v["id"], "room": v["room"], "room_label": v["room_label"], "node": v["node"], "state": v["state"],
                    "status": v["status"], "rescuer": v["rescuer"]} for v in sorted(self.victims.values(), key=lambda q: q["id"])]
        return {"id": sc.get("id"), "room": sc.get("room"), "room_label": sc.get("room_label"), "rung": sc.get("rung"),
                "simulated": bool(sc.get("simulated")), "sample": bool(sc.get("sample")), "victims": victims,
                "mismatches": len(sc.get("mismatches") or [])}

    def state(self) -> dict:
        now = self.clock()
        reg = self.registration
        anchors = []
        if reg:
            for aid, a in sorted(self.anchors_site.items()):
                X, Y = reg.apply(a["x"], a["y"])
                anchors.append({"id": aid, "x": round(X, 2), "y": round(Y, 2)})
        units = []
        for u in self.units.values():
            pub = {k: v for k, v in u.items() if not k.startswith("_")}
            if u["route"]:
                pub["route"] = {"polyline": u["route"]["polyline"], "length_m": u["route"]["length_m"], "valid": u["route"]["valid"],
                                "done": u["route"].get("done", False), "steps": [s["text"] for s in u["route"].get("steps", [])]}
            units.append(pub)
        md = None
        if self.mayday:
            md = {**{k: self.mayday[k] for k in ("unit", "room", "room_label", "reason")}, "elapsed_s": round(now - self.mayday["t"], 1)}
        scout = {"phase": self.scout["phase"], "room": self.scout["room"], "smoke": self.scout["smoke"], "scan": (self.scan or {}).get("id"),
                 "victims": [{k: v[k] for k in ("id", "x", "y", "room", "room_label", "node", "state", "status", "rescuer")} for v in self.victims.values()]}
        phones = [{k: e.get(k) for k in ("id", "geometry", "x", "y", "room", "room_label", "node", "match", "label", "status", "rescuer", "people", "report_status")}
                  for e in sorted(self.phones.values(), key=lambda q: q["id"])]
        return {"type": "state", "t": round(now, 2), "phase": self.phase, "elapsed_s": round(now - self.started_t, 1) if self.started_t else 0.0,
                "units": units, "hazards": self.hazards, "mayday": md, "anchors": anchors, "scout": scout, "phones": phones}

    def metrics(self) -> dict:
        """Numbers for the after-action report, computed from the timeline."""
        def first(kind, contains=None):
            return next((e for e in self.timeline if e["kind"] == kind and (contains is None or contains in e["text"])), None)
        md, contact, par = first("mayday", "MAYDAY"), first("contact"), first("par")
        assign = next((e for e in self.timeline if e["kind"] == "assign" and "rit rescue" in e["text"] and md and e["t"] >= md["t"]), None)
        out = {"replans": self.stats["replans"], "hazards": len(self.hazards)}
        if md and assign:
            out["mayday_to_assign_s"] = round(assign["t"] - md["t"], 1)
        if md and contact:
            out["mayday_to_contact_s"] = round(contact["t"] - md["t"], 1)
        if md and par:
            out["mayday_to_all_out_s"] = round(par["t"] - md["t"], 1)
        return out
