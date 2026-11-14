"""Beacon link: relays the Beacon module (victims located by their phones; sibling lane L16, port 1885) to the
command board, the incident and the reports.

    SANDTABLE_BEACON = auto    (default) use the Beacon server at BEACON_URL when it answers; when it does not, relay the
                               hand-made sample instead, labelled ("sample": true, link.connected false), and switch back
                               to the live server as soon as it answers again
                       sample  always the sample (fixtures/beacon/sample_beacons.json), for development and for running
                               with no Beacon server
                       off     nothing

The frozen feed is ../sandtable-beacon-victim-phones/docs/BEACON_FEED.md. Its physics rules are kept here: a ring stays a
ring, two candidates stay two candidates, nothing gets x/y unless its geometry is "fix", devices are not people.

The sample is staged like a real deployment: until pucks are in for this incident (`pucks_in()`), the relayed sample has
no pucks, phones or presence and every suite is "unscanned"; then the sample's phones appear. With the live server the
Beacon module itself decides what exists. Either way the incident only takes phones as targets once pucks are in.
This module never raises into the server: the Beacon server may be absent, slow or restarted.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger("sandtable.beacon")
SAMPLE = config.FIXTURES / "beacon" / "sample_beacons.json"
STORY = config.FIXTURES / "beacon" / "story_two_pucks.json"        # a recorded run of the simulated Beacon module (scripts/make_beacon_story.py)
SCENE = config.FIXTURES / "beacon" / "two_puck_scene.json"         # the owner's two-puck setup: puck positions and the simulated phones
POLL_S = 0.5


def _http(method: str, url: str, body: dict | None = None, timeout: float = 1.5):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def load_sample() -> dict | None:
    """The sample kept in this tree (fixtures/beacon/sample_beacons.json: the Beacon module's own sample, refreshed only when
    scripts/vendor_beacon.py runs, so tests and recordings never change under us)."""
    try:
        obj = json.loads(SAMPLE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.error("beacon: no sample at %s", SAMPLE)
        return None
    obj["sample"] = True
    for k in ("phones", "presence", "pucks"):
        for item in obj.get(k) or []:
            item["sample"] = True
    return obj


def _unscanned(obj: dict) -> dict:
    """The sample before any puck is in: no pucks, phones or presence; every suite of the record unscanned."""
    o = copy.deepcopy(obj)
    o.update({"seq": 0, "pucks": [], "phones": [], "presence": [], "room": None, "room_label": None})
    acc = o.get("accountability") or {}
    for r in acc.get("suites") or []:
        exp = r.get("expected") or {}
        r.update({"phones_opt_in": 0, "people_reported": 0, "located": 0, "cold_devices": 0, "cold_range": [0, 0], "app_registered": 0, "safe_outside": 0,
                  "unaccounted": {"low": exp.get("low"), "high": exp.get("high")}, "state": "unscanned", "priority": None,
                  "why": "no puck in range: nothing is known about this suite beyond the record"})
    if acc:
        rows = acc.get("suites") or []
        acc["totals"] = {"expected_people": sum((r.get("expected") or {}).get("people") or 0 for r in rows), "phones_opt_in": 0, "people_reported": 0,
                         "located": 0, "cold_devices": 0, "suites_unscanned": len(rows)}
        acc["priorities"] = []
        acc["text"] = "SAMPLE. No pucks are in yet: every suite is unscanned."
    return o


def apply_searches(obj: dict, searches: dict) -> dict:
    """A crew searched a phone's candidate point and found nobody: that candidate is pruned ("searched by <crew>: nobody there");
    with one candidate left the phone is a fix there. This is Sandtable's annotation on the Beacon module's object (the module has no
    search input); the phone carries "annotated" saying so."""
    if not searches:
        return obj
    phones, changed = [], False
    for p in obj.get("phones") or []:
        done = searches.get(p.get("id"))
        if not done or p.get("geometry") != "candidates":
            phones.append(p)
            continue
        keep, gone = [], []
        for c in p.get("candidates") or []:
            (gone if any(math.dist((c["x"], c["y"]), (q["x"], q["y"])) <= 1.0 for q in done) else keep).append(c)
        if not gone:
            phones.append(p)
            continue
        changed = True
        by = done[-1]["by"]
        q = dict(p)
        q["pruned"] = list(p.get("pruned") or []) + [{"x": c["x"], "y": c["y"], "reason": f"searched by {by}: nobody there"} for c in gone]
        rep = p.get("report") or {}
        n = rep.get("people")
        who = (f"{n} {'person' if n == 1 else 'people'}" if n else "occupant") + (f", {rep['status']}" if rep.get("status") else "") + \
              (", cannot move" if rep.get("can_move") is False else "")
        if len(keep) == 1:
            c = keep[0]
            q.update({"geometry": "fix", "x": c["x"], "y": c["y"], "sigma_m": c.get("sigma_m"), "cov": None, "geo": c.get("geo"),
                      "room": c.get("room"), "room_label": c.get("room_label"), "room_prob": None, "candidates": [],
                      "text": f"Phone {p['id']}: {who}. Fix in {c.get('room_label')} at the other candidate: {by} searched the first one and found nobody there"})
        else:
            tot = sum(float(c.get("weight") or 0) for c in keep) or 1.0
            q["candidates"] = [{**c, "weight": round(float(c.get("weight") or 0) / tot, 3)} for c in keep]
        q["annotated"] = f"Sandtable: a candidate removed by a crew's search ({by}); not a Beacon module output"
        phones.append(q)
    return {**obj, "phones": phones, "annotated": True} if changed else obj


class BeaconLink:
    def __init__(self, emit: Callable[[dict], None], incident=None, url: str | None = None, mode: str | None = None, fetch=None):
        self.emit, self.incident = emit, incident
        self.mode = (mode or os.environ.get("SANDTABLE_BEACON") or "auto").strip().lower()
        self.url = (url or os.environ.get("BEACON_URL") or "http://127.0.0.1:1885").rstrip("/")
        self.fetch = fetch or _http
        self.beacons: dict | None = None        # the latest relayed Beacons object
        self.brief: dict | None = None          # the Beacon server's /api/brief (live only)
        self.connected = False
        self.source: str | None = None          # "live" | "sample" | None
        self.last_ok: float | None = None
        self.error: str | None = None
        self.pucks_are_in = False
        self.placed: dict[str, str] = {}        # puck id -> how its position is known ("by hand": tape-measured and clicked onto the plan)
        self.story: dict | None = None          # a recorded run being replayed: {"frames": {step: obj}, "step": name, "meta": {...}}
        self._sample = load_sample() if self.mode != "off" else None
        self._key = None
        self._brief_seq = None

    # ---- status
    def link_info(self) -> dict:
        out = {"connected": self.connected, "url": self.url, "mode": self.mode, "source": self.source,
               "age_s": round(time.time() - self.last_ok, 1) if self.last_ok else None, "sample": self.source == "sample", "error": self.error}
        if self.story is not None:
            out.update({"replay": True, "story": self.story["meta"].get("name"), "step": self.story["step"],
                        "note": "replay of a recorded run of the SIMULATED Beacon module"})
        return out

    def state_info(self) -> dict:
        b = self.beacons or {}
        phones = b.get("phones") or []
        return {"connected": self.connected, "source": self.source, "simulated": bool(b.get("simulated", True)), "sample": self.source == "sample",
                "phones": len(phones), "fixes": sum(1 for p in phones if p.get("geometry") == "fix"),
                "people_reported": sum(int((p.get("report") or {}).get("people") or 0) for p in phones), "pucks_in": self.pucks_are_in}

    # ---- the loop
    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:                                   # never into the server
                log.exception("beacon link")
            await asyncio.sleep(POLL_S)

    async def poll_once(self) -> None:
        if self.story is not None:                              # a recorded run: identical objects on every run (replay determinism)
            self.source, self.connected, self.brief = "replay", False, None
            frame = self.story["frames"].get(self.story["step"]) if self.story["step"] else None
            obj = frame if frame is not None else _unscanned(self.story["first"])
            self._accept({**obj, "replay": True, "simulated": True, "sample": False})
            return
        if self.mode == "off":
            return
        obj = None
        if self.mode == "auto":
            try:
                obj = await asyncio.to_thread(self.fetch, "GET", self.url + "/api/beacons", None, 1.5)
                if not isinstance(obj, dict) or obj.get("type") != "beacons":
                    raise ValueError("not a Beacons object")
                if not self.connected:
                    log.info("beacon: connected to the Beacon server at %s", self.url)
                self.connected, self.source, self.last_ok, self.error = True, "live", time.time(), None
                if obj.get("seq") != self._brief_seq:
                    try:
                        self.brief = await asyncio.to_thread(self.fetch, "GET", self.url + "/api/brief", None, 1.5)
                        self._brief_seq = obj.get("seq")
                    except Exception:
                        pass
            except Exception as exc:
                if self.connected or self.error is None:
                    log.warning("beacon: Beacon server at %s not answering (%s): relaying the SAMPLE", self.url, type(exc).__name__)
                self.connected, self.error, obj = False, f"{type(exc).__name__}", None
        if obj is None:
            if self._sample is None:
                return
            self.source, self.brief = "sample", None
            obj = self._sample if self.pucks_are_in else _unscanned(self._sample)
        self._accept(obj)

    def _accept(self, obj: dict) -> None:
        searches = self.incident.phone_searches() if self.incident is not None and hasattr(self.incident, "phone_searches") else {}
        key = (self.source, self.pucks_are_in, obj.get("seq"), obj.get("t"), (self.story or {}).get("step"), json.dumps(searches, sort_keys=True))
        if key == self._key:
            return
        self._key = key
        obj = apply_searches(obj, searches)
        if self.placed and obj.get("pucks"):
            obj = {**obj, "pucks": [{**p, "placed": self.placed[p["id"]]} if p.get("id") in self.placed else p for p in obj["pucks"]]}
        self.beacons = obj
        self.emit({**obj, "type": "beacons", "link": self.link_info()})
        if self.incident is not None:
            self.incident.on_beacons(obj)

    def refresh(self) -> None:
        """Re-relay the current object (after a reset or when pucks go in)."""
        self._key = None

    async def repoll(self) -> None:
        """Re-relay now (after a crew's search changed what is known)."""
        self.refresh()
        await self.poll_once()

    # ---- actions on the Beacon server (all tolerant)
    async def remote(self, method: str, path: str, body: dict | None = None, timeout: float = 3.0) -> dict:
        if self.mode != "auto":
            return {"ok": False, "reason": f"Beacon link mode is {self.mode}: no Beacon server is used"}
        try:
            out = await asyncio.to_thread(self.fetch, method, self.url + path, body, timeout)
            return {"ok": True, "result": out}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "reason": f"Beacon server answered {exc.code} on {path}"}
        except Exception as exc:
            return {"ok": False, "reason": f"Beacon server not reachable ({type(exc).__name__})"}

    async def pucks_in(self, body: dict, remote: bool = True) -> dict:
        """Pucks are in the room: forward them to the Beacon server (POST /api/pucks) and let phones become targets.
        Pucks given with positions ({"pucks": [{id, x, y}], "room"}) are placed by hand (tape-measured, clicked onto the plan)."""
        self.pucks_are_in = True
        if body.get("pucks"):
            self.placed = {str(p["id"]): str(p.get("placed") or "by hand") for p in body["pucks"] if p.get("id")}
        if self.incident is not None:
            self.incident.activate_beacon(True)
        if not remote or self.story is not None:
            self.refresh()
            await self.poll_once()
            return {"ok": True, "reason": "replay of a recorded run: nothing sent to a Beacon server" if self.story is not None else "not sent"}
        out = await self.remote("POST", "/api/pucks", {**body, "pucks": [{k: p[k] for k in ("id", "x", "y", "sigma_m") if k in p} for p in body["pucks"]]}
                                if body.get("pucks") else body)
        if not out["ok"]:
            out["source"] = "sample" if self._sample is not None else None
        self.refresh()
        await self.poll_once()
        return out

    # ---- a recorded run (the hands-free beacon exercise)
    def start_story(self, path=None) -> bool:
        try:
            data = json.loads((path or STORY).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.error("beacon: no recorded run at %s: the script uses the live module or the sample instead", path or STORY)
            return False
        frames = {f["step"]: f["beacons"] for f in data.get("frames") or []}
        if not frames:
            return False
        self.story = {"frames": frames, "order": [f["step"] for f in data["frames"]], "first": data["frames"][0]["beacons"], "step": None,
                      "meta": {"name": Path(path or STORY).stem, "recorded": data.get("recorded"), "vendored_commit": data.get("vendored_commit"), "note": data.get("note")}}
        self.refresh()
        return True

    async def story_step(self, step: str) -> None:
        if self.story is None or step not in self.story["frames"]:
            log.warning("beacon: no recorded step %r", step)
            return
        self.story["step"] = step
        self.refresh()
        await self.poll_once()

    def stop_story(self) -> None:
        if self.story is not None:
            self.story = None
            self.source = None
            self.refresh()

    async def reset(self, remote: bool = False) -> None:
        self.pucks_are_in = False
        self.placed = {}
        self.stop_story()
        if self.incident is not None:
            self.incident.activate_beacon(False)
        if remote:
            await self.remote("POST", "/api/reset", {})
        self.refresh()


# ---------------------------------------------------------------------------- accountability (sandtable.accountability/1)
def _outcome(row: dict, here: list[dict]) -> str:
    """One line per suite: what happened to the people located there, or what the evidence is when nobody was."""
    def who(ps):
        return ", ".join(f"{p['id']}" + (f" ({p['people']} {'person' if p['people'] == 1 else 'people'})" if p.get("people") else "") for p in ps)
    out = [p for p in here if p["status"] == "out"]
    moving = [p for p in here if p["status"] in ("reached", "evacuating")]
    assigned = [p for p in here if p["status"] == "assigned"]
    waiting = [p for p in here if p["status"] == "located"]
    parts = []
    if out:
        parts.append(f"brought out: {who(out)}")
    if moving:
        parts.append(f"reached, coming out: {who(moving)}")
    if assigned:
        parts.append(f"crew on the way: {who(assigned)}")
    if waiting:
        parts.append(f"located, not reached: {who(waiting)}")
    if parts:
        return "; ".join(parts)
    return {"unscanned": "unscanned: nothing known beyond the record", "silent": "silent: a puck heard nothing (weak evidence of empty, never proof)",
            "vacant-on-record": "vacant on the record", "devices-heard": "devices heard, no opt-in phone (devices are not people)",
            "people-reported": "people reported, none located"}.get(row.get("state"), str(row.get("state")))


def accountability(beacons: dict | None, incident, twin, provider) -> dict:
    """The Beacon object's accountability (record suites against phones) joined with what only this board knows:
    crews in each room, hazards, radar victims, and who was assigned to and reached whom."""
    acc = (beacons or {}).get("accountability") or {}
    rows_in = acc.get("suites") or []
    floor = acc.get("floor") or twin.exercise_floor
    if not rows_in:                                              # no Beacon data at all: the record alone
        from .scout.session import floor_number
        try:
            listed = provider.costar.list_tenants(floor_number(twin))
        except Exception:
            listed = []
        tenants: dict = {}
        for t in listed:
            tenants.setdefault(t.get("suite"), []).append(t)
        suite_rooms = [r for r in twin.rooms() if r.get("suite") and r["kind"] not in ("corridor", "stair")]
        base = {"expected": None, "phones_opt_in": 0, "people_reported": 0, "located": 0, "cold_devices": 0, "unaccounted": None,
                "state": "unscanned", "priority": None, "why": "no Beacon data"}

        def name(ts):
            return " / ".join(str(t.get("name")) for t in ts if t.get("name")) or None

        def hz(ts):
            return "; ".join(sorted({t["life_hazard"] for t in ts if t.get("life_hazard")})) or None
        def expected(ts):
            emp = sum(int(t.get("employees") or 0) for t in ts)
            if emp:
                return {"people": emp, "low": max(0, round(emp * 0.5)), "high": round(emp * 1.2), "basis": "employees in the building record: an estimate of who is in"}
            return None
        if suite_rooms:
            by_suite: dict = {}
            for r in suite_rooms:
                by_suite.setdefault(r["suite"], []).append(r)
            for suite, rs in sorted(by_suite.items(), key=lambda kv: str(kv[0])):
                ts = tenants.get(suite, [])
                rs = sorted(rs, key=lambda q: q["id"])
                rows_in.append({**base, "suite": suite, "room": rs[0]["id"], "rooms": [q["id"] for q in rs],
                                "room_label": rs[0]["label"] if len(rs) == 1 else f"Suite {suite} ({len(rs)} rooms)", "tenant": name(ts), "life_hazard": hz(ts),
                                "expected": expected(ts)})
            for suite, ts in sorted(tenants.items(), key=lambda kv: str(kv[0])):    # the storey's other tenancies: context, not in the twin
                if suite not in by_suite:
                    rows_in.append({**base, "suite": suite, "room": None, "room_label": f"Suite {suite}" if suite else None, "tenant": name(ts),
                                    "life_hazard": hz(ts), "expected": expected(ts), "why": "elsewhere on this storey: not in the twin"})
        else:                                                    # a scanned suite whose rooms carry no suite numbers: rooms, then the floor's tenants
            for r in twin.rooms():
                if r["kind"] not in ("corridor", "stair", "elevator", "restroom", "mechanical"):
                    rows_in.append({**base, "suite": None, "room": r["id"], "room_label": r["label"], "tenant": None, "life_hazard": None})
            for suite, ts in sorted(tenants.items(), key=lambda kv: str(kv[0])):
                rows_in.append({**base, "suite": suite, "room": None, "room_label": f"Suite {suite}" if suite else None, "tenant": name(ts), "life_hazard": hz(ts),
                                "why": "tenant on this floor in the record; its rooms are not in the twin"})
    people = list(incident.phones.values()) + list(incident.victims.values())
    rows = []
    for r in rows_in:
        room = r.get("room")
        rooms_of = set(r.get("rooms") or ([room] if room else []))
        here = [p for p in people if p.get("room") in rooms_of]
        assigned = sorted({p["rescuer"] for p in here if p.get("rescuer")})
        rows.append({**{k: r.get(k) for k in ("suite", "room", "room_label", "tenant", "life_hazard", "expected", "phones_opt_in", "people_reported",
                                              "located", "cold_devices", "unaccounted", "state", "priority", "why")},
                     "rooms": sorted(rooms_of),
                     "crews": sorted(u["id"] for u in incident.units.values() if u.get("room") in rooms_of),
                     "hazards": [h["id"] for h in incident.hazards if h["ref"] in rooms_of],
                     "radar_victims": [v["id"] for v in incident.victims.values() if v.get("room") in rooms_of],
                     "targets": sorted(p["id"] for p in here),
                     "assigned": assigned[0] if assigned else None,
                     "reached": any(p["status"] in ("reached", "evacuating", "out") for p in here),
                     "out": bool(here) and all(p["status"] == "out" for p in here if p.get("rescuer")) and any(p.get("rescuer") for p in here),
                     "outcome": _outcome(r, here)})
    totals = dict(acc.get("totals") or {})
    exp = [(r.get("expected") or {}) for r in rows_in]
    un = [(r.get("unaccounted") or {}) for r in rows_in]
    totals.update({"expected_people": sum(int(e.get("people") or 0) for e in exp), "expected_low": sum(int(e.get("low") or 0) for e in exp),
                   "expected_high": sum(int(e.get("high") or 0) for e in exp), "unaccounted_low": sum(int(u.get("low") or 0) for u in un if isinstance(u, dict)),
                   "unaccounted_high": sum(int(u.get("high") or 0) for u in un if isinstance(u, dict)),
                   "safe_outside": sum(int(r.get("safe_outside") or 0) for r in rows_in)})
    totals.update({"targets": len(people), "assigned": sum(1 for p in people if p.get("rescuer")),
                   "reached": sum(1 for p in people if p["status"] in ("reached", "evacuating", "out")),
                   "out": sum(1 for p in people if p["status"] == "out")})
    src = dict(acc.get("source") or {})
    src["beacon"] = "sample (hand-made, not a result)" if (beacons or {}).get("sample") else ("simulated" if (beacons or {}).get("simulated", True) else "measured") if beacons else "none"
    return {"type": "accountability", "schema": "sandtable.accountability/1", "floor": floor, "rows": rows, "totals": totals, "source": src,
            "priorities": acc.get("priorities") or [],
            "limits": ["Expected head counts are estimates from floor area and a load factor, never a census",
                       "Devices are not people: cold counts are device bands",
                       "A silent suite is weak evidence of empty, never proof",
                       "Unaccounted is a range: expected minus everybody reported inside or outside"]}
