"""Real pucks as a scout source: the shared uwb-kit hub in `mesh5`, plus CIR frames when the CIR firmware is flashed.

STATUS: SIMULATION-ONLY this weekend. Written for ESP32 + DW3000 kit boards (`hardware/firmware/esp32-reference/`);
the owner's boards are DWM3001CDK, for which the kit has no firmware or parser yet. Exercised only against the kit
simulator, the kit's fake-board emulator and synthetic CIR frames.

What arrives from where (this is what the firmware actually provides):

* **Ranging and self-location** come from the kit hub, exactly as INTERFACE.md v1 says: `hello` (profile `mesh5`,
  `frame: "mesh"`), `range` for every pair of nodes (`d`, and optionally `rssi`, `fp`, `los`), and `pos` for every
  node in the hub's relative mesh frame (N1 at the origin, N2 on +x, N3 with y > 0; solved by the kit's MDS).
* **CIR frames do NOT go through the kit hub.** The CIR firmware (`hardware/firmware/uwbkit_tagless`, lane L08's
  delta on the kit firmware) writes them as *binary* frames (magic A5 5A, CRC-16) on the board's USB serial after
  `CIR ON`, and as UDP datagrams to port 1946 of the address configured with the kit's `HUB` command. The kit's
  line parser would count them as garbage. So there are two side channels, both taken from L08's `io/boards.py`:
    - `udp`   : boards on Wi-Fi (`WIFI ...`, `HUB <laptop> 1801`, `SAVE`) send CIR datagrams to UDP 1946 on the
                laptop while their ASCII lines go to the kit hub on UDP 1801. `CirUdpReceiver` listens on 1946.
    - `serial`: boards on USB. Only one program can own a serial port, so `SerialCirBridge` owns them: it sends
                `CIR ON 20 921600`, keeps the binary frames, and forwards every ASCII kit line over UDP to a kit
                hub started with `--source udp:127.0.0.1:1801`, which then does ranging and self-location as usual.
  Each board only captures frames it *overhears* (not addressed to it), so with one USB gateway you get the four
  links into that board; for all ordered pairs put all five on USB (powered hub) or on Wi-Fi.

What was exercised on this bench (no board was ever attached): the kit path against the kit simulator and the
kit's fake-board emulator over real UDP (`hardware/recordings/`, tests); the CIR frame parser on mixed ASCII and
binary byte streams and the UDP receiver over loopback with frames from the encoder below. NOT exercised: the
serial bridge's handshake with a real board, the real accumulator contents, real frame rates.

    python -m sandtable.scout.source_kit --url ws://127.0.0.1:1910/stream --seconds 8              # what do the pucks see?
    python -m sandtable.scout.source_kit --url ... --watch                                           # rung-2 live view (terminal)
    python -m sandtable.scout.source_kit --url ... --cir udp:0.0.0.0:1946                            # rung-1 first light
    python -m sandtable.scout.source_kit --replay hardware/recordings/scout_mesh5_fakeboard.ndjson   # offline

The returned recording has the radar engine's shape (docs/SCOUT_CONTRACT.md 2.1, `sim.simulate`): `meta`, `pucks`,
`ranges`, `frames`, plus `pucks_rel` (the kit's self-location), `id_map` (kit id -> puck id), `hello`, `cir_stats`.
`truth` is None: real pucks have no ground truth.

CIR framing, encoder and parser copied from lane L08 (`../tagless-uwb-echo-radar/src/tagless/io/boards.py`,
`wire.py`); L08's status applies: exercised with the encoder, a fake board and UDP loopback, never with real boards.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import struct
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

# ---- physics of the CIR window (L08 wire.py) --------------------------------------------------------------------
TS_NS = 1.0016                     # accumulator sample period: 1 / (2 * 499.2 MHz)
BANDWIDTH_MHZ = 499.2
CARRIER_MHZ = 6489.6               # DW3000 channel 5
N_TAPS = 64                        # taps per frame (TL_CIR_N)
K0 = 8                             # nominal first-path index inside the window (TL_CIR_PRE)
CIR_UDP_PORT = 1946                # hard-coded in the firmware delta (tagless_on_rx)


# ---- ids ----------------------------------------------------------------------------------------------------------
def default_id_map(node_ids) -> dict:
    """Kit node ids -> puck ids. Mesh nodes keep their number (N3 -> P3); anything else is numbered in sorted order."""
    ids = sorted(set(node_ids))
    if ids and all(i[:1] == "N" and i[1:].isdigit() for i in ids):
        return {i: f"P{i[1:]}" for i in ids}
    return {i: f"P{k + 1}" for k, i in enumerate(ids)}


# ---- L08 binary CIR frames ------------------------------------------------------------------------------------------
MAGIC = b"\xA5\x5A"
_HDR = struct.Struct("<BBBBHIHHhhBB")        # after the magic: 20 bytes
HDR_LEN = 2 + _HDR.size
FLAG_SYNTHETIC = 0x80
_LETTERS = "NAT"                              # id byte: low nibble = digit, high nibble = 0 for N, 1 for A, 2 for T


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), as tl_crc16 in the firmware."""
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def _node_num(nid: str) -> int:
    digit = int("".join(ch for ch in nid if ch.isdigit()) or 0) & 0x0F
    return digit | ((_LETTERS.index(nid[0].upper()) if nid[:1].upper() in _LETTERS else 0) << 4)


