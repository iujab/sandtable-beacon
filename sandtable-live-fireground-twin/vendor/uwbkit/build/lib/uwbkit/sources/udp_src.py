"""Wi-Fi UDP source: boards send line-protocol datagrams to the hub (default port 1801).

    --source udp:0.0.0.0:1801

One parser per sender address. The hub remembers which node id lives at which address, so
commands (``SURVEY``, ``ANTDLY`` ...) can be sent back to a board at the address it sends from.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..protocol import LineParser
from . import Source

log = logging.getLogger("uwbkit.source.udp")


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, src: "UdpSource", emit):
        self.src, self.emit = src, emit

    def datagram_received(self, data: bytes, addr) -> None:
        src = self.src
        peer = src.peers.get(addr)
        if peer is None:
            if len(src.peers) >= 64:      # not a UWB network: ignore strangers
                return
            peer = src.peers[addr] = {"parser": LineParser(src.proto, src.id_map), "nodes": set(), "last_t": None,
                                      "datagrams": 0, "banner": None}
        peer["datagrams"] += 1
        peer["last_t"] = time.time()
        if not data.endswith(b"\n"):
            data += b"\n"
        for m in peer["parser"].feed(data):
            typ = m.get("type")
            if typ == "node" and isinstance(m.get("id"), str):
                peer["nodes"].add(m["id"])
                src.node_addr[m["id"]] = addr
                if m.get("banner"):
                    peer["banner"] = {k: v for k, v in m.items() if k not in ("type", "banner")}
            elif typ == "range" and m.get("a") not in src.node_addr:
                src.node_addr[m["a"]] = addr      # best guess until the banner arrives
            self.emit(m)

    def error_received(self, exc) -> None:   # ICMP port unreachable etc.: harmless
        pass


class UdpSource(Source):
    def __init__(self, host: str = "0.0.0.0", port: int = 1801, proto: str = "auto", id_map: dict | None = None):
        self.host, self.port, self.proto, self.id_map = host, int(port), proto, id_map
        self.name = f"udp:{host}:{self.port}"
        self.peers: dict = {}
        self.node_addr: dict = {}
        self._transport = None
        self.error = None

    async def run(self, emit) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                self._transport, _ = await loop.create_datagram_endpoint(
                    lambda: _Proto(self, emit), local_addr=(self.host, self.port))
                self.error = None
                break
            except OSError as e:
                self.error = str(e)
                log.error("cannot listen on udp %s:%s (%s); retrying", self.host, self.port, e)
                await asyncio.sleep(2.0)
        log.info("listening for boards on udp %s:%s", self.host, self.port)
        try:
            await asyncio.Event().wait()
        finally:
            self._transport.close()
            self._transport = None

    async def send_command(self, line: str, node: str | None = None) -> bool:
        if self._transport is None:
            return False
        if node is None:
            addrs = list(self.peers)
        else:
            addrs = [self.node_addr[node]] if node in self.node_addr else list(self.peers)
        data = line.encode("ascii", "replace") + b"\n"
        for addr in addrs:
            self._transport.sendto(data, addr)
        return bool(addrs)

    def info(self) -> dict:
        now = time.time()
        senders = []
        for addr, p in self.peers.items():
            st = p["parser"].stats
            senders.append({"addr": f"{addr[0]}:{addr[1]}", "nodes": sorted(p["nodes"]), "datagrams": p["datagrams"],
                            "lines_ok": st["lines_ok"], "lines_bad": st["lines_bad"],
                            "bad_checksum": st["bad_checksum"], "lines_other": st["lines_other"],
                            "last_line_age_s": None if p["last_t"] is None else round(now - p["last_t"], 2),
                            "banner": p["banner"]})
        return {"name": self.name, "listening": self._transport is not None, "error": self.error,
                "lines_ok": sum(s["lines_ok"] for s in senders),
                "lines_bad": sum(s["lines_bad"] for s in senders),
                "bad_checksum": sum(s["bad_checksum"] for s in senders),
                "senders": senders}
