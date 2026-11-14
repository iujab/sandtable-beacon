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


def twin_hash(twin_raw: dict) -> str:
    """The building, not the session: tags (pins added during incidents) and private bookkeeping keys are left out."""
    import hashlib
    stable = {k: v for k, v in twin_raw.items() if k != "tags" and not str(k).startswith("_")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:12]


def live_story_path(twin_raw: dict) -> Path:
    """Where a recorded Beacon run on a LIVE twin is kept: private, under data/live/, keyed by the twin it was made on."""
    live = getattr(config, "LIVE_DIR", config.DATA_DIR / "live")
    return Path(live) / "beacon" / f"story_{twin_hash(twin_raw)}.json"


def scene_for(twin, room: str) -> dict:
    """Two pucks and two simulated phones inside any room of any twin (a live twin has no fixture scene): pucks one metre in from the
    door and at the room's centre; phones in the two corners farthest from the door, one metre in. Which geometry each phone gets
    (a fix, two candidates) is whatever the Beacon module computes in that room."""
    from .scout.session import room_door
    poly = twin.room(room)["polygon"]
    c = twin.room_centre(room)
    d = (room_door(twin, room) or {}).get("at") or poly[0]

    def toward(p, q, m):
        L = math.dist(p, q) or 1.0
        return [round(p[0] + (q[0] - p[0]) * min(1.0, m / L), 2), round(p[1] + (q[1] - p[1]) * min(1.0, m / L), 2)]
    corners = sorted(poly, key=lambda v: -math.dist(v, d))
    p1, p2 = toward(d, c, 1.0), [round(c[0], 2), round(c[1], 2)]
    ph = [toward(corners[0], c, 1.0), toward(corners[1] if len(corners) > 1 else c, c, 1.0)]
    reports = [{"status": "trapped", "text": "Two of us here, smoke under the door", "people": 2, "can_move": False, "battery_pct": 38},
               {"status": "injured", "text": "Leg hurt", "people": 1, "can_move": False, "battery_pct": 71}]
    return {"room": room, "floor": twin.room(room).get("floor") or twin.exercise_floor, "derived": True,
            "pucks": [{"id": "P1", "x": p1[0], "y": p1[1]}, {"id": "P2", "x": p2[0], "y": p2[1]}],
            "phones": [{"key": f"victim-phone-{i + 1}", "x": q[0], "y": q[1], "report": reports[i]} for i, q in enumerate(ph)]}


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
        q["pruned"] = list(p.get("pruned") or []) + [{"x": c["x"], "y": c["y"], "reason": f"searched by {by}: nobody there", "by": "crew-search"} for c in gone]
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
            obj = {**obj, "pucks": [{**p, "placed": self.placed[p["id"]], **({"source": "hand"} if self.placed[p["id"]] == "by hand" else {})}
                                    if p.get("id") in self.placed else p for p in obj["pucks"]]}
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
        out = await self.remote("POST", "/api/pucks", {**body, "pucks": [{**{k: p[k] for k in ("id", "x", "y", "sigma_m") if k in p},
                                                                         **({"source": "hand"} if self.placed.get(str(p.get("id"))) == "by hand" else {})}
                                                                        for p in body["pucks"]]}
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
                      "scene": data.get("scene_data"), "live_twin": bool(data.get("live_twin")),
                      "meta": {"name": "live building (private)" if data.get("live_twin") else Path(path or STORY).stem,
                               "recorded": data.get("recorded"), "vendored_commit": data.get("vendored_commit"), "note": data.get("note")}}
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
            "people-reported": "people reported, none located", "against-record": "people or devices where the record has no tenant; none reached yet",
            "not-covered": "no puck here: nobody accounted for"}.get(row.get("state"), str(row.get("state")))


