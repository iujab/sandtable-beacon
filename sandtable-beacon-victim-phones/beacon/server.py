"""Beacon server: the feed of docs/BEACON_FEED.md over HTTP, WebSocket, SSE, GeoJSON and Cursor-on-Target.

    python -m beacon.demo                           (or: uvicorn beacon.server:app --port 1885)

Engines (BEACON_SOURCE, or create_app(source=...)):
    sim   the simulated world (beacon.sim.World) stepped at SIM_HZ, feeding BeaconSession (locator + tracker) at STEP_HZ
    live  real phones (the iOS app) post observations to POST /api/ingest; nothing is simulated
    kit   real pucks through the uwb-kit hub (beacon.source_kit, when it exists)
    fake  the hand-made sample replayed in phases, every message marked "sample": true

If the simulator or the locator cannot be imported, `sim` falls back to `fake`, logs it loudly and says SAMPLE in
`hello.notice` and in every Beacons object. It never pretends.

Structure (pattern copied from Sandtable's sandtable/server.py, with credit): one AppState holds the engine,
the session and the subscriber queues; `router` (an APIRouter) carries every route so Sandtable can mount it under
a prefix; `create_app()` adds CORS and the static mounts.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, feeds
from .twin import Twin, _pip

log = logging.getLogger("beacon.server")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[beacon] %(levelname)s %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)


# ============================================================================ the fake engine (hand-made sample)
class FakeEngine:
    """Plays fixtures/beacon/sample_beacons.json through the phases cold -> band -> ring -> candidates -> fix,
    PHASE_S seconds each, looping. Built from the sample's own objects; every message carries "sample": true."""

    PHASES = ("cold", "band", "ring", "candidates", "fix")
    PHASE_S = 4.0

    def __init__(self, twin: Twin, sample: dict, notice: str | None = None):
        self.twin = twin
        self.sample = sample
        self.notice = notice
        self.t0 = time.time()
        self.seq = 0
        self._frames = self._build()

    def restart(self) -> None:
        self.t0 = time.time()

    def phase(self, now: float) -> str:
        k = int(((now - self.t0) % (self.PHASE_S * len(self.PHASES))) // self.PHASE_S)
        return self.PHASES[k]

    def _arcs(self, cx: float, cy: float, r: float, room: str | None) -> list[list[float]]:
        if not room:
            return [[0.0, 360.0]]
        poly = self.twin.room(room)["polygon"]
        ok = [_pip((cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))), poly) for a in range(360)]
        arcs, start = [], None
        for a in range(361):
            v = ok[a] if a < 360 else False
            if v and start is None:
                start = a
            if not v and start is not None:
                arcs.append([float(start), float(a - 1)])
                start = None
        if len(arcs) >= 2 and arcs[0][0] == 0.0 and arcs[-1][1] == 359.0:
            arcs = [[arcs[-1][0], arcs[0][1] + 360.0]] + arcs[1:-1]
        return arcs or [[0.0, 360.0]]

    def _geo(self, x: float, y: float) -> dict:
        lat, lon = self.twin.to_geo(x, y)
        return {"lat": round(lat, 7), "lon": round(lon, 7)}

    def _build(self) -> dict[str, dict]:
        s = self.sample
        pucks = s["pucks"]
        fixp = next((p for p in s["phones"] if p.get("geometry") == "fix"), s["phones"][0])
        zonep = next((p for p in s["phones"] if p.get("geometry") == "zone"), None)
        p1 = pucks[0]
        nulls = {"x": None, "y": None, "sigma_m": None, "cov": None, "geo": None, "node": None}
        frames: dict[str, dict] = {}

        def base(n_pucks: int, phones: list, presence_pucks: int) -> dict:
            b = copy.deepcopy(s)
            b["pucks"] = copy.deepcopy(pucks[:n_pucks])
            ids = {p["id"] for p in b["pucks"]}
            b["presence"] = [c for c in copy.deepcopy(s["presence"]) if c.get("puck") in ids][:presence_pucks]
            b["phones"] = phones
            return b

        # cold: one puck, devices counted, no phone
        frames["cold"] = base(1, [], 1)
        # band: the phone starts its SOS; Bluetooth only -> a zone
        ph = copy.deepcopy(fixp)
        ph.update(nulls)
        ph.update({"method": "ble-rssi", "geometry": "zone", "confidence": 0.7, "room_prob": 0.7, "ring": None, "candidates": [], "pruned": []})
        zone = copy.deepcopy(zonep["zone"]) if zonep else {"room": s["room"], "room_label": s["room_label"], "prob": 0.7, "band": "mid",
                                                           "puck": p1["id"], "r_min_m": 2.0, "r_max_m": 6.0,
                                                           "polygon": self.twin.room(s["room"])["polygon"], "geo": None}
        zone["puck"] = p1["id"]
        ph["zone"] = zone
        ph["ranges"] = [{"puck": p1["id"], "kind": "ble", "d_m": 5.5, "sigma_m": 3.0, "rssi": -74.0, "los": None, "age_s": 0.4}]
        ph["text"] = f"Phone {ph['id']}: 2 people, cannot move, trapped. Bluetooth only: in {zone['room_label']} (probability 0.7), {zone['r_min_m']:g} to {zone['r_max_m']:g} m from puck {p1['id']}"
        frames["band"] = base(1, [ph], 1)
        # ring: UWB ranging to one puck
        r = math.hypot(fixp["x"] - p1["x"], fixp["y"] - p1["y"])
        ph = copy.deepcopy(fixp)
        ph.update(nulls)
        ph.update({"method": "uwb-twr", "geometry": "ring", "confidence": 0.9, "room": None, "room_label": None, "room_prob": None,
                   "candidates": [], "pruned": [], "zone": None,
                   "ring": {"puck": p1["id"], "cx": p1["x"], "cy": p1["y"], "r_m": round(r, 2), "sigma_m": 0.12,
                            "arcs": self._arcs(p1["x"], p1["y"], r, p1.get("room")), "kind": "uwb", "geo": self._geo(p1["x"], p1["y"])}})
        ph["ranges"] = [rg for rg in fixp["ranges"] if rg["puck"] == p1["id"]]
        ph["text"] = f"Phone {ph['id']}: 2 people, cannot move, trapped. UWB range only: {_half(r)} from puck {p1['id']}, on the arc the twin leaves inside {s['room_label']}; no position yet"
        frames["ring"] = base(1, [ph], 1)
        # candidates: the second puck lands -> two mirror points across the line P1-P2
        if len(pucks) >= 2:
            p2 = pucks[1]
            ux, uy = p2["x"] - p1["x"], p2["y"] - p1["y"]
            n = math.hypot(ux, uy) or 1.0
            ux, uy = ux / n, uy / n
            vx, vy = fixp["x"] - p1["x"], fixp["y"] - p1["y"]
            pr = vx * ux + vy * uy
            fx, fy = p1["x"] + pr * ux, p1["y"] + pr * uy
            mx, my = 2 * fx - fixp["x"], 2 * fy - fixp["y"]
            loc = self.twin.locate(mx, my)
            ph = copy.deepcopy(fixp)
            ph.update(nulls)
            ph.update({"method": "uwb-twr", "geometry": "candidates", "confidence": 0.8, "ring": None, "zone": None, "pruned": [],
                       "candidates": [
                           {"x": fixp["x"], "y": fixp["y"], "sigma_m": 0.35, "weight": 0.5, "room": fixp["room"], "room_label": fixp["room_label"], "geo": self._geo(fixp["x"], fixp["y"])},
                           {"x": round(mx, 2), "y": round(my, 2), "sigma_m": 0.35, "weight": 0.5, "room": loc["room"], "room_label": loc["room_label"], "geo": self._geo(mx, my)}]})
            same = loc["room"] == fixp["room"]
            ph["room"], ph["room_label"], ph["room_prob"] = (fixp["room"], fixp["room_label"], 0.95) if same else (None, None, None)
            ph["ranges"] = [rg for rg in fixp["ranges"] if rg["puck"] in (p1["id"], p2["id"])]
            ph["text"] = f"Phone {ph['id']}: 2 people, cannot move, trapped. Two possible spots, mirror images across the line between pucks {p1['id']} and {p2['id']}"
            frames["candidates"] = base(2, [ph], 2)
        else:
            frames["candidates"] = frames["ring"]
        frames["fix"] = copy.deepcopy(s)
        return frames

    def frame(self, now: float) -> dict:
        ph = self.phase(now)
        b = copy.deepcopy(self._frames[ph])
        self.seq += 1
        b.update({"seq": self.seq, "t": round(now, 3), "sample": True, "source": "fake", "simulated": True, "phase": ph})
        lim = ["SAMPLE: hand-made sample replayed in phases (cold, band, ring, candidates, fix); not the simulator and not a measurement."]
        if self.notice:
            lim.insert(0, self.notice)
        b["limits"] = lim + [x for x in (b.get("limits") or []) if not x.startswith("SAMPLE")]
        for coll in ("pucks", "phones", "presence"):
            for item in b.get(coll) or []:
                item["sample"] = True
                item["simulated"] = True
        return b


