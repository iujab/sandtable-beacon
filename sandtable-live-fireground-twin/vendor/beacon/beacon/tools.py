"""The Beacon command agent's tools: what it can look up, measure and do.

Seven tools over three layers joined by position: the occupants' phones (Beacon), the building
record (the CoStar layer: tenants by suite, kind of business, area, vacancy) and the interior twin
(the Matterport layer: rooms, doors, stairs, walking routes).

Every result is DISCRETE: suite numbers, room labels, whole metres, counts, bands, tenths for
probabilities and the basis text of every estimate. No timestamps, no coordinates, no noisy floats.
The tools read one FROZEN brief taken when the agent's turn starts (`session.brief()`, which is
already rounded), so the same situation always yields the same prompt and a recorded real model
run replays exactly.

`radio`, `assign` and `file_record_check` are actions: they are appended to the plan being built
(and the agent emits them on the stream); `file_record_check` also appends to
`data/record_checks.json` (git-ignored) with the status "filed locally (fixture mode)".

Patterns (catalogue text, wall words, ring room shares) copied from Sandtable's command agent
(`sandtable-live-fireground-twin/sandtable/agent/tools.py`, sibling project, lane L01), with credit.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import config
from .twin import Twin

# Call signs, as in Sandtable's incident (sandtable/incident.py: E1 Engine 1, RIT Rescue team).
UNITS = [{"id": "RIT", "label": "Rescue team", "kind": "rit"}, {"id": "E1", "label": "Engine 1", "kind": "engine"}]
UNIT_IDS = tuple(u["id"] for u in UNITS)
TASKS = ("rescue", "search", "scan", "stage", "hold", "exit")
PRIORITIES = ("routine", "urgent", "emergency")
ACTION_TOOLS = {"radio", "assign", "file_record_check"}
KIT_PUCKS = 5            # the owner's kit: five boards (CLAUDE.md / docs/PHONE_FEASIBILITY.md)
RECORD_STATUS_FIXTURE = "filed locally (fixture mode)"

CATALOG = [
    # (name, layer, signature, description)
    ("get_phones", "beacon", "{}",
     "Occupants' phones heard by the pucks: each opt-in phone (B1..) with what its measurements support (fix, candidates, ring, zone or none), "
     "where in words, how sure, the suite its user registered in the app (home_suite), and the occupant's own report (status, people, can move); "
     "for a ring, the rooms its possible arc crosses; devices heard without the app as bands per puck (devices, not people); what this cannot tell."),
    ("suite_accountability", "costar", '{"suite": "310"?}',
     "The building record's suites on the fire floor against the phones: kind of business, life hazard, vacant on record, expected people "
     "(an estimate with its basis), app users registered, safe outside, phones and people reported, located, devices heard, unaccounted for "
     "(a range), state, the roster's priority and why; totals; disagreements between the record and the phones. One suite with \"suite\"."),
    ("next_puck_plan", "costar", "{}",
     "Where to throw the next puck: suites with no puck in range, ranked by the record's expected people and life hazard, each with the door "
     "to throw through and the walking distance from the fire room in whole metres; pucks placed and left in the kit."),
    ("route_to", "matterport", '{"target": "B1"}',
     "Walking route in the twin from the attack stair (the stair nearest the target; says which and why) to a phone id (a fix leads to the phone, "
     "candidates to the more probable spot, a ring or a zone to the room to search), a suite number or a room: length in whole metres, rooms crossed, steps."),
    ("radio", "action", '{"to": "RIT", "text": "...", "priority": "routine|urgent|emergency", "target": "B1"?}',
     "Transmit an order or information as COMMAND. to: RIT, E1 or ALL. target (optional): the phone id or suite the order is about."),
    ("assign", "action", '{"unit": "RIT", "task": "rescue|search|scan|stage|hold", "target": "B1"}',
     "Give a crew a task and a destination: a phone id (rescue), a suite (search, or scan = carry the next puck there), a room or a stair. "
     "The route from the attack stair is computed and shown to everyone. A new assignment replaces the unit's previous one."),
    ("file_record_check", "action", '{"suite": "309", "text": "..."}',
     "File a verification request against the building record for one suite (for example: vacant on record, but a phone reports from it), "
     "with the evidence in the text. Filed locally in fixture mode; nothing is sent anywhere."),
]

GEOMETRY_MEANS = {"fix": "one point, with an uncertainty: the only geometry that is a position",
                  "candidates": "two or more separate spots, any may be right: search them all, most probable first",
                  "ring": "a distance from one puck, not a place: search along the arc",
                  "zone": "a room, from Bluetooth signal strength: search the room",
                  "none": "heard, nothing spatial"}
PHONE_CANNOT_TELL = ["anyone whose phone has no app, or who has no phone: they are not heard as people",
                     "people from devices heard without the app: those are devices (watches, earbuds, laptops), not people, and some phones do not advertise",
                     "whether the occupant's report is still true: it is what they typed or picked, and a stale phone may have moved"]
STANDING_LIMITS = ["Devices heard without the app are devices, not people.",
                   "A ring is a distance from one puck, not a position; candidates are separate spots, any may be right.",
                   "Expected head counts are estimates from floor area and building-code load factors, not a census; unaccounted for is a range."]


def catalog_text(catalog: list[tuple] | None = None) -> str:
    return "\n".join(f"- {name} {sig}  [{layer}] {desc}" for name, layer, sig, desc in (CATALOG if catalog is None else catalog))


# ============================================================================ the frozen brief
def beacons_of(source) -> dict:
    """The latest Beacons object from a session, the server's state, a zero-argument callable, or a dict."""
    if callable(source) and not hasattr(source, "beacons"):
        source = source()
    if isinstance(source, dict):
        return source
    if source is not None and hasattr(source, "beacons"):
        return source.beacons() or {}
    if source is not None and isinstance(getattr(source, "latest", None), dict):
        return source.latest
    return {}


