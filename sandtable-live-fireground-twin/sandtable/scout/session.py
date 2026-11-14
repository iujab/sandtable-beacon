"""Scout session: a crew throws UWB pucks into a room; the pucks self-locate, register into the twin
and scan as a multistatic radar. This module runs that pipeline, paces it so people can follow it,
assembles the Scan object (`sandtable.scan/1`, see docs/SCOUT_CONTRACT.md 2.3 and
fixtures/scout/sample_scan.json) and hands it to the incident.

Engines (`SANDTABLE_SCOUT_ENGINE`):
  sim  (default)  the radar engine of this tree: scene -> sim -> dsp -> register -> mismatch.
                  Everything it produces is SIMULATED (no puck has been built).
  fake            replays the hand-made sample scan through the same phases in about 6 s; every
                  message carries "sample": true. Also the fallback when the sim engine cannot be
                  imported or fails, which is logged loudly and announced on the board.

The engine modules are imported lazily so that a broken radar can never stop the server.
`truth()` is the simulator's ground truth, for evaluation only: nothing in the scan is read from it.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import time
from typing import Callable

from .. import config

log = logging.getLogger("sandtable.scout")
SAMPLE_SCAN = config.FIXTURES / "scout" / "sample_scan.json"
PHASES = ("idle", "thrown", "ranging", "located", "registered", "scanning", "done", "failed")
BASE_LIMITS = [
    "A coarse echo map and outline, not a LiDAR-grade model",
    "A person who is neither moving nor breathing looks like furniture",
    "The twin is the map: the pucks add who is in the room without a tag, whether they breathe or move, and where the room differs from the twin",
]


# ---------------------------------------------------------------------------- twin helpers
def fire_room(twin, provider) -> str:
    """The exercise's fire room, chosen exactly like Director._fire_room(): a suite whose tenant
    carries a life hazard, as far from Stair A as the floor allows."""
    floor_no = int(twin.floor()["label"])
    hazard_suites = {t["suite"] for t in provider.costar.list_tenants(floor_no) if t.get("life_hazard")}
    best = None
    for r in twin.rooms("suite"):
        route = twin.route("stairA", r["id"])
        if not route["ok"]:
            continue
        key = (r.get("suite") in hazard_suites, route["length_m"], r["id"])
        if best is None or key > best[0]:
            best = (key, r["id"])
    return best[1]


def room_door(twin, room_id: str) -> dict | None:
    """The room's way in: a door whose inner room is this room, preferring one that opens on a corridor."""
    doors = [d for d in twin.data["doors"] if d["rooms"] and d["rooms"][0] == room_id]
    doors = doors or [d for d in twin.data["doors"] if room_id in d["rooms"]]
    if not doors:
        return None

    def corridor(d):
        outer = next((r for r in d["rooms"] if r != room_id), None)
        return outer is not None and outer in twin._rooms and twin.room(outer)["kind"] == "corridor"
    return sorted(doors, key=lambda d: (not corridor(d), d["id"]))[0]


def door_outside_node(twin, room_id: str) -> str | None:
    """A walkable graph node just outside the room's door, on the corridor side (where a crew stands to throw)."""
    d = room_door(twin, room_id)
    if d is None:
        return None
    outer = next((r for r in d["rooms"] if r != room_id), None)
    nodes = [n for n in twin.data["graph"]["nodes"] if n["room"] == outer and n["kind"] == "sweep" and math.dist(n["at"], d["at"]) <= 3.0]
    if nodes:
        return min(nodes, key=lambda n: (math.dist(n["at"], d["at"]), n["id"]))["id"]
    return d.get("node")


def compass(twin, dx: float, dy: float) -> str:
    """Compass name (north/east/south/west) of a direction given in the twin frame, through the twin's georeference."""
    b = twin._b
    east = dx * math.sin(b) - dy * math.cos(b)
    north = dx * math.cos(b) + dy * math.sin(b)
    bearing = math.degrees(math.atan2(east, north)) % 360.0
    return ("north", "east", "south", "west")[int(((bearing + 45.0) % 360.0) // 90.0)]


def _seg_dist(p, a, b) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    k = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - ay) * vy) / L2))
    return math.hypot(p[0] - (ax + k * vx), p[1] - (ay + k * vy))


