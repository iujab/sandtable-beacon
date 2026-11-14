"""Makerfabs MaUWB (ESP32-S3 + DW3000 behind an STM32 running Makerfabs' AT-command firmware).

The module reports ranges by itself once configured (``AT+SETRPT=1``). The line format changed
between firmware versions; all three are accepted:

    AT+RANGE=tid:1,mask:04,seq:63,range:(0,0,30,0,0,0,0,0),rssi:(0.00,0.00,-77.93,0.00,0.00,0.00,0.00,0.00)
        (comment in Makerfabs' example sketch example/esp32s3_at_t0 / get_range)
    AT+RANGE=tid:x1,mask:x2,seq:x3,range:(8 values),rssi:(8 values),ancid:(8 values)
        (AT command manual V1.1.1, section 3.15)
    AT+RANGE=tid:0,mask:01,seq:205,range:(90,0,0,0,0,0,0,0),ancid:(0,-1,-1,-1,-1,-1,-1,-1)
        (AT command manual V1.1.2, section 3.15: RSSI removed)

Source: https://github.com/Makerfabs/MaUWB_ESP32S3-with-STM32-AT-Command (README, example sketches
and the "Makerfabs UWB AT Module AT Command Manual" PDF). ``tid`` = tag id (decimal), ``mask`` =
hex bitmask of valid slots, ``seq`` = tag sequence, ``range`` = centimetres per anchor slot,
``ancid`` = the device id of the anchor in each slot (-1 = none).

Mapping: ``tid`` n -> ``T<n+1>``; anchor id n (from ``ancid``, else the slot index) -> ``A<n+1>``.
Never run against a real module in this build: format taken from the documents above.
"""
from __future__ import annotations

import re

_RE = re.compile(r"AT\+RANGE=tid:(\d+),mask:([0-9A-Fa-f]+),seq:(\d+),range:\(([^)]*)\)"
                 r"(?:,rssi:\(([^)]*)\))?(?:,ancid:\(([^)]*)\))?")


def _nums(s, conv):
    out = []
    for tok in (s or "").split(","):
        try:
            out.append(conv(tok.strip()))
        except ValueError:
            out.append(None)
    return out


class MaUwbAdapter:
    name = "mauwb"

    def __init__(self, id_map: dict | None = None):
        self.id_map = id_map or {}
        self.seen = False

    def parse(self, line: str):
        m = _RE.search(line)
        if not m:
            if self.seen and (line.startswith("AT+") or line in ("OK", "ERROR")):
                return []   # command echo / acknowledgement from the module
            return None
        self.seen = True
        tid, mask, seq = int(m.group(1)), int(m.group(2), 16), int(m.group(3))
        ranges = _nums(m.group(4), int)
        rssi = _nums(m.group(5), float) if m.group(5) else []
        ancid = _nums(m.group(6), int) if m.group(6) else []
        tag = self.id_map.get(f"tag{tid}", f"T{tid + 1}")
        out = []
        for i, cm in enumerate(ranges):
            if cm is None or cm <= 0 or not (mask >> i) & 1:
                continue
            aid = ancid[i] if i < len(ancid) and ancid[i] is not None and ancid[i] >= 0 else i
            msg = {"type": "range", "a": tag, "b": self.id_map.get(f"anchor{aid}", f"A{aid + 1}"),
                   "d": cm / 100.0, "bseq": seq}
            if i < len(rssi) and rssi[i] is not None and rssi[i] < 0:
                msg["rssi"] = rssi[i]
            out.append(msg)
        return out


_default = MaUwbAdapter()


def parse(line: str):
    """Module-level convenience: one line -> list of raw messages, or None if not this format."""
    return _default.parse(line)