def _half(d: float) -> str:
    v = round(float(d) * 2) / 2
    return f"{v:g} m"


# ============================================================================ state
class AppState:
    def __init__(self, source: str | None = None, story: bool = False, scenario: str | None = None):
        self.requested = (source or config.BEACON_SOURCE).strip().lower()
        if self.requested not in config.SOURCES:
            raise ValueError(f"unknown BEACON_SOURCE {self.requested!r}: one of {config.SOURCES}")
        self.twin_raw = config.twin_raw()
        self.site_raw = config.site_raw()
        self.twin = Twin(self.twin_raw)
        self.labels = config.labels(self.site_raw, self.twin_raw)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()
        self.cot_clients: set[asyncio.Queue] = set()
        self.events: deque = deque(maxlen=200)
        self.latest: dict | None = None
        self.engine = self.requested
        self.notice: str | None = None
        self.session = None
        self.world = None
        self.fake: FakeEngine | None = None
        self.kit = None
        self.story = None
        self.autostory = story
        self.scenario = scenario
        self.started = False
        self._tasks: list[asyncio.Task] = []
        self._last_hello: str | None = None
        self._cot_keys: dict[str, str] = {}
        self._fake_key: str | None = None
        self._fake_last = 0.0
        self._lock = threading.Lock()
        self._init_engine()
        self.agent = None
        self.agent_note: str | None = None
        self._init_agent()

    # ---- engines
    def _sample(self) -> dict:
        return json.loads(config.SAMPLE_JSON.read_text(encoding="utf-8"))

    def _use_fake(self, notice: str | None = None) -> None:
        self.engine = "fake"
        self.notice = notice
        self.session, self.world = None, None
        self.fake = FakeEngine(self.twin, self._sample(), notice)

    def _make_session(self, source: str, simulated: bool, rungs: dict | None):
        from .session import BeaconSession
        return BeaconSession(self.twin_raw, self.site_raw, labels=self.labels, emit=self.emit, source=source,
                             simulated=simulated, rungs=rungs,
                             persist_path=config.PUCKS_LIVE_JSON if source == "live" else None)

    def _init_engine(self) -> None:
        req = self.requested
        if req == "fake":
            self._use_fake(None)
            return
        if req == "sim":
            try:
                from . import locate, track  # noqa: F401  (the real locator must be there for the sim engine)
                from .sim import World
                self.session = self._make_session("sim", True, {"cold": True, "ble": True, "uwb": True, "bearing": True})
                self.world = World(self.twin_raw, seed=config.SIM_SEED)
                self.engine = "sim"
            except Exception as exc:                                    # degrade loudly, never silently
                msg = (f"SAMPLE: the simulator or the locator could not be loaded ({type(exc).__name__}: {exc}); "
                       "playing the hand-made sample instead")
                log.error("=" * 72)
                log.error(msg)
                log.error("=" * 72)
                self._use_fake(msg)
            return
        if req == "live":
            self.session = self._make_session("live", False, {"cold": False, "ble": True, "uwb": True, "bearing": True})
            self.engine = "live"
            return
        if req == "kit":
            self.session = self._make_session("pucks", False, {"cold": True, "ble": True, "uwb": False, "bearing": False})
            self.engine = "kit"
            try:
                from . import source_kit
                self.kit = source_kit
            except Exception as exc:
                self.notice = (f"kit source not available ({type(exc).__name__}: {exc}): no puck stream is read; "
                               "POST /api/ingest still works")
                log.error(self.notice)

    @property
    def simulated(self) -> bool:
        if self.engine in ("sim", "fake"):
            return True
        return False

    @property
    def source(self) -> str:
        return {"sim": "sim", "fake": "fake", "live": "live", "kit": "pucks"}[self.engine]

    # ---- broadcasting (thread-safe: the session emits from the stepping thread)
    def emit(self, msg: dict) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            self._broadcast(msg)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._broadcast(msg)
        else:
            loop.call_soon_threadsafe(self._broadcast, msg)

    def _broadcast(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "beacons":
            self.latest = msg
        elif typ == "beacon_event":
            self.events.append(msg)
        elif typ == "reset":
            self.events.clear()
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
        if typ == "beacons":
            self._cot_tick(msg)

    def _cot_tick(self, b: dict) -> None:
        if not self.cot_clients:
            return
        events = feeds.cot_events(b, time.time(), twin=self.twin)
        keys = {uid: key for uid, _xml, key in events}
        changed = [xml for uid, xml, key in events if self._cot_keys.get(uid) != key]
        self._cot_keys = keys
        for xml in changed:
            for q in list(self.cot_clients):
                try:
                    q.put_nowait(xml)
                except asyncio.QueueFull:
                    pass

    def hello(self) -> dict:
        noise = None
        if self.simulated:
            try:
                from . import models
                noise = models.DEFAULT_NOISE
            except Exception:
                noise = None
        rungs = (self.latest or {}).get("rungs") if self.engine == "fake" else (self.session.rungs if self.session else None)
        h = {"type": "hello", "module": "sandtable-beacon", "v": 1, "simulated": self.simulated, "source": self.source,
             "engine": self.engine, "sample": self.engine == "fake", "rungs": rungs or {"cold": False, "ble": False, "uwb": False, "bearing": False},
             "labels": self.labels, "noise": noise, "notice": self.notice,
             "rig": {"scale": self.session.rig_scale if self.session else 1.0},
             "search_room": self.session.search_room_obj() if self.session else None}
        if self.story is not None:
            st = self.story.status()
            h["story"] = {k: st[k] for k in ("running", "done", "script", "step", "title", "error")}
        return h

    def beacons(self) -> dict:
        if self.latest is not None:
            return self.latest
        if self.engine == "fake":
            return self.fake.frame(time.time())
        return self.session.beacons()

    # ---- lifecycle
    async def startup(self) -> None:
        if self.started:
            return
        self.started = True
        self.loop = asyncio.get_running_loop()
        if self.engine == "fake":
            self._tasks.append(asyncio.create_task(self._fake_loop()))
        else:
            if self.engine == "sim":
                self._tasks.append(asyncio.create_task(self._sim_loop()))
            if self.engine == "kit" and self.kit is not None:
                self._tasks.append(asyncio.create_task(self._kit_loop()))
            self._tasks.append(asyncio.create_task(self._step_loop()))
            if self.engine == "sim":
                if self.autostory:
                    self.start_story(config.DEFAULT_SCENARIO)
                elif self.scenario:
                    try:
                        self.load_scenario(self.scenario)
                    except Exception as exc:
                        log.error("could not load scenario %r: %s", self.scenario, exc)
        self._tasks.append(asyncio.create_task(self._hello_loop()))

    async def shutdown(self) -> None:
        if self.story is not None:
            self.story.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self.started = False

    async def _hello_loop(self) -> None:
        while True:
            try:
                h = self.hello()
                key = json.dumps(h, sort_keys=True, default=str)
                if key != self._last_hello:
                    self._last_hello = key
                    self.emit(h)
            except Exception:
                log.exception("hello loop")
            await asyncio.sleep(0.5)

    async def _fake_loop(self) -> None:
        period = 1.0 / config.STEP_HZ
        last_phase = None
        while True:
            try:
                now = time.time()
                b = self.fake.frame(now)
                ph = b["phase"]
                if ph != last_phase:
                    if ph == "cold":
                        self.emit({"type": "reset", "sample": True})
                    text = {"cold": "SAMPLE: puck P1 lands; devices counted, no identities",
                            "band": "SAMPLE: an opt-in phone starts its SOS beacon (Bluetooth band)",
                            "ring": "SAMPLE: UWB ranging to one puck: a ring, no position",
                            "candidates": "SAMPLE: a second puck: two candidate points",
                            "fix": "SAMPLE: a third puck: one fix"}[ph]
                    name = {"cold": "pucks_changed", "band": "phone_new", "ring": "status_changed", "candidates": "pucks_changed", "fix": "phone_fix"}[ph]
                    pid = None if ph in ("cold", "candidates") else (b["phones"][0]["id"] if b["phones"] else None)
                    self.emit({"type": "beacon_event", "t": round(now, 3), "name": name, "id": pid, "text": text, "sample": True})
                    last_phase = ph
                self.emit(b)
            except Exception:
                log.exception("fake loop")
            await asyncio.sleep(period)

    async def _sim_loop(self) -> None:
        period = 1.0 / config.SIM_HZ
        while True:
            t0 = time.time()
            try:
                with self._lock:
                    obs = self.world.step(t0)
                for o in obs or []:
                    try:
                        self.session.ingest(o)
                    except ValueError as exc:
                        log.warning("simulator observation rejected: %s", exc)
            except Exception:
                log.exception("sim loop")
            await asyncio.sleep(max(0.0, period - (time.time() - t0)))

    async def _step_loop(self) -> None:
        period = 1.0 / config.STEP_HZ
        while True:
            t0 = time.time()
            try:
                await asyncio.to_thread(self.session.step, t0)
            except Exception:
                log.exception("session step")
            await asyncio.sleep(max(0.02, period - (time.time() - t0)))

    async def _kit_loop(self) -> None:
        """Real pucks through the kit hub. Expects beacon.source_kit.run(url, ingest) (async); see the report."""
        try:
            await self.kit.run(config.UWBKIT_URL, self.session.ingest)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.notice = f"kit source stopped: {type(exc).__name__}: {exc}"
            log.exception("kit loop")

    # ---- operations
    def require_session(self):
        if self.session is None:
            raise HTTPException(409, f"the {self.engine} engine is a hand-made sample: it has no session to change"
                                + (f" ({self.notice})" if self.notice else ""))
        return self.session

    def _init_agent(self) -> None:
        """The command agent (W4, beacon.agent.BeaconAgent), when it imports and there is a session; never silently."""
        if self.session is None:
            self.agent_note = "the command agent needs a session (the hand-made sample has none)"
            return
        try:
            from .agent import BeaconAgent
            self.agent = BeaconAgent(self.session, self.twin, self.site_raw, self.emit)
        except Exception as exc:
            self.agent = None
            self.agent_note = f"command agent not available ({type(exc).__name__}: {exc})"
            log.warning(self.agent_note)
            return

        def status():
            a = self.agent
            last = getattr(a, "last", None)
            if not last:
                return None
            return {"busy": bool(getattr(a, "busy", False)), "backend": getattr(a, "backend", None), "summary": last.get("summary")}
        self.session.agent_status = status

    def session_room(self) -> str | None:
        return self.session.room if self.session is not None else (self.latest or {}).get("room")

    def default_layout_room(self) -> str:
        """The session's room; else the configured fire room (BEACON_ROOM); else the first suite with a life-hazard tenant."""
        room = self.session_room()
        if room:
            return room
        try:
            r = self.twin.room(config.DEFAULT_ROOM)
            if r.get("kind") == "suite":
                return r["id"]
        except ValueError:
            pass
        floor = self.twin.exercise_floor
        idx = next((i for i, f in enumerate(self.twin.data["floors"]) if f["id"] == floor), 0)
        hazard = {str(t.get("suite")) for t in self.site_raw.get("tenants", []) if t.get("life_hazard") and t.get("floor") == idx + 1}
        for r in self.twin.rooms("suite"):
            if r.get("floor") == floor and str(r.get("suite")) in hazard:
                return r["id"]
        return next(r["id"] for r in self.twin.rooms("suite") if r.get("floor") == floor)

    def require_sim(self):
        if self.engine != "sim" or self.world is None:
            raise HTTPException(409, f"simulator only: the source is {self.source!r}" + (" (hand-made sample)" if self.engine == "fake" else ""))
        return self.world

    def reset(self) -> None:
        if self.story is not None:
            self.story.stop()
        if self.engine == "fake":
            self.fake.restart()
            self.emit({"type": "reset", "sample": True})
            return
        if self.world is not None:
            with self._lock:
                self.world.clear()
        self.session.reset()

    def throw_pucks(self, room: str, n: int, seed: int | None, strategy: str | None = None) -> list[dict]:
        """Simulated pucks: n == 2 (or a strategy) places the recommended layout (beacon.layout), otherwise a random throw."""
        world = self.require_sim()
        self.twin.room(room)
        if strategy is not None or n == 2:
            if n != 2:
                raise ValueError("a layout strategy places exactly 2 pucks")
            from . import layout
            lay = layout.recommend(self.twin, room, n=2, strategy=strategy or "door-wall")
            with self._lock:
                world.set_pucks([{"id": p["id"], "x": p["x"], "y": p["y"]} for p in lay["pucks"]])
                pucks = [dict(p) for p in world.pucks]
        else:
            with self._lock:
                world.throw_pucks(room, n=n, seed=seed)
                pucks = [dict(p) for p in world.pucks]
        floor = self.twin.room(room)["floor"]
        self.session.set_pucks(pucks, room=room, floor=floor)
        return pucks

    def set_pucks(self, pucks: list[dict], room: str | None, floor: str | None, merge: bool) -> dict:
        session = self.require_session()
        res = session.set_pucks(pucks, room=room, floor=floor, merge=merge)
        if self.world is not None:
            with self._lock:
                self.world.set_pucks([{k: p.get(k) for k in ("id", "x", "y", "sigma_m") if p.get(k) is not None} for p in session.pucks])
        return res

    def load_scenario(self, name: str) -> dict:
        world = self.require_sim()
        path = (config.SCENARIOS_DIR / f"{name}.json").resolve()
        if config.SCENARIOS_DIR.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"unknown scenario {name!r}")
        if self.story is not None:
            self.story.stop()
        self.session.reset()
        data = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            world.clear()
            world.load_scenario(data)
            pucks = [dict(p) for p in world.pucks]
        room = data.get("room") or (self.twin.locate(pucks[0]["x"], pucks[0]["y"])["room"] if pucks else None)
        if pucks:
            self.session.set_pucks(pucks, room=room, floor=data.get("floor"))
        return {"name": name, "room": room, "pucks": len(pucks), "title": data.get("title")}

    def start_story(self, script: str | None) -> dict:
        self.require_sim()
        from .story import Story
        if self.story is not None:
            self.story.stop()
        self.story = Story(self, script or config.DEFAULT_SCENARIO)
        self.story.start()
        return self.story.status()

    def sim_phone_key_eid(self, key: str) -> tuple[dict | None, str | None]:
        world = self.require_sim()
        with self._lock:
            truth = world.truth()
        for ph in truth.get("phones", []):
            if ph.get("key") == key:
                return ph, ph.get("eid")
        return None, None