def _floor_rows(rows_in: list[dict], incident) -> list[dict]:
    """Per row: phones, people reported and located are never below what the incident holds for the row's rooms
    (each phone once, people from its report). The Beacon object's own table can lag behind (a story's frozen table)."""
    out = []
    for r in rows_in:
        r = dict(r)
        rooms_of = set(r.get("rooms") or ([r["room"]] if r.get("room") else []))
        mine = [q for q in incident.phones.values() if q.get("room") in rooms_of]
        if mine:
            people = sum(int(q.get("people") or 1) for q in mine)
            delta = max(0, people - int(r.get("people_reported") or 0))
            r["phones_opt_in"] = max(int(r.get("phones_opt_in") or 0), len(mine))
            r["people_reported"] = max(int(r.get("people_reported") or 0), people)
            r["located"] = max(int(r.get("located") or 0), sum(1 for q in mine if q.get("geometry") == "fix"))
            if delta and isinstance(r.get("unaccounted"), dict):
                r["unaccounted"] = {**r["unaccounted"], "low": max(0, int(r["unaccounted"].get("low") or 0) - delta),
                                    "high": max(0, int(r["unaccounted"].get("high") or 0) - delta)}
            if r.get("state") in ("unscanned", "silent", "devices-heard", "vacant-on-record"):
                r["state"] = "people-reported"
        out.append(r)
    return out


def _record_live(provider) -> bool:
    return (getattr(provider, "modes", None) or {}).get("costar") == "live"


def _suite_key(v) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    return t[6:].strip() if t.lower().startswith("suite ") else t


def _people_word(n: int) -> str:
    return f"{n} {'person' if n == 1 else 'people'}"


def _phone_room(p: dict, twin=None) -> str | None:
    """The room a phone is counted in: a fix or a zone, candidates that all agree, or for a ring the room of the puck
    it is ranged from (the ring's centre: a count, never a position)."""
    if p.get("room"):
        return p["room"]
    if (p.get("zone") or {}).get("room"):
        return p["zone"]["room"]
    rooms = {c.get("room") for c in p.get("candidates") or []}
    if len(rooms) == 1:
        return rooms.pop()
    ring = p.get("ring") or {}
    if twin is not None and ring.get("cx") is not None:
        return twin.locate(ring["cx"], ring["cy"])["room"]
    return None


