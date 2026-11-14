"""Hub sources: where raw measurements come from (simulator, boards on serial/UDP, replay files).

Contract: docs/CONTRACTS.md section 3. A source calls ``emit(msg)`` for every raw message; the hub
stamps, de-duplicates, calibrates, solves and publishes.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("uwbkit.source")


class Source:
    """Base class for hub sources."""

    simulated: bool = False
    name: str = "?"            # goes into hello.source
    self_ticking: bool = False  # True: the source emits {"type":"tick"} itself (sim, replay)
    passthrough: bool = False   # True: messages are already final (replay); the hub does not re-solve

    async def run(self, emit) -> None:  # pragma: no cover - interface
        """Long-running coroutine. Never raises on I/O errors; returns only on cancellation."""
        raise NotImplementedError

    async def send_command(self, line: str, node: str | None = None) -> bool:
        """Send one command line to the board(s). Sources without a back-channel return False."""
        return False

    def info(self) -> dict:
        """Diagnostics for ``uwbkit doctor`` / ``GET /diag``."""
        return {"name": self.name}

    def describe(self) -> dict:
        """Optional hints for the hub's ``hello``/``anchors``: any of
        profile, rate_hz, dims, nodes, anchors, calibrated, room, scenario, noise."""
        return {}


class MultiSource(Source):
    """Several sources feeding one hub, e.g. a gateway board on USB plus boards on Wi-Fi UDP."""

    def __init__(self, sources: list[Source]):
        self.sources = sources
        self.name = "+".join(s.name for s in sources)
        self.simulated = any(s.simulated for s in sources)

    async def run(self, emit) -> None:
        await asyncio.gather(*(s.run(emit) for s in self.sources))

    async def send_command(self, line: str, node: str | None = None) -> bool:
        results = await asyncio.gather(*(s.send_command(line, node) for s in self.sources),
                                       return_exceptions=True)
        return any(r is True for r in results)

    def info(self) -> dict:
        return {"name": self.name, "sources": [s.info() for s in self.sources]}

    def describe(self) -> dict:
        out: dict = {}
        for s in self.sources:
            out.update(s.describe())
        return out


def make_source(spec: str, *, scenario=None, seed=None, loop=False, speed=1.0, proto="auto",
                id_map: dict | None = None) -> Source:
    """Build a source from a ``--source`` spec.

    ``sim`` | ``sim:<scenario>`` | ``serial:<port>[@baud]`` (port may be ``auto`` or a comma list)
    | ``udp:<host>:<port>`` | ``replay:<file.ndjson>`` | several joined with ``+``.
    """
    spec = (spec or "sim").strip()
    if "+" in spec and not spec.startswith("replay:"):
        return MultiSource([make_source(p, scenario=scenario, seed=seed, loop=loop, speed=speed, proto=proto,
                                        id_map=id_map)
                            for p in spec.split("+") if p.strip()])
    kind, _, rest = spec.partition(":")
    kind = kind.lower()
    if kind == "sim":
        from .sim_src import SimSource
        return SimSource(scenario or rest or "office-walk", seed=seed, speed=speed)
    if kind == "replay":
        from .replay import ReplaySource
        if not rest:
            raise ValueError("replay source needs a file: --source replay:session.ndjson")
        return ReplaySource(rest, loop=loop, speed=speed)
    if kind == "serial":
        try:
            from .serial_src import SerialSource
        except ImportError as e:  # pragma: no cover
            raise ValueError(f"serial source unavailable: {e}") from e
        port, _, baud = (rest or "auto").partition("@")
        return SerialSource(port or "auto", int(baud) if baud else 115200, proto=proto, id_map=id_map)
    if kind == "udp":
        try:
            from .udp_src import UdpSource
        except ImportError as e:  # pragma: no cover
            raise ValueError(f"udp source unavailable: {e}") from e
        host, _, port = (rest or "0.0.0.0:1801").rpartition(":")
        return UdpSource(host or "0.0.0.0", int(port or 1801), proto=proto, id_map=id_map)
    raise ValueError(f"unknown source {spec!r}: use sim | serial:<port> | udp:<host>:<port> | replay:<file>")


__all__ = ["Source", "MultiSource", "make_source"]
