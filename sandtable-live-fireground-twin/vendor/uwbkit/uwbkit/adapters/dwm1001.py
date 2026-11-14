"""Qorvo / Decawave DWM1001-DEV (MDEK1001) running the stock PANS firmware: UART shell output.

Open the shell with two Enter presses, then type ``les`` or ``lec`` on a TAG module. Formats:

    les   1151[5.00,8.00,2.25]=6.48 0CA8[0.00,8.00,2.25]=6.51 111C[5.00,0.00,2.25]=3.18 1150[0.00,0.00,2.25]=3.16 le_us=2576 est[2.57,1.98,1.68,100]
          (sample quoted on the Qorvo forum, e.g. https://forum.qorvo.com/t/use-les-to-show-anchor-tag-rangings/11521;
           per anchor: <4-hex id>[x,y,z]=<distance m>; est[x,y,z,quality] is the module's own position)
    lec   DIST,4,AN0,1151,5.00,8.00,2.25,6.44,AN1,0CA8,0.00,8.00,2.25,6.50,AN2,111C,5.00,0.00,2.25,3.24,AN3,1150,0.00,0.00,2.25,3.19,POS,2.55,2.01,1.71,98
          (DWM1001 Firmware API Guide, shell command "lec"; this line shape is recalled from that guide
           and was NOT re-fetched in this build: treat as likely, unverified)
    lec   POS,0,4300,1.14,1.64,0.73,100,xB1
          (what a listener / gateway node prints for each tag it hears,
           https://forum.qorvo.com/t/lec-command-output-in-uart-shell-mdek1001/5075: positions only, no ranges)

Mapping: 4-hex anchor ids -> ``A1..`` in sorted order of first appearance (override with
``id_map={"1151": "A1", ...}``); the connected tag -> ``T1`` (``id_map={"tag": "T2"}``). The anchor
coordinates PANS reports are surfaced as ``{"type": "anchors_hint"}`` (the hub adopts them when it
has no anchors); the module's own position goes to the log only: the kit's solver computes ``pos``.
Never run against a real module in this build.
"""
from __future__ import annotations

import re

_LES = re.compile(r"([0-9A-Fa-f]{4})\[(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)\]=(-?[\d.]+)")
_EST = re.compile(r"est\[(-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(\d+)\]")


class Dwm1001Adapter:
    name = "dwm1001"

    def __init__(self, id_map: dict | None = None):
        self.id_map = dict(id_map or {})
        self.tag = self.id_map.get("tag", "T1")
        self.anchor_ids: dict = {}
        self.coords: dict = {}
        self._hint_sent = None

    def _kit_id(self, hex_id: str) -> str:
        hex_id = hex_id.upper()
        if hex_id in self.id_map:
            return self.id_map[hex_id]
        if hex_id not in self.anchor_ids:
            self.anchor_ids[hex_id] = f"A{len(self.anchor_ids) + 1}"
        return self.anchor_ids[hex_id]

    def _emit(self, items, est) -> list:
        # assign ids in sorted order within a line so the mapping does not depend on report order
        for hex_id in sorted(i[0].upper() for i in items):
            self._kit_id(hex_id)
        out = []
        for hex_id, x, y, z, d in items:
            kid = self._kit_id(hex_id)
            self.coords[kid] = {"x": x, "y": y, "z": z}
            if d > 0:
                out.append({"type": "range", "a": self.tag, "b": kid, "d": d})
        snapshot = tuple(sorted((k, v["x"], v["y"], v["z"]) for k, v in self.coords.items()))
        if len(self.coords) >= 3 and snapshot != self._hint_sent:
            self._hint_sent = snapshot
            out.append({"type": "anchors_hint", "anchors": {k: dict(v) for k, v in sorted(self.coords.items())}})
        if est:
            out.append({"type": "log", "text": f"dwm1001 own estimate x={est[0]} y={est[1]} z={est[2]} qf={est[3]}"})
        return out

    def parse(self, line: str):
        try:
            if line.startswith("DIST,"):
                f = line.split(",")
                items, est, i = [], None, 2
                while i < len(f):
                    if f[i].startswith("AN") and i + 5 < len(f) + 0 and len(f) >= i + 6:
                        items.append((f[i + 1], float(f[i + 2]), float(f[i + 3]), float(f[i + 4]), float(f[i + 5])))
                        i += 6
                    elif f[i] == "POS" and len(f) >= i + 5:
                        est = (f[i + 1], f[i + 2], f[i + 3], f[i + 4])
                        i += 5
                    else:
                        i += 1
                return self._emit(items, est) if items else []
            if line.startswith("POS,"):
                return [{"type": "log", "text": "dwm1001 listener " + line}]
            found = _LES.findall(line)
            if found:
                items = [(h, float(x), float(y), float(z), float(d)) for h, x, y, z, d in found]
                m = _EST.search(line)
                return self._emit(items, m.groups() if m else None)
            if line.startswith("dwm>"):
                return []
        except (ValueError, IndexError):
            return []
        return None


_default = Dwm1001Adapter()


def parse(line: str):
    """Module-level convenience: one line -> list of raw messages, or None if not this format."""
    return _default.parse(line)
