"""Board line protocol v1 (docs/CONTRACTS.md section 4): what the firmware prints, parsed into the
raw messages a hub source emits (section 3).

    #UWBKIT fw=1.0 chip=DW3000 role=tag id=T1 mac=A1B2C3D4E5F6 ch=5 antdly=16385      banner
    R,412,T1,A2,3.412,3.447,-78.5,-80.1,0.93*5C                                       one ranging result
    E,T1,button,n=1                                                                   event
    S,T1,tag,123,3.91,9.8,1200,3                                                      status (1 Hz)
    K,A1,antdly,16385                                                                 command reply
    # free text                                                                       log

Lines may end with an NMEA-style checksum ``*HH`` (XOR of every byte before the ``*``); it is
verified when present. Boards running a vendor's stock firmware are handled by ``uwbkit.adapters``
(auto-detected from the line shape). Nothing here ever raises on bad input.
"""
from __future__ import annotations

import math
import re

__all__ = ["parse_line", "parse_lines", "LineParser", "checksum", "format_range", "format_event",
           "format_status", "format_banner", "format_kv", "parse_banner", "los_from_power"]

MAX_LINE = 400
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,11}$")
_CS = re.compile(r"\*([0-9A-Fa-f]{2})$")
_KIT_PREFIX = ("R,", "E,", "S,", "K,", "#")


def checksum(body: str) -> str:
    """NMEA-style XOR of every byte of ``body`` as two uppercase hex digits."""
    c = 0
    for b in body.encode("ascii", "replace"):
        c ^= b
    return f"{c:02X}"


def _cs(body: str, with_checksum: bool) -> str:
    return f"{body}*{checksum(body)}" if with_checksum else body


def _f(v, nd: int) -> str:
    return "" if v is None else f"{float(v):.{nd}f}"


def format_range(bseq, a, b, d, raw=None, rssi=None, fp=None, los=None, with_checksum=True) -> str:
    fields = [_f(raw, 3), _f(rssi, 1), _f(fp, 1), _f(los, 2)]
    while fields and fields[-1] == "":
        fields.pop()
    body = ",".join(["R", str(int(bseq)), str(a), str(b), f"{float(d):.3f}"] + fields)
    return _cs(body, with_checksum)


def format_event(id, name, data=None, with_checksum=True) -> str:
    parts = ["E", str(id), str(name)] + [f"{k}={v}" for k, v in (data or {}).items()]
    return _cs(",".join(parts), with_checksum)


def format_status(id, role, uptime_s, vbat=None, rate_hz=None, ok=0, fail=0, with_checksum=True) -> str:
    body = ",".join(["S", str(id), str(role), str(int(uptime_s)), _f(vbat, 2), _f(rate_hz, 1),
                     str(int(ok)), str(int(fail))])
    return _cs(body, with_checksum)


def format_banner(fw, chip, role, id, mac, ch, antdly, ip=None, with_checksum=False) -> str:
    body = f"#UWBKIT fw={fw} chip={chip} role={role} id={id} mac={mac} ch={ch} antdly={antdly}"
    if ip:
        body += f" ip={ip}"
    return _cs(body, with_checksum)


def format_kv(id, key, value, with_checksum=True) -> str:
    return _cs(f"K,{id},{key},{value}", with_checksum)