def _node_id(b: int) -> str:
    return f"{_LETTERS[(b >> 4) % 3]}{b & 0x0F}"


def encode_frame(msg: dict, synthetic: bool = True) -> bytes:
    """A `cir` message -> the firmware's binary frame. Used by tests and fake boards; synthetic frames carry flag bit 7."""
    h = np.asarray(msg["_iq"])
    peak = float(max(np.abs(h.real).max(initial=0), np.abs(h.imag).max(initial=0)))
    shift = 0
    while peak / (1 << shift) > 32767 and shift < 3:
        shift += 1
    iq = np.empty(len(h) * 2, dtype="<i2")
    iq[0::2] = np.clip(np.rint(h.real / (1 << shift)), -32768, 32767)
    iq[1::2] = np.clip(np.rint(h.imag / (1 << shift)), -32768, 32767)
    flags = shift | (FLAG_SYNTHETIC if synthetic else 0)
    body = _HDR.pack(1, flags, _node_num(msg["tx"]), _node_num(msg["rx"]), int(msg.get("seq", 0)) & 0xFFFF,
                     int(msg.get("t", 0) * 1000) & 0xFFFFFFFF, int(round(msg["fp"] * 64)) & 0xFFFF,
                     int(msg.get("pacc", 0)) & 0xFFFF, int(round(msg.get("rxp", -120.0) * 100)),
                     int(round(msg.get("fpp", -120.0) * 100)), len(h), 0) + iq.tobytes()
    return MAGIC + body + struct.pack("<H", crc16(body))


def parse_kit_line(line: str) -> dict | None:
    """One ASCII kit board line -> {"type": "kitline"|"node"|"range", ...}; enough to find banners and `K,..,cir` replies."""
    s = line.strip()
    if not s:
        return None
    if "*" in s[-3:]:
        body, _, cs = s.rpartition("*")
        x = 0
        for ch in body.encode():
            x ^= ch
        if cs.upper() != f"{x:02X}":
            return {"type": "kitline", "raw": line.strip(), "bad_checksum": True}
        s = body
    if s.startswith("#UWBKIT"):
        kv = dict(p.split("=", 1) for p in s.split()[1:] if "=" in p)
        return {"type": "node", "banner": True, "raw": line.strip(), **kv}
    f = s.split(",")
    if f[0] == "K" and len(f) >= 4 and f[2] == "cir":
        return {"type": "node", "ext": "cir", "id": f[1], "cir": f[3], "raw": line.strip()}
    return {"type": "kitline", "raw": line.strip()}


