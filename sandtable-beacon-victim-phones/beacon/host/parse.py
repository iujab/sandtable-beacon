"""One tolerant parser for distance lines from a DWM3001CDK, whatever firmware prints them.

EXPERIMENTAL. Written from forum examples; never run against a real board. The exact log format of Qorvo's
stock Nearby Interaction firmware (QANI / QNI 3.0) is NOT confirmed (docs/research/puck_hardware.md section 2).
Run `python -m beacon.host --learn ...` on the real stream and paste a few lines back if the format differs.

Line shapes it was written against (sources in docs/research/puck_hardware.md section 2):

- early Qorvo NI firmware over RTT (2022, forum topic 12439):
      {"TWR": {"R":6613,"a16":"0x0001","S":"ERR","D cm":0}}
  "S" is the status ("ERR" = failed round: dropped), "D cm" the distance in centimetres, "R" a round counter,
  "a16" the peer's 16-bit address.
- DW3_QM33 SDK CLI firmware over USB CDC on J20 (forum topic 13430):
      "Addr":"0x0000","Status":"Ok","D_cm":61, ...
  On failure the status is e.g. "RX_TIMEOUT" and the distance field is absent (forum topic 22984).
- plain text, as a log might print it: `distance: 1.23 m`, `range=123cm`, `dist 1230 mm`.

Rules:
- a distance key carries its unit in its name (`D cm`, `D_cm`, `dist_mm`, `range_m`) or the value carries it
  (`1.23 m`, `123cm`); a unit-less decimal is read as metres; a unit-less integer is ambiguous and dropped
  unless the caller says what it is (`unitless="cm"`);
- a status field, when present, must say OK (`Ok`, `OK`, `SUCCESS`, `0`); anything else (`ERR`, `RX_TIMEOUT`) drops it;
- values outside [min_m, max_m] are dropped as implausible.

Pure standard library; no I/O.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

__all__ = ["Reading", "ParseResult", "parse_line", "MIN_M", "MAX_M", "LEARN_SAMPLE"]

MIN_M = 0.05     # closer than 5 cm: a zero or a calibration artefact, not a range
MAX_M = 30.0     # the research's longest reported phone-to-board range is about 14 m (forum 25626); 30 m leaves margin

_UNIT_FACTORS = {"m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0,
                 "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01, "centimetre": 0.01, "centimetres": 0.01,
                 "mm": 0.001, "millimeter": 0.001, "millimeters": 0.001, "millimetre": 0.001, "millimetres": 0.001}

# normalised key (lower case, letters and digits only) -> unit implied by the key name (None = look at the value)
_DIST_KEYS: dict[str, str | None] = {}
for _base in ("d", "dist", "distance", "range", "rng"):
    _DIST_KEYS[_base] = None
    for _u in ("m", "cm", "mm"):
        _DIST_KEYS[_base + _u] = _u
# "dm" is "d_m" (metres, as in this project's own feed), never decimetres

_STATUS_KEYS = {"s", "status", "st", "state", "result"}
_OK_STATUS = {"ok", "success", "succeeded", "0", "good", "valid", "rangingok"}
_PEER_KEYS = {"a16", "addr", "address", "peer", "mac", "dst", "src"}

_UNIT_RE = r"(?P<unit>millimet(?:er|re)s?|centimet(?:er|re)s?|met(?:er|re)s?|mm|cm|m)?"
_NUM_RE = r"(?P<num>-?\d+(?:\.\d+)?)"
# "key": value   or   "key":"value"   (JSON or a JSON fragment)
_QUOTED_KV = re.compile(r'"(?P<key>[^"]{1,40})"\s*:\s*(?:"(?P<sval>[^"]{0,60})"|' + _NUM_RE + r")")
# key: value unit   key=value   key value unit (bare text)
_BARE_KV = re.compile(r"(?<![A-Za-z0-9_])(?P<key>[A-Za-z][A-Za-z0-9_]{0,30})\s*(?:[:=]\s*|\s+)" + _NUM_RE + r"\s*" + _UNIT_RE + r"(?![A-Za-z])")
_BARE_STATUS = re.compile(r"(?<![A-Za-z0-9_])(?:status|state|st)\s*[:=]\s*\"?(?P<sval>[A-Za-z_0-9]+)", re.IGNORECASE)


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


@dataclass
class Reading:
    """One distance found in a line."""
    d_m: float
    status: str | None = None
    peer: str | None = None
    key: str = ""

    def as_dict(self) -> dict:
        return {"d_m": self.d_m, "status": self.status, "peer": self.peer, "key": self.key}


@dataclass
class ParseResult:
    """What the parser made of one line: accepted readings, and why anything else was dropped."""
    line: str
    readings: list[Reading] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.readings)

    def explain(self) -> str:
        if self.readings:
            parts = [f"{r.d_m:.3f} m" + (f" (peer {r.peer})" if r.peer else "") + f" from '{r.key}'" for r in self.readings]
            return "RANGE " + "; ".join(parts) + (("  | dropped: " + "; ".join(self.dropped)) if self.dropped else "")
        if self.dropped:
            return "dropped: " + "; ".join(self.dropped)
        return "no distance in this line"


def _to_metres(value: float, key_unit: str | None, value_unit: str | None, has_decimal: bool, unitless: str) -> tuple[float | None, str]:
    unit = key_unit or (value_unit.lower() if value_unit else None)
    if unit:
        return value * _UNIT_FACTORS[unit], ""
    if unitless in _UNIT_FACTORS:
        return value * _UNIT_FACTORS[unitless], ""
    if has_decimal:
        return value, ""            # a unit-less decimal: metres (what Apple's API and most logs use)
    return None, "integer with no unit (rerun with --unitless cm or --unitless m if you know it)"


def _status_ok(status: str | None) -> bool:
    return status is None or _norm(str(status)) in _OK_STATUS


def _check(d_m: float | None, why: str, status: str | None, key: str, min_m: float, max_m: float, dropped: list[str]) -> float | None:
    if d_m is None:
        dropped.append(f"'{key}': {why}")
        return None
    if not _status_ok(status):
        dropped.append(f"'{key}': status {status!r} is not OK")
        return None
    if not (min_m <= d_m <= max_m):
        dropped.append(f"'{key}': {d_m:.3f} m is implausible (outside {min_m}..{max_m} m)")
        return None
    return d_m


def _walk_json(node, out: list[dict]) -> None:
    """Collect every dict in a parsed JSON value."""
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            _walk_json(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_json(v, out)


def _json_objects(line: str):
    """Yield every top-level JSON object embedded in the line (logs often prefix JSON with a tag or timestamp)."""
    dec = json.JSONDecoder()
    i = line.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(line, i)
        except ValueError:
            i = line.find("{", i + 1)
            continue
        yield obj
        i = line.find("{", end)


def _from_dicts(dicts: list[dict], min_m: float, max_m: float, unitless: str, res: ParseResult) -> None:
    for d in dicts:
        status = None
        peer = None
        for k, v in d.items():
            nk = _norm(str(k))
            if nk in _STATUS_KEYS and isinstance(v, (str, int)) and not isinstance(v, bool):
                status = str(v)
            elif nk in _PEER_KEYS and isinstance(v, (str, int)) and not isinstance(v, bool):
                peer = str(v)
        if status is not None and not _status_ok(status) and not any(_norm(str(k)) in _DIST_KEYS for k in d):
            res.dropped.append(f"status {status!r}, no distance")
        for k, v in d.items():
            nk = _norm(str(k))
            if nk not in _DIST_KEYS:
                continue
            value_unit = None
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)):
                num, has_dec = float(v), isinstance(v, float)
            elif isinstance(v, str):
                m = re.fullmatch(r"\s*" + _NUM_RE + r"\s*" + _UNIT_RE + r"\s*", v)
                if not m:
                    continue
                num, has_dec, value_unit = float(m.group("num")), "." in m.group("num"), m.group("unit")
            else:
                continue
            d_m, why = _to_metres(num, _DIST_KEYS[nk], value_unit, has_dec, unitless)
            d_m = _check(d_m, why, status, str(k), min_m, max_m, res.dropped)
            if d_m is not None:
                res.readings.append(Reading(round(d_m, 4), status, peer, str(k)))


def parse_line(line: str, *, min_m: float = MIN_M, max_m: float = MAX_M, unitless: str = "auto") -> ParseResult:
    """Find the distance(s) in one log line. Never raises on garbage; returns what it accepted and what it dropped and why."""
    if not isinstance(line, str):
        try:
            line = bytes(line).decode("utf-8", "replace")
        except Exception:
            line = str(line)
    text = line.strip().replace("\x00", "")
    res = ParseResult(line=text)
    if not text:
        return res

    # 1. whole JSON objects in the line
    dicts: list[dict] = []
    for obj in _json_objects(text):
        _walk_json(obj, dicts)
    if dicts:
        _from_dicts(dicts, min_m, max_m, unitless, res)
        if res.readings or res.dropped:
            return res

    # 2. JSON fragments ("Status":"Ok","D_cm":61) that do not parse as a whole: quoted key-value pairs
    pairs = list(_QUOTED_KV.finditer(text))
    if pairs:
        frag: dict = {}
        for m in pairs:
            if m.group("num") is not None:
                frag[m.group("key")] = float(m.group("num")) if "." in m.group("num") else int(m.group("num"))
            else:
                frag[m.group("key")] = m.group("sval")
        before = (len(res.readings), len(res.dropped))
        _from_dicts([frag], min_m, max_m, unitless, res)
        if (len(res.readings), len(res.dropped)) != before:
            return res

    # 3. bare text: "distance: 1.23 m", "range=123cm", "Status: RX_TIMEOUT"
    sm = _BARE_STATUS.search(text)
    status = sm.group("sval") if sm else None
    for m in _BARE_KV.finditer(text):
        key = m.group("key")
        nk = _norm(key)
        if nk not in _DIST_KEYS:
            continue
        num = float(m.group("num"))
        d_m, why = _to_metres(num, _DIST_KEYS[nk], m.group("unit"), "." in m.group("num"), unitless)
        d_m = _check(d_m, why, status, key, min_m, max_m, res.dropped)
        if d_m is not None:
            res.readings.append(Reading(round(d_m, 4), status, None, key))
        break       # one distance per bare-text line: the first distance-like key wins
    return res


# Lines used by --dry-run (and the tests): the research's examples plus garbage. NOT measurements.
LEARN_SAMPLE = [
    "SEGGER J-Link V7.94 - Real time terminal output",
    "ACCESSORY_RANGING_ROLE: Initiator",
    '{"TWR": {"R":6613,"a16":"0x0001","S":"ERR","D cm":0}}',
    '{"TWR": {"R":6614,"a16":"0x0001","S":"OK","D cm":231}}',
    '{"Block":12, "results":[{"Addr":"0x0000","Status":"Ok","D_cm":61,"LPDoA_deg":0.00,"LAoA_deg":0.00,"LFoM":0}]}',
    '"Addr":"0x0001","Status":"RX_TIMEOUT"',
    "distance: 1.23 m",
    "\x1b[0m<inf> garbage \xff\xfe 12:34:56 ###",
    "range=987654cm",
]
