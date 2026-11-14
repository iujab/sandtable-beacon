"""Simulator source: steps ``uwbkit.sim.Simulator`` at the scenario rate on the wall clock."""
from __future__ import annotations

import asyncio
import logging
import time

from . import Source

log = logging.getLogger("uwbkit.source.sim")
MAX_RATE_HZ = 100.0   # the hub steps the simulator at most this fast (real UWB kits do 10-100 Hz)


class SimSource(Source):
    simulated = True
    self_ticking = True

    def __init__(self, scenario="office-walk", seed=None, speed: float = 1.0):
        from ..sim import Simulator, load_scenario
        self._Simulator = Simulator
        self._load = load_scenario
        self._seed = seed
        self.sim = None
        self._changed = False
        self.set_scenario(scenario)
        if speed and speed != 1.0:
            self.sim.control("speed", float(speed))

    # -- control ----------------------------------------------------------
    def set_scenario(self, spec) -> None:
        """Load a built-in name, a path or an inline dict. Raises ValueError on a bad scenario."""
        scenario = self._load(spec)
        self.sim = self._Simulator(scenario, seed=self._seed)
        self.name = f"sim:{self.sim.name}"
        self._changed = True

    def describe(self) -> dict:
        s = self.sim
        return {
            "profile": s.profile, "rate_hz": min(MAX_RATE_HZ, float(s.rate_hz)), "dims": int(s.dims),
            "nodes": s.nodes, "anchors": s.anchors, "calibrated": "sim",
            "room": getattr(s, "room", {}) or {}, "scenario": s.name,
            "noise": getattr(s, "noise_model", None) or (s.scenario.get("noise") if hasattr(s, "scenario") else None),
            "description": (s.scenario.get("description") if hasattr(s, "scenario") else None),
        }

    def info(self) -> dict:
        s = self.sim
        return {"name": self.name, "scenario": s.name, "t_sim": round(float(s.t_sim), 3),
                "paused": bool(s.paused), "speed": float(s.speed)}

    # -- loop -------------------------------------------------------------
    async def run(self, emit) -> None:
        next_t = time.monotonic()
        while True:
            sim = self.sim
            now = time.time()
            if self._changed:
                self._changed = False
                emit({"type": "_reconfig"})
            try:
                msgs = sim.step(now)
            except Exception:  # never let a scenario bug kill the hub
                log.exception("simulator step failed")
                msgs = []
            for m in msgs:
                emit(m)
            emit({"type": "tick", "t": now})
            next_t += 1.0 / min(MAX_RATE_HZ, max(0.5, float(sim.rate_hz)))
            delay = next_t - time.monotonic()
            if delay < -1.0:  # fell far behind (laptop slept): resynchronise
                next_t = time.monotonic()
                delay = 0.0
            await asyncio.sleep(max(0.0, delay))