def _num(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _auto(s: str):
    """'3' -> 3, '3.5' -> 3.5, anything else stays a string."""
    try:
        return int(s)
    except ValueError:
        v = _num(s)
        return s if v is None else v


def los_from_power(rssi, fp):
    """Line-of-sight confidence from total and first-path receive power (dBm): a gap below 6 dB is
    line of sight (1.0), above 10 dB is not (0.0), linear in between (DW1000 user manual 4.7)."""
    if rssi is None or fp is None:
        return None
    return round(min(1.0, max(0.0, (10.0 - (rssi - fp)) / 4.0)), 2)


def parse_banner(line: str):
    """``#UWBKIT fw=.. chip=.. role=.. id=.. mac=.. ch=.. antdly=..`` -> dict, or None."""
    try:
        line = line.strip()
        m = _CS.search(line)
        if m:
            line = line[:m.start()]
        if not line.startswith("#UWBKIT"):
            return None
        out = {}
        for tok in line[7:].split():
            k, eq, v = tok.partition("=")
            if eq and k:
                out[k.lower()] = _auto(v) if k.lower() in ("ch", "antdly") else v
        return out if _ID.match(str(out.get("id", ""))) else None
    except Exception:
        return None


def _parse_kit(line: str):
    """-> (messages, status) with status in ok | bad | badcs."""
    m = _CS.search(line)
    if m:
        body = line[:m.start()]
        if checksum(body) != m.group(1).upper():
            return [], "badcs"
        line = body
    if line.startswith("#"):
        if line.startswith("#UWBKIT"):
            b = parse_banner(line)
            if b is None:
                return [], "bad"
            msg = {"type": "node", "banner": True}
            msg.update(b)
            return [msg], "ok"
        return [{"type": "log", "text": line[1:].strip()}], "ok"
    f = [x.strip() for x in line.split(",")]
    kind = f[0]
    if kind == "R":
        if len(f) < 5 or not _ID.match(f[2]) or not _ID.match(f[3]) or f[2] == f[3]:
            return [], "bad"
        d = _num(f[4])
        if d is None or not f[1].isdigit():
            return [], "bad"
        msg = {"type": "range", "a": f[2], "b": f[3], "d": d, "bseq": int(f[1])}
        for key, idx in (("raw", 5), ("rssi", 6), ("fp", 7), ("los", 8)):
            if len(f) > idx and f[idx] != "":
                v = _num(f[idx])
                if v is None:
                    return [], "bad"
                msg[key] = v
        if "los" not in msg:
            los = los_from_power(msg.get("rssi"), msg.get("fp"))
            if los is not None:
                msg["los"] = los
        return [msg], "ok"
    if kind == "E":
        if len(f) < 3 or not _ID.match(f[1]) or not f[2]:
            return [], "bad"
        data = {}
        for tok in f[3:]:
            k, eq, v = tok.partition("=")
            if eq and k:
                data[k] = _auto(v)
        return [{"type": "event", "id": f[1], "name": f[2], "data": data}], "ok"
    if kind == "S":
        if len(f) < 4 or not _ID.match(f[1]):
            return [], "bad"
        msg = {"type": "node", "id": f[1], "role": f[2]}
        for key, idx, conv in (("uptime_s", 3, int), ("battery", 4, float), ("rate_hz", 5, float),
                               ("ok", 6, int), ("fail", 7, int)):
            if len(f) > idx and f[idx] != "":
                v = _num(f[idx])
                if v is None:
                    return [], "bad"
                msg[key] = conv(v)
        return [msg], "ok"
    if kind == "K":
        if len(f) < 4 or not _ID.match(f[1]) or not f[2]:
            return [], "bad"
        return [{"type": "node", "id": f[1], "kv": {f[2]: _auto(",".join(f[3:]))}}], "ok"
    return [], "bad"


class LineParser:
    """Incremental parser: bytes in, raw messages out. Tolerates partial lines, boot-loader noise,
    binary garbage and vendor formats (``proto="auto"`` tries the kit protocol, then the adapters)."""

    def __init__(self, proto: str = "auto", id_map: dict | None = None):
        self.proto = (proto or "auto").lower()
        self._buf = bytearray()
        self.stats = {"lines_ok": 0, "lines_bad": 0, "bad_checksum": 0, "lines_other": 0, "bytes": 0,
                      "by_type": {}, "proto_seen": {}}
        self._adapters = None
        self._id_map = id_map

    def _load_adapters(self):
        if self._adapters is None:
            try:
                from .adapters import make_adapters
                self._adapters = make_adapters(self._id_map)
            except Exception:
                self._adapters = {}
        return self._adapters

    def parse(self, line: str) -> list:
        """One text line -> list of raw messages (possibly empty). Updates ``stats``."""
        try:
            line = line.strip().strip("\x00")
            if not line:
                return []
            msgs, status, proto = [], "other", "uwbkit"
            if self.proto in ("auto", "uwbkit") and line.startswith(_KIT_PREFIX):
                msgs, status = _parse_kit(line)
            elif self.proto != "uwbkit":
                for name, adapter in self._load_adapters().items():
                    if self.proto not in ("auto", name):
                        continue
                    res = adapter.parse(line)
                    if res is not None:
                        msgs, status, proto = res, "ok", name
                        break
            if status == "ok":
                self.stats["lines_ok"] += 1
                self.stats["proto_seen"][proto] = self.stats["proto_seen"].get(proto, 0) + 1
                for m in msgs:
                    t = m.get("type", "?")
                    self.stats["by_type"][t] = self.stats["by_type"].get(t, 0) + 1
            elif status == "badcs":
                self.stats["bad_checksum"] += 1
            elif status == "bad":      # looked like a kit line but did not parse
                self.stats["lines_bad"] += 1
            else:                       # boot-loader chatter, vendor text we do not know: keep for doctor
                self.stats["lines_other"] += 1
                return [{"type": "log", "text": line[:200]}]
            return msgs
        except Exception:
            self.stats["lines_bad"] += 1
            return []

    def feed(self, data: bytes) -> list:
        out = []
        if not data:
            return out
        self.stats["bytes"] += len(data)
        self._buf += data
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            raw = bytes(self._buf[:i])
            del self._buf[:i + 1]
            if len(raw) > MAX_LINE:
                self.stats["lines_bad"] += 1
                continue
            text = raw.decode("ascii", "replace").replace("\r", "").strip("\x00")
            if "�" in text or any(ord(c) < 32 and c != "\t" for c in text):
                if text.strip():
                    self.stats["lines_bad"] += 1   # binary garbage / wrong baud rate
                continue
            out.extend(self.parse(text))
        if len(self._buf) > 4 * MAX_LINE:   # a flood without newlines: not a line protocol
            self._buf.clear()
            self.stats["lines_bad"] += 1
        return out


_DEFAULT = None


def parse_lines(line, proto: str = "auto") -> list:
    """One line (str or bytes) -> list of raw messages; vendor formats may yield several."""
    global _DEFAULT
    try:
        if isinstance(line, (bytes, bytearray)):
            line = bytes(line).decode("ascii", "replace")
        if proto == "auto":
            if _DEFAULT is None:
                _DEFAULT = LineParser("auto")
            return _DEFAULT.parse(str(line))
        return LineParser(proto).parse(str(line))
    except Exception:
        return []


def parse_line(line):
    """One line -> one raw message (docs/CONTRACTS.md section 3 shapes) or None."""
    msgs = parse_lines(line)
    return msgs[0] if msgs else None
