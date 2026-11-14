"""Boards still running a vendor example sketch (so a tired owner sees ranges before reflashing).

Makerfabs ESP32 UWB (DW1000) examples built on the DW1000Ranging library print, per result:

    from: 1786\t Range: 2.43 m\t RX power: -75.31 dBm

(https://github.com/Makerfabs/Makerfabs-ESP32-UWB, example/tag/uwb_tag.ino and example/anchor:
``Serial.print("from: "); ...getShortAddress(), HEX; "\\t Range: "; getRange(); " m"; "\\t RX power: ";
getRXPower(); " dBm"``). The hex number is the OTHER device's short address. The reporting board
does not say who it is, so it is assumed to be ``T1`` and each short address becomes ``A1..`` in
order of first appearance (override: ``id_map={"1786": "A1", "self": "T1"}``).
"""
from __future__ import annotations

import re

_RE = re.compile(r"from:\s*([0-9A-Fa-f]{1,4})\s+Range:\s*(-?[\d.]+)\s*m(?:\s+RX power:\s*(-?[\d.]+)\s*dBm)?")
# Qorvo's DW3000 example apps (and Arduino ports of them) print one line per exchange: "DIST: 3.45 m".
# There is no peer address in it, so it is always the single link self -> A1: a smoke test only.
_DIST = re.compile(r"^\s*DIST:\s*(-?[\d.]+)\s*m\b")


class VendorExampleAdapter:
    name = "example"

    def __init__(self, id_map: dict | None = None):
        self.id_map = dict(id_map or {})
        self.me = self.id_map.get("self", "T1")
        self.peers: dict = {}

    def parse(self, line: str):
        m = _RE.search(line)
        if not m:
            d = _DIST.match(line)
            if d:
                try:
                    return [{"type": "range", "a": self.me, "b": self.id_map.get("peer", "A1"), "d": float(d.group(1))}]
                except ValueError:
                    return []
            return None
        addr = m.group(1).upper()
        peer = self.id_map.get(addr)
        if peer is None:
            if addr not in self.peers:
                self.peers[addr] = f"A{len(self.peers) + 1}"
            peer = self.peers[addr]
        try:
            d = float(m.group(2))
        except ValueError:
            return []
        msg = {"type": "range", "a": self.me, "b": peer, "d": d}
        if m.group(3):
            try:
                msg["rssi"] = float(m.group(3))
            except ValueError:
                pass
        return [msg]


_default = VendorExampleAdapter()


def parse(line: str):
    return _default.parse(line)