class FrameParser:
    """Bytes in, messages out. ASCII kit lines and binary CIR frames may be interleaved (as on a board's USB serial);
    garbage is skipped and counted; a frame with a bad CRC is dropped and the parser resynchronises. (L08 boards.py)"""

    def __init__(self):
        self.buf = bytearray()
        self.stats = {"frames": 0, "bad_crc": 0, "garbage": 0, "lines": 0}
        self.synthetic = False

    def feed(self, data: bytes, now: float | None = None) -> list[dict]:
        self.buf += data
        out = []
        b = self.buf
        t = time.time() if now is None else now
        while b:
            if b[0] == 0xA5:
                if len(b) < 2:
                    break
                if b[1] == 0x5A:
                    if len(b) < HDR_LEN:
                        break
                    ver, flags, tx, rx, seq, t_ms, fp_q6, pacc, rxp, fpp, n, fmt = _HDR.unpack_from(b, 2)
                    total = HDR_LEN + n * 4 + 2
                    if ver != 1 or fmt != 0 or n == 0:
                        del b[0]
                        self.stats["garbage"] += 1
                        continue
                    if len(b) < total:
                        break
                    body = bytes(b[2:total - 2])
                    if crc16(body) != struct.unpack_from("<H", b, total - 2)[0]:
                        self.stats["bad_crc"] += 1
                        del b[0]
                        continue
                    iq = np.frombuffer(body, dtype="<i2", offset=_HDR.size).astype(np.float32) * float(1 << (flags & 3))
                    msg = {"type": "cir", "t": t, "seq": seq, "tx": _node_id(tx), "rx": _node_id(rx), "n": n,
                           "ts_ns": TS_NS, "fp": fp_q6 / 64.0, "pacc": pacc, "rxp": rxp / 100.0, "fpp": fpp / 100.0,
                           "board_ms": t_ms, "_iq": (iq[0::2] + 1j * iq[1::2]).astype(np.complex64)}
                    if flags & FLAG_SYNTHETIC:
                        msg["synthetic"] = True
                        self.synthetic = True
                    out.append(msg)
                    self.stats["frames"] += 1
                    del b[:total]
                    continue
                del b[0]
                self.stats["garbage"] += 1
                continue
            nl = b.find(b"\n")
            mg = b.find(b"\xA5")
            if nl != -1 and (mg == -1 or nl < mg):
                raw = bytes(b[:nl])
                del b[:nl + 1]
                text = "".join(ch for ch in raw.decode("ascii", "ignore") if " " <= ch <= "~")
                if "#UWBKIT" in text:                     # ESP32 boot noise in front of a banner is normal
                    text = text[text.index("#UWBKIT"):]
                m = parse_kit_line(text)
                if m:
                    self.stats["lines"] += 1
                    out.append(m)
                continue
            if mg > 0:
                self.stats["garbage"] += mg
                del b[:mg]
                continue
            if len(b) > 512:
                self.stats["garbage"] += len(b)
                b.clear()
            break
        return out


