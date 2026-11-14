"""The scripted walk-through for TWO pucks (POST /api/sim/story, `python -m beacon.demo --story`). SIMULATED.

About 35 to 45 s, condition-based (each step waits until the published geometry shows what it is meant to show, with a
minimum dwell so people can read it, and a timeout so a noisy run never hangs):

  0. reset
  1. puck P1 is set down beside the door                    -> the cold device count appears (if the scene has devices)
  2. the trapped occupant's SOS arrives (UWB)               -> a ring around P1, clipped by the twin
  3. puck P2 lands carelessly in the middle of the room     -> two candidates; the board says it cannot choose
  4. the crew slides P2 along the door wall                 -> the mirror spot is now outside the room and is dismissed
     (beacon.layout "door-wall" position)                      (by the twin, or visibly on the occupant's stated suite): a fix
  5. the second phone (iPhone 15 in the scene) appears      -> resolves the same way
  6. the occupant who is already out is counted (safe outside)
  7. accountability and priorities settle
  8. the command agent runs (only when beacon.agent is wired into the server)

Positions, reports and the fire room come from the scenario file (fixtures/beacon/scenarios/<script>.json) and the
puck positions from beacon.layout; nothing here hard-codes a room or a coordinate. Roles: the trapped phone = the
first opt-in UWB phone in the fire room; the second phone = the first opt-in UWB phone in another room (else any
opt-in phone in another room); the one already out = the first phone whose report says safe_outside.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import config

log = logging.getLogger("beacon.story")

# Measured, not estimated: wall-clock length of the suite-310 walk-through, from the start request to "done", on the
# simulator with the replay/fallback agent (steps wait for conditions, so a run varies by a few seconds).
EXPECTED_S = 32.5
EXPECTED_BASIS = ("measured: two runs of suite-310 on the simulator, 2 pucks, reflex-fallback agent, start request to 'done': "
                  "32.5 s and 32.4 s (Sat Nov 14, after the round-2 fixes)")


class Story:
    def __init__(self, state, script: str | None = None):
        self.state = state
        self.script = script or config.DEFAULT_SCENARIO
        self.task: asyncio.Task | None = None
        self.step = -1
        self.title = ""
        self.t0: float | None = None
        self.log: list[dict] = []
        self.running = False
        self.done = False
        self.error: str | None = None
        self.duration_s: float | None = None
        self.scenario = self._load()

    # ---- scenario and layout
    def _load(self) -> dict:
        from . import layout
        from .sim import load_scenario_file
        tw0 = self.state.twin
        try:
            if self.script == "auto":
                from . import autoscene
                sc = autoscene.build(tw0)
            else:
                sc = load_scenario_file(self.script)
                named_room = sc.get("room") or (((sc.get("pucks") or {}).get("throw") or {}).get("room") if isinstance(sc.get("pucks"), dict) else None)
                if named_room and named_room not in {r["id"] for r in tw0.data.get("rooms", [])}:
                    # a fixture scene on another twin (BEACON_TWIN_JSON): build the scene for the twin that is loaded
                    from . import autoscene
                    sc = autoscene.build(tw0)
        except Exception as exc:
            raise ValueError(f"unknown walk-through script {self.script!r}: {exc}")
        pk = sc.get("pucks")
        room = sc.get("room") or (((pk or {}).get("throw") or {}).get("room") if isinstance(pk, dict) else None)
        if not room:
            raise ValueError(f"scenario {self.script!r} names no fire room")
        tw = self.state.twin
        tw.room(room)
        phones = sc.get("phones") or []
        opt = [p for p in phones if p.get("opt_in", True) and not (p.get("report") or {}).get("safe_outside") and not p.get("outside")]
        self.trapped = next((p for p in opt if p.get("uwb", True) and p.get("room") == room), None)
        if self.trapped is None:
            raise ValueError(f"scenario {self.script!r} has no opt-in UWB phone in {room}")
        others = [p for p in opt if p is not self.trapped]
        self.neighbour = next((p for p in others if p.get("uwb", True)), None) or next(iter(others), None)
        self.out = next((p for p in phones if (p.get("report") or {}).get("safe_outside")), None)
        self.room = room
        self.floor = sc.get("floor") or tw.room(room)["floor"]
        good = layout.recommend(tw, room, n=2, strategy="door-wall")
        careless = layout.recommend(tw, room, n=2, strategy="careless")
        self.p1 = dict(good["pucks"][0], id="P1")
        self.p2_good = dict(good["pucks"][1], id="P2")
        # the careless P2: the mid-room point of the comparison layout that is farther from P1
        cp = max(careless["pucks"], key=lambda q: (q["x"] - self.p1["x"]) ** 2 + (q["y"] - self.p1["y"]) ** 2)
        self.p2_careless = {"id": "P2", "x": cp["x"], "y": cp["y"]}       # refined in _careless_spot() when the locator is there
        self.layout = good
        return sc

    def _careless_spot(self) -> None:
        """A mid-room landing spot for P2 that leaves the trapped phone with two in-room candidates (the teaching moment:
        nothing can choose). The director knows the scenario's truth, like any scripted demo; the locator decides what is
        shown. Falls back to the comparison layout's mid-room point."""
        import math
        from . import locate
        from .twin import _pip, _poly_dist
        tw = self.state.twin
        poly = tw.room(self.room)["polygon"]
        cx, cy = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        pts = []
        x = min(xs) + 1.0
        while x <= max(xs) - 1.0:
            y = min(ys) + 1.0
            while y <= max(ys) - 1.0:
                if _pip((x, y), poly) and _poly_dist((x, y), poly)[0] >= 1.0 and math.dist((x, y), (self.p1["x"], self.p1["y"])) >= 2.5:
                    pts.append((x, y))
                y += 0.5
            x += 0.5
        pts.sort(key=lambda q: math.dist(q, (cx, cy)))
        import random
        ctx = locate.Context(tw, floor=self.floor)
        ph = (float(self.trapped["x"]), float(self.trapped["y"]))
        rnd = random.Random(310)
        shortlist = []
        for q in pts[:80]:
            pk = [self.p1, {"id": "P2", "x": q[0], "y": q[1]}]
            # the spot must leave two well separated in-room candidates not once but under range noise too
            ok, sep = 0, 0.0
            for trial in range(8):
                jitter = 0.0 if trial == 0 else 0.12
                # distance only, as a real iPhone session delivers it (no line-of-sight value)
                rs = [{"puck": p["id"], "kind": "uwb", "d_m": math.dist((p["x"], p["y"]), ph) + rnd.gauss(0.0, jitter), "n": 5} for p in pk]
                e = locate.estimate(ctx, pk, rs)
                c = e.get("candidates") or []
                if e.get("geometry") == "candidates" and len(c) == 2 and all(k.get("room") == self.room for k in c):
                    d = math.dist((c[0]["x"], c[0]["y"]), (c[1]["x"], c[1]["y"]))
                    if d >= 3.0:
                        ok += 1
                        sep = d if trial == 0 else sep
            if ok >= 7:
                shortlist.append((sep, q))
        if not shortlist:
            return
        # Second pass: play the short list through the real pipeline (simulated world, tracker, the phone's own key and
        # seed) and keep the spot where "two candidates" appears soonest and holds: the "cannot choose" moment must not
        # flicker on the board.
        shortlist.sort(reverse=True)
        best = None
        try:
            from . import sim
            from .track import PhoneTrack
            for _sep, q in shortlist[:6]:
                w = sim.World(tw, seed=int(getattr(self.state.world, "seed", config.SIM_SEED)))
                w.set_pucks([{"id": "P1", "x": self.p1["x"], "y": self.p1["y"]}])
                tp = self.trapped
                w.upsert_phone(tp["key"], ph[0], ph[1], opt_in=True, uwb=True, chip=tp.get("chip") or "U1", model=tp.get("model"),
                               report=dict(tp.get("report") or {}))
                tr, t, k, seq = None, 1000.0, 0, []

                def run(dur, keep):
                    nonlocal tr, t, k
                    end = t + dur
                    while t < end:
                        t += 0.1
                        k += 1
                        for o in w.step(t):
                            if o.get("eid") and o.get("kind") in ("uwb", "sos", "bearing", "report"):
                                if tr is None:
                                    tr = PhoneTrack(o["eid"])
                                tr.add(o)
                        if tr is not None and k % 3 == 0:
                            g = tr.estimate(ctx, w.pucks, t)["geometry"]
                            if keep:
                                seq.append(g)

                run(3.0, False)
                w.set_pucks([{"id": "P1", "x": self.p1["x"], "y": self.p1["y"]}, {"id": "P2", "x": q[0], "y": q[1]}])
                if tr is not None:
                    for pid in ("P1", "P2"):
                        tr.forget_puck(pid, t)
                run(7.0, True)
                tail = seq[5:]                      # after the first second and a half
                share = (sum(1 for g in tail if g == "candidates") / len(tail)) if tail else 0.0
                if best is None or share > best[0]:
                    best = (share, q)
                if share >= 0.99:
                    break
        except Exception:
            log.exception("careless-spot rehearsal failed; using the first short-listed spot")
        q = best[1] if best is not None and best[0] >= 0.8 else shortlist[0][1]
        self.p2_careless = {"id": "P2", "x": round(q[0], 2), "y": round(q[1], 2)}

    # ---- control
    def start(self) -> None:
        self.stop()
        self.running, self.done, self.error = True, False, None
        self.task = asyncio.get_running_loop().create_task(self._run())

    def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.running = False

    def status(self) -> dict:
        return {"running": self.running, "done": self.done, "script": self.script, "step": self.step, "title": self.title,
                "t_s": round(time.time() - self.t0, 1) if self.t0 else None, "log": self.log[-12:], "error": self.error,
                "pucks": 2, "simulated": True, "expected_s": EXPECTED_S, "expected_basis": EXPECTED_BASIS,
                "last_duration_s": self.duration_s}

    # ---- helpers
    def _say(self, step: int, title: str, text: str) -> None:
        self.step, self.title = step, title
        t = time.time()
        self.log.append({"step": step, "t_s": round(t - (self.t0 or t), 1), "title": title})
        self.state.emit({"type": "beacon_event", "t": round(t, 3), "name": "story", "id": None, "text": f"SIMULATED walk-through: {text}",
                         "step": step, "simulated": True})
        # with an external bundle the text may name rooms or tenants of a private building: keep it out of logs
        log.info("story step %d%s", step, "" if config.EXTERNAL_BUNDLE else ": " + str(text))

    def _sharing_note(self) -> str:
        """How the simulator lets two phones use one board (W2's sim_model.board_sharing), said plainly."""
        mode = ((getattr(self.state.world, "sim_model", None) or {}).get("board_sharing") or "pairs")
        if mode == "turns":
            return "(simulated taking turns: one board serves one phone at a time, so each phone's range is refreshed less often)"
        return "(simulated as a second board at each puck position: one board serves one phone at a time)"

    def _phone(self, key: str) -> dict | None:
        _truth, eid = self.state.sim_phone_key_eid(key)
        return self.state.session.phone_by_eid(eid) if eid else None

    def _geom(self, key: str) -> str | None:
        return (self._phone(key) or {}).get("geometry")

    async def _wait(self, cond, dwell: float, timeout: float) -> bool:
        """Wait at least `dwell` seconds and until cond() is true (at most `timeout` seconds). True when cond held."""
        t0 = time.time()
        while True:
            try:
                ok = bool(cond())
            except Exception:
                ok = False
            el = time.time() - t0
            if (ok and el >= dwell) or el >= timeout:
                return ok
            await asyncio.sleep(0.25)

    def _upsert(self, p: dict, **over) -> None:
        kw = {"floor": p.get("floor"), "opt_in": p.get("opt_in", True), "uwb": p.get("uwb", True), "pointing": p.get("pointing", False),
              "report": p.get("report")}
        for k in ("chip", "model", "camera_assist", "heading_deg"):
            if p.get(k) is not None:
                kw[k] = p[k]
        kw.update(over)
        with self.state._lock:
            self.state.world.upsert_phone(p["key"], float(p["x"]), float(p["y"]), **kw)

    def _place(self, pucks: list[dict]) -> None:
        with self.state._lock:
            self.state.world.set_pucks([{"id": p["id"], "x": p["x"], "y": p["y"]} for p in pucks])
            believed = [dict(p) for p in self.state.world.pucks]
        self.state.session.set_pucks(believed, room=self.room, floor=self.floor)

    def _setup_world(self) -> None:
        st = self.state
        created = getattr(st, "sim_created", None)
        if isinstance(created, dict):
            created.clear()                     # phones a page had created are gone with the world
        with st._lock:
            st.world.clear()
            # everything that only advertises: phones with no app, wearables, laptops, and the occupants' phones (app closed)
            for p in self.scenario.get("phones") or []:
                if p.get("outside"):
                    continue
                kw = {"advertises": p["advertises"]} if (not p.get("opt_in", True) and p.get("advertises") is not None) else \
                     ({} if not p.get("opt_in", True) else {"advertises": True})
                st.world.upsert_phone(p["key"], float(p["x"]), float(p["y"]), floor=p.get("floor"), opt_in=False, **kw)
            for cd in self.scenario.get("cold_devices") or []:
                if cd.get("n"):
                    st.world.add_cold_devices(cd["room"], int(cd["n"]), kind=cd.get("class"))
                else:
                    room = cd.get("room") or st.twin.locate(float(cd["x"]), float(cd["y"]))["room"]
                    st.world.add_cold_devices(room, 1, kind=cd.get("class", "phone"), positions=[[cd["x"], cd["y"]]],
                                              keys=[cd["key"]] if cd.get("key") else None)

    # ---- the script
    async def _run(self) -> None:
        st = self.state
        try:
            self.t0 = time.time()
            self.log = []
            try:
                await asyncio.to_thread(self._careless_spot)
            except Exception:
                log.exception("could not choose a careless spot; using the comparison layout")
            st.session.reset()
            self._setup_world()
            s = st.session
            tw = st.twin
            label = tw.room(self.room)["label"]
            try:
                s.set_search_room(None)
            except Exception:
                pass
            self._say(0, "reset", f"fire on floor {tw.floor(self.floor).get('label', self.floor)}, {label}. Two pucks, no puck down yet.")
            await asyncio.sleep(1.5)

            self._place([self.p1])
            self._say(1, "puck 1", f"puck P1 is set down beside the door of {label}: on its own it can only count Bluetooth devices")
            await self._wait(lambda: any(c["puck"] == "P1" for c in s.beacons()["presence"]), 3.0, 7.0)

            tk = self.trapped["key"]
            self._upsert(self.trapped)
            self._say(2, "SOS", "a trapped occupant's SOS arrives: one puck gives a ring around P1, clipped by the twin, no position")
            await self._wait(lambda: self._geom(tk) == "ring", 4.0, 9.0)

            self._place([self.p1, self.p2_careless])
            self._say(3, "puck 2, careless", "puck P2 lands in the middle of the room: two rings cross at two mirror spots, "
                                              "both inside the room; the board cannot choose")
            await self._wait(lambda: self._geom(tk) == "candidates", 5.0, 9.0)

            self._place([self.p1, self.p2_good])
            self._say(4, "puck 2, door wall", f"the crew slides P2 along the door wall ({self.layout['wall']}): the mirror spot now "
                                              f"falls outside the room and is dismissed: one fix")
            await self._wait(lambda: self._geom(tk) == "fix", 4.0, 9.0)
            if self._geom(tk) == "candidates":
                # The mirror spot is somewhere the twin allows (for example the next room of the same suite), and the
                # occupant's stated suite covers both spots: command decides, and the board shows it as an assumption.
                s.set_search_room(self.room)
                self._say(4, "search room", f"the mirror spot is in a place the twin allows, so command declares {label} the search room: "
                                            f"the spot outside it is dismissed, and shown as dismissed")
                await self._wait(lambda: self._geom(tk) == "fix", 3.0, 6.0)

            if self.neighbour is not None:
                nk = self.neighbour["key"]
                self._upsert(self.neighbour)
                nlabel = tw.room(self.neighbour["room"])["label"] if self.neighbour.get("room") else "the next room"
                model = self.neighbour.get("model") or "phone"
                self._say(5, "second phone", f"a second phone ({model}) reports from {nlabel}: it resolves the same way "
                                             f"{self._sharing_note()}")
                got = await self._wait(lambda: self._geom(nk) == "fix", 4.0, 8.0)
                if not got:
                    # No bearing fallback: an iPhone 14 or 15 reports no direction to an accessory. A ring stays a ring.
                    g = self._geom(nk)
                    self._say(5, "second phone", "only one puck reaches that phone, so it stays a ring: a third puck would decide"
                              if g == "ring" else f"that phone stays {g}: the board does not guess")

            if self.out is not None:
                ok_key = self.out["key"]
                self._upsert(self.out)
                self._say(6, "safe outside", f"an occupant of {label} pressed 'I am out': counted as accounted for, not searched for")
                await self._wait(lambda: ((self._phone(ok_key) or {}).get("report") or {}).get("safe_outside"), 3.0, 10.0)

            self._say(7, "accountability", "the building record's tenant roster against the phones: who is still unaccounted for")
            await asyncio.sleep(4.0)

            agent = getattr(st, "agent", None)
            if agent is not None and hasattr(agent, "run"):
                self._say(8, "agent", "the command agent reads the phones and the record and proposes search priorities")
                try:
                    await asyncio.wait_for(agent.run("walk-through"), timeout=90.0)
                except asyncio.TimeoutError:
                    log.warning("the command agent did not finish within 90 s")
                except Exception:
                    log.exception("the command agent failed during the walk-through")
            self.done = True
            self.duration_s = round(time.time() - self.t0, 1)
            self._say(9, "done", f"walk-through complete ({self.duration_s:g} s)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("walk-through failed")
        finally:
            self.running = False