STATE: AppState | None = None
_OPTIONS: dict[str, Any] = {}


async def get_state() -> AppState:
    """The running state; created and started lazily when the router is mounted in another app (Sandtable)."""
    global STATE
    if STATE is None:
        STATE = AppState(**_OPTIONS)
    if not STATE.started:
        await STATE.startup()
    return STATE


def S() -> AppState:
    assert STATE is not None
    return STATE


router = APIRouter()


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(400, "the body must be JSON")


def _obj(body: Any) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(400, "the body must be a JSON object")
    return body


# ---------------------------------------------------------------------------- read routes
@router.get("/healthz")
async def healthz():
    s = await get_state()
    b = s.beacons()
    return {"ok": True, "simulated": s.simulated, "source": s.source, "engine": s.engine, "sample": s.engine == "fake",
            "phones": len(b.get("phones") or []), "pucks": len(b.get("pucks") or []), "notice": s.notice}


@router.get("/api/beacons")
async def api_beacons():
    return (await get_state()).beacons()


@router.get("/api/brief")
async def api_brief():
    from .session import brief_from_beacons
    return brief_from_beacons((await get_state()).beacons())


@router.get("/api/accountability")
async def api_accountability():
    b = (await get_state()).beacons()
    acc = b.get("accountability")
    if acc is None:
        raise HTTPException(404, "no accountability: the building-record module (beacon.roster) is not available")
    return acc