# ---- CIR side channels -----------------------------------------------------------------------------------------------
class _Collector:
    def __init__(self, max_frames: int = 200_000):
        self.frames: list[dict] = []
        self.log: list[str] = []
        self.max_frames = max_frames
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _add(self, m: dict) -> None:
        with self._lock:
            if len(self.frames) < self.max_frames:
                self.frames.append(m)

    def take(self) -> list[dict]:
        with self._lock:
            out, self.frames = self.frames, []
        return out

    def stop(self) -> None:
        self._stop.set()
        for th in self._threads:
            th.join(2.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


class CirUdpReceiver(_Collector):
    """Boards on Wi-Fi: one binary CIR frame per datagram to UDP 1946 (firmware delta, `tagless_on_rx`)."""

    def __init__(self, host: str = "0.0.0.0", port: int = CIR_UDP_PORT):
        super().__init__()
        self.host, self.port = host, port
        self.parsers: dict = {}
        self.sock = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        th = threading.Thread(target=self._run, daemon=True)
        th.start()
        self._threads.append(th)
        return self

    def _run(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            p = self.parsers.setdefault(addr, FrameParser())
            for m in p.feed(data):
                if m.get("type") == "cir":
                    self._add(m)
        self.sock.close()


class SerialCirBridge(_Collector):
    """Boards on USB: own the serial ports, switch CIR on, keep the binary frames, forward ASCII kit lines over UDP to
    a kit hub (`python -m uwbkit hub --source udp:127.0.0.1:1801 --profile mesh5`) so ranging continues. (L08 logic.)
    NOT exercised against a real board."""

    def __init__(self, ports: str = "auto", forward=("127.0.0.1", 1801), cycle_hz: int = 20, baud_fast: int = 921600):
        super().__init__()
        self.ports, self.forward, self.cycle_hz, self.baud_fast = ports, forward, cycle_hz, baud_fast
        self.capable: list[str] = []

    def start(self):
        from serial.tools import list_ports
        ports = [p.device for p in list_ports.comports()] if self.ports in ("", "auto") else \
            [p for p in self.ports.split(",") if p]
        if not ports:
            raise RuntimeError("no serial ports found")
        for p in ports:
            th = threading.Thread(target=self._reader, args=(p,), daemon=True)
            th.start()
            self._threads.append(th)
        return self

    def _reader(self, port: str):
        import serial
        parser = FrameParser()
        fsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if self.forward else None

        def emit(m):
            raw = m.get("raw")
            if fsock is not None and raw and m.get("type") != "cir":
                try:
                    fsock.sendto((raw + "\n").encode(), tuple(self.forward))
                except OSError:
                    pass
            if m.get("type") == "cir":
                self._add(m)
        try:
            ser = serial.Serial(port, 115200, timeout=0.2)
            ser.write(b"?\nCIR?\n")
            t_end, ok = time.time() + 3.0, False
            while time.time() < t_end and not ok:
                for m in parser.feed(ser.read(256)):
                    ok = ok or (m.get("type") == "node" and m.get("ext") == "cir")
                    emit(m)
            if not ok:
                self.log.append(f"{port}: no board with the CIR firmware answered `CIR?`; skipped (stock kit firmware?)")
                ser.close()
                return
            self.capable.append(port)
            ser.write(f"CIR ON {self.cycle_hz} {self.baud_fast}\n".encode())
            ser.flush()
            time.sleep(0.25)
            ser.baudrate = self.baud_fast
            parser = FrameParser()
            while not self._stop.is_set():
                data = ser.read(4096)
                if data:
                    for m in parser.feed(data):
                        emit(m)
            ser.baudrate = 115200
            ser.write(b"CIR OFF\n")
            ser.close()
        except Exception as e:                      # port vanished, permission, ...
            self.log.append(f"{port}: {e}")


def open_cir(spec: str | None):
    """`udp:0.0.0.0:1946` | `serial:auto` | `serial:COM5,COM6[>127.0.0.1:1801]` | None -> a started collector or None."""
    if not spec:
        return None
    kind, _, arg = spec.partition(":")
    if kind == "udp":
        host, _, port = arg.rpartition(":")
        return CirUdpReceiver(host or "0.0.0.0", int(port or CIR_UDP_PORT)).start()
    if kind == "serial":
        ports, _, fwd = arg.partition(">")
        h, _, p = (fwd or "127.0.0.1:1801").rpartition(":")
        return SerialCirBridge(ports or "auto", forward=(h, int(p))).start()
    raise ValueError(f"unknown CIR source {spec!r} (udp:HOST:PORT or serial:PORTS)")


# ---- kit messages -> scout recording ---------------------------------------------------------------------------------
def load_ndjson(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def mds_xy(ids: list[str], ranges: list[dict]) -> tuple[dict, float]:
    """Classical MDS on the median pairwise ranges, oriented like the kit's mesh frame (first id at the origin, second
    on +x, third with y > 0). Fallback when the hub publishes no `pos` (e.g. started with --no-solve)."""
    n = len(ids)
    if n < 3:
        return {}, float("nan")
    idx = {k: i for i, k in enumerate(ids)}
    acc = defaultdict(list)
    for m in ranges:
        if m.get("a") in idx and m.get("b") in idx and m.get("d") is not None:
            i, j = sorted((idx[m["a"]], idx[m["b"]]))
            acc[(i, j)].append(float(m["d"]))
    D = np.full((n, n), np.nan)
    np.fill_diagonal(D, 0.0)
    for (i, j), v in acc.items():
        D[i, j] = D[j, i] = float(np.median(v))
    if np.isnan(D).any():
        return {}, float("nan")
    J = np.eye(n) - 1.0 / n
    B = -0.5 * J @ (D ** 2) @ J
    w, V = np.linalg.eigh(B)
    o = np.argsort(w)[::-1][:2]
    X = V[:, o] * np.sqrt(np.maximum(w[o], 0))
    X = X - X[0]
    ang = math.atan2(X[1, 1], X[1, 0])
    R = np.array([[math.cos(-ang), -math.sin(-ang)], [math.sin(-ang), math.cos(-ang)]])
    X = X @ R.T
    if X[2, 1] < 0:
        X[:, 1] = -X[:, 1]
    err = [abs(np.hypot(*(X[i] - X[j])) - D[i, j]) for i in range(n) for j in range(i + 1, n)]
    return {k: [round(float(X[i, 0]), 3), round(float(X[i, 1]), 3)] for k, i in idx.items()}, float(np.sqrt(np.mean(np.square(err))))


def cir_to_frames(cir_msgs: list[dict], id_map: dict) -> dict:
    """`cir` messages -> {(tx_puck, rx_puck): {"t": ndarray[T], "cir": complex64 ndarray[T, n], "fp_index": ndarray[T]}}."""
    by = defaultdict(list)
    for m in cir_msgs:
        tx, rx = id_map.get(m["tx"], m["tx"]), id_map.get(m["rx"], m["rx"])
        by[(tx, rx)].append(m)
    out = {}
    for key, ms in by.items():
        ms.sort(key=lambda m: m["t"])
        n = max(len(m["_iq"]) for m in ms)
        cir = np.zeros((len(ms), n), np.complex64)
        for k, m in enumerate(ms):
            cir[k, :len(m["_iq"])] = m["_iq"]
        out[key] = {"t": np.array([m["t"] for m in ms], float), "cir": cir,
                    "fp_index": np.array([m["fp"] for m in ms], float),
                    "pacc": np.array([m.get("pacc", 0) for m in ms], float),
                    "fpp_dbm": np.array([m.get("fpp", np.nan) for m in ms], float)}
    return out


def from_messages(msgs: list[dict], cir_msgs: list[dict] | None = None, id_map: dict | None = None,
                  window_s: float | None = None) -> dict:
    """Kit messages (+ optional `cir` messages) -> a scout recording (see module docstring)."""
    hello = None
    for m in msgs:
        if m.get("type") == "hello":
            hello = m                                   # the last hello wins (it is re-sent when nodes change)
    rng = [m for m in msgs if m.get("type") == "range" and m.get("a") and m.get("b")]
    pos = [m for m in msgs if m.get("type") == "pos"]
    if window_s is not None and rng:
        tmax = max(float(m.get("t", 0)) for m in rng)
        rng = [m for m in rng if float(m.get("t", 0)) >= tmax - window_s]
        pos = [m for m in pos if float(m.get("t", 0)) >= tmax - window_s]
    nodes = sorted((hello or {}).get("nodes", {}) or {m["a"] for m in rng} | {m["b"] for m in rng})
    if not nodes:
        nodes = sorted({m["a"] for m in rng} | {m["b"] for m in rng})
    idm = dict(id_map or default_id_map(nodes))
    ranges = []
    for m in rng:
        r = {"t": float(m.get("t", 0.0)), "a": idm.get(m["a"], m["a"]), "b": idm.get(m["b"], m["b"]), "d": m.get("d")}
        for k in ("rssi", "fp", "los"):
            if m.get(k) is not None:
                r[k] = m[k]
        ranges.append(r)
    per = defaultdict(list)
    for m in pos:
        per[m["id"]].append(m)
    pucks_rel, sig = {}, {}
    for nid, ps in per.items():
        last = ps[-max(1, len(ps) // 2):]                # second half of the window: the MDS has settled
        pucks_rel[idm.get(nid, nid)] = [round(float(np.median([p["x"] for p in last])), 3),
                                        round(float(np.median([p["y"] for p in last])), 3)]
        cov = last[-1].get("cov")
        sig[idm.get(nid, nid)] = round(math.sqrt(max(0.0, (cov[0] + cov[2]) / 2)), 3) if cov else None
    self_loc = "kit-pos"
    if len(pucks_rel) < len(nodes):
        pr, rms = mds_xy([idm.get(n, n) for n in nodes], ranges)
        if pr:
            pucks_rel, sig, self_loc = pr, {k: round(rms, 3) for k in pr}, "mds-from-ranges"
    frames = cir_to_frames(cir_msgs or [], idm) if cir_msgs else {}
    ts = [r["t"] for r in ranges]
    dur = (max(ts) - min(ts)) if ts else 0.0
    n_links = len({tuple(sorted((r["a"], r["b"]))) for r in ranges})
    fhz = (sum(len(f["t"]) for f in frames.values()) / max(1, len(frames)) / dur) if frames and dur > 0 else None
    synthetic_cir = any(m.get("synthetic") for m in (cir_msgs or []))
    have = {k: sum(1 for r in ranges if k in r) for k in ("fp", "rssi", "los")}
    # simulated if ANY hello said so: a hub sends its first hello before it has noticed a fake-board emulator
    hellos = [m for m in msgs if m.get("type") == "hello"]
    simulated = (not hellos) or any(bool(h.get("simulated", True)) for h in hellos) or synthetic_cir
    meta = {"simulated": simulated, "source": (hello or {}).get("source"), "profile": (hello or {}).get("profile"),
            "frame": (hello or {}).get("frame", "mesh"), "kit_version": (hello or {}).get("kit_version"),
            "noise": ((hello or {}).get("sim") or {}).get("noise"), "duration_s": round(dur, 2),
            "n_ranges": len(ranges), "n_links": n_links,
            "range_rate_hz": round(len(ranges) / max(1, n_links) / dur, 2) if dur > 0 else None,
            "optional_fields": {k: (round(v / len(ranges), 3) if ranges else 0.0) for k, v in have.items()},
            "self_location": self_loc, "cir": bool(frames), "synthetic_cir": synthetic_cir,
            "tap_ns": TS_NS, "bandwidth_mhz": BANDWIDTH_MHZ, "carrier_mhz": CARRIER_MHZ,
            "n_taps": max((f["cir"].shape[1] for f in frames.values()), default=N_TAPS), "k0": K0,
            "frame_hz": round(fhz, 2) if fhz else None}
    return {"meta": meta, "pucks": sorted(set(idm.values()) & (set(pucks_rel) | {r["a"] for r in ranges} | {r["b"] for r in ranges})),
            "id_map": idm, "pucks_rel": pucks_rel, "pucks_rel_sigma": sig, "ranges": ranges, "frames": frames,
            "hello": hello, "truth": None}


def collect(url: str, seconds: float = 6.0, cir: str | None = None, cir_collector=None) -> dict:
    """Read the kit hub for `seconds` (and the CIR side channel, if any) and return the scout recording."""
    from uwbkit.client import Stream
    col = cir_collector or open_cir(cir)
    msgs, t_end = [], time.time() + seconds
    st = Stream(url, reconnect=False, timeout=3.0)
    try:
        for m in st:
            msgs.append(m)
            if time.time() >= t_end:
                break
    except ConnectionError as e:
        raise ConnectionError(f"no kit hub at {url}: {e}") from e
    finally:
        st.close()
    frames = col.take() if col else None
    if col and cir_collector is None:
        col.stop()
    rec = from_messages(msgs, frames)
    if col:
        rec["cir_stats"] = {"log": list(col.log), "parsers": {str(k): v.stats for k, v in getattr(col, "parsers", {}).items()},
                            "capable_ports": list(getattr(col, "capable", []))}
    return rec


# ---- bring-up report (CLI) -------------------------------------------------------------------------------------------
def report(rec: dict) -> str:
    from . import shadow
    m = rec["meta"]
    lines = [f"source {m['source']}  profile {m['profile']}  {'SIMULATED' if m['simulated'] else 'REAL BOARDS'}  "
             f"{m['duration_s']} s, {m['n_ranges']} ranges on {m['n_links']} links ({m['range_rate_hz']} Hz per link)",
             f"optional fields present (fraction of ranges): {m['optional_fields']}",
             f"self-location ({m['self_location']}, relative frame):"]
    for p in rec["pucks"]:
        xy = rec["pucks_rel"].get(p)
        lines.append(f"  {p}: {xy}  sigma {rec['pucks_rel_sigma'].get(p)}")
    if rec["pucks_rel"]:
        res = shadow.locate(rec["ranges"], rec["pucks_rel"])
        lines.append("link table (cross-link fit; a link with score >= 0.5 counts as shadowed):")
        for r in res["per_link"]:
            lines.append(f"  {r['a']}-{r['b']}  d {r['d_m']}  fp {r['fp_dbm']}  rssi {r['rssi_dbm']}  los {r['los']}  "
                         f"below fit {r['drop_db']} dB  score {r['score']}")
        lines.append(f"shadow: present={res['present']} xy=({res['x']}, {res['y']}) sigma {res['sigma_m']} "
                     f"conf {res['confidence']}  {res['why']}")
    if rec["frames"]:
        lines.append(f"CIR: {len(rec['frames'])} ordered links, {m['frame_hz']} frames/s per link"
                     f"{' (SYNTHETIC frames)' if m['synthetic_cir'] else ''}")
        for (tx, rx), f in sorted(rec["frames"].items()):
            mag = np.abs(f["cir"]).mean(0)
            pk = int(np.argmax(mag))
            lines.append(f"  {tx}>{rx}  {len(f['t'])} frames  first-path index {np.median(f['fp_index']):.2f} "
                         f"(expect about {K0})  peak tap {pk}  peak {mag[pk]:.0f}  pre-path noise {mag[1:5].mean():.1f}  "
                         f"accumulated {np.median(f['pacc']):.0f} symbols")
    elif m.get("cir") is False:
        lines.append("CIR: no frames (stock kit firmware, or no side channel given: --cir udp:0.0.0.0:1946 | serial:auto)")
    for x in rec.get("cir_stats", {}).get("log", []):
        lines.append("  cir log: " + x)
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sandtable.scout.source_kit",
                                 description="What do the pucks see? Kit hub (mesh5) + optional CIR side channel.")
    ap.add_argument("--url", default=None, help="kit hub stream, e.g. ws://127.0.0.1:1910/stream (default $UWBKIT_URL)")
    ap.add_argument("--replay", default=None, help="read a kit NDJSON recording instead of a hub")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--cir", default=None, help="udp:0.0.0.0:1946 (boards on Wi-Fi) | serial:auto[>127.0.0.1:1801] (boards on USB)")
    ap.add_argument("--watch", action="store_true", help="rung 2 live: learn the empty room for --learn s, then print the shadow fix every second")
    ap.add_argument("--learn", type=float, default=4.0)
    ap.add_argument("--save", default=None, help="write the kit messages as NDJSON (a replayable recording)")
    a = ap.parse_args(argv)
    import os
    url = a.url or os.environ.get("UWBKIT_URL") or "ws://127.0.0.1:1910/stream"
    if a.replay:
        rec = from_messages(load_ndjson(a.replay))
        print(report(rec))
        return 0
    if not a.watch:
        rec = collect(url, a.seconds, cir=a.cir)
        print(report(rec))
        if a.save:
            Path(a.save).write_text("\n".join(json.dumps(r) for r in rec["ranges"]) + "\n", encoding="utf-8")
        return 0
    from uwbkit.client import Stream
    from . import shadow
    print(f"learning the empty room for {a.learn:.0f} s: nobody between the pucks ...", flush=True)
    msgs, base, last = [], None, 0.0
    t0 = time.time()
    for m in Stream(url, timeout=3.0):
        msgs.append(m)
        now = time.time()
        if base is None and now - t0 >= a.learn:
            rec = from_messages(msgs)
            base = shadow.baseline(rec["ranges"], seconds=a.learn)
            print(f"baseline on {len(base['links'])} links. Walk between the pucks.", flush=True)
        if base is not None and now - last >= 1.0:
            last = now
            msgs = msgs[-2000:]
            rec = from_messages(msgs)
            res = shadow.locate(rec["ranges"], rec["pucks_rel"], baseline=base, window_s=1.5)
            down = " ".join(f"{d['a']}-{d['b']}" for d in res["links_down"]) or "-"
            print(f"{time.strftime('%H:%M:%S')}  {'PRESENT' if res['present'] else 'clear  '}  "
                  f"xy=({res['x']}, {res['y']})  sigma {res['sigma_m']}  conf {res['confidence']}  down: {down}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