def brief_of(source) -> dict:
    """The discrete brief (W3's `session.brief()` when the source has one, else built from its Beacons object)."""
    if callable(source) and not hasattr(source, "beacons"):
        source = source()
    if isinstance(source, dict):
        if str(source.get("schema", "")).startswith("sandtable.beacons.brief"):
            return source
        return with_stated_evidence(brief_from_beacons(source), source)
    if source is not None and callable(getattr(source, "brief", None)):
        try:
            return with_stated_evidence(source.brief(), beacons_of(source))
        except Exception:
            pass
    b = beacons_of(source)
    return with_stated_evidence(brief_from_beacons(b), b)


def with_stated_evidence(brief: dict, beacons: dict | None) -> dict:
    """Adds what docs/BEACON_FEED.md A8 publishes and the brief may lack: a phone's `assumptions` (a mirror candidate
    dismissed on the occupant's stated suite or on command's search room: kind, room, text) and `conflict`, and the
    Beacons object's `search_room`. Only when present, so a situation without them gives the same prompt as before."""
    if not isinstance(brief, dict) or not isinstance(beacons, dict):
        return brief
    by_id = {p.get("id"): p for p in beacons.get("phones") or [] if isinstance(p, dict)}
    for ph in brief.get("phones") or []:
        src = by_id.get(ph.get("id")) or {}
        if src.get("assumptions") and not ph.get("assumptions"):
            ph["assumptions"] = [{"kind": a.get("kind"), "room": a.get("room"), "text": a.get("text")}
                                 for a in src["assumptions"] if isinstance(a, dict)]
        if src.get("conflict") and not ph.get("conflict"):
            ph["conflict"] = src["conflict"] if isinstance(src["conflict"], str) else (src["conflict"] or {}).get("text")
    if beacons.get("search_room") and not brief.get("search_room"):
        sr = beacons["search_room"]
        brief["search_room"] = {"room": sr.get("room"), "room_label": sr.get("room_label")} if isinstance(sr, dict) else sr
    return brief


def brief_from_beacons(b: dict) -> dict:
    try:
        from .session import brief_from_beacons as w3_brief         # W3's discrete brief (docs/BEACON_FEED.md section 4, GET /api/brief)
        return w3_brief(b)
    except ImportError:
        return _brief_local(b)


def _brief_local(b: dict) -> dict:
    """Minimal stand-in for `session.brief_from_beacons`, used only if beacon.session cannot be imported."""
    b = b or {}
    r05 = lambda v: None if v is None else round(float(v) * 2) / 2
    phones = []
    for p in sorted(b.get("phones") or [], key=lambda q: q["id"]):
        rep = p.get("report") or {}
        e = {"id": p["id"], "method": p.get("method"), "geometry": p.get("geometry"), "stale": bool(p.get("stale")),
             "room": p.get("room"), "room_label": p.get("room_label"),
             "confidence": None if p.get("confidence") is None else round(float(p["confidence"]), 1),
             "report": {k: rep.get(k) for k in ("status", "people", "can_move", "still", "floor_reported", "home_suite")}, "text": p.get("text")}
        e["report"]["safe_outside"] = bool(rep.get("safe_outside"))
        e["report"]["battery_pct"] = None if rep.get("battery_pct") is None else int(round(rep["battery_pct"] / 10.0) * 10)
        if p.get("geometry") == "fix":
            e.update({"x": r05(p.get("x")), "y": r05(p.get("y")), "sigma_m": max(0.5, r05(p.get("sigma_m")) or 0.5)})
        if p.get("ring"):
            r = p["ring"]
            e["ring"] = {"puck": r.get("puck"), "r_m": r05(r.get("r_m")), "sigma_m": max(0.5, r05(r.get("sigma_m")) or 0.5), "kind": r.get("kind"),
                         "arcs": [[round(a0 / 5) * 5, round(a1 / 5) * 5] for a0, a1 in (r.get("arcs") or [])]}
        if p.get("candidates"):
            e["candidates"] = [{"x": r05(c["x"]), "y": r05(c["y"]), "weight": round(float(c.get("weight") or 0), 1), "room": c.get("room")} for c in p["candidates"]]
        if p.get("zone"):
            z = p["zone"]
            e["zone"] = {"room": z.get("room"), "room_label": z.get("room_label"), "band": z.get("band"), "puck": z.get("puck"),
                         "prob": None if z.get("prob") is None else round(float(z["prob"]), 1)}
        phones.append(e)
    out = {"schema": "sandtable.beacons.brief/1", "simulated": bool(b.get("simulated")), "source": b.get("source"), "floor": b.get("floor"),
           "room": b.get("room"), "room_label": b.get("room_label"), "rungs": b.get("rungs"),
           "pucks": [{"id": p["id"], "x": r05(p.get("x")), "y": r05(p.get("y")), "room": p.get("room")} for p in sorted(b.get("pucks") or [], key=lambda q: q["id"])],
           "phones": phones,
           "presence": [{"id": c["id"], "puck": c.get("puck"), "room": c.get("room"), "room_label": c.get("room_label"), "count": c.get("count"),
                         "count_range": c.get("count_range")} for c in sorted(b.get("presence") or [], key=lambda q: q["id"])],
           "limits": b.get("limits") or []}
    if b.get("sample"):
        out["sample"] = True
    if b.get("accountability"):
        out["accountability"] = {k: v for k, v in b["accountability"].items() if k != "assumptions"}
    return out