@router.get("/api/directory")
async def api_directory():
    s = await get_state()
    try:
        from . import roster
    except Exception:
        return []
    try:
        return roster.app_directory(s.site_raw, s.twin)
    except Exception as exc:
        log.exception("roster.app_directory")
        raise HTTPException(500, f"directory failed: {exc}")


@router.get("/api/bundle")
async def api_bundle():
    s = await get_state()
    return {"twin": s.twin_raw, "site": s.site_raw, "labels": s.labels,
            "config": {"source": s.source, "engine": s.engine, "simulated": s.simulated, "sample": s.engine == "fake",
                       "notice": s.notice, "room": config.DEFAULT_ROOM, "floor": s.twin.exercise_floor,
                       "stale_s": config.STALE_S, "retention_s": config.RETENTION_S, "step_hz": config.STEP_HZ,
                       "sim_hz": config.SIM_HZ, "rig": {"scale": s.session.rig_scale if s.session else 1.0},
                       "scenarios": sorted(p.stem for p in config.SCENARIOS_DIR.glob("*.json")) if config.SCENARIOS_DIR.is_dir() else []}}


@router.get("/api/rig")
async def api_rig_get():
    s = await get_state()
    return {"scale": s.session.rig_scale if s.session else 1.0, "applies": s.engine == "live"}


