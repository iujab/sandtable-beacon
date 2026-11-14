"""The command agent's tools: what it can look up, measure and do.

Each tool returns a compact, deterministic dict (ids, labels, whole metres) so that a
decision is reproducible and a recorded model conversation replays exactly. Unit positions
come from the frozen snapshot of the situation being handled, not from live state.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..spatial import geo_distance_m, hose_stretch, plan_anchor_drops
from .llm import LLM, file_sha

log = logging.getLogger("sandtable.agent")

ACTION_TOOLS = {"radio", "assign", "mark_hazard", "request_resource", "add_observation", "file_record_correction"}

CATALOG = [
    # (name, layer, signature, description)
    ("get_building_record", "costar", "{}", "The building record: type, area, floors, year, construction, sprinklers, contacts; which fields are missing or synthetic; open cross-checks between layers."),
    ("list_tenants", "costar", '{"floor": 3?}', "Tenants (suite, category, life hazard, hazmat). Default: the exercise floor. Names are withheld in fixture mode."),
    ("list_vacant_space", "costar", '{"floor": 3?}', "Available space in the record, plus suites that exist in the twin without an active registration."),
    ("outside_context", "vexcel", "{}", "Around the building: the four sides A-D with compass bearings, apparatus position, entrances, hydrants with distances, nearest fire stations, building height."),
    ("list_aerial_imagery", "vexcel", "{}", "Aerial images available for this address (id, kind, resolution, source, date)."),
    ("look_at_aerial", "vexcel", '{"image_id": "ortho", "question": "..."}', "Vision: you are shown the aerial image with the footprint, side letters A-D, hydrants (H) and Engine 1 (E1) drawn on it, and answer one precise question."),
    ("measure_outside", "vexcel", '{"from_ref": "E1", "to_ref": "hydrant:h1"}', "Ground distance in metres between two outside references: an apparatus id, entrance:<id>, hydrant:<id>, station:<index>, building."),
    ("describe_floor", "matterport", "{}", "The exercise floor in the twin: size, suites, core rooms, stairs (discharge side, standpipe), tags, corridor loop."),
    ("locate_unit", "uwb", '{"unit": "E1"}', "Where a crew is (room from the twin), who the record says is in that suite, and the walking distance to each stair with current hazards."),
    ("route", "matterport", '{"src": "RIT", "dst": "E1", "avoid": ["rcE"]?}', "Walking route in the twin. src/dst: a unit id, suite number, room id, stair id (stairA), door id, node id, or \"exit\" (dst only). Current hazards are always avoided; avoid adds more."),
    ("nearest_exit", "matterport", '{"src": "E1"}', "Best reachable stair from a unit or place with current hazards, with length and steps."),
    ("hose_stretch", "matterport", '{"target": "310", "stair": "stairA", "standpipe": true?}', "Hose needed to reach a suite: outside leg (aerial/geo), stair leg, inside leg (twin)."),
    ("plan_anchor_drops", "uwb", "{}", "Where to drop the UWB anchors on this floor for tracking coverage (line-of-sight analysis in the twin)."),
    ("radio", "action", '{"to": "RIT", "text": "...", "priority": "routine|urgent|emergency"}', "Transmit an order or information as COMMAND. to: a unit id or ALL."),
    ("assign", "action", '{"unit": "RIT", "task": "rit_rescue|exit|search|attack|stage|hold", "target": "E1", "via": ["rcS"]?}', "Give a crew a task and a destination (unit id, suite, room, stair, or \"exit\"); the route is computed around current hazards and shown to everyone."),
    ("mark_hazard", "action", '{"ref": "rcE", "kind": "blocked|fire|collapse|smoke", "note": "..."}', "Mark a room, door, stair or corridor edge as lost; all later routes avoid it."),
    ("request_resource", "action", '{"what": "second alarm", "reason": "..."}', "Ask dispatch for more resources (logged; an exercise contacts nobody)."),
    ("add_observation", "action", '{"text": "...", "unit": "E1"?, "at": "310"?}', "Pin a lasting note in the twin at a crew's position or at a place (suite, room, stair, door, hazard ref)."),
    ("file_record_correction", "action", '{"field": "...", "current": "...", "proposed": "...", "evidence": {...}}', "File an evidence-backed correction or verification request against the building record."),
]


# Field runs (a radar scan and/or occupants' phones exist) get their own catalogue: the one above with
# get_scan / get_phones / get_accountability added, and the tools that take a place also taking a victim id
# (V1), a scan flag id (M1) or a phone id (B1). CATALOG itself and its text must never change: it is embedded
# in the system prompt of every recorded run of the mayday exercise.
def _targets(scan: bool, phones: bool) -> str:
    t = []
    if scan:
        t.append("a victim id from the scan (V1)")
    if phones:
        t.append("an opt-in phone id (B1: a fix leads to the phone, two candidates to the more probable one, a ring or a zone to the door of the room to search)")
    return ", ".join(t)


def field_catalog(scan: bool = True, phones: bool = False) -> list[tuple]:
    ids = "/".join((["V1"] if scan else []) + (["B1"] if phones else []))
    tgt = _targets(scan, phones)
    pin = ", ".join((["victim id V1", "scan flag id M1"] if scan else []) + (["phone id B1"] if phones else []))
    over = {
        "route": ("matterport", f'{{"src": "RIT", "dst": "{ids.split("/")[0]}", "avoid": ["rcE"]?}}', f"Walking route in the twin. src/dst: a unit id, {tgt}, suite number, room id, stair id (stairA), door id, node id, or \"exit\" (dst only). Current hazards are always avoided; avoid adds more." + (" passes_flags lists scan flags (M1) the route passes within reach of." if scan else "") + (" For a phone, target says what the route leads to." if phones else "")),
        "nearest_exit": ("matterport", f'{{"src": "{ids.split("/")[0]}"}}', f"Best reachable stair from a unit, a {ids} id or a place with current hazards, with length and steps."),
        "assign": ("action", f'{{"unit": "RIT", "task": "rescue|rit_rescue|exit|search|attack|stage|hold", "target": "{ids.split("/")[-1]}", "via": ["rcS"]?}}', f"Give a crew a task and a destination (unit id, {tgt}, suite, room, stair, or \"exit\"); the route is computed around current hazards and shown to everyone. Task rescue + a {ids} id sends a crew to that person."),
        "add_observation": ("action", f'{{"text": "...", "unit": "E1"?, "at": "{"M1" if scan else "B1"}"?}}', f"Pin a lasting note in the twin at a crew's position or at a place (suite, room, stair, door, hazard ref, {pin})."),
    }
    cat = [(n, *over[n]) if n in over else (n, l, sg, d) for n, l, sg, d in CATALOG]
    at = next(i for i, c in enumerate(cat) if c[0] == "locate_unit")
    extra = ([SCOUT_TOOL] if scan else []) + (list(BEACON_TOOLS) if phones else [])
    return cat[:at] + extra + cat[at:]


SCOUT_TOOL = ("get_scan", "uwb", "{}", "The radar scan of the room the pucks were thrown into: what it found (victims with breathing rate, confidence and uncertainty; flags where the room differs from the twin), how it was made (rung, pucks, registration fit: residual and ambiguity, 0 = one clear placement, 1 = cannot tell two apart) and what it can and cannot tell.")
BEACON_TOOLS = (
    ("get_phones", "beacon", "{}", "Occupants' phones heard by the pucks: each opt-in phone (B1..) with what its measurements support (fix, candidates, ring, zone or none), where in words, how sure, and the occupant's own report (status, people, can move); devices heard without the app as counts per puck (devices, not people); what this can and cannot tell."),
    ("get_accountability", "costar", "{}", "The building record's suites against the phones: who the record expects (an estimate with its basis), phones, people reported, located, devices heard, unaccounted for (a range), state, priority, and the crews assigned and arrived."),
)
SCOUT_CATALOG = field_catalog(scan=True, phones=False)
ALL_FIELD_TOOLS = field_catalog(scan=True, phones=True)

# What each radar rung can tell, and what no rung can (docs/SCOUT_CONTRACT.md section 0). Fixed text: discrete facts.
RUNG_CAN_TELL = {
    "cir-radar": ["the position of a person who breathes or moves, with an uncertainty", "their breathing rate", "a coarse echo outline of the room", "where static echoes disagree with the twin"],
    "link-shadow": ["that a person is between pucks, from the links their body shadows, with a coarse position"],
    "tags-only": ["only where the pucks themselves are"],
}
# What a phone's geometry means, and what phones cannot tell (BEACON_FEED.md section 0). Fixed text.
GEOMETRY_MEANS = {"fix": "one point, with an uncertainty: the only geometry that is a position",
                  "candidates": "two or more separate spots, either may be right: search them all, most probable first",
                  "ring": "a distance from one puck, not a place: search along the arc",
                  "zone": "a room, from Bluetooth signal strength: search the room",
                  "none": "heard, nothing spatial"}
PHONE_CANNOT_TELL = ["anyone whose phone has no app, or no phone: they are not heard as people",
                     "people from devices heard without the app: those are devices (watches, earbuds, laptops), not people, and some phones do not advertise",
                     "whether the occupant's report is still true: it is what they typed or picked, and a stale phone may have moved"]
CANNOT_TELL = ["a person who neither breathes nor moves: to this radar they look like furniture, so the scan never clears a room",
               "who a person is, or whether they are conscious or injured",
               "fire, heat or smoke"]


def catalog_text(catalog: list[tuple] | None = None) -> str:
    return "\n".join(f"- {name} {sig}  [{layer}] {desc}" for name, layer, sig, desc in (CATALOG if catalog is None else catalog))


def _half(v) -> float | None:
    """Metres rounded to the half metre (stable against centimetre noise)."""
    return None if v is None else round(float(v) * 2) / 2


def _tenth(v) -> float | None:
    return None if v is None else round(float(v), 1)


def _point_of(item: dict) -> list[float] | None:
    """A victim's or a flag's position in the twin frame, from whichever shape the scan or the brief uses."""
    if item.get("x") is not None and item.get("y") is not None:
        return [float(item["x"]), float(item["y"])]
    at = item.get("at")
    if isinstance(at, (list, tuple)) and len(at) >= 2:
        return [float(at[0]), float(at[1])]
    w = item.get("where")
    if isinstance(w, dict):
        if isinstance(w.get("at"), (list, tuple)):
            return [float(w["at"][0]), float(w["at"][1])]
        if isinstance(w.get("a"), (list, tuple)) and isinstance(w.get("b"), (list, tuple)):
            return [(w["a"][0] + w["b"][0]) / 2, (w["a"][1] + w["b"][1]) / 2]
    return None


def _seg_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    k = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - k * dx, p[1] - a[1] - k * dy)


class ToolBox:
    def __init__(self, provider, twin, incident, llm: LLM):
        self.provider, self.twin, self.incident, self.llm = provider, twin, incident, llm
        self.snapshot: dict | None = None
        self.vision_meta: dict | None = None
        self.scan_brief: dict | None = None            # the brief of the last `scan` situation (set by the agent)
        self.beacon_brief: dict | None = None          # the brief of the last `phone` situation (set by the agent)
        self._fns: dict[str, Callable[..., Any]] = {name: getattr(self, f"t_{name}") for name, *_ in ALL_FIELD_TOOLS}

    async def call(self, name: str, args: dict) -> dict:
        if name not in self._fns:
            return {"error": f"unknown tool {name!r}"}
        fn, args = self._fns[name], dict(args or {}) if isinstance(args, dict) else {}
        params = inspect.signature(fn).parameters
        ignored = [k for k in args if k not in params]
        try:
            out = fn(**{k: v for k, v in args.items() if k in params})
            if hasattr(out, "__await__"):
                out = await out
            if ignored and isinstance(out, dict):              # one fixed phrase, no argument names or values echoed
                out = {**out, "note": "unrecognised arguments were ignored"}
            return out
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        except (ValueError, KeyError, PermissionError) as exc:
            return {"error": str(exc)}

    # ---- helpers
    def _snap(self) -> dict:
        return self.snapshot or self.incident.snapshot()

    def _unit(self, unit_id: str) -> dict:
        for u in self._snap()["units"]:
            if u["id"] == unit_id:
                return u
        raise ValueError(f"unknown unit {unit_id!r}")

    def _ref(self, ref):
        """A unit id becomes its node in the frozen snapshot; a victim or flag id from the scan becomes its
        position in the twin; anything else passes through to the twin."""
        if isinstance(ref, str) and any(u["id"] == ref for u in self._snap()["units"]):
            u = self._unit(ref)
            if not u["node"]:
                raise ValueError(f"{ref} has no position yet")
            return u["node"]
        if isinstance(ref, str) and ref in self._phone_entries() and ref not in self.twin._rooms and ref not in self.twin._nodes:
            target = self._phone_target(ref)[0]
            return list(target) if isinstance(target, list) else target
        item = self._scan_item(ref)
        if item is not None:
            p = item["_at"]
            if p is None:
                raise ValueError(f"{ref} has no position in the scan (range only)")
            return [_half(p[0]), _half(p[1])]
        return ref

    # ---- the scan (scout mode)
    def _scan_sources(self) -> list[dict]:
        """Where the scan is known, most discrete first: the brief of the scan situation, the compact scan in the
        frozen snapshot, the whole Scan stored on the incident (docs/SCOUT_CONTRACT.md 2.3). Their shapes differ
        (words, strings or numbers); everything read from them is merged by id and rounded here."""
        out = [self.scan_brief, getattr(self.incident, "scan_brief", None), (self.snapshot or {}).get("scan"), getattr(self.incident, "scan", None)]
        return [s for s in out if isinstance(s, dict) and s]

    def has_scan(self) -> bool:
        return bool(self._scan_sources())

    def _scan_field(self, key: str, default=None):
        for s in self._scan_sources():
            if s.get(key) is not None:
                return s[key]
        return default

    def _entries(self, key: str) -> dict[str, list[dict]]:
        """victims or mismatches of every source, grouped by id (first appearance order, brief first)."""
        out: dict[str, list[dict]] = {}
        for s in self._scan_sources():
            if isinstance(s.get(key), list):
                for i in s[key]:
                    if isinstance(i, dict) and i.get("id"):
                        out.setdefault(str(i["id"]), []).append(i)
        return out

    def _scan_item(self, ref) -> dict | None:
        """A victim (V1) or a flag (M1) of the scan, merged over the sources, with "_at" = its position when one is known."""
        if not isinstance(ref, str) or ref in self.twin._rooms or ref in self.twin._nodes or ref in self.twin._doors or ref in self.twin._stairs:
            return None
        entries = self._entries("victims").get(ref) or self._entries("mismatches").get(ref)
        if not entries:
            return None
        merged = {k: v for e in reversed(entries) for k, v in e.items()}
        merged["_at"] = next((pt for pt in (_point_of(e) for e in entries) if pt is not None), None)
        return merged

    def _is_victim(self, ref) -> bool:
        return isinstance(ref, str) and ref in self._entries("victims")

    def _flag_points(self) -> list[tuple[str, list[float], float]]:
        out = []
        for fid, entries in self._entries("mismatches").items():
            p = next((pt for pt in (_point_of(e) for e in entries) if pt is not None), None)
            radius = 0.5
            for e in entries:
                w = e.get("where") if isinstance(e.get("where"), dict) else {}
                r = w.get("radius_m", e.get("radius_m"))
                if isinstance(r, (int, float)):
                    radius = float(r)
                    break
            if p is not None:
                out.append((fid, p, radius))
        return out

    def _passes_flags(self, polyline: list) -> list[str]:
        """Scan flags within arm's reach (flag radius + 0.5 m) of a walking route."""
        hits = []
        pts = polyline if len(polyline) > 1 else polyline * 2
        for fid, p, radius in self._flag_points():
            if pts and min(_seg_dist(p, a, b) for a, b in zip(pts, pts[1:])) <= radius + 0.5:
                hits.append(fid)
        return hits

    def _door_of(self, room_id: str | None, p: list[float]) -> tuple[str, float] | None:
        doors = [d for d in self.twin.data["doors"] if room_id and room_id in d["rooms"]]
        if not doors:
            return None
        d = min(doors, key=lambda d: math.dist(d["at"], p))
        return d["id"], math.dist(d["at"], p)

    @staticmethod
    def _first(entries: list[dict], *keys, kind=None):
        for e in entries:
            for k in keys:
                v = e.get(k)
                if v is not None and (kind is None or (isinstance(v, kind) and (kind is bool or not isinstance(v, bool)))):
                    return v
        return None

    def _confidence(self, entries: list[dict]):
        num = self._first(entries, "confidence", kind=(int, float))
        if num is not None:
            return _tenth(num)
        pct = self._first(entries, "confidence_pct", kind=(int, float))
        return _tenth(pct / 100) if pct is not None else self._first(entries, "confidence", kind=str)

    def _victim_view(self, vid: str, entries: list[dict]) -> dict:
        p = next((pt for pt in (_point_of(e) for e in entries) if pt is not None), None)
        p = [_half(p[0]), _half(p[1])] if p else None
        room = self._first(entries, "room", kind=str) or (self.twin.locate(*p)["room"] if p else None)
        label = self._first(entries, "room_label", kind=str) or (self.twin.room(room)["label"] if room in self.twin._rooms else None)
        door = self._door_of(room, p) if p else None
        where = self._first(entries, "where", "text", kind=str)
        if not where and p and room in self.twin._rooms:           # no text anywhere: say it from the room's walls
            poly = self.twin.room(room)["polygon"]
            x0, x1, y0, y1 = min(q[0] for q in poly), max(q[0] for q in poly), min(q[1] for q in poly), max(q[1] for q in poly)
            ew = ("west", p[0] - x0) if p[0] - x0 <= x1 - p[0] else ("east", x1 - p[0])
            ns = ("south", p[1] - y0) if p[1] - y0 <= y1 - p[1] else ("north", y1 - p[1])
            where = f"about {_half(ew[1]):g} m from the {ew[0]} wall and {_half(ns[1]):g} m from the {ns[0]} wall"
        bpm = self._first(entries, "breathing_bpm", kind=(int, float))
        sigma = self._first(entries, "sigma_m", kind=(int, float))
        moving = self._first(entries, "moving", kind=bool)
        return {"id": vid, "room_label": label, "where": where, "from_door_m": round(door[1]) if door else None,
                "state": self._first(entries, "state", kind=str), "moving": moving,
                "breathing_bpm": int(round(bpm)) if bpm is not None else None, "confidence": self._confidence(entries),
                "uncertainty_m": max(0.5, _half(sigma)) if sigma is not None else self._first(entries, "uncertainty", kind=str) or "position unknown",
                "links": self._first(entries, "n_links", kind=int)}

    def _flag_view(self, fid: str, entries: list[dict]) -> dict:
        return {"id": fid, "kind": self._first(entries, "kind", kind=str), "where": self._first(entries, "where", kind=str),
                "means": self._first(entries, "text", "means", kind=str), "confidence": self._confidence(entries)}

    def scan_view(self) -> dict | None:
        """The scan as the agent sees it: the brief's facts plus how reliable they are and what the scan cannot tell.
        Discrete only: ids, labels, words, whole numbers, tenths and half metres."""
        if not self.has_scan():
            return None
        srcs = self._scan_sources()
        regs = [s["registration"] for s in srcs if isinstance(s.get("registration"), dict)]
        outlines = [s["outline"] for s in srcs if isinstance(s.get("outline"), dict)]
        rung = self._scan_field("rung")
        n = self._first(srcs, "n_pucks", "pucks", kind=int)
        if n is None:
            n = next((len(s["pucks"]) for s in srcs if isinstance(s.get("pucks"), list)), None)
        room = self._scan_field("room")
        room_label = self._scan_field("room_label") or (self.twin.room(room)["label"] if room in self.twin._rooms else None)
        res = next((s["resolution"] for s in srcs if isinstance(s.get("resolution"), dict)), {})
        simulated, sample = bool(self._scan_field("simulated", True)), bool(self._first(srcs, "sample", kind=bool))
        can = list(RUNG_CAN_TELL.get(rung, []))
        cannot = list(CANNOT_TELL) + [f"anyone outside {room_label or 'the scanned room'}: only that room was searched"]
        if isinstance(n, int) and n == 2:
            can, cannot = ["that a person is present, and their range from the pair"], cannot + ["a position: two pucks give a range only"]
        elif isinstance(n, int) and n < 2:
            can, cannot = [], cannot + ["anything: one puck cannot hear its own echo"]
        if sample:
            cannot.append("anything real: this is the hand-made sample scan, not a radar result")
        elif simulated:
            cannot.append("anything measured: this scan is simulated (radar simulator), not from real boards")
        registration = {"ok": self._first(regs, "ok", kind=bool), "method": self._first(regs, "method", kind=str)}
        for key in ("residual_m", "ambiguity"):
            v = self._first(regs, key, kind=(int, float))
            if v is not None:
                registration[key] = _tenth(v)
        conf = self._first(regs, "confidence")
        if conf is not None:
            registration["confidence"] = _tenth(conf) if isinstance(conf, (int, float)) else conf
        outline_src = self._first(outlines, "source", kind=str) or self._first(srcs, "outline", kind=str)
        outline_conf = self._first(outlines, "confidence", kind=(int, float))
        limits = next(([str(x).split(" (noise model")[0] for x in s["limits"]] for s in srcs if isinstance(s.get("limits"), list)), [])   # the model's parameters stay out of prompts
        return {"scan": self._first(srcs, "id", "scan", kind=str), "simulated": simulated, "sample": sample, "room": room, "room_label": room_label,
                "rung": rung, "pucks": n, "registration": registration,
                "outline": {"source": outline_src, **({"confidence": _tenth(outline_conf)} if outline_conf is not None else {})} if outline_src else None,
                "range_resolution_m": _tenth(res["range_m"]) if isinstance(res.get("range_m"), (int, float)) else None,
                "victims": [self._victim_view(vid, e) for vid, e in self._entries("victims").items()],
                "flags": [self._flag_view(fid, e) for fid, e in self._entries("mismatches").items()],
                "limits": limits, "can_tell": can, "cannot_tell": cannot}

    def t_get_scan(self) -> dict:
        view = self.scan_view()
        return view if view is not None else {"scan": None, "reason": "no scan in this incident: no pucks have been thrown"}

    # ---- occupants' phones (Beacon mode; ../sandtable-beacon-victim-phones/docs/BEACON_FEED.md)
    def _beacon_sources(self) -> list[dict]:
        """Where the phones are known, most discrete first: the brief of the phone situation, a brief kept on the
        incident, the frozen snapshot's phones, the whole Beacons object kept on the incident. Merged by id here."""
        inc = self.incident
        frozen = [self.beacon_brief, getattr(inc, "beacon_brief", None), {"phones": (self.snapshot or {}).get("phones")}]
        frozen = [s for s in frozen if isinstance(s, dict) and any(v for v in s.values())]
        if not frozen:                  # no phone in this incident's frozen state: a live object alone never makes a run a phone run
            return []
        live = self._live_beacons()
        return frozen + ([live] if live else [])

    def _live_beacons(self) -> dict | None:
        """The latest whole Beacons object, if the incident keeps one. It changes while the agent thinks, so it only
        fills in details the frozen brief and snapshot lack (see _phone_entries)."""
        inc = self.incident
        b = getattr(inc, "beacons", None)
        if not isinstance(b, dict):
            b = getattr(getattr(inc, "beacon_link", None), "beacons", None)
        return b if isinstance(b, dict) else None

    def has_phones(self) -> bool:
        return bool(self._phone_entries())

    @staticmethod
    def _phones_in(src: dict) -> list[dict]:
        ph = src.get("phones")
        if isinstance(ph, dict):                       # keyed by id
            ph = [{"id": k, **v} for k, v in ph.items() if isinstance(v, dict)]
        if isinstance(ph, list):
            return [x for x in ph if isinstance(x, dict) and x.get("id")]
        if isinstance(src.get("phone"), dict) and src["phone"].get("id"):
            return [src["phone"]]
        if src.get("id") and src.get("geometry"):     # the situation data is one phone
            return [src]
        return []

    def _phone_entries(self) -> dict[str, list[dict]]:
        """Every source's entry per phone id, frozen sources first. A live entry is used only when no frozen one says
        otherwise about its geometry: an improved geometry arrives with the next `phone` situation, not mid-turn."""
        out: dict[str, list[dict]] = {}
        live = self._live_beacons()
        for src in self._beacon_sources():
            for ph in self._phones_in(src):
                pid = str(ph["id"])
                if src is live and pid in out:
                    frozen = self._first(out[pid], "geometry", kind=str)
                    if frozen is not None and ph.get("geometry") != frozen:
                        continue
                out.setdefault(pid, []).append(ph)
        return out

    def _beacon_field(self, key: str, default=None):
        for src in self._beacon_sources():
            if src.get(key) is not None:
                return src[key]
        return default

    def _report_of(self, entries: list[dict]) -> dict:
        r = self._first(entries, "report", kind=dict) or {}
        status = r.get("status") or self._first(entries, "report", "report_status", kind=str)     # a snapshot may flatten it ("status" there is the rescue's)
        people = r.get("people") if r.get("people") is not None else self._first(entries, "people", kind=int)
        can_move = r.get("can_move") if r.get("can_move") is not None else self._first(entries, "can_move", kind=bool)
        out = {"status": status, "people": people, "can_move": can_move, "text": r.get("text")}
        if r.get("safe_outside") or self._first(entries, "safe_outside", kind=bool):
            out["safe_outside"] = True
        if isinstance(r.get("battery_pct"), (int, float)) and r["battery_pct"] < 20:
            out["battery_low"] = True                  # only the threshold: the percentage drifts and must not reach prompts
        if r.get("floor_reported") is not None:
            out["floor_reported"] = r["floor_reported"]
        return out

    def _wall_text(self, room: str | None, p: list[float]) -> str | None:
        if room not in self.twin._rooms or p is None:
            return None
        poly = self.twin.room(room)["polygon"]
        x0, x1, y0, y1 = min(q[0] for q in poly), max(q[0] for q in poly), min(q[1] for q in poly), max(q[1] for q in poly)
        ew = ("west", p[0] - x0) if p[0] - x0 <= x1 - p[0] else ("east", x1 - p[0])
        ns = ("south", p[1] - y0) if p[1] - y0 <= y1 - p[1] else ("north", y1 - p[1])
        return f"about {_half(ew[1]):g} m from the {ew[0]} wall and {_half(ns[1]):g} m from the {ns[0]} wall"

    def _ring_rooms(self, ring: dict) -> list[tuple[str, int]]:
        """Rooms the possible arcs of a ring cross, with the share of the arc in each (percent, most first)."""
        cx, cy, r = _half(ring["cx"]), _half(ring["cy"]), _half(ring["r_m"])          # from discrete inputs only: stable shares
        counts: dict[str, int] = {}
        for a0, a1 in ring.get("arcs") or [[0.0, 360.0]]:
            a0, a1 = 5 * round(float(a0) / 5), 5 * round(float(a1) / 5)
            a1 = a1 + 360.0 if a1 < a0 else a1
            n = max(1, int((a1 - a0) / 5))
            for i in range(n + 1):
                a = math.radians(a0 + (a1 - a0) * i / n)
                loc = self.twin.locate(cx + r * math.cos(a), cy + r * math.sin(a))
                if loc["room"] and not loc["off_map_m"]:
                    counts[loc["room"]] = counts.get(loc["room"], 0) + 1
        total = sum(counts.values()) or 1
        return sorted(((k, round(100 * v / total)) for k, v in counts.items()), key=lambda kv: (-kv[1], kv[0]))

    def _door_ref(self, room: str) -> str | None:
        doors = sorted(d["id"] for d in self.twin.data["doors"] if room in d["rooms"])
        return doors[0] if doors else None

    def _phone_target(self, pid: str) -> tuple[object, str]:
        """(twin reference, what it is) for a phone id, following the contract: a fix leads to the phone, candidates
        to the most probable one, a ring or a zone to the door of the most probable room (to search it)."""
        entries = self._phone_entries().get(pid)
        if not entries:
            raise ValueError(f"unknown phone {pid!r}")
        rep = self._report_of(entries)
        if rep.get("safe_outside"):
            raise ValueError(f"{pid} reports safe outside: accounted for, not a target")
        vt = getattr(self.incident, "victim_target", None)
        t = vt(pid) if callable(vt) else None
        if isinstance(t, dict) and t.get("dst") is not None:          # exactly where incident.assign will send the crew
            dst = t["dst"]
            dst = [_half(dst[0]), _half(dst[1])] if isinstance(dst, (list, tuple)) else dst
            return dst, str(t.get("label") or pid)
        geom = self._first(entries, "geometry", kind=str)
        if geom == "fix":
            p = next((pt for pt in (_point_of(e) for e in entries) if pt is not None), None)
            if p is not None:
                return [_half(p[0]), _half(p[1])], f"the phone itself (fix, {pid})"
        if geom == "candidates":
            cands = sorted((c for c in (self._first(entries, "candidates", kind=list) or []) if isinstance(c, dict) and c.get("x") is not None),
                           key=lambda c: -float(c.get("weight") or 0))
            if cands:
                c = cands[0]
                return [_half(c["x"]), _half(c["y"])], f"the more probable of {len(cands)} candidate spots (weight {_tenth(c.get('weight'))}); the other is just as possible to check"
        room = None
        if geom == "ring":
            ring = self._first(entries, "ring", kind=dict)
            if ring and ring.get("cx") is not None:
                rooms = self._ring_rooms(ring)
                room = rooms[0][0] if rooms else None
        if geom in ("zone", "ring") and room is None:
            z = self._first(entries, "zone", kind=dict) or {}
            room = z.get("room") or self._first(entries, "room", kind=str)
        if room and self._door_ref(room):
            kind = "range only" if geom == "ring" else "room only"
            return self._door_ref(room), f"the door of {self.twin.room(room)['label']}, to search it ({kind}: no position)"
        raise ValueError(f"{pid} has no location the crews can go to ({geom or 'unknown'} geometry)")

    def _phone_view(self, pid: str, entries: list[dict]) -> dict:
        geom = self._first(entries, "geometry", kind=str)
        room = self._first(entries, "room", kind=str)
        view = {"id": pid, "geometry": geom, "method": self._first(entries, "method", kind=str),
                "room_label": self._first(entries, "room_label", kind=str) or (self.twin.room(room)["label"] if room in self.twin._rooms else None),
                "room_prob": _tenth(self._first(entries, "room_prob", kind=(int, float))) if self._first(entries, "room_prob", kind=(int, float)) is not None else None,
                "confidence": self._confidence(entries), "report": self._report_of(entries),
                "stale": bool(self._first(entries, "stale", kind=bool))}
        leads = self._first(entries, "target", kind=str)               # the incident's own wording of where a crew is sent
        if leads:
            view["crew_goes_to"] = leads
        rs, crew = self._first(entries, "status", kind=str), self._first(entries, "rescuer", kind=str)
        if rs or crew:
            view["rescue"] = {"status": rs, "crew": crew}
        if geom == "fix":
            p = next((pt for pt in (_point_of(e) for e in entries) if pt is not None), None)
            p = [_half(p[0]), _half(p[1])] if p else None
            sigma = self._first(entries, "sigma_m", kind=(int, float))
            door = self._door_of(room or (self.twin.locate(*p)["room"] if p else None), p) if p else None
            view.update({"where": self._wall_text(room or (self.twin.locate(*p)["room"] if p else None), p) or self._first(entries, "where", kind=str),
                         "uncertainty_m": max(0.5, _half(sigma)) if sigma is not None else None, "from_door_m": round(door[1]) if door else None})
        elif geom == "candidates":
            cands = sorted((c for c in (self._first(entries, "candidates", kind=list) or []) if isinstance(c, dict)), key=lambda c: -float(c.get("weight") or 0))
            view["candidates"] = [{"rank": i + 1, "weight": _tenth(c.get("weight")), "room_label": c.get("room_label"),
                                   "where": self._wall_text(c.get("room"), [_half(c["x"]), _half(c["y"])]) if c.get("x") is not None else c.get("where")}
                                  for i, c in enumerate(cands)]
        elif geom == "ring":
            ring = self._first(entries, "ring", kind=dict) or {}
            if ring.get("r_m") is not None:
                rooms = self._ring_rooms(ring) if ring.get("cx") is not None else []
                puck_room = self.twin.locate(ring["cx"], ring["cy"])["room"] if ring.get("cx") is not None else None
                view["ring"] = {"puck": ring.get("puck"), "radius_m": _half(ring["r_m"]), "band": "thin (UWB)" if ring.get("kind") == "uwb" else "fat (Bluetooth, metres wide)",
                                "puck_where": self._wall_text(puck_room, [_half(ring["cx"]), _half(ring["cy"])]) if puck_room else None,
                                "arc_in": [{"room_label": self.twin.room(r)["label"], "share_pct": pct} for r, pct in rooms]}
        elif geom == "zone":
            z = self._first(entries, "zone", kind=dict) or {}
            view["zone"] = {"room_label": z.get("room_label") or view["room_label"], "prob": _tenth(z["prob"]) if isinstance(z.get("prob"), (int, float)) else None,
                            "band": z.get("band"), "puck": z.get("puck")}
        view["text"] = self._first(entries, "text", kind=str)
        return view

    def phones_view(self) -> dict | None:
        """Discrete view of the phones for prompts: ids, labels, words, whole numbers, tenths, half metres; no ages."""
        entries = self._phone_entries()
        if not entries:
            return None
        srcs = self._beacon_sources()
        presence = next((src["presence"] for src in srcs if isinstance(src.get("presence"), list)), [])
        sample = bool(self._first(srcs, "sample", kind=bool))
        simulated = bool(self._beacon_field("simulated", True))
        cannot = list(PHONE_CANNOT_TELL)
        if sample:
            cannot.append("anything real: this is the hand-made sample, not the Beacon module's output")
        elif simulated:
            cannot.append("anything measured: the phones and pucks are simulated")
        limits = next(([str(x) for x in src["limits"]] for src in srcs if isinstance(src.get("limits"), list)), [])
        return {"simulated": simulated, "sample": sample, "source": self._beacon_field("source"),
                "room_label": self._beacon_field("room_label"), "rungs": self._beacon_field("rungs"),
                "phones": [self._phone_view(pid, e) for pid, e in entries.items()],
                "devices_without_app": [{"puck": c.get("puck"), "room_label": c.get("room_label"), "count": c.get("count"),
                                         "range": c.get("count_range"), "note": "devices, not people"} for c in presence if isinstance(c, dict)],
                "geometry_means": GEOMETRY_MEANS, "limits": limits, "cannot_tell": cannot}

    def accountability_view(self) -> dict | None:
        """The accountability table, trimmed to discrete facts. Suites against phones from the Beacon module (the frozen
        brief first, else the latest object, else the record alone); crews, targets and who was reached are joined here
        from the frozen snapshot, so the result does not change while the agent thinks."""
        if (getattr(self.provider, "modes", None) or {}).get("costar") == "live":
            return self._accountability_live_view()
        acc = None
        if self._live_beacons() is not None:                               # the one table the panel, the report and the incident's last line use
            try:
                from ..beacon_link import accountability as joined
                acc = joined(self._live_beacons(), self.incident, self.twin, self.provider)
            except Exception:
                log.exception("accountability table failed")
        if acc is None:
            acc = next((src["accountability"] for src in self._beacon_sources()
                        if isinstance(src.get("accountability"), dict) and (src["accountability"].get("suites") or src["accountability"].get("rows"))), None)
        if acc is None:                                                    # the table the incident keeps, if it keeps one
            inc_acc = getattr(self.incident, "accountability", None)
            inc_acc = inc_acc() if callable(inc_acc) else inc_acc
            if isinstance(inc_acc, dict) and (inc_acc.get("rows") or inc_acc.get("suites")):
                acc = inc_acc
        if acc is None:
            try:
                from ..beacon_link import accountability as record_table          # the server builder's join, used for its record-only rows
                acc = record_table(None, self.incident, self.twin, self.provider)
            except Exception:
                return None
        snap = self._snap()
        snap_phones = [q for q in snap.get("phones") or [] if isinstance(q, dict)]
        people = [{"id": q.get("id"), "room": q.get("room"), "rescuer": q.get("rescuer"), "status": q.get("status")}
                  for q in list(snap.get("phones") or []) + list((snap.get("scan") or {}).get("victims") or []) if isinstance(q, dict)]
        rows = []
        for r in acc.get("rows") or acc.get("suites") or []:
            if not isinstance(r, dict):
                continue
            exp = r.get("expected") or {}
            un = r.get("unaccounted") or {}
            room = r.get("room")
            here = [q for q in people if q["room"] == room]
            mine = [q for q in snap_phones if q.get("room") in set(r.get("rooms") or ([room] if room else []))]     # never below what the incident holds
            rows.append({"suite": r.get("suite"), "tenant": r.get("industry") or r.get("tenant"), "life_hazard": r.get("life_hazard"),
                         "expected": {"low": exp.get("low"), "high": exp.get("high"), "basis": exp.get("basis")} if exp else None,
                         "phones": max(int(r.get("phones_opt_in") or 0), len(mine)),
                         "people_reported": max(int(r.get("people_reported") or 0), sum(int(q.get("people") or 1) for q in mine)),
                         "located": max(int(r.get("located") or 0), sum(1 for q in mine if q.get("geometry") == "fix")),
                         "devices_heard": r.get("cold_devices"), "unaccounted": [un.get("low"), un.get("high")] if un else None,
                         "state": r.get("state"), "priority": r.get("priority"), "why": r.get("why"),
                         "crews_here": sorted(u["id"] for u in snap["units"] if u.get("room") == room),
                         "targets": sorted(q["id"] for q in here if q["id"]),
                         "assigned": sorted({q["rescuer"] for q in here if q.get("rescuer")}),
                         "reached": sorted(q["id"] for q in here if q.get("status") in ("reached", "evacuating", "out"))})
        src = acc.get("source")
        totals = {k: v for k, v in (acc.get("totals") or {}).items() if k not in ("targets", "assigned", "reached", "out")}
        for k, col in (("phones_opt_in", "phones"), ("people_reported", "people_reported"), ("located", "located")):
            if k in totals:
                totals[k] = max(int(totals[k] or 0), sum(int(r[col] or 0) for r in rows))
        un_total = [totals.get("unaccounted_low"), totals.get("unaccounted_high")] if "unaccounted_low" in totals else None
        return {"floor": acc.get("floor"), "source": (src.get("text") or src.get("beacon")) if isinstance(src, dict) else src,
                "unaccounted": un_total, "suites": rows, "totals": totals,
                "priorities": [{k: q.get(k) for k in ("rank", "suite", "target", "text")} for q in acc.get("priorities") or [] if isinstance(q, dict)],
                "limits": acc.get("limits") or []}

    def _accountability_live_view(self) -> dict | None:
        """Live record (contract section 4): the headline, the storey summary and compact rows, counts only (no tenant names)."""
        try:
            from ..beacon_link import accountability as table
            acc = table(self._live_beacons(), self.incident, self.twin, self.provider)
        except Exception:
            log.exception("live accountability failed")
            return None
        rows = []
        for r in acc.get("rows") or []:
            exp, un, rec = r.get("expected") or {}, r.get("unaccounted") or {}, r.get("record") or {}
            rows.append({"suite": r.get("suite"), "room_label": r.get("room_label"), "scope": r.get("scope"), "covered": r.get("covered"),
                         "record": {k: rec.get(k) for k in ("status", "tenants", "employees", "area_sf", "text")},
                         "life_hazard": r.get("life_hazard"), "expected": [exp.get("low"), exp.get("high")], "expected_basis": exp.get("basis"),
                         "phones": r.get("phones_opt_in"), "people_reported": r.get("people_reported"), "located": r.get("located"),
                         "devices_heard": r.get("cold_devices"), "unaccounted": [un.get("low"), un.get("high")] if un else None,
                         "state": r.get("state"), "priority": r.get("priority"), "why": r.get("why"),
                         "crews_here": r.get("crews"), "targets": r.get("targets"), "assigned": r.get("assigned"), "reached": r.get("reached")})
        t = acc.get("totals") or {}
        return {"headline": acc.get("headline"), "storey": acc.get("storey"), "source": (acc.get("source") or {}).get("text"),
                "unaccounted": [t.get("unaccounted_low"), t.get("unaccounted_high")], "suites": rows,
                "totals": {k: t.get(k) for k in ("expected_low", "expected_high", "unaccounted_low", "unaccounted_high", "people_reported", "located",
                                                 "against_record_people", "not_covered_suites")},
                "priorities": [{k: q.get(k) for k in ("rank", "suite", "target", "text")} for q in acc.get("priorities") or []],
                "limits": acc.get("limits") or []}

    def t_get_phones(self) -> dict:
        view = self.phones_view()
        return view if view is not None else {"phones": [], "reason": "no phone has been heard in this incident"}

    def t_get_accountability(self) -> dict:
        view = self.accountability_view()
        return view if view is not None else {"suites": [], "reason": "no accountability table yet: no pucks or phones"}

    def _blocked(self, avoid=None) -> list[str]:
        return sorted({h["ref"] for h in self._snap()["hazards"]} | set(avoid or []))

    def _route_view(self, r: dict) -> dict:
        if not r["ok"]:
            return {"ok": False, "reason": r["reason"]}
        rooms = [self.twin.room(q) for q in r["rooms"]]
        return {"ok": True, "length_m": round(r["length_m"]), "from": r["src_resolved"]["label"], "to": r["dst_resolved"]["label"],
                "to_id": r["dst_resolved"]["id"], "via": [q["label"] for q in rooms[1:-1]], "steps": [s["text"] for s in r["steps"]]}

    def _floor_no(self) -> int:
        label = self.twin.floor()["label"]
        try:
            return int(label)
        except (TypeError, ValueError):                       # a live twin may label its floor "Level 10"
            import re
            m = re.search(r"-?\d+", str(label or ""))
            return int(m.group(0)) if m else None

    def _tenant_view(self, t: dict) -> dict:
        return {"suite": t.get("suite"), "category": t.get("industry"), "since": t.get("registered_since"), "life_hazard": t.get("life_hazard"),
                "hazmat": t.get("hazmat"), "hazard_basis": "inferred from category" if t.get("hazard_basis") else None, "synthetic": bool(t.get("synthetic"))}

    # ---- record (CoStar layer)
    def t_get_building_record(self) -> dict:
        site = self.provider.site()
        prop = self.provider.costar.get_property()
        sources = prop.pop("field_sources", {})
        missing = sorted(k for k, v in prop.items() if v is None)
        return {"mode": self.provider.modes["costar"], "record": {k: v for k, v in prop.items() if v is not None},
                "missing_fields": missing, "missing_reason": "CoStar-only fields are empty in fixture mode" if self.provider.modes["costar"] == "fixture" else None,
                "synthetic_fields": sorted(k for k, v in sources.items() if v.get("synthetic")),
                "sources": {k: v["source"] for k, v in sources.items() if k in prop and prop[k] is not None},
                "cross_checks": [{"id": c["id"], "layers": c["layers"], "text": c["text"]} for c in site.get("cross_checks", [])]}

    def t_list_tenants(self, floor: int | None = None) -> dict:
        floor = int(floor) if floor is not None else self._floor_no()
        tenants = self.provider.costar.list_tenants(floor)
        counts: dict[str, int] = {}
        for t in tenants:
            counts[t.get("suite")] = counts.get(t.get("suite"), 0) + 1
        return {"floor": floor, "tenants": [self._tenant_view(t) for t in tenants], "suites_with_several_registrations": sorted(s for s, n in counts.items() if n > 1 and s is not None),
                "note": "names withheld in fixture mode; life hazards are inferred from the business category" if self.provider.modes["costar"] == "fixture" else None}

    def t_list_vacant_space(self, floor: int | None = None) -> dict:
        floor = int(floor) if floor is not None else self._floor_no()
        listed = self.provider.costar.list_available_space(floor)
        unregistered = [r["suite"] for r in self.twin.rooms("suite") if r.get("registered") is False] if floor == self._floor_no() else []
        return {"floor": floor, "available_in_record": listed, "suites_without_active_registration": unregistered,
                "note": "a suite without a registration may be vacant, or its number may be a fixture filler: verify on site"}

    # ---- outside (Vexcel layer)
    def _outside_point(self, ref: str) -> tuple[float, float, str]:
        site = self.provider.site()
        ctx, prop = site["context"], site["property"]
        if ref == "building":
            return prop["lat"], prop["lon"], "building centre"
        kind, _, key = ref.partition(":")
        if kind == "hydrant":
            h = next((h for h in ctx["hydrants"] if h["id"] == key), None)
            if h:
                return h["lat"], h["lon"], f"hydrant {key}"
        elif kind == "entrance":
            e = next((e for e in ctx["entrances"] if e["id"] == key), None)
            if e:
                return e["lat"], e["lon"], e["label"]
        elif kind == "station":
            s = ctx["fire_stations"][int(key)]
            return s["lat"], s["lon"], s["name"]
        else:
            a = next((a for a in ctx["apparatus_positions"] if a["id"] == ref), None)
            if a:
                return a["lat"], a["lon"], a["label"]
        raise ValueError(f"unknown outside reference {ref!r}")

    def t_outside_context(self) -> dict:
        site = self.provider.site()
        ctx, prop = site["context"], site["property"]
        eng = ctx["apparatus_positions"][0] if ctx.get("apparatus_positions") else None
        hydrants = []
        for h in ctx.get("hydrants", [])[:6]:
            hydrants.append({"id": h["id"], "to_building_m": h.get("distance_m"), "size": h.get("size"),
                             "to_engine_m": round(geo_distance_m(eng["lat"], eng["lon"], h["lat"], h["lon"])) if eng else None})
        structure = self.provider.vexcel.get_structure(prop["lat"], prop["lon"]) or {}
        return {"sides": {k: {"faces_bearing_deg": round(v["bearing_deg"]), "street": v.get("street")} for k, v in site.get("sides", {}).items()},
                "apparatus": [{"id": a["id"], "label": a["label"], "side": a.get("side"), "basis": a.get("source")} for a in ctx.get("apparatus_positions", [])],
                "entrances": [{"id": e["id"], "label": e["label"], "side": e.get("side"), "leads_to": e.get("leads_to"), "basis": e.get("source")} for e in ctx.get("entrances", [])],
                "hydrants": hydrants, "hydrant_source": (ctx.get("hydrants") or [{}])[0].get("source"),
                "fire_stations": [{"name": s.get("name"), "distance_m": s.get("distance_m")} for s in ctx.get("fire_stations", [])],
                "building_height_m": structure.get("height_m"), "height_source": structure.get("source"),
                "roof": {"material": structure.get("roof_material"), "condition": structure.get("roof_condition")}}

    def t_list_aerial_imagery(self) -> dict:
        prop = self.provider.site()["property"]
        return {"mode": self.provider.modes["vexcel"],
                "images": [{"id": i["id"], "kind": i["kind"], "gsd_m": i.get("gsd_m"), "native_gsd_m": i.get("native_gsd_m"), "source": i["source"], "capture_date": i.get("capture_date")}
                           for i in self.provider.vexcel.find_imagery(prop["lat"], prop["lon"])]}

    def t_measure_outside(self, from_ref: str, to_ref: str) -> dict:
        a, b = self._outside_point(from_ref), self._outside_point(to_ref)
        bearing = math.degrees(math.atan2((b[1] - a[1]) * math.cos(math.radians(a[0])), b[0] - a[0])) % 360
        return {"from": a[2], "to": b[2], "distance_m": round(geo_distance_m(a[0], a[1], b[0], b[1])), "bearing_deg": round(bearing)}

    def annotate_aerial(self, image_id: str) -> tuple[Path, str]:
        """Draw footprint, side letters, hydrants and the engine on a copy of the image. Returns (path, content hash)."""
        from PIL import Image, ImageDraw, ImageFont
        site = self.provider.site()
        im = self.provider.vexcel.get_image(image_id)
        px = lambda lat, lon: tuple(self.provider.vexcel.world_to_pixel(image_id, lat, lon))
        img = Image.open(im["abs_path"]).convert("RGB")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=max(18, im["width"] // 45))
        except TypeError:
            font = ImageFont.load_default()
        ring = [px(lat, lon) for lon, lat in site["footprint"]["coordinates"][0]]
        d.line(ring + [ring[0]], fill=(255, 221, 0), width=max(3, im["width"] // 300))
        prop = site["property"]
        c = px(prop["lat"], prop["lon"])
        m_per_px = im.get("gsd_m") or 0.3
        frame = site.get("frame") or {}
        reach = (max(frame.get("length_m", 40), frame.get("width_m", 40)) / 2 + 9) / m_per_px
        for letter, s in site.get("sides", {}).items():
            b = math.radians(s["bearing_deg"])
            p = (c[0] + reach * math.sin(b), c[1] - reach * math.cos(b))
            d.ellipse([p[0] - 16, p[1] - 16, p[0] + 16, p[1] + 16], fill=(255, 221, 0))
            d.text((p[0] - 7, p[1] - 12), letter, fill=(0, 0, 0), font=font)
        for h in site["context"].get("hydrants", [])[:6]:
            p = px(h["lat"], h["lon"])
            if 0 <= p[0] < im["width"] and 0 <= p[1] < im["height"]:
                d.ellipse([p[0] - 7, p[1] - 7, p[0] + 7, p[1] + 7], fill=(0, 200, 255), outline=(0, 0, 0))
                d.text((p[0] + 9, p[1] - 10), "H" + h["id"][1:], fill=(0, 230, 255), font=font)
        for a in site["context"].get("apparatus_positions", []):
            p = px(a["lat"], a["lon"])
            d.rectangle([p[0] - 9, p[1] - 9, p[0] + 9, p[1] + 9], fill=(255, 60, 40), outline=(255, 255, 255))
            d.text((p[0] + 12, p[1] - 10), a["id"], fill=(255, 90, 70), font=font)
        out = config.DATA_DIR / f"annotated_{image_id}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        params = json.dumps({"fp": site["footprint"], "sides": site.get("sides"), "ctx": site["context"].get("hydrants", [])[:6], "app": site["context"].get("apparatus_positions"), "v": 1}, sort_keys=True)
        sha = hashlib.sha256((file_sha(im["abs_path"]) + params).encode()).hexdigest()
        return out, sha

    async def t_look_at_aerial(self, image_id: str = "ortho", question: str = "") -> dict:
        im = self.provider.vexcel.get_image(image_id)
        path, sha = self.annotate_aerial(image_id)
        system = ("You are reading one aerial (overhead, north up) image of a building for a fire department pre-incident plan. "
                  "The yellow outline is the building; yellow circles A, B, C, D mark its four sides; cyan dots H1.. are hydrants; the red square E1 is the first engine. "
                  "Answer only from what is visible. If something cannot be seen at this resolution, say so. Plain text, at most 110 words, no lists.")
        user = f"Image: {im['kind']} aerial, about {im.get('gsd_m')} m per pixel, source {im['source']}.\nQuestion: {question}"
        text, meta = await self.llm.complete(system, [{"role": "user", "content": user}], tier="main", image_path=str(path), image_sha=sha)
        self.vision_meta = meta
        return {"image": image_id, "source": im["source"], "synthetic": bool(im.get("synthetic")), "answer": " ".join(text.split())}

    # ---- inside (Matterport layer) + live positions (UWB)
    def t_describe_floor(self) -> dict:
        return self.twin.describe()

    def t_locate_unit(self, unit: str) -> dict:
        u = self._unit(unit)
        if not u["room"]:
            return {"unit": unit, "located": False}
        tenants = [self._tenant_view(t) for t in self.provider.costar.list_tenants(self._floor_no()) if u["suite"] and t.get("suite") == u["suite"]]
        exits = []
        for ex in self.twin.exits():
            r = self.twin.route(u["node"], ex["id"], self._blocked())
            exits.append({"stair": ex["id"], "reachable": r["ok"], "walk_m": round(r["length_m"]) if r["ok"] else None, "discharge": ex["discharge"]})
        return {"unit": unit, "located": True, "status": u["status"], "room": u["room"], "room_label": u["room_label"], "suite": u["suite"],
                "tenants_in_record": tenants, "exits": exits, "position_source": "UWB tag" if u["physical"] else "virtual unit (no tag)"}

    def t_route(self, src, dst, avoid: list[str] | None = None) -> dict:
        r = self.twin.route(self._ref(src), self._ref(dst) if dst != "exit" else "exit", self._blocked(avoid))
        out = self._route_view(r)
        out["avoiding"] = self._blocked(avoid)
        self._scan_route_extras(out, r, dst)
        return out

    def t_nearest_exit(self, src) -> dict:
        r = self.twin.nearest_exit(self._ref(src), self._blocked())
        out = self._route_view(r)
        out["avoiding"] = self._blocked()
        self._scan_route_extras(out, r, None)
        return out

    def _scan_route_extras(self, out: dict, r: dict, dst) -> None:
        """Scout runs only (a scan exists): which scan flags the route passes, and the victim it leads to.
        Without a scan the result is exactly what it always was (recorded runs depend on it)."""
        if not r.get("ok"):
            return
        if self.has_scan():
            out["passes_flags"] = self._passes_flags(r["polyline"])
            if self._is_victim(dst):
                out["victim"] = dst
        if isinstance(dst, str) and dst in self._phone_entries():
            out["phone"], out["target"] = dst, self._phone_target(dst)[1]

    def t_hose_stretch(self, target, stair: str = "stairA", standpipe: bool = True) -> dict:
        return hose_stretch(self.twin, self.provider.site(), self._ref(target), stair, standpipe=bool(standpipe))

    def t_plan_anchor_drops(self) -> dict:
        p = plan_anchor_drops(self.twin)
        return {"anchors": [{"id": a["id"], "near": a["near"]} for a in p["anchors"]], "coverage_pct": p["coverage_pct"],
                "line_of_sight_pct": p["line_of_sight_pct"], "method": p["method"]}

    # ---- actions
    def t_radio(self, to: str, text: str, priority: str = "routine") -> dict:
        return self.incident.radio("COMMAND", to, text, priority)

    def t_assign(self, unit: str, task: str, target=None, via: list[str] | None = None) -> dict:
        try:
            return self.incident.assign(unit, task, target, via)
        except (ValueError, KeyError):
            if not self._is_victim(target) and not (isinstance(target, str) and target in self._phone_entries()):
                raise
            # The incident does not know this victim id (docs/SCOUT_CONTRACT.md 2.3 says it should):
            # send the crew to the victim's position in the twin instead, and say so in the log.
            log.warning("incident.assign does not accept victim id %s: assigning to its position in the twin", target)
            ref = self._ref(target)
            return self.incident.assign(unit, task, tuple(ref) if isinstance(ref, list) else ref, via)

    def t_mark_hazard(self, ref: str, kind: str = "blocked", note: str = "") -> dict:
        hz = self.incident.mark_hazard(ref, kind, note, source="agent")
        return {"ok": True, "id": hz["id"], "label": hz["label"]}

    def t_request_resource(self, what: str, reason: str = "") -> dict:
        return self.incident.request_resource(what, reason)

    def t_add_observation(self, text: str, unit: str | None = None, at=None) -> dict:
        position = None
        if at is not None:
            ref = self._ref(at)
            if isinstance(ref, str) and "-" in ref and ref not in self.twin._rooms:
                a, b = ref.split("-", 1)
                pa, pb = self.twin.node(a)["at"], self.twin.node(b)["at"]
                position = [(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2]
            else:
                position = list(self.twin.resolve(ref)["at"])
        tag = self.incident.add_observation(text, unit, position)
        return {"ok": True, "tag": tag["id"], "status": tag.get("status")}

    def t_file_record_correction(self, field: str, current=None, proposed=None, evidence: dict | None = None) -> dict:
        c = self.provider.costar.file_correction(field, current, proposed, evidence or {})
        self.incident._log("correction", f"Record correction filed: {field} -> {proposed}")
        return {"ok": True, "id": c["id"], "status": c["status"]}
