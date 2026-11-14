"""Where puck-side log lines come from. Each source is a thread that calls `on_line(text)` per line and reconnects quietly.

EXPERIMENTAL; none of these has been connected to a real board. Sources (docs/research/puck_hardware.md section 2):

- `RttTelnetSource(host, port)`: SEGGER's RTT telnet channel. "The J-Link software provides a TELNET-like channel on a
  localhost port (default: 19021)" while a J-Link connection is active (for example `JLinkExe` connected to the board,
  or `JLinkGDBServer`), RTT channel 0. https://kb.segger.com/RTT . Qorvo's stock NI firmware logs over RTT on the J-Link
  port J9 ("QNI uses a rtt_reporter task to print all the info", https://forum.qorvo.com/t/dwm3001cdk-nearby-interaction-trying-to-get-ranging-via-usb-uart/13613).
- `FileFollowSource(path)`: follows a file written by `JLinkRTTLogger` (like `tail -f`; starts at the end of the file).
- `SerialSource(port)`: a USB serial port, for the DW3_QM33 SDK CLI firmware, which prints JSON lines with `D_cm` on the
  nRF52833's own USB (J20) (https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430). Needs `pyserial`. The stock
  NI firmware prints NOTHING on any serial port ("QANI doesn't have usb uart support").
- `ReplaySource(lines)`: `--dry-run`; replays built-in sample lines (the research's examples, not measurements).
"""
from __future__ import annotations

import os
import socket
import threading
import time

__all__ = ["LineSource", "RttTelnetSource", "FileFollowSource", "SerialSource", "ReplaySource", "pick_serial_port", "parse_hostport"]

SEGGER_VID = 0x1366     # USB vendor id of SEGGER J-Link (the J9 port's virtual COM port)
NORDIC_VID = 0x1915     # USB vendor id Nordic firmware commonly uses on the nRF52833's own USB (J20); not confirmed for Qorvo's CLI


def parse_hostport(text: str, default_port: int = 19021) -> tuple[str, int]:
    """'HOST:PORT', 'HOST' or ':PORT' -> (host, port)."""
    text = (text or "").strip()
    if not text:
        return "127.0.0.1", default_port
    if text.startswith("[") and "]" in text:                 # [ipv6]:port
        host, _, rest = text[1:].partition("]")
        return host, int(rest.lstrip(":") or default_port)
    if text.count(":") == 1:
        host, port = text.split(":")
        return host or "127.0.0.1", int(port or default_port)
    return text, default_port


class LineSource(threading.Thread):
    """Base: a daemon thread with a stop event, a connected flag and counters. Subclasses implement `_session()`."""

    kind = "source"

    def __init__(self, on_line, stop: threading.Event, log=print):
        super().__init__(daemon=True, name=f"beacon-host-{self.kind}")
        self.on_line = on_line
        self.stop_event = stop
        self.log = log
        self.connected = False
        self.lines = 0
        self.last_line_t: float | None = None
        self._said_down = False

    def _emit(self, text: str) -> None:
        self.lines += 1
        self.last_line_t = time.time()
        try:
            self.on_line(text)
        except Exception as e:      # noqa: BLE001 - a bad line must never kill the reader
            self.log(f"{self.kind}: line handler failed: {type(e).__name__}: {e}")

    def _session(self) -> None:     # read until the connection ends; raise OSError on failure
        raise NotImplementedError

    def describe(self) -> str:
        return self.kind

    def run(self) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                self._session()
                backoff = 1.0
            except (OSError, ValueError) as e:
                if not self._said_down:
                    self.log(f"{self.kind}: {self.describe()} not available ({type(e).__name__}: {e}); retrying quietly")
                    self._said_down = True
            except Exception as e:  # noqa: BLE001
                self.log(f"{self.kind}: unexpected {type(e).__name__}: {e}; retrying")
            self.connected = False
            self.stop_event.wait(backoff)
            backoff = min(backoff * 1.6, 8.0)

    def _connected_now(self) -> None:
        self.connected = True
        if self._said_down:
            self.log(f"{self.kind}: {self.describe()} connected")
        self._said_down = False


class _LineSplitter:
    """Bytes in, text lines out. Strips telnet IAC negotiation and NULs; tolerates CR, LF and CRLF."""

    def __init__(self):
        self.buf = bytearray()

    @staticmethod
    def _strip_iac(data: bytes) -> bytes:
        if b"\xff" not in data:
            return data
        out = bytearray()
        i = 0
        while i < len(data):
            b = data[i]
            if b == 0xFF and i + 1 < len(data):
                cmd = data[i + 1]
                if cmd == 0xFF:              # escaped 0xFF
                    out.append(0xFF)
                    i += 2
                elif cmd in (0xFB, 0xFC, 0xFD, 0xFE):   # WILL WONT DO DONT + option
                    i += 3
                else:
                    i += 2
            else:
                out.append(b)
                i += 1
        return bytes(out)

    def feed(self, data: bytes) -> list[str]:
        self.buf += self._strip_iac(data).replace(b"\x00", b"")
        lines = []
        while True:
            idx = -1
            for sep in (b"\n", b"\r"):
                j = self.buf.find(sep)
                if j != -1 and (idx == -1 or j < idx):
                    idx = j
            if idx == -1:
                break
            raw = bytes(self.buf[:idx])
            del self.buf[: idx + 1]
            text = raw.decode("utf-8", "replace").strip()
            if text:
                lines.append(text)
        if len(self.buf) > 8192:            # a line that never ends: flush it rather than grow forever
            lines.append(bytes(self.buf).decode("utf-8", "replace").strip())
            self.buf.clear()
        return lines