@router.post("/api/rig")
async def api_rig_post(request: Request):
    s = await get_state()
    body = _obj(await _json_body(request))
    session = s.require_session()
    try:
        scale = float(body.get("scale"))
    except (TypeError, ValueError):
        raise HTTPException(400, "scale must be a number")
    try:
        session.set_rig_scale(scale)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "scale": session.rig_scale, "applies": s.engine == "live"}


@router.get("/api/debug/heat/{phone_id}")
async def api_heat(phone_id: str):
    s = await get_state()
    session = s.require_session()
    try:
        heat = await asyncio.to_thread(session.heat, phone_id, time.time())
    except KeyError:
        raise HTTPException(404, f"no phone {phone_id!r}")
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    if heat is None:
        raise HTTPException(404, f"no heat grid for {phone_id!r} (no usable ranges)")
    return heat


# ---------------------------------------------------------------------------- write routes
@router.post("/api/pucks")
async def api_pucks(request: Request):
    s = await get_state()
    body = _obj(await _json_body(request))
    room, floor = body.get("room"), body.get("floor")
    try:
        if room is not None:
            s.twin.room(room)
        if "pucks" in body:
            if not isinstance(body["pucks"], list):
                raise ValueError("pucks must be a list")
            res = s.set_pucks(body["pucks"], room, floor, bool(body.get("merge")))
            return {"ok": True, **res}
        if "n" in body:
            n = int(body["n"])
            if not 1 <= n <= 5:
                raise ValueError("n must be between 1 and 5 pucks")
            seed = body.get("seed")
            strategy = body.get("strategy")
            if strategy is not None and strategy not in ("door-wall", "exterior-wall", "careless"):
                raise ValueError("strategy must be door-wall, exterior-wall or careless")
            pucks = s.throw_pucks(room or s.session_room() or config.DEFAULT_ROOM, n, int(seed) if seed is not None else None, strategy)
            return {"ok": True, "pucks": s.session.pucks, "thrown": len(pucks), "room": room or s.session_room() or config.DEFAULT_ROOM,
                    "strategy": strategy or ("door-wall" if n == 2 else "throw")}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    raise HTTPException(400, "send {'pucks': [...]} or {'n': 1..5}")