def _accountability_live(beacons: dict | None, incident, twin, provider) -> dict:
    """Section 4 of docs/BEACON_BOARD_CONTRACT.md: the record's columns from the provider (this board's record),
    the phone columns from the Beacon object, one row per suite, the storey's other tenancies as `scope: storey`."""
    from .scout.session import floor_number
    storey = floor_number(twin)
    try:
        tenants = provider.costar.list_tenants(storey)
    except Exception:
        tenants = []
    try:
        spaces = provider.costar.list_available_space(storey)
    except Exception:
        spaces = []
    by_tenant: dict = {}
    for t in tenants:
        by_tenant.setdefault(_suite_key(t.get("suite")), []).append(t)
    space_of = {}
    for a in spaces:
        space_of.setdefault(_suite_key(a.get("suite")), []).append(a)
    b = beacons or {}
    phones = [q for q in b.get("phones") or [] if isinstance(q, dict)]
    presence = [c for c in b.get("presence") or [] if isinstance(c, dict)]
    puck_rooms = set()
    for pk in b.get("pucks") or []:
        r = twin.locate(pk["x"], pk["y"])["room"] if pk.get("x") is not None else pk.get("room")
        if r:
            puck_rooms.add(r)

    groups: dict = {}
    for r in twin.rooms():
        if r["kind"] in ("corridor", "stair", "elevator", "restroom", "mechanical") and not r.get("suite"):
            continue
        if r["kind"] in ("corridor", "stair"):
            continue
        key = ("suite", _suite_key(r.get("suite"))) if r.get("suite") else ("room", r["id"])
        groups.setdefault(key, []).append(r)

    def named(ts):                                   # an occupancy with no company, name or head count is not a known tenant
        return [t for t in ts if t.get("name") or t.get("industry") or t.get("employees") is not None]

    def record_of(suite):
        allts = by_tenant.get(suite, []) if suite is not None else []
        ts = named(allts)
        unnamed = len(allts) - len(ts)
        sp = space_of.get(suite, []) if suite is not None else []
        emp = [int(t["employees"]) for t in ts if t.get("employees") is not None]
        area_t = sum(int(t.get("sf") or 0) for t in allts)
        area_s = max((int(a.get("sf") or 0) for a in sp), default=0)
        also = f"; {unnamed} occupancy {'entry' if unnamed == 1 else 'entries'} with no tenant named" if unnamed else ""
        if ts:
            status = "tenant"
            text = f"{len(ts)} {'tenancy' if len(ts) == 1 else 'tenancies'} on record" + (f", {sum(emp)} employees" if emp else ", head count not in the record") + also
        elif unnamed:                                # somebody holds the suite; the record does not say who or how many
            status, text = "unnamed", f"{unnamed} occupancy {'entry' if unnamed == 1 else 'entries'} on record, no tenant named, no head count"
        elif sp:
            status, text = "available", "listed as available space with no current tenant" + (f" ({area_s} sf)" if area_s else "") + also
        else:
            status, text = "vacant", "no current tenant on the record and not listed as available" + also
        rec = {"status": status, "tenants": len(ts), "employees": sum(emp) if emp else None, "area_sf": (area_t or area_s or None), "text": text}
        if emp:
            n = sum(emp)
            expd = {"people": n, "low": max(0, round(n * 0.5)), "high": round(n * 1.2), "basis": "employees in the building record"}
        elif ts:
            expd = {"people": None, "low": None, "high": None, "basis": "tenant on record, head count not in the record"}
        elif unnamed:
            expd = {"people": None, "low": None, "high": None, "basis": "an occupancy entry on record with no tenant named and no head count"}
        else:
            expd = {"people": 0, "low": 0, "high": 0, "basis": "no current tenant on the record"}
        name = " / ".join(str(t.get("name")) for t in ts if t.get("name")) or None
        hz = "; ".join(sorted({t["life_hazard"] for t in ts if t.get("life_hazard")})) or None
        return rec, expd, name, hz

    rows_in = []
    for (kind, key), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        rs = sorted(rs, key=lambda q: q["id"])
        ids = [q["id"] for q in rs]
        suite = key if kind == "suite" else None
        rec, expd, name, hz = record_of(suite)
        here = [q for q in phones if _phone_room(q, twin) in ids and not (q.get("report") or {}).get("safe_outside")]
        n_people = sum(int((q.get("report") or {}).get("people") or 1) for q in here)
        # The incident remembers every phone that reported from these rooms, also after a crew has brought its people out or the
        # phone itself says it is outside: the row must never show fewer people than were reported here (the table once lagged).
        mine = [q for q in incident.phones.values() if q.get("room") in ids]
        n_phones = max(len(here), len(mine))
        n_people = max(n_people, sum(int(q.get("people") or 1) for q in mine))
        out_people = sum(int(q.get("people") or 1) for q in mine if q.get("status") == "out")
        n_located = max(sum(1 for q in here if q.get("geometry") == "fix"), sum(1 for q in mine if q.get("geometry") == "fix" or q.get("status") in ("reached", "evacuating", "out")))
        cold = [c for c in presence if c.get("room") in ids]
        cold_n = max((int(c.get("count") or 0) for c in cold), default=0)
        cold_rng = [max((int((c.get("count_range") or [0, 0])[0]) for c in cold), default=0), max((int((c.get("count_range") or [0, 0])[1]) for c in cold), default=0)]
        safe = sum(1 for q in phones if (q.get("report") or {}).get("safe_outside") and suite is not None and _suite_key((q.get("report") or {}).get("home_suite")) == suite)
        covered = any(i in puck_rooms for i in ids)
        if rec["status"] in ("available", "vacant") and (n_people or cold_n):
            state = "against-record"
            what = (f"{n_phones} phone{'s' if n_phones != 1 else ''} report {_people_word(n_people)} in it" if n_people else f"{cold_n} devices are heard in it (devices, not people)")
            why = ("the record lists this suite as available space with no tenant; " if rec["status"] == "available" else "the record has no current tenant for this suite; ") + what
        elif n_people and rec["status"] == "unnamed":
            state = "people-reported"
            why = (f"the record has an occupancy entry for this suite but names no tenant and gives no head count; "
                   f"{n_phones} phone{'s' if n_phones != 1 else ''} report {_people_word(n_people)} in it")
        elif n_people:
            state, why = "people-reported", f"{n_phones} phone{'s' if n_phones != 1 else ''} report {_people_word(n_people)}; {n_located} located"
        elif cold_n:
            state, why = "devices-heard", f"{cold_n} devices heard and no opt-in phone: devices are not people"
        elif covered:
            state, why = "silent", "a puck is here and heard nothing: weak evidence of empty, never proof"
        elif rec["status"] in ("tenant", "unnamed"):
            state, why = "not-covered", ("a tenancy on record" if rec["status"] == "tenant" else "an occupancy entry on record") + " with no puck in it: nobody there is accounted for"
        else:
            state, why = "vacant-on-record", "no tenant on the record and no puck here"
        accounted = n_people + safe
        if state == "not-covered" or expd["low"] is None:
            un = {"low": expd["low"], "high": expd["high"]} if expd["low"] is not None else None
        else:
            un = {"low": max(0, expd["low"] - accounted), "high": max(0, expd["high"] - accounted)}
        label = rs[0]["label"] if len(rs) == 1 else f"Suite {suite} ({len(rs)} rooms)"
        rows_in.append({"suite": suite, "room": ids[0], "rooms": ids, "room_label": label, "tenant": name, "life_hazard": hz, "expected": expd,
                        "phones_opt_in": n_phones, "people_reported": n_people, "people_out": out_people, "located": n_located,
                        "cold_devices": cold_n, "cold_range": cold_rng, "safe_outside": safe, "unaccounted": un, "state": state, "priority": None, "why": why,
                        "scope": "scan", "record": rec, "covered": covered, "_targets": sorted({q["id"] for q in here} | {q["id"] for q in mine})})
    in_twin = {r["suite"] for r in rows_in if r["suite"] is not None}
    for suite in sorted((k for k in by_tenant if k is not None and k not in in_twin and named(by_tenant[k])), key=str):
        rec, expd, name, hz = record_of(suite)
        rng = f"about {expd['low']} to {expd['high']} people on record" if expd["low"] is not None else "head count not in the record"
        rows_in.append({"suite": suite, "room": None, "rooms": [], "room_label": f"Suite {suite}", "tenant": name, "life_hazard": hz, "expected": expd,
                        "phones_opt_in": 0, "people_reported": 0, "located": 0, "cold_devices": 0, "cold_range": [0, 0], "safe_outside": 0,
                        "unaccounted": {"low": expd["low"], "high": expd["high"]} if expd["low"] is not None else None, "state": "not-covered", "priority": None,
                        "why": f"same storey, not in the scan and no puck there: {rng}", "scope": "storey", "record": rec, "covered": False, "_targets": []})

    # priorities: people reported against the record first, then people reported, then tenancies nobody has covered (most people on record first)
    def rank_key(r):
        exp_hi = (r["expected"] or {}).get("high") or 0
        return ({"against-record": 0, "people-reported": 1, "not-covered": 2, "devices-heard": 3}[r["state"]],
                -(r["people_reported"] or 0) if r["state"] in ("against-record", "people-reported") else -exp_hi, 0 if r.get("life_hazard") else 1, str(r["suite"] or r["room"]))
    ranked = sorted((r for r in rows_in if r["state"] in ("against-record", "people-reported", "not-covered", "devices-heard")
                     and not (r["state"] == "against-record" and not r["people_reported"] and not r["cold_devices"])), key=rank_key)
    priorities = []
    for i, r in enumerate(ranked, 1):
        r["priority"] = i
        priorities.append({"rank": i, "suite": r["suite"], "room": r["room"], "target": (r["_targets"] or [None])[0], "text": f"{r['room_label']}: {r['why']}"})

    nc = [r for r in rows_in if r["state"] == "not-covered"]
    nc_storey = [r for r in nc if r["scope"] == "storey"]
    st_low = sum(int((r["expected"] or {}).get("low") or 0) for r in nc)
    st_high = sum(int((r["expected"] or {}).get("high") or 0) for r in nc)
    storey_label = str(twin.floor().get("label") or storey)
    against = [r for r in rows_in if r["state"] == "against-record" and r["people_reported"]]
    unnamed_rows = [r for r in rows_in if r["record"]["status"] == "unnamed" and r["people_reported"]]
    parts = []
    for r in against + unnamed_rows:
        said = (f"The record lists {r['room_label']} as available space with no tenant" if r["record"]["status"] == "available"
                else f"The record does not say who is in {r['room_label']} (an occupancy entry with no tenant named and no head count)" if r["record"]["status"] == "unnamed"
                else f"The record has no current tenant for {r['room_label']}")
        n_rep, n_out = r["people_reported"], r.get("people_out") or 0
        parts.append(f"{said}; " + (f"the phones reported {_people_word(n_rep)} in it, all brought out." if n_out and n_out >= n_rep
                                    else f"the phones reported {_people_word(n_rep)} in it, {n_out} brought out so far." if n_out
                                    else f"the phones say {_people_word(n_rep)} {'is' if n_rep == 1 else 'are'} in it."))
    if not (against or unnamed_rows):
        rep = sum(r["people_reported"] for r in rows_in)
        parts.append(f"Phones report {_people_word(rep)} on this storey." if rep else "No phone reports anyone on this storey yet.")
    if nc:
        parts.append(f"{len(nc)} {'tenancy' if len(nc) == 1 else 'tenancies'} on storey {storey_label} {'has' if len(nc) == 1 else 'have'} no puck: "
                     + (f"about {st_low} to {st_high} people on record, nobody accounted for." if st_high else "head counts not in the record, nobody accounted for."))
    headline = " ".join(parts)

    people = list(incident.phones.values()) + list(incident.victims.values())
    rows = []
    for r in rows_in:
        rooms_of = set(r["rooms"])
        here = [q for q in people if q.get("room") in rooms_of]
        assigned = sorted({q["rescuer"] for q in here if q.get("rescuer")})
        rows.append({**{k: r.get(k) for k in ("suite", "room", "room_label", "tenant", "life_hazard", "expected", "phones_opt_in", "people_reported",
                                              "located", "cold_devices", "unaccounted", "state", "priority", "why")},
                     "rooms": sorted(rooms_of), "scope": r["scope"], "record": r["record"], "covered": r["covered"],
                     "cold_range": r["cold_range"], "safe_outside": r["safe_outside"],
                     "crews": sorted(u["id"] for u in incident.units.values() if u.get("room") in rooms_of),
                     "hazards": [h["id"] for h in incident.hazards if h["ref"] in rooms_of],
                     "radar_victims": [v["id"] for v in incident.victims.values() if v.get("room") in rooms_of],
                     "targets": sorted(q["id"] for q in here),
                     "assigned": assigned[0] if assigned else None,
                     "reached": any(q["status"] in ("reached", "evacuating", "out") for q in here),
                     "out": bool(here) and all(q["status"] == "out" for q in here if q.get("rescuer")) and any(q.get("rescuer") for q in here),
                     "outcome": _outcome(r, here)})
    exp = [(r.get("expected") or {}) for r in rows_in]
    un = [(r.get("unaccounted") or {}) for r in rows_in]
    totals = {"expected_people": sum(int(e.get("people") or 0) for e in exp), "expected_low": sum(int(e.get("low") or 0) for e in exp),
              "expected_high": sum(int(e.get("high") or 0) for e in exp), "unaccounted_low": sum(int(u.get("low") or 0) for u in un),
              "unaccounted_high": sum(int(u.get("high") or 0) for u in un), "safe_outside": sum(r["safe_outside"] for r in rows_in),
              "phones_opt_in": sum(r["phones_opt_in"] for r in rows_in), "people_reported": sum(r["people_reported"] for r in rows_in),
              "located": sum(r["located"] for r in rows_in), "cold_devices": sum(r["cold_devices"] for r in rows_in),
              "suites_unscanned": sum(1 for r in rows_in if not r["covered"]),
              "against_record_people": sum(r["people_reported"] for r in against), "not_covered_suites": len(nc),
              "targets": len(people), "assigned": sum(1 for q in people if q.get("rescuer")),
              "reached": sum(1 for q in people if q["status"] in ("reached", "evacuating", "out")), "out": sum(1 for q in people if q["status"] == "out")}
    try:
        lab = provider.labels().get("costar") or {}
    except Exception:
        lab = {"real_sponsor_data": True, "text": "Building record: CoStar (live)."}
    src = {"costar_mode": "live", "real_sponsor_data": bool(lab.get("real_sponsor_data")), "text": lab.get("text"),
           "beacon": "sample (hand-made, not a result)" if b.get("sample") else ("simulated" if b.get("simulated", True) else "measured") if beacons else "none"}
    return {"type": "accountability", "schema": "sandtable.accountability/1", "floor": twin.exercise_floor, "rows": rows, "totals": totals, "source": src,
            "priorities": priorities, "headline": headline,
            "storey": {"label": storey_label, "tenancies": len(named(tenants)), "in_scan": sum(len(named(by_tenant.get(sx, []))) for sx in in_twin),
                       "not_covered": len(nc_storey), "expected_low": sum(int((r["expected"] or {}).get("low") or 0) for r in nc_storey),
                       "expected_high": sum(int((r["expected"] or {}).get("high") or 0) for r in nc_storey)},
            "limits": ["Expected head counts are employees in the building record, never a census of who is in now",
                       "Devices are not people: cold counts are device bands",
                       "A silent suite is weak evidence of empty, never proof",
                       "Unaccounted is a range: expected minus everybody reported inside or outside"]}