def walls(twin, polygon) -> list[dict]:
    """Each edge of the polygon with the compass name of its outward normal."""
    area = sum(polygon[i][0] * polygon[(i + 1) % len(polygon)][1] - polygon[(i + 1) % len(polygon)][0] * polygon[i][1] for i in range(len(polygon)))
    out = []
    for i in range(len(polygon)):
        a, b = polygon[i], polygon[(i + 1) % len(polygon)]
        ex, ey = b[0] - a[0], b[1] - a[1]
        nx, ny = (ey, -ex) if area > 0 else (-ey, ex)
        out.append({"index": i, "a": a, "b": b, "name": compass(twin, nx, ny)})
    return out


def _fmt(d: float, step: float) -> str:
    v = round(d / step) * step
    return f"{v:.1f}"


def wall_text(twin, polygon, x: float, y: float, step: float = 0.1) -> str:
    """'2.4 m from the west wall, 2.1 m from the south wall': the two nearest walls that face different ways."""
    ws = sorted(((round(_seg_dist((x, y), w["a"], w["b"]), 3), w["index"], w["name"]) for w in walls(twin, polygon)))
    if not ws:
        return ""
    first = ws[0]
    perpendicular = {"north": ("east", "west"), "south": ("east", "west"), "east": ("north", "south"), "west": ("north", "south")}[first[2]]
    second = next((w for w in ws[1:] if w[2] in perpendicular), None) or next((w for w in ws[1:] if w[2] != first[2]), None)
    parts = [f"{_fmt(first[0], step)} m from the {first[2]} wall"]
    if second:
        parts.append(f"{_fmt(second[0], step)} m from the {second[2]} wall")
    return ", ".join(parts)


def _jsonable(obj):
    """Plain JSON types only (numpy scalars and arrays become floats and lists)."""
    return json.loads(json.dumps(obj, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o) if hasattr(o, "__float__") else str(o)))


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