@router.post("/api/reset")
async def api_reset():
    s = await get_state()
    s.reset()
    return {"ok": True}


@router.get("/api/ingest/example")
async def api_ingest_example():
    if not config.INGEST_EXAMPLE_JSON.is_file():
        raise HTTPException(404, "fixtures/beacon/ingest_example.json is missing")
    return json.loads(config.INGEST_EXAMPLE_JSON.read_text(encoding="utf-8"))


@router.post("/api/ingest")
async def api_ingest(request: Request):
    s = await get_state()
    session = s.require_session()
    body = await _json_body(request)
    try:
        return session.ingest_batch(body, origin="http", now=time.time())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/search_room")
async def api_search_room_get():
    s = await get_state()
    if s.session is None:
        return {"search_room": (s.latest or {}).get("search_room")}
    return {"search_room": s.session.search_room_obj()}


@router.post("/api/search_room")
async def api_search_room_post(request: Request):
    s = await get_state()
    session = s.require_session()
    body = _obj(await _json_body(request))
    room = body.get("room")
    if room is not None and not isinstance(room, str):
        raise HTTPException(400, "room must be a room id or null")
    try:
        return {"ok": True, "search_room": session.set_search_room(room or None)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/layout")
async def api_layout(room: str | None = None, n: int = 2, strategy: str = "door-wall"):
    """The recommended puck layout for a room (beacon.layout): drawn as 'place the pucks here' ghosts."""
    s = await get_state()
    if strategy not in ("door-wall", "exterior-wall", "careless"):
        raise HTTPException(400, "strategy must be door-wall, exterior-wall or careless")
    if n != 2:
        raise HTTPException(400, "layouts are for n=2 pucks")
    try:
        from . import layout
    except Exception as exc:
        raise HTTPException(501, f"layout module not available: {exc}")
    try:
        return layout.recommend(s.twin, room or s.default_layout_room(), n=n, strategy=strategy)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _app_link(request: Request, url: str | None) -> dict:
    from urllib.parse import quote
    base = (url or str(request.base_url)).strip()
    if not (base.startswith("http://") or base.startswith("https://")) or len(base) > 200:
        raise HTTPException(400, "url must be an http(s) base URL")
    base = base.rstrip("/")
    return {"link": "sandtablebeacon://server?url=" + quote(base, safe=""), "server": base}


@router.get("/api/app_link")
async def api_app_link(request: Request, url: str | None = None):
    """The link that opens the Beacon iOS app with this server filled in (the QR code below encodes it)."""
    return _app_link(request, url)


@router.get("/api/app_link.svg")
async def api_app_link_svg(request: Request, url: str | None = None):
    import io
    try:
        import segno
    except Exception as exc:
        raise HTTPException(501, f"segno (QR codes) is not installed: {exc}")
    link = _app_link(request, url)["link"]
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111", light="#fff",
                                     title="Open in Sandtable Beacon")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------- simulator routes (409 otherwise)