def closing_line(incident, names: str | None = None) -> str:
    """What is true when the crews are out of an incident with phones or radar victims (never "accountability complete"):
    crews out, reported people out of reported, suites never searched, and the unaccounted range from the same table."""
    people = list(incident.phones.values()) + list(incident.victims.values())
    total = sum(int(q.get("people") or 1) for q in people)
    out = sum(int(q.get("people") or 1) for q in people if q.get("status") == "out")
    try:
        acc = accountability(getattr(incident, "beacons", None), incident, incident.twin, incident.provider)
    except Exception:
        acc = {}
    t, rows = acc.get("totals") or {}, acc.get("rows") or []
    k = t.get("suites_unscanned")
    if k is None:
        k = sum(1 for r in rows if r.get("state") in ("unscanned", "not-covered"))
    known = any(isinstance(r.get("unaccounted"), dict) and r["unaccounted"].get("high") is not None for r in rows)
    un = (f"{t.get('unaccounted_low', 0)} to {t.get('unaccounted_high', 0)} (an estimate)" if known else "not known (the record gives no head counts)")
    return f"All crews out{' with ' + names if names else ''}. Reported people out: {out} of {total}. Not searched: {k} suite{'s' if k != 1 else ''}. Unaccounted on the record: {un}."


def record_vs_phones(acc: dict | None) -> dict | None:
    """The report's difference between the record and the phones (section 4), or None."""
    if acc and acc.get("headline") and any(r.get("people_reported") and (r.get("state") == "against-record" or (r.get("record") or {}).get("status") == "unnamed")
                                           for r in acc.get("rows") or []):
        return {"id": "record_vs_phones", "kind": "record_vs_phones", "source": ["costar", "beacon"], "text": acc["headline"]}
    return None


def accountability(beacons: dict | None, incident, twin, provider) -> dict:
    """The Beacon object's accountability (record suites against phones) joined with what only this board knows:
    crews in each room, hazards, radar victims, and who was assigned to and reached whom."""
    if _record_live(provider):                                   # the record is this board's to read (contract section 4); fixture mode is unchanged
        return _accountability_live(beacons, incident, twin, provider)
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
    rows_in = _floor_rows(rows_in, incident)
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
    for k in ("phones_opt_in", "people_reported", "located"):                  # never below the rows (which are never below the incident)
        if k in totals or any(r.get(k) for r in rows_in):
            totals[k] = max(int(totals.get(k) or 0), sum(int(r.get(k) or 0) for r in rows_in))
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