# ---------------------------------------------------------------------------- the session
class ScoutSession:
    def __init__(self, twin, provider, emit: Callable[[dict], None], incident=None, clock: Callable[[], float] = time.time):
        self.twin, self.provider, self.emit, self.incident, self.clock = twin, provider, emit, incident, clock
        self.pace = config.env_float("SANDTABLE_SCOUT_PACE", 1.0)     # 1.0 = human-readable pacing; tests shrink it
        self.phase = "idle"
        self.scan: dict | None = None
        self.engine: str | None = None          # the engine that produced the current scan: "sim" | "fake"
        self.fallback_reason: str | None = None
        self.room: str | None = None
        self.pucks: list[dict] = []
        self._truth: dict | None = None
        self._task: asyncio.Task | None = None
        self._n = 0

    # ---- control
    @property
    def running(self) -> bool:
        return self.phase not in ("idle", "done", "failed") or bool(self._task and not self._task.done())

    def start(self, room_id: str | None = None, n_pucks: int = 5, seed: int = 310) -> asyncio.Task:
        """Starts deploy() as a background task (what the server and the exercise script call)."""
        if self.running:
            raise RuntimeError("a scan is already running")
        self.phase = "thrown"                   # claimed at once, so a second request is refused
        self._task = asyncio.create_task(self._deploy_logged(room_id, n_pucks, seed))
        return self._task

    async def _deploy_logged(self, room_id, n_pucks, seed):
        try:
            return await self.deploy(room_id, n_pucks, seed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scout deploy failed")

    def reset(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.phase, self.scan, self.engine, self.fallback_reason, self.room, self.pucks, self._truth = "idle", None, None, None, None, [], None
        self._n = 0

    def truth(self) -> dict | None:
        """Simulation ground truth (the scene the simulator rendered). Evaluation only; None for the sample engine."""
        if self._truth is None:
            return None
        return {**self._truth, "note": "SIMULATION GROUND TRUTH for evaluation scripts only. Never shown as a result.", "scan_id": (self.scan or {}).get("id")}

    # ---- the pipeline
    async def deploy(self, room_id: str | None = None, n_pucks: int = 5, seed: int = 310) -> dict:
        room = room_id or fire_room(self.twin, self.provider)
        self.twin.room(room)                                           # validates the reference
        self.room, self._truth, self.fallback_reason, self.pucks = room, None, None, []
        self._n += 1
        if self.incident is not None:
            self.incident.scout.update({"phase": "thrown", "room": room})
        engine = (os.environ.get("SANDTABLE_SCOUT_ENGINE") or "sim").strip().lower()
        if engine != "fake":
            try:
                mods = self._import_engine()
            except Exception as exc:                                    # loud, never silent
                log.error("SCOUT RADAR ENGINE IMPORT FAILED (%s: %s): replaying the hand-made SAMPLE scan instead", type(exc).__name__, exc)
                self._announce_fallback(f"radar engine not available ({type(exc).__name__}: {exc})")
            else:
                try:
                    return await self._run_sim(mods, room, int(n_pucks), int(seed))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception("SCOUT RADAR ENGINE FAILED: replaying the hand-made SAMPLE scan instead")
                    self._announce_fallback(f"radar engine failed ({type(exc).__name__}: {exc})")
        return await self._run_fake(room)

    @staticmethod
    def _import_engine():
        from . import dsp, mismatch, register, scene, sim          # lazily: a broken radar must not break the server
        return {"scene": scene, "sim": sim, "dsp": dsp, "register": register, "mismatch": mismatch}

    def _announce_fallback(self, why: str) -> None:
        self.fallback_reason = why
        text = f"Scout: {why}. Replaying the hand-made SAMPLE scan instead (not a result)."
        self._set_phase("failed", text, sample=True, fallback=True)
        self.emit({"type": "alert", "level": "warn", "text": text, "unit": None})
        if self.incident is not None:
            self.incident.log_scout(text)

    def _set_phase(self, phase: str, text: str, pucks: list | None = None, sample: bool = False, **extra) -> dict:
        self.phase = phase
        if pucks is not None:
            self.pucks = pucks
        if self.incident is not None:
            self.incident.scout["phase"] = phase
        msg = {"type": "scout", "phase": phase, "text": text, "t": round(self.clock(), 2), "room": self.room,
               "pucks": self.pucks, "sample": bool(sample), **extra}
        self.emit(msg)
        return msg

    async def _hold(self, since: float, seconds: float) -> None:
        loop = asyncio.get_running_loop()
        rest = seconds * self.pace - (loop.time() - since)
        if rest > 0:
            await asyncio.sleep(rest)

    def _who(self) -> str:
        if self.incident is not None and "E1" in self.incident.units:
            return self.incident.units["E1"]["label"]
        return "The crew"

    # ---- fake engine: the hand-made sample through the same phases (~6 s)
    async def _run_fake(self, room: str) -> dict:
        self.engine = "fake"
        sample = json.loads(SAMPLE_SCAN.read_text(encoding="utf-8"))
        loop = asyncio.get_running_loop()
        label = sample.get("room_label") or self.twin.room(room)["label"]
        ids = [{"id": p["id"]} for p in sample["pucks"]]
        steps = [
            ("thrown", f"SAMPLE: {self._who()} threw {len(ids)} pucks through the door of {label}", ids, {}),
            ("ranging", f"SAMPLE: pucks ranging each other over {len(sample['links'])} links", ids, {}),
            ("located", "SAMPLE: pucks self-located (their own frame: rotation and mirror unknown)", ids, {"frame": "rel"}),
            ("registered", f"SAMPLE: {sample['registration']['text']}", [{"id": p["id"], "x": p["x"], "y": p["y"]} for p in sample["pucks"]], {"frame": "twin"}),
        ]
        for phase, text, pucks, extra in steps:
            t0 = loop.time()
            self._set_phase(phase, text, pucks, sample=True, **extra)
            await self._hold(t0, 1.0)
        dur = float(sample.get("timing", {}).get("scan_s", 12.0))
        for k in range(3):
            t0 = loop.time()
            frac = (k + 1) / 3
            self._set_phase("scanning", f"SAMPLE: scanning as a radar, {frac * dur:.0f} of {dur:.0f} s of frames (shown compressed)",
                            sample=True, progress=round(frac, 2), sweep_s=dur)
            await self._hold(t0, 0.6)
        scan = self._decorate(copy.deepcopy(sample), room_hint=room)
        scan["t"] = round(self.clock(), 2)
        scan["engine"] = "fake"
        if self.fallback_reason:
            scan["fallback"] = self.fallback_reason
        return self._finish(scan, sample=True)

    # ---- sim engine: the real pipeline on simulated pucks
    async def _run_sim(self, m: dict, room: str, n: int, seed: int) -> dict:
        self.engine = "sim"
        loop = asyncio.get_running_loop()
        twin = self.twin
        r = twin.room(room)
        label, poly = r["label"], r["polygon"]
        door = room_door(twin, room)
        timing: dict = {}

        t0 = loop.time()
        self._set_phase("thrown", f"{self._who()} threw {n} pucks through the door of {label} (simulated pucks)", [{"id": f"P{i + 1}"} for i in range(n)])
        sc = await asyncio.to_thread(m["scene"].build_scene, twin, room, n, seed)
        self._truth = _jsonable(sc)
        await self._hold(t0, 1.5)

        t0 = loop.time()
        self._set_phase("ranging", f"Pucks ranging each other: {n * (n - 1) // 2} links of two-way ranging (simulated)")
        c0 = time.perf_counter()
        rec = await asyncio.to_thread(m["sim"].simulate, sc)
        timing["simulate_s"] = round(time.perf_counter() - c0, 2)
        await self._hold(t0, 2.0)

        t0 = loop.time()
        c0 = time.perf_counter()
        loc = await asyncio.to_thread(m["dsp"].self_locate, rec)
        timing["self_locate_s"] = round(time.perf_counter() - c0, 2)
        rel = [{"id": k, "x": _r(v[0]), "y": _r(v[1])} for k, v in sorted(loc["pucks_rel"].items())]
        self._set_phase("located", f"Self-located: {loc.get('n', len(rel))} pucks, mesh fits to {float(loc.get('rms_m', 0.0)):.2f} m RMS "
                                   "(the pucks' own frame: rotation and mirror still unknown)", rel, frame="rel")
        echoes = await asyncio.to_thread(m["dsp"].static_echoes, rec)
        room_geo = {"polygon": poly, "door": door["at"]} if door else {"polygon": poly, "door": None}
        reg = await asyncio.to_thread(m["register"].register, loc["pucks_rel"], echoes, room_geo)
        await self._hold(t0, 1.5)

        t0 = loop.time()
        pucks_xy = {k: [float(v[0]), float(v[1])] for k, v in reg["pucks"].items()}
        placed = [{"id": k, "x": _r(v[0]), "y": _r(v[1])} for k, v in sorted(pucks_xy.items())]
        self._set_phase("registered", reg.get("text") or f"Registered into the twin (residual {float(reg.get('residual_m', 0)):.2f} m)",
                        placed, frame="twin" if reg.get("ok") else "rel")
        await self._hold(t0, 1.0)

        dur = float(sc.get("duration_s", 12.0))
        show = 4.0
        c0 = time.perf_counter()
        work = asyncio.ensure_future(asyncio.to_thread(m["dsp"].find_victims, rec, pucks_xy, None, poly))
        steps = 4
        for k in range(steps):
            t0 = loop.time()
            frac = (k + 1) / steps
            self._set_phase("scanning", f"Scanning as a radar: {frac * dur:.0f} of {dur:.0f} s of simulated frames "
                                        f"(shown compressed, about {dur / show:.0f}x)", progress=round(frac, 2), sweep_s=dur)
            await self._hold(t0, show / steps)
        vic = await work
        timing["find_victims_s"] = round(time.perf_counter() - c0, 2)
        known = [{"at": [float(v["x"]), float(v["y"])]} for v in vic.get("victims") or [] if v.get("x") is not None]   # a located person is not debris
        mm = await asyncio.to_thread(lambda: m["mismatch"].find_mismatches(pucks_xy, echoes, poly, door=door["at"] if door else None,
                                                                            height_m=sc.get("height_m"), known=known))
        outline = {"polygon": poly, "source": "twin", "confidence": round(max(0.0, 1.0 - float(reg.get("ambiguity") or 0.0)), 2) if reg.get("ok") else 0.3}
        if not poly:
            box = m["register"].fit_box(loc["pucks_rel"], echoes)
            outline = {"polygon": box["polygon"], "source": "box-fit", "confidence": box.get("confidence"), "text": box.get("text")}

        scan = self._assemble(rec, sc, loc, reg, pucks_xy, vic, mm, outline, timing, dur, show, room)
        return self._finish(scan, sample=False)

    def _assemble(self, rec, sc, loc, reg, pucks_xy, vic, mm, outline, timing, dur, show, room) -> dict:
        twin = self.twin
        r = twin.room(room)
        meta = rec.get("meta") or {}
        sig = math.hypot(float(loc.get("rms_m") or 0.0), float(reg.get("residual_m") or 0.0))
        pucks = [{"id": k, "x": _r(v[0]), "y": _r(v[1]), "sigma_m": round(max(sig, 0.05), 2)} for k, v in sorted(pucks_xy.items())]
        pairs: dict = {}
        for rg in rec.get("ranges") or []:
            key = tuple(sorted((rg["a"], rg["b"])))
            p = pairs.setdefault(key, {"d": [], "los": []})
            p["d"].append(float(rg["d"]))
            p["los"].append(1.0 if rg.get("los", True) else 0.0)
        links = [{"a": a, "b": b, "d_m": round(sum(p["d"]) / len(p["d"]), 2), "used": True, "los_frac": round(sum(p["los"]) / len(p["los"]), 2)}
                 for (a, b), p in sorted(pairs.items())]
        per_link = {tuple(sorted((pl["tx"], pl["rx"]))): pl for pl in (vic.get("per_link") or [])}
        for lk in links:
            pl = per_link.get((lk["a"], lk["b"]))
            if pl is not None:
                lk["used"] = bool(pl.get("used", True))
        victims = []
        for v in vic.get("victims") or []:
            victims.append({k: (_jsonable(val) if not isinstance(val, (str, bool, type(None))) else val) for k, val in v.items()})
        noise = meta.get("noise") or sc.get("noise") or {}
        limits = list(BASE_LIMITS)
        limits.insert(0, "SIMULATED: no puck has been built; ranges and radar frames come from the simulator" +
                      (f" (noise model: {json.dumps(_jsonable(noise), sort_keys=True)})" if noise else ""))
        if vic.get("why"):
            limits.append(vic["why"])
        if len(pucks) == 2:
            limits.append("Two pucks give presence and range only, not a position")
        if not reg.get("ok"):
            limits.append("Registration into the twin failed: positions are in the pucks' own frame")
        elif not reg.get("confident", float(reg.get("ambiguity") or 0.0) < 0.6):
            limits.append("Registration is ambiguous (another pose fits almost as well): check the puck positions against the door")
        tap = meta.get("tap_ns")
        scan = {
            "type": "scan", "schema": "sandtable.scan/1", "id": f"scan-{self._n}", "t": round(self.clock(), 2),
            "floor": r.get("floor") or twin.exercise_floor, "room": room, "room_label": r["label"], "simulated": True, "engine": "sim",
            "rung": "cir-radar", "n_pucks": len(pucks), "pucks": pucks, "links": links,
            "registration": {"ok": bool(reg.get("ok")), "confident": reg.get("confident"), "method": reg.get("method"), "residual_m": _r(reg.get("residual_m")),
                             "ambiguity": _r(reg.get("ambiguity")), "scale": 1.0, "text": reg.get("text"), "transform": _jsonable(reg.get("transform") or {})},
            "victims": victims, "echo_map": _jsonable(vic.get("echo_map") or {}), "outline": _jsonable(outline),
            "mismatches": _jsonable(mm or []),
            "resolution": {"range_m": round(float(tap) * 0.299792458, 2) if tap else None, "bandwidth_mhz": meta.get("bandwidth_mhz"),
                           "tap_ns": tap, "carrier_mhz": meta.get("carrier_mhz")},
            "limits": limits,
            "timing": {**timing, "scan_s": dur, "shown_s": show, "compressed": True, "frame_hz": meta.get("frame_hz") or sc.get("frame_hz")},
        }
        return self._decorate(_jsonable(scan), room_hint=room)

    def _decorate(self, scan: dict, room_hint: str | None = None) -> dict:
        """What the session adds to every scan: geo for pucks and victims, victim ids, plain-words text, room and graph node."""
        twin = self.twin
        try:
            poly = (scan.get("outline") or {}).get("polygon") or twin.room(scan.get("room") or room_hint)["polygon"]
        except ValueError:
            poly = []
        for p in scan.get("pucks") or []:
            if p.get("x") is not None:
                lat, lon = twin.to_geo(p["x"], p["y"])
                p["geo"] = {"lat": round(lat, 7), "lon": round(lon, 7)}
        for i, v in enumerate(scan.get("victims") or []):
            v["id"] = v.get("id") or f"V{i + 1}"
            bpm = v.get("breathing_bpm")
            if v.get("moving"):
                what = "Moving person"
            elif v.get("state") == "breathing" and bpm:
                what = f"Still person, breathing about {round(float(bpm))} per minute"
            elif v.get("state") == "breathing":
                what = "Still person, breathing"
            else:
                what = "Echo of a possible person, no breathing seen"
            if v.get("x") is None:
                v.update({"room": scan.get("room"), "room_label": scan.get("room_label"), "node": None, "geo": None})
                extra = f" about {float(v['excess_m']):.1f} m of extra path" if v.get("excess_m") is not None else ""
                v["text"] = f"{what}: present{extra}, but not placed (too few pucks for a position)"
                continue
            loc = twin.locate(v["x"], v["y"])
            lat, lon = twin.to_geo(v["x"], v["y"])
            v.update({"room": loc["room"], "room_label": loc["room_label"], "node": loc["node"], "geo": {"lat": round(lat, 7), "lon": round(lon, 7)}})
            v["where"] = wall_text(twin, poly, v["x"], v["y"]) if poly else None
            v["text"] = f"{what}, {v['where']}" if v["where"] else what
        return scan

    def _finish(self, scan: dict, sample: bool) -> dict:
        self.scan = scan
        n_v = len([v for v in scan.get("victims") or [] if v.get("x") is not None])
        n_m = len(scan.get("mismatches") or [])
        found = (f"{n_v} person located" if n_v == 1 else f"{n_v} people located") + f", {n_m} mismatch flag" + ("" if n_m == 1 else "s")
        prefix = "SAMPLE: " if sample else ""
        self._set_phase("done", f"{prefix}Scan of {scan['room_label']} complete: {found}", [{"id": p["id"], "x": p["x"], "y": p["y"]} for p in scan["pucks"]],
                        sample=sample, frame="twin", scan=scan["id"])
        self.emit(scan)
        if self.incident is not None:
            self.incident.set_scan(scan, self.brief())
        return scan

    # ---- the agent's view
    def brief(self) -> dict:
        """DISCRETE, deterministic summary for the agent's prompt: ids, labels, words and whole numbers only.
        No floats (positions are rounded to 0.5 m and written as text, confidences as whole percents in steps of 10),
        no timestamps, stable ordering."""
        sc = self.scan
        if not sc:
            return {"scan": None}
        twin = self.twin
        try:
            poly = (sc.get("outline") or {}).get("polygon") or twin.room(sc["room"])["polygon"]
        except ValueError:
            poly = []

        def pct(c):
            return None if c is None else int(round(float(c) * 10)) * 10

        def word(c):
            return None if c is None else ("high" if c >= 0.7 else "medium" if c >= 0.4 else "low")

        def half(v):
            return round(float(v) * 2) / 2

        victims = []
        for v in sorted(sc.get("victims") or [], key=lambda q: q["id"]):
            b = {"id": v["id"], "room": v.get("room"), "room_label": v.get("room_label"), "node": v.get("node"), "state": v.get("state"),
                 "moving": bool(v.get("moving")), "breathing_bpm": int(round(float(v["breathing_bpm"]))) if v.get("breathing_bpm") else None,
                 "confidence": word(v.get("confidence")), "confidence_pct": pct(v.get("confidence"))}
            if v.get("x") is not None:
                hx, hy = half(v["x"]), half(v["y"])
                b["where"] = wall_text(twin, poly, hx, hy, step=0.5) if poly else None
                b["at"] = f"x {hx:.1f} m, y {hy:.1f} m (twin frame)"
                b["uncertainty"] = f"about {max(0.5, half(v.get('sigma_m') or 0.5)):.1f} m"
            else:
                b["where"], b["at"], b["uncertainty"] = None, None, "position unknown (range only)"
            victims.append(b)
        mismatches = []
        for mm in sorted(sc.get("mismatches") or [], key=lambda q: q.get("id", "")):
            w = mm.get("where") or {}
            if w.get("kind") == "point" and w.get("at"):
                hx, hy = half(w["at"][0]), half(w["at"][1])
                where = wall_text(twin, poly, hx, hy, step=0.5) if poly else f"x {hx:.1f} m, y {hy:.1f} m"
                desc = "an echo where the twin shows open floor (debris or an object not in the twin)"
            elif w.get("kind") == "segment" and w.get("a") and w.get("b"):
                mid = ((w["a"][0] + w["b"][0]) / 2, (w["a"][1] + w["b"][1]) / 2)
                near = min(walls(twin, poly), key=lambda q: _seg_dist(mid, q["a"], q["b"]))["name"] if poly else None
                where = f"the {near} wall" if near else None
                desc = "a wall of the twin whose echo is missing (opening, collapse, or a stale twin)"
            else:
                where, desc = None, mm.get("kind")
            if mm.get("kind") == "unexpected_reflector":
                desc = "an echo where the twin shows open floor (debris or an object not in the twin)"
            mismatches.append({"id": mm.get("id"), "kind": mm.get("kind"), "where": where, "means": desc, "confidence": word(mm.get("confidence")),
                               "confidence_pct": pct(mm.get("confidence"))})
        reg = sc.get("registration") or {}
        amb = reg.get("ambiguity")
        return {"scan": sc["id"], "floor": sc.get("floor"), "room": sc["room"], "room_label": sc["room_label"],
                "simulated": bool(sc.get("simulated")), "sample": bool(sc.get("sample")), "rung": sc.get("rung"), "pucks": int(sc.get("n_pucks") or len(sc.get("pucks") or [])),
                "registration": {"ok": bool(reg.get("ok")), "method": reg.get("method"),
                                 "confidence": word(None if amb is None else 1.0 - float(amb))},
                "outline": (sc.get("outline") or {}).get("source"),
                "victims": victims, "mismatches": mismatches, "limits": [str(x).split(" (noise model")[0] for x in sc.get("limits") or []]}