@router.post("/api/sim/phone")
async def api_sim_phone(request: Request):
    s = await get_state()
    world = s.require_sim()
    body = _obj(await _json_body(request))
    key = body.get("key")
    if not isinstance(key, str) or not key or len(key) > 64:
        raise HTTPException(400, "key must be a non-empty string (at most 64 characters)")
    try:
        if body.get("remove"):
            with s._lock:
                world.remove_phone(key)
            return {"ok": True, "removed": key}
        x, y = float(body["x"]), float(body["y"])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("x and y must be finite")
        report = body.get("report")
        if report is not None and not isinstance(report, dict):
            raise ValueError("report must be an object")
        with s._lock:
            ph = world.upsert_phone(key, x, y, floor=body.get("floor"), opt_in=bool(body.get("opt_in", True)),
                                    uwb=bool(body.get("uwb", True)), pointing=bool(body.get("pointing", False)), report=report)
    except (KeyError, TypeError) as exc:
        raise HTTPException(400, f"missing or bad field: {exc}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "phone": ph}


@router.get("/api/sim/phone/{key}")
async def api_sim_phone_get(key: str):
    s = await get_state()
    s.require_sim()
    truth, eid = s.sim_phone_key_eid(key)
    if truth is None:
        raise HTTPException(404, f"no simulated phone {key!r}")
    phone = s.session.phone_by_eid(eid) if eid else None
    return {"key": key, "truth": {"x": truth.get("x"), "y": truth.get("y"), "floor": truth.get("floor")},
            "phone": phone, "ack": s.session.ack(phone)}


@router.post("/api/sim/scenario")
async def api_sim_scenario(request: Request):
    s = await get_state()
    s.require_sim()
    body = _obj(await _json_body(request))
    try:
        return {"ok": True, **s.load_scenario(str(body.get("name") or config.DEFAULT_SCENARIO))}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/sim/story")
async def api_sim_story(request: Request):
    s = await get_state()
    s.require_sim()
    body = _obj(await _json_body(request))
    action = body.get("action", "start")
    if action == "stop":
        if s.story is not None:
            s.story.stop()
        return {"ok": True, "story": s.story.status() if s.story else None}
    if action != "start":
        raise HTTPException(400, "action must be 'start' or 'stop'")
    try:
        return {"ok": True, "story": s.start_story(body.get("script"))}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/sim/story")
async def api_sim_story_status():
    s = await get_state()
    return {"story": s.story.status() if s.story else None}


@router.get("/api/sim/truth")
async def api_sim_truth():
    """Ground truth, for evaluation scripts only, never for a board."""
    s = await get_state()
    world = s.require_sim()
    with s._lock:
        return world.truth()


# ---------------------------------------------------------------------------- feeds
@router.get("/feed/beacons.geojson")
async def feed_geojson():
    s = await get_state()
    fc = feeds.beacons_to_geojson(s.beacons(), twin=s.twin)
    return JSONResponse(fc, media_type="application/geo+json", headers={"Cache-Control": "no-store"})


@router.get("/feed/cot.xml")
async def feed_cot():
    s = await get_state()
    xml = feeds.beacons_to_cot(s.beacons(), time.time(), twin=s.twin)
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "no-store"})