# ============================================================================ discrete words
def whole(v) -> int | None:
    return None if v is None else int(round(float(v)))


def tenth(v) -> float | None:
    return None if v is None else round(float(v), 1)


def confidence_band(c) -> str | None:
    if c is None:
        return None
    c = float(c)
    return "high" if c >= 0.8 else "medium" if c >= 0.5 else "low"


def devices_band(n) -> str:
    """Cold device counts change from one scan window to the next: the prompt says only whether any were heard."""
    return "some" if int(n or 0) > 0 else "none"


def battery_band(pct) -> str | None:
    if pct is None:
        return None
    return "low (under 20%)" if float(pct) < 20 else "ok"


def uncertainty_words(sigma) -> str:
    """Three wide bands of the 1-sigma radius, so that run-to-run noise does not change the words."""
    s = float(sigma or 0.5)
    return "within a metre or two" if s <= 1.5 else "within a few metres" if s <= 3.0 else "only roughly (more than 3 m)"


_DEVICES_RE = re.compile(r"about \d+ devices")


def band_devices_in_text(text: str | None) -> str | None:
    """'about 3 devices' -> 'some devices': a roster sentence without the cold count (it changes every scan window)."""
    if not text:
        return text
    return _DEVICES_RE.sub("some devices", text)


def _compass(c: float) -> str:
    return ("north", "east", "south", "west")[int(round((c % 360.0) / 90.0)) % 4]


def part_of_room(twin: Twin, room_id: str | None, x, y) -> str | None:
    """'south-west quarter' of the room: which half along each axis of the room's bounding box, named by compass from the
    twin's georeference. Coarse on purpose: the words do not change with centimetres of noise unless the point sits
    on a centre line of the room."""
    if not room_id or x is None or y is None or room_id not in twin._rooms:
        return None
    poly = twin.room(room_id)["polygon"]
    cx = (min(q[0] for q in poly) + max(q[0] for q in poly)) / 2
    cy = (min(q[1] for q in poly) + max(q[1] for q in poly)) / 2
    b = float((twin.data.get("georef") or {}).get("bearing_deg", 90.0))
    hx = _compass(b) if float(x) >= cx else _compass(b + 180.0)          # +x of the building frame points to compass b
    hy = _compass(b - 90.0) if float(y) >= cy else _compass(b + 90.0)    # +y points to compass b - 90
    ns, ew = (hx, hy) if hx in ("north", "south") else (hy, hx)
    return f"{ns}-{ew} quarter"


def _ccw(poly) -> bool:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) > 0


def _seg_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    k = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - k * dx, p[1] - a[1] - k * dy)


def wall_words(twin: Twin, room_id: str | None, x, y) -> str | None:
    """'about 2 m from the south wall and 3 m from the west wall' in whole metres, wall names from the twin's
    georeference (the same compass naming as the session's text lines, beacon/session.py describe_point)."""
    if not room_id or x is None or y is None or room_id not in twin._rooms:
        return None
    poly = twin.room(room_id)["polygon"]
    b = float((twin.data.get("georef") or {}).get("bearing_deg", 90.0))
    ccw = _ccw(poly)
    best: dict[str, float] = {}
    for i in range(len(poly)):
        a, c = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = c[0] - a[0], c[1] - a[1]
        nx, ny = (dy, -dx) if ccw else (-dy, dx)
        compass = (b - math.degrees(math.atan2(ny, nx))) % 360.0
        name = ("north", "east", "south", "west")[int(round(compass / 90.0)) % 4]
        d = _seg_dist((float(x), float(y)), a, c)
        best[name] = min(best.get(name, float("inf")), d)
    if not best:
        return None
    first = min(best, key=best.get)
    perp = {"north": ("east", "west"), "south": ("east", "west"), "east": ("north", "south"), "west": ("north", "south")}[first]
    others = [n for n in perp if n in best]

    def part(name):
        m = int(round(best[name]))
        return f"against the {name} wall" if m == 0 else f"{m} m from the {name} wall"
    if not others:
        return "about " + part(first)
    return "about " + part(first) + " and " + part(min(others, key=best.get))


