"""Replay source: plays back an NDJSON recording made with ``POST /record`` (or ``--record``).

Messages are passed through unchanged except that ``t`` is shifted onto the hub's clock, so a
replayed session looks live. ``hello.simulated`` is preserved from the recording; a file without
a ``hello`` is treated as simulated (unknown provenance is never presented as real).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from . import Source

log = logging.getLogger("uwbkit.source.replay")


def read_ndjson(path: str) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and "type" in m:
                out.append(m)
    return out


class ReplaySource(Source):
    self_ticking = True
    passthrough = True

    def __init__(self, path: str, loop: bool = False, speed: float = 1.0):
        self.path = path
        self.loop = loop
        self.speed = max(0.05, float(speed or 1.0))
        if not os.path.exists(path):
            raise ValueError(f"replay file not found: {path}")
        self.messages = read_ndjson(path)
        if not self.messages:
            raise ValueError(f"replay file has no messages: {path}")
        self.name = f"replay:{os.path.basename(path)}"
        self._hello = next((m for m in self.messages if m.get("type") == "hello"), None)
        self._anchors = next((m for m in self.messages if m.get("type") == "anchors"), None)
        self.simulated = bool(self._hello.get("simulated", True)) if self._hello else True
        self.recorded_source = self._hello.get("source") if self._hello else None
        self.position = 0
        self.loops = 0

    def describe(self) -> dict:
        d: dict = {}
        h = self._hello or {}
        for k in ("profile", "rate_hz", "dims", "nodes", "room", "frame"):
            if k in h:
                d[k] = h[k]
        if isinstance(h.get("sim"), dict):
            d["scenario"] = h["sim"].get("scenario")
            d["noise"] = h["sim"].get("noise")
        if self._anchors:
            d["anchors"] = self._anchors.get("anchors", {})
            d["calibrated"] = self._anchors.get("calibrated", "manual")
        if not d.get("nodes"):  # hand-made file: infer nodes from the traffic
            nodes: dict = {}
            for m in self.messages:
                for key in ("a", "b", "id"):
                    v = m.get(key)
                    if isinstance(v, str) and v not in nodes and v != "hub":
                        nodes[v] = {"role": "anchor" if v.startswith("A") else "mesh" if v.startswith("N") else "tag"}
            d["nodes"] = dict(sorted(nodes.items()))
        d["recorded_source"] = self.recorded_source
        return d

    def info(self) -> dict:
        return {"name": self.name, "messages": len(self.messages), "position": self.position,
                "loops": self.loops, "loop": self.loop, "speed": self.speed}

    async def run(self, emit) -> None:
        timed = [m for m in self.messages if isinstance(m.get("t"), (int, float))]
        if not timed:
            log.warning("replay file has no timed messages")
            await asyncio.Event().wait()
        t0 = timed[0]["t"]
        while True:
            start = time.time()
            first_hello_skipped = False
            for i, m in enumerate(self.messages):
                self.position = i
                typ = m.get("type")
                if typ == "hello":
                    first_hello_skipped = True
                    continue  # the hub sends its own hello built from describe()
                out = dict(m)
                if isinstance(m.get("t"), (int, float)):
                    due = start + (m["t"] - t0) / self.speed
                    delay = due - time.time()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    out["t"] = round(due, 4)
                emit({"type": "_pub", "msg": out})
            self.loops += 1
            if not self.loop:
                emit({"type": "_pub", "msg": {"type": "event", "t": round(time.time(), 3), "id": "hub",
                                             "name": "replay_end", "data": {"file": os.path.basename(self.path)}}})
                await asyncio.Event().wait()  # keep the hub alive; state stays queryable
            await asyncio.sleep(0.2)