@router.get("/feed/cot/stream")
async def feed_cot_stream(once: int = 0):
    """Server-Sent Events, one CoT <event> per message: everything current on connect, then each item when it changes.
    `?once=1` sends the current picture and closes."""
    s = await get_state()
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)

    def frame(xml: str) -> str:
        return "event: cot\ndata: " + xml.replace("\n", "\ndata: ") + "\n\n"

    async def gen():
        try:
            yield ': sandtable beacon cursor-on-target stream (how="m-s" marks simulated positions; not tested against a TAK server)\n\n'
            for _uid, xml, _key in feeds.cot_events(s.beacons(), time.time(), twin=s.twin):
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


# ---------------------------------------------------------------------------- streams
def _initial(s: AppState) -> list[dict]:
    return [s.hello(), s.beacons()]


@router.get("/events")
async def events(once: int = 0):
    """SSE: the same objects as /ws, one per `data:` line. `?once=1` sends hello and the latest Beacons object, then closes."""
    s = await get_state()
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)

    async def gen():
        try:
            for m in _initial(s):
                yield "data: " + json.dumps(m) + "\n\n"
            if once:
                return
            s.clients.add(q)
            while True:
                try:
                    m = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield "data: " + json.dumps(m) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            s.clients.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.websocket("/ws")
async def ws(sock: WebSocket):
    s = await get_state()
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)

    async def reader():
        while True:
            txt = await sock.receive_text()
            if txt.strip().lower() == "ping":
                try:
                    q.put_nowait({"type": "pong"})
                except asyncio.QueueFull:
                    pass

    rd = asyncio.create_task(reader())
    try:
        for m in _initial(s):
            await sock.send_text(json.dumps(m))
        s.clients.add(q)
        while True:
            get = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({get, rd}, return_when=asyncio.FIRST_COMPLETED)
            if rd in done:
                get.cancel()
                break
            await sock.send_text(json.dumps(get.result()))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        s.clients.discard(q)
        rd.cancel()


# ============================================================================ the app
def create_app(source: str | None = None, story: bool = False, scenario: str | None = None) -> FastAPI:
    """FastAPI app for port 1885: the router, CORS *, web/ at /, fixtures/ at /fixtures/ (read-only)."""
    global _OPTIONS
    opts = {"source": source, "story": story, "scenario": scenario}
    _OPTIONS = opts

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global STATE, _OPTIONS
        if STATE is not None and STATE.started:
            await STATE.shutdown()
        _OPTIONS = opts
        STATE = AppState(**opts)
        await STATE.startup()
        try:
            yield
        finally:
            await STATE.shutdown()

    app = FastAPI(title="Sandtable Beacon", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)
    app.mount("/fixtures", StaticFiles(directory=str(config.FIXTURES)), name="fixtures")
    if config.WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
    return app


# ---------------------------------------------------------------------------- command agent routes (W4's agent_routes)
def _get_session():
    s = S()
    return s.require_session()


def _get_agent():
    s = S()
    if s.agent is None:
        raise HTTPException(503, s.agent_note or "command agent not available")
    return s.agent


def _register_agent_routes() -> None:
    try:
        from .agent_routes import register
    except Exception as exc:
        note = f"agent routes not available ({type(exc).__name__}: {exc})"
        log.warning(note)

        @router.get("/api/agent")
        async def api_agent_missing():
            s = await get_state()
            return {"busy": False, "backend": None, "plan": None, "log": [], "available": False,
                    "notice": s.agent_note or note}

        @router.post("/api/agent/run")
        async def api_agent_run_missing():
            raise HTTPException(501, note)
        return
    register(router, _get_session, _get_agent)


_register_agent_routes()
app = create_app()
