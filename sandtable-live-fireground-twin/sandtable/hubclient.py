"""Link to the bench's shared uwb-kit hub.

Consumes the hub's stream exactly as vendor/uwbkit/INTERFACE.md specifies, using the
kit's own client. There is no ranging or solving code here: positions arrive solved.
"""
from __future__ import annotations

import asyncio
import logging

from uwbkit.client import AsyncStream, Kit

from . import config

log = logging.getLogger("sandtable.hub")


class HubLink:
    def __init__(self, incident, url: str | None = None):
        self.incident = incident
        self.url = url or config.UWBKIT_URL
        self.kit = Kit(config.hub_http_base(), timeout=2.0)
        self.hello: dict | None = None
        self.connected = False
        self.last_msg_t = 0.0
        self._pending: dict[str, tuple[float, float]] = {}
        self._busy: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        while True:
            try:
                async for msg in AsyncStream(self.url):
                    self.connected = True
                    self.last_msg_t = self._loop.time()
                    kind = msg.get("type")
                    if kind == "hello":
                        self.hello = msg
                        self.incident.on_hello(msg)
                    elif kind == "anchors":
                        self.incident.on_anchors(msg)
                    elif kind == "pos":
                        self.incident.on_pos(msg)
                    elif kind == "event":
                        self.incident.on_event(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                      # the kit client reconnects by itself; this is the last resort
                log.warning("hub stream error: %s", exc)
            self.connected = False
            self.incident.hub["connected"] = False
            await asyncio.sleep(1.0)

    def alive(self) -> bool:
        return bool(self._loop) and self.connected and (self._loop.time() - self.last_msg_t) < 3.0

    # --- simulator helpers (refused by the kit when the source is real boards) -------------
    def teleport(self, tag: str, x: float, y: float) -> None:
        """Non-blocking: keeps only the newest target per tag."""
        self._pending[tag] = (x, y)
        if tag in self._busy or not self._loop:
            return
        self._busy.add(tag)
        self._loop.run_in_executor(None, self._drain, tag)

    def _drain(self, tag: str) -> None:
        try:
            while tag in self._pending:
                x, y = self._pending.pop(tag)
                try:
                    self.kit.teleport(tag, x, y)
                except Exception as exc:
                    log.debug("teleport failed: %s", exc)
                    break
        finally:
            self._busy.discard(tag)
