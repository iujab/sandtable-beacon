"""The scripted walk-through (POST /api/sim/story, `python -m beacon.demo --story`). SIMULATED.

About 30 to 40 s, condition-based (each step waits until the published geometry shows what it is meant to show, with a
minimum dwell so people can see it, and a timeout so a noisy run never hangs):

  0. reset
  1. puck 1 lands in the fire room                          -> the cold device count appears
  2. the trapped occupant's phone starts its SOS            -> Bluetooth only: a band / zone
  3. its UWB ranging starts                                 -> a thin ring, clipped by the twin
  4. puck 2 lands                                           -> two candidates (or one, after the twin prunes)
  5. puck 3 lands (if still no fix: the occupant points the phone at a puck)   -> one fix
  6. a second phone reports from the next suite (Bluetooth only)
  7. an occupant of the fire suite who is already out says so (safe outside)
  8. accountability and priorities settle

Positions, reports and the fire room come from the scenario file (fixtures/beacon/scenarios/<script>.json, written by
the simulator's builder); nothing here hard-codes a room or a coordinate. The roles are picked from the scenario:
the trapped phone = the first opt-in UWB phone in the fire room, the neighbour = the first opt-in phone in another room,
the one already out = the first phone whose report says safe_outside.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import config

log = logging.getLogger("beacon.story")


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
        self.scenario = self._load()

    # ---- scenario
    def _load(self) -> dict:
        from .sim import load_scenario_file
        try:
            sc = load_scenario_file(self.script)
        except Exception as exc:
            raise ValueError(f"unknown walk-through script {self.script!r}: {exc}")
        room = sc.get("room") or ((sc.get("pucks") or {}).get("throw") or {}).get("room")
        if not room:
            raise ValueError(f"scenario {self.script!r} names no fire room")
        self.state.twin.room(room)
        phones = sc.get("phones") or []
        opt = [p for p in phones if p.get("opt_in", True) and not (p.get("report") or {}).get("safe_outside")]
        self.trapped = next((p for p in opt if p.get("uwb", True) and p.get("room") == room), None) or next(iter(opt), None)
        if self.trapped is None:
            raise ValueError(f"scenario {self.script!r} has no opt-in phone to find")
        self.neighbour = next((p for p in opt if p is not self.trapped and p.get("room") not in (None, room)), None)
        self.out = next((p for p in phones if (p.get("report") or {}).get("safe_outside")), None)
        self.room = room
        self.floor = sc.get("floor") or self.state.twin.room(room)["floor"]
        th = (sc.get("pucks") or {}).get("throw") if isinstance(sc.get("pucks"), dict) else None
        self.n_pucks = int((th or {}).get("n", 3)) if th else max(3, len(sc.get("pucks") or []))
        self.n_pucks = max(3, min(5, self.n_pucks))
        self.seed = (th or {}).get("seed", sc.get("seed"))
        self.explicit_pucks = sc.get("pucks") if isinstance(sc.get("pucks"), list) else None
        return sc

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
                "simulated": True}

    # ---- helpers
    def _say(self, step: int, title: str, text: str) -> None:
        self.step, self.title = step, title
        t = time.time()
        self.log.append({"step": step, "t_s": round(t - (self.t0 or t), 1), "title": title})
        self.state.emit({"type": "beacon_event", "t": round(t, 3), "name": "story", "id": None, "text": f"SIMULATED walk-through: {text}",
                         "step": step, "simulated": True})
        log.info("story step %d: %s", step, text)

    def _phone(self, key: str) -> dict | None:
        _truth, eid = self.state.sim_phone_key_eid(key)
        return self.state.session.phone_by_eid(eid) if eid else None

    async def _wait(self, cond, dwell: float, timeout: float) -> bool:
        """Wait at least `dwell` seconds and until cond() is true (at most `timeout` seconds). True when cond held."""
        t0 = time.time()
        ok = False
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

    def _land(self, k: int) -> None:
        with self.state._lock:
            self.state.world.set_pucks(self.true_pucks[:k])
            believed = [dict(p) for p in self.state.world.pucks]
        self.state.session.set_pucks(believed, room=self.room, floor=self.floor)

    # ---- the script
    async def _run(self) -> None:
        st = self.state
        try:
            self.t0 = time.time()
            self.log = []
            st.session.reset()
            with st._lock:
                st.world.clear()
                if self.explicit_pucks:
                    st.world.set_pucks(self.explicit_pucks)
                else:
                    st.world.throw_pucks(self.room, n=self.n_pucks, seed=self.seed)
                self.true_pucks = [{"id": p["id"], "x": p["x"], "y": p["y"], "floor": p.get("floor")} for p in st.world.truth()["pucks"]]
                st.world.set_pucks([])
                # everything that only advertises: phones with no app, wearables, laptops; the occupants' phones too, app closed
                for p in self.scenario.get("phones") or []:
                    if p.get("outside"):
                        continue
                    if not p.get("opt_in", True):
                        st.world.upsert_phone(p["key"], float(p["x"]), float(p["y"]), floor=p.get("floor"), opt_in=False,
                                              **({"advertises": p["advertises"]} if p.get("advertises") is not None else {}))
                    else:
                        st.world.upsert_phone(p["key"], float(p["x"]), float(p["y"]), floor=p.get("floor"), opt_in=False, advertises=True)
                for cd in self.scenario.get("cold_devices") or []:
                    if cd.get("n"):
                        st.world.add_cold_devices(cd["room"], int(cd["n"]), kind=cd.get("class"))
                    else:
                        room = cd.get("room") or st.twin.locate(float(cd["x"]), float(cd["y"]))["room"]
                        st.world.add_cold_devices(room, 1, kind=cd.get("class", "phone"), positions=[[cd["x"], cd["y"]]],
                                                  keys=[cd["key"]] if cd.get("key") else None)
            label = st.twin.room(self.room)["label"]
            self._say(0, "reset", f"fire on floor {st.twin.floor(self.floor).get('label', self.floor)}, {label}. No puck yet.")
            await asyncio.sleep(1.5)

            s = st.session
            self._land(1)
            self._say(1, "puck 1", f"puck P1 lands in {label}: it can only count Bluetooth devices (no identities)")
            await self._wait(lambda: any(c["puck"] == "P1" for c in s.beacons()["presence"]), 3.0, 8.0)

            tk = self.trapped["key"]
            self._upsert(self.trapped, uwb=False)
            self._say(2, "SOS", "an occupant's app starts its SOS beacon: status and a Bluetooth signal-strength band")
            await self._wait(lambda: (self._phone(tk) or {}).get("geometry") not in (None, "none"), 3.0, 8.0)

            self._upsert(self.trapped)
            self._say(3, "UWB", "the phone has UWB: ranging to P1 gives a thin ring, clipped by the twin")
            await self._wait(lambda: (self._phone(tk) or {}).get("geometry") == "ring" and str((self._phone(tk) or {}).get("method", "")).startswith("uwb"), 3.5, 8.0)

            self._land(2)
            self._say(4, "puck 2", "puck P2 lands: two rings cross at two mirror-image candidates; the twin removes what is impossible")
            await self._wait(lambda: (self._phone(tk) or {}).get("geometry") in ("candidates", "fix"), 4.0, 8.0)

            if self.n_pucks >= 3:
                self._land(3)
                self._say(5, "puck 3", "puck P3 lands: three rings meet at one point")
                got = await self._wait(lambda: (self._phone(tk) or {}).get("geometry") == "fix", 3.0, 8.0)
            else:
                got = False
            if not got:
                self._upsert(self.trapped, pointing=True)
                self._say(5, "bearing", "the occupant holds the phone up toward a puck: its direction turns the ring into a point")
                await self._wait(lambda: (self._phone(tk) or {}).get("geometry") == "fix", 3.0, 8.0)

            if self.neighbour is not None:
                nk = self.neighbour["key"]
                self._upsert(self.neighbour)
                nlabel = st.twin.room(self.neighbour["room"])["label"] if self.neighbour.get("room") else "the next room"
                self._say(6, "neighbour", f"a second phone reports from {nlabel}: Bluetooth only, through the wall")
                await self._wait(lambda: self._phone(nk) is not None, 3.0, 8.0)

            if self.out is not None:
                self._upsert(self.out)
                self._say(7, "safe outside", "an occupant of the fire suite pressed 'I am out': accounted for, not searched for")
                ok_key = self.out["key"]
                await self._wait(lambda: ((self._phone(ok_key) or {}).get("report") or {}).get("safe_outside"), 3.0, 10.0)

            self._say(8, "accountability", "the building record's tenant roster against the phones: who is still unaccounted for")
            await asyncio.sleep(4.0)
            self.done = True
            self._say(9, "done", "walk-through complete")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("walk-through failed")
        finally:
            self.running = False