def ring_rooms(twin: Twin, cx: float, cy: float, r: float, arcs, floor: str | None = None) -> list[tuple[str, int]]:
    """Rooms the possible arcs of a ring cross, with the share of the arc in each, percent rounded to 10, most first."""
    counts: dict[str, int] = {}
    for a0, a1 in arcs or [[0.0, 360.0]]:
        a0, a1 = 5 * round(float(a0) / 5), 5 * round(float(a1) / 5)
        a1 = a1 + 360.0 if a1 < a0 else a1
        n = max(1, int((a1 - a0) / 5))
        for i in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * i / n)
            loc = twin.locate(cx + r * math.cos(a), cy + r * math.sin(a), floor)
            if loc["room"] and not loc["off_map_m"]:
                counts[loc["room"]] = counts.get(loc["room"], 0) + 1
    total = sum(counts.values()) or 1
    shares = [(k, int(round(10 * v / total)) * 10) for k, v in counts.items()]
    return sorted(((k, p) for k, p in shares if p > 0), key=lambda kv: (-kv[1], kv[0]))


# ============================================================================ the tool box
class ToolBox:
    def __init__(self, brief: dict, twin: Twin, site: dict, record_path: Path | None = None, run: str = "phones#0"):
        self.brief = brief or {}
        self.twin = twin
        self.site = site or {}
        self.run = run
        self.record_path = Path(record_path) if record_path is not None else config.DATA_DIR / "record_checks.json"
        self.orders: list[dict] = []
        self.assignments: dict[str, dict] = {}
        self.record_checks: list[dict] = []
        self.calls: list[dict] = []
        self._fns: dict[str, Callable[..., Any]] = {name: getattr(self, f"t_{name}") for name, *_ in CATALOG}

    def call(self, name: str, args: dict | None) -> dict:
        if name not in self._fns:
            return {"error": f"unknown tool {name!r}; the tools are: {', '.join(self._fns)}"}
        if args is not None and not isinstance(args, dict):
            return {"error": f"bad arguments for {name}: args must be an object"}
        try:
            out = self._fns[name](**(args or {}))
        except TypeError as exc:
            out = {"error": f"bad arguments for {name}: {exc}"}
        except (ValueError, KeyError) as exc:
            out = {"error": str(exc).strip("'\"")}
        self.calls.append({"tool": name, "args": args or {}, "result": out})
        return out

    # ---- helpers
    @property
    def acc(self) -> dict:
        return self.brief.get("accountability") or {}

    def _rows(self) -> list[dict]:
        return list(self.acc.get("suites") or [])

    def _phones(self) -> list[dict]:
        return list(self.brief.get("phones") or [])

    def _phone(self, pid: str) -> dict | None:
        return next((p for p in self._phones() if p.get("id") == pid), None)

    def _puck(self, pid: str) -> dict | None:
        return next((p for p in self.brief.get("pucks") or [] if p.get("id") == pid), None)

    def _label(self, room: str | None) -> str | None:
        return self.twin.room(room)["label"] if room in self.twin._rooms else None

    def _floor_label(self) -> str | None:
        f = self.brief.get("floor") or self.twin.exercise_floor
        try:
            return str(self.twin.floor(f).get("label") or f)
        except KeyError:
            return f

    def _suite_room(self, suite) -> str | None:
        s = str(suite or "").replace("Suite", "").strip()
        return self.twin._suite_room.get(s)

    def _door_words(self, room: str) -> str | None:
        """'from the West corridor' for a suite's door."""
        doors = sorted((d for d in self.twin.data.get("doors", []) if room in (d.get("rooms") or [])), key=lambda d: d["id"])
        for d in doors:
            other = next((r for r in d["rooms"] if r != room), None)
            if other in self.twin._rooms:
                return f"from the {self._label(other)}"
        return None

    def _ring_view(self, ring: dict) -> dict:
        puck = self._puck(ring.get("puck"))
        view = {"puck": ring.get("puck"), "puck_in": self._label((puck or {}).get("room")) if puck else None,
                "radius_m": whole(ring.get("r_m")),
                "band": "thin (UWB, about a hand's width)" if ring.get("kind") == "uwb" else "fat (Bluetooth signal strength, metres wide)",
                "clipped_by_twin": (ring.get("arcs") or [[0, 360]]) not in ([[0, 360]], [[0.0, 360.0]])}
        if puck and puck.get("x") is not None and ring.get("r_m") is not None:
            shares = ring_rooms(self.twin, float(puck["x"]), float(puck["y"]), float(ring["r_m"]), ring.get("arcs"), self.brief.get("floor"))
            view["arc_crosses"] = [{"room_label": self._label(r), "share_pct": p} for r, p in shares]
        return view

    def _phone_view(self, p: dict) -> dict:
        rep = p.get("report") or {}
        geom = p.get("geometry") or "none"
        report = {"status": rep.get("status"), "people": rep.get("people"), "can_move": rep.get("can_move"),
                  "battery": battery_band(rep.get("battery_pct")), "still": rep.get("still"),
                  "floor_reported": rep.get("floor_reported"), "home_suite": rep.get("home_suite")}
        view = {"id": p["id"], "geometry": geom, "method": p.get("method"), "stale": bool(p.get("stale"))}
        if rep.get("safe_outside"):                    # accounted for: no motion or battery state (it changes and matters no more)
            report = {"status": rep.get("status"), "people": rep.get("people"), "home_suite": rep.get("home_suite")}
            view.update({"safe_outside": True, "accounted_for": True, "report": report,
                         "where": "outside the building by the occupant's own report (pressed 'I am out')"})
            return view
        view["report"] = report
        if p.get("assumptions"):                       # A8: a position that rests on a statement, not on the measurements alone
            view["rests_on"] = [{"kind": a.get("kind"), "room_label": self._label(a.get("room")), "text": a.get("text")} for a in p["assumptions"]]
        if p.get("conflict"):
            view["conflict"] = str(p["conflict"])[:300]
        if geom == "fix":
            room = p.get("room") or self.twin.locate(p["x"], p["y"], self.brief.get("floor"))["room"]
            view["room_label"] = self._label(room) or p.get("room_label")
            part = part_of_room(self.twin, room, p.get("x"), p.get("y"))
            view["where"] = ", ".join(x for x in [f"in {view['room_label']}" if view["room_label"] else None,
                                                  f"{part} of the room" if part else None,
                                                  uncertainty_words(p.get("sigma_m"))] if x)
        elif geom == "candidates":
            cands = p.get("candidates") or []
            view["room_label"] = p.get("room_label")
            spots = []
            for c in cands:                                    # no weights and a fixed order: both drift with noise
                room = c.get("room") or self.twin.locate(c["x"], c["y"], self.brief.get("floor"))["room"]
                spots.append({"room_label": self._label(room), "where": part_of_room(self.twin, room, c.get("x"), c.get("y"))})
            view["candidates"] = sorted(spots, key=lambda c: (str(c["room_label"]), str(c["where"])))
            view["where"] = (f"{'two' if len(cands) == 2 else len(cands)} possible spots, not a position: send a crew to check "
                             f"{'both' if len(cands) == 2 else 'all of them'}, or move a puck to the wall to decide")
        elif geom == "ring":
            view["ring"] = self._ring_view(p.get("ring") or {})
            view["where"] = f"somewhere on a ring of about {view['ring']['radius_m']} m around puck {view['ring']['puck']}: range only, no position"
        elif geom == "zone":
            z = p.get("zone") or {}
            view["room_label"] = z.get("room_label") or p.get("room_label")
            view["zone"] = {"room_label": view["room_label"], "band": z.get("band"), "puck": z.get("puck"), "prob": tenth(z.get("prob"))}
            view["where"] = f"in {view['room_label'] or 'an unknown room'} by Bluetooth signal strength: search the room"
        else:
            view["where"] = "heard, no position"
        return view

    def _flags(self) -> dict:
        b = self.brief
        return {"simulated": bool(b.get("simulated")), "sample": bool(b.get("sample")), "live": b.get("source") == "live"}

    def _data_label(self) -> str:
        f = self._flags()
        if f["sample"]:
            return "SAMPLE: the hand-made sample of the feed, not the simulator and not a measurement"
        if f["simulated"]:
            return "SIMULATED: phones, pucks and devices come from the simulator's stated noise model, not from a measurement"
        return "LIVE: phones reporting through the opt-in app; ranges are real measurements with their stated uncertainty"

    def _record_label(self) -> str:
        src = self.acc.get("source") or {}
        return src.get("text") or "Building record: fixture, not CoStar data."

    # ---- read tools
    def t_get_phones(self) -> dict:
        phones = [self._phone_view(p) for p in self._phones()]
        devices = [{"puck": c.get("puck"), "room_label": c.get("room_label"), "devices": devices_band(c.get("count")),
                    "note": "devices, not people"} for c in self.brief.get("presence") or []]
        cannot = list(PHONE_CANNOT_TELL)
        cannot.append(self._data_label())
        out = {**self._flags(), "data": self._data_label(), "floor": self._floor_label(),
               "fire_room": self.brief.get("room_label"),
               **({"search_room": (self.brief["search_room"] or {}).get("room_label") if isinstance(self.brief["search_room"], dict) else self.brief["search_room"]}
                  if self.brief.get("search_room") else {}),
               "pucks": [{"id": p["id"], "in": self._label(p.get("room"))} for p in self.brief.get("pucks") or []],
               "phones": phones, "devices_without_app": devices, "geometry_means": GEOMETRY_MEANS, "cannot_tell": cannot}
        if not phones:
            out["reason"] = "no opt-in phone has been heard"
        return out

    def _row_view(self, r: dict) -> dict:
        exp = r.get("expected") or {}
        un = r.get("unaccounted") or {}
        return {"suite": r.get("suite"), "room_label": r.get("room_label"), "kind_of_business": r.get("industry"),
                "life_hazard": r.get("life_hazard"), "vacant_on_record": bool(r.get("vacant")),
                "expected": {"people": exp.get("people"), "low": exp.get("low"), "high": exp.get("high"), "basis": exp.get("basis")},
                "app_users_registered": r.get("app_registered"), "safe_outside": r.get("safe_outside"),
                "phones_inside": r.get("phones_opt_in"), "people_reported": r.get("people_reported"), "located": r.get("located"),
                "devices_heard": devices_band(r.get("cold_devices")),
                "unaccounted": [un.get("low"), un.get("high")] if un else None,
                "state": r.get("state"), "roster_priority": r.get("priority"), "why": band_devices_in_text(r.get("why"))}

    def disagreements(self) -> list[dict]:
        """Where the record and the phones disagree: the roster's own record checks, app users registered to a suite
        the record lists as vacant, and ring phones whose possible arc lies mostly in a suite vacant on record."""
        rows = {r.get("suite"): r for r in self._rows()}
        out: dict[str, list[str]] = {}
        for c in self.acc.get("record_checks") or []:
            out.setdefault(str(c.get("suite")), []).append(band_devices_in_text(c.get("text")))
        for s, r in rows.items():
            if r.get("vacant") and (r.get("app_registered") or 0) > 0:
                n = r["app_registered"]
                out.setdefault(s, []).append(f"{n} app user{'s' if n != 1 else ''} registered Suite {s} as their suite, which the record lists as vacant")
        for p in self._phones():
            rep = p.get("report") or {}
            if rep.get("safe_outside") or p.get("geometry") != "ring" or not p.get("ring"):
                continue
            view = self._ring_view(p["ring"])
            top = (view.get("arc_crosses") or [None])[0]
            if top and top["share_pct"] >= 50:
                room = next((rid for rid, room in self.twin._rooms.items() if room.get("label") == top["room_label"]), None)
                suite = (self.twin._rooms.get(room) or {}).get("suite")
                if suite in rows and rows[suite].get("vacant"):
                    out.setdefault(suite, []).append(f"phone {p['id']}'s range ring lies mostly in Suite {suite}, which the record lists as vacant")
        return [{"suite": s, "evidence": ev} for s, ev in sorted(out.items())]

    def t_suite_accountability(self, suite: str | None = None) -> dict:
        rows = self._rows()
        if not rows:
            return {"suites": [], "reason": "no accountability table: the building record module is not available or no floor is set"}
        if suite is not None:
            s = str(suite).replace("Suite", "").strip()
            row = next((r for r in rows if r.get("suite") == s), None)
            if row is None:
                raise ValueError(f"no suite {suite!r} on this floor; suites: {', '.join(r.get('suite') for r in rows)}")
            return {"record": self._record_label(), "suite": self._row_view(row),
                    "disagreements": [d for d in self.disagreements() if d["suite"] == s]}
        t = self.acc.get("totals") or {}
        prop = self.site.get("property") or {}
        try:
            from . import roster
            factors = {k: int(round(v["sf_per_person"])) for k, v in roster.LOAD_FACTORS_SF.items()}
            util = {"open_hours_pct": int(round(100 * roster.UTILISATION_OPEN)), "outside_open_hours_pct": int(round(100 * roster.UTILISATION_CLOSED)),
                    "open_hours": f"{int(roster.OPEN_HOURS[0]):02d}:00 to {int(roster.OPEN_HOURS[1]):02d}:00 local"}
        except Exception:
            factors, util = None, None
        return {"record": self._record_label(), "floor": self._floor_label(),
                "suites": [self._row_view(r) for r in rows],
                "totals": {"occupied_suites": (t.get("suites") or 0) - (t.get("suites_vacant_on_record") or 0),
                           "vacant_on_record": t.get("suites_vacant_on_record"),
                           "expected_people": t.get("expected_people"), "expected_high": t.get("expected_high"),
                           "phones_inside": t.get("phones_opt_in"), "people_reported": t.get("people_reported"), "located": t.get("located"),
                           "safe_outside": t.get("safe_outside"), "occupied_suites_unscanned": t.get("suites_unscanned"),
                           "unaccounted": [t.get("unaccounted_low"), t.get("unaccounted_high")],
                           "unaccounted_means": "the record's expected people minus everyone accounted for (reported inside by an opt-in phone, or safe outside); "
                                                "people already reported by phone are not part of this range"},
                "estimate_basis": {"load_factors_sf_per_person": factors, "share_present": util,
                                   "note": "expected people are estimates from floor area and building-code load factors unless the record carries a head count"},
                "roll_call": {"property_manager": prop.get("property_manager"),
                              "note": None if prop.get("property_manager") else "no property-manager contact in this record (a CoStar field, empty in fixture mode): no tenant roll call possible from the record"},
                "disagreements": self.disagreements()}

    def _walk_from(self, src_room: str | None, dst_room: str) -> int | None:
        if not src_room:
            return None
        try:
            r = self.twin.route(src_room, dst_room)
        except ValueError:
            return None
        return whole(r["length_m"]) if r.get("ok") else None

    def t_next_puck_plan(self) -> dict:
        fire = self.brief.get("room")
        rows = {r.get("suite"): r for r in self._rows()}
        suites = []
        for p in self.acc.get("priorities") or []:
            if p.get("action") != "scan":
                continue
            r = rows.get(p.get("suite")) or {}
            room = r.get("room") or self._suite_room(p.get("suite"))
            exp = r.get("expected") or {}
            suites.append({"rank": len(suites) + 1, "suite": p.get("suite"), "room_label": self._label(room),
                           "kind_of_business": r.get("industry"), "life_hazard": r.get("life_hazard"),
                           "expected": {"people": exp.get("people"), "low": exp.get("low"), "high": exp.get("high")},
                           "door": self._door_words(room) if room else None,
                           "walk_from_fire_room_m": self._walk_from(fire, room) if room else None,
                           "why": band_devices_in_text(r.get("why"))})
        vacant = [r.get("suite") for r in self._rows() if r.get("state") == "vacant-on-record"]
        placed = len(self.brief.get("pucks") or [])
        return {"fire_room": self.brief.get("room_label"), "pucks_placed": placed, "pucks_left_in_kit": max(0, KIT_PUCKS - placed),
                "ranking": "the record's expected people, doubled where the tenant's occupants may not leave unaided; ties by walking distance from the fire room",
                "suites": suites,
                "vacant_on_record_not_scanned": vacant,
                "note": ("vacant-on-record suites are not ranked, but the record can be wrong: a phone or device heard there is a record check"
                         if vacant else None)}

    def _target(self, target) -> tuple[Any, str, str | None]:
        """(twin reference, what the route leads to, room id) for a phone id, a suite number, a room id or a stair id."""
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target must be a phone id (B1), a suite number, a room or a stair")
        t = target.strip()
        ph = self._phone(t)
        if ph is not None:
            rep = ph.get("report") or {}
            if rep.get("safe_outside"):
                raise ValueError(f"{t} reports safe outside: accounted for, not a target")
            geom = ph.get("geometry")
            # Routes lead to the room (its walking node), not to the noisy point: the length in whole metres stays put.
            if geom == "fix" and ph.get("x") is not None:
                room = ph.get("room") or self.twin.locate(ph["x"], ph["y"], self.brief.get("floor"))["room"]
                part = part_of_room(self.twin, room, ph["x"], ph["y"])
                return room, f"{self._label(room)}, then the phone's spot in the {part} of the room ({t}: fix, {uncertainty_words(ph.get('sigma_m'))})", room
            if geom == "candidates" and ph.get("candidates"):
                rooms = sorted({c.get("room") or self.twin.locate(c["x"], c["y"], self.brief.get("floor"))["room"] for c in ph["candidates"]},
                               key=lambda r: str(r))
                room = rooms[0]
                n = len(ph["candidates"])
                where = " and ".join(self._label(r) or str(r) for r in rooms)
                return room, f"{self._label(room)}: {t} has {n} candidate spots in {where}, any of them may be right; check {'both' if n == 2 else 'all'}", room
            room = None
            if geom == "ring" and ph.get("ring"):
                shares = self._ring_view(ph["ring"]).get("arc_crosses") or []
                lab = shares[0]["room_label"] if shares else None
                room = next((rid for rid, r in self.twin._rooms.items() if r.get("label") == lab), None)
            if geom == "zone" and ph.get("zone"):
                room = ph["zone"].get("room")
            if room is None:
                room = ph.get("room")
            if room in self.twin._rooms:
                kind = "range only: search the arc" if geom == "ring" else "room only: search the room"
                return room, f"{self._label(room)}, where most of {t}'s possible location lies ({kind}; no position)", room
            raise ValueError(f"{t} has no location a crew can go to ({geom or 'no'} geometry)")
        room = t if t in self.twin._rooms else self._suite_room(t)
        if room:
            return room, self._label(room), room
        if t in self.twin._stairs:
            st = self.twin.stair(t)
            return t, st["label"], st["room"]
        raise ValueError(f"unknown target {target!r}: a phone id ({', '.join(p['id'] for p in self._phones()) or 'none heard'}), a suite number, a room or a stair")

    def _route(self, target) -> dict:
        ref, leads_to, room = self._target(target)
        options = []
        for ex in self.twin.exits():
            try:
                r = self.twin.route(ex["id"], ref)
            except ValueError:
                continue
            if r.get("ok"):
                options.append((r["length_m"], ex, r))
        if not options:
            return {"ok": False, "target": target, "reason": f"no walkable route from any stair to {leads_to}"}
        options.sort(key=lambda o: (o[0], o[1]["id"]))
        length, ex, r = options[0]
        rooms = [self._label(q) or q for q in r["rooms"]]
        others = [{"stair": o[1]["label"], "length_m": whole(o[0])} for o in options[1:]]
        why = f"nearest stair to the target by walking distance ({whole(length)} m" + (
            "; " + ", ".join(f"{o['stair']} {o['length_m']} m" for o in others) if others else "") + ")"
        return {"ok": True, "target": target, "leads_to": leads_to, "attack_stair": ex["label"], "why_this_stair": why,
                "stair_discharge": ex.get("discharge"), "length_m": whole(length), "to": self._label(room) or leads_to,
                "via": rooms[1:-1] if len(rooms) > 2 else [], "steps": [s["text"] for s in r.get("steps") or []]}

    def t_route_to(self, target) -> dict:
        return self._route(target)

    # ---- actions
    def t_radio(self, to: str, text: str, priority: str = "routine", target: str | None = None) -> dict:
        to = str(to or "").strip().upper()
        if to not in UNIT_IDS + ("ALL",):
            raise ValueError(f"unknown call sign {to!r}: one of {', '.join(UNIT_IDS)} or ALL")
        text = " ".join(str(text or "").split())
        if not text:
            raise ValueError("a radio message needs text")
        pr = str(priority or "routine").strip().lower()
        pr = pr if pr in PRIORITIES else "routine"
        order = {"to": to, "text": text[:400], "priority": pr, "target": (str(target).strip() if target else None)}
        if order["target"] is None and to in self.assignments:
            order["target"] = self.assignments[to].get("target")
        self.orders.append(order)
        return {"ok": True, "order": len(self.orders), "to": to, "priority": pr}

    def t_assign(self, unit: str, task: str, target=None) -> dict:
        unit = str(unit or "").strip().upper()
        if unit not in UNIT_IDS:
            raise ValueError(f"unknown unit {unit!r}: one of {', '.join(UNIT_IDS)}")
        task = str(task or "").strip().lower()
        if task not in TASKS:
            raise ValueError(f"unknown task {task!r}: one of {', '.join(TASKS)}")
        route = None
        if target is not None and str(target).strip() and str(target).strip().lower() != "exit":
            route = self._route(str(target))
            if not route.get("ok"):
                return {"ok": False, "unit": unit, "reason": route.get("reason")}
        replaced = unit in self.assignments
        a = {"unit": unit, "task": task, "target": (str(target).strip() if target is not None else None),
             "route": ({k: route[k] for k in ("attack_stair", "length_m", "to", "via", "leads_to")} if route else None)}
        self.assignments[unit] = a
        out = {"ok": True, "unit": unit, "task": task, "target": a["target"], "route": a["route"]}
        if replaced:
            out["note"] = "replaces the unit's previous assignment"
        return out

    def t_file_record_check(self, suite, text: str) -> dict:
        s = str(suite or "").replace("Suite", "").strip()
        if s not in self.twin._suite_room:
            raise ValueError(f"no suite {suite!r} in the twin")
        text = " ".join(str(text or "").split())
        if not text:
            raise ValueError("a record check needs the evidence as text")
        mode = (self.acc.get("source") or {}).get("costar_mode") or "fixture"
        status = RECORD_STATUS_FIXTURE if mode == "fixture" else "filed locally (no CoStar write interface is wired)"
        check = {"suite": s, "text": text[:400], "status": status}
        self.record_checks.append(check)
        self._append_record_file({**check, "run": self.run, "filed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                  "simulated": bool(self.brief.get("simulated")), "sample": bool(self.brief.get("sample"))})
        return {"ok": True, "check": len(self.record_checks), "suite": s, "status": status}

    def _append_record_file(self, entry: dict) -> None:
        try:
            self.record_path.parent.mkdir(parents=True, exist_ok=True)
            data = []
            if self.record_path.is_file():
                try:
                    data = json.loads(self.record_path.read_text(encoding="utf-8"))
                    data = data if isinstance(data, list) else []
                except json.JSONDecodeError:
                    data = []
            data.append(entry)
            tmp = self.record_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(self.record_path)
        except OSError:
            pass                           # the plan still carries the check; the file is a convenience copy