class RttTelnetSource(LineSource):
    kind = "rtt"

    def __init__(self, host: str, port: int, on_line, stop, log=print):
        super().__init__(on_line, stop, log)
        self.host, self.port = host, port

    def describe(self) -> str:
        return f"RTT telnet {self.host}:{self.port} (is JLinkExe or JLinkGDBServer connected to the board?)"

    def _session(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=3.0) as s:
            s.settimeout(0.5)
            self._connected_now()
            split = _LineSplitter()
            while not self.stop_event.is_set():
                try:
                    data = s.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    raise ConnectionError("J-Link closed the RTT telnet connection")
                for line in split.feed(data):
                    self._emit(line)


class FileFollowSource(LineSource):
    kind = "rtt-log"

    def __init__(self, path: str, on_line, stop, log=print, from_start: bool = False):
        super().__init__(on_line, stop, log)
        self.path = path
        self.from_start = from_start

    def describe(self) -> str:
        return f"RTT log file {os.path.basename(self.path)} (is JLinkRTTLogger writing it?)"

    def _session(self) -> None:
        with open(self.path, "rb") as f:
            if not self.from_start:
                f.seek(0, os.SEEK_END)
            self.from_start = True          # after a rotation, read the new file from its start
            self._connected_now()
            split = _LineSplitter()
            pos = f.tell()
            while not self.stop_event.is_set():
                data = f.read(65536)
                if data:
                    pos = f.tell()
                    for line in split.feed(data):
                        self._emit(line)
                    continue
                try:
                    size = os.path.getsize(self.path)
                except OSError:
                    raise FileNotFoundError("the log file went away")
                if size < pos:              # truncated or replaced: start again
                    return
                self.stop_event.wait(0.2)


def pick_serial_port(log=print) -> str | None:
    """`--serial auto`: the nRF52833's own USB (J20) when it can be told apart, else the J-Link VCOM, else the first port."""
    try:
        from serial.tools import list_ports      # pyserial
    except ImportError:
        log("serial: pyserial is not installed (pip install pyserial)")
        return None
    ports = list(list_ports.comports())
    if not ports:
        return None
    for p in ports:
        log(f"serial: candidate {p.device}  vid={p.vid if p.vid is None else hex(p.vid)}  {p.description}")

    def score(p) -> int:
        text = f"{p.description} {p.manufacturer or ''} {p.product or ''}".lower()
        if p.vid == NORDIC_VID or any(w in text for w in ("qorvo", "dw3", "qm33", "cli")):
            return 0
        if p.vid == SEGGER_VID or "j-link" in text or "segger" in text:
            return 1
        if "acm" in p.device.lower() or "usbmodem" in p.device.lower():
            return 2
        return 3

    best = sorted(ports, key=score)[0]
    log(f"serial: auto picked {best.device}")
    return best.device


class SerialSource(LineSource):
    kind = "serial"

    def __init__(self, port: str, on_line, stop, log=print, baud: int = 115200):
        super().__init__(on_line, stop, log)
        self.port_arg, self.baud = port, baud
        self.port: str | None = None

    def describe(self) -> str:
        return f"serial port {self.port or self.port_arg} at {self.baud} baud"

    def _session(self) -> None:
        try:
            import serial                            # pyserial, optional
        except ImportError as e:
            raise OSError("pyserial is not installed (pip install pyserial)") from e
        self.port = pick_serial_port(self.log) if self.port_arg == "auto" else self.port_arg
        if not self.port:
            raise OSError("no serial port found (data cable? board on J20 with the CLI firmware?)")
        with serial.Serial(self.port, self.baud, timeout=0.5) as ser:
            self._connected_now()
            split = _LineSplitter()
            while not self.stop_event.is_set():
                data = ser.read(4096)
                if data:
                    for line in split.feed(data):
                        self._emit(line)


class ReplaySource(LineSource):
    """--dry-run: replays sample lines (NOT measurements) in a loop, one every `period_s`."""
    kind = "dry-run"

    def __init__(self, lines: list[str], on_line, stop, log=print, period_s: float = 0.25):
        super().__init__(on_line, stop, log)
        self.sample = list(lines)
        self.period_s = period_s

    def describe(self) -> str:
        return "built-in sample lines (the research's examples; not measurements)"

    def _session(self) -> None:
        self._connected_now()
        i = 0
        while not self.stop_event.is_set():
            self._emit(self.sample[i % len(self.sample)])
            i += 1
            self.stop_event.wait(self.period_s)
