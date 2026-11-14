"""USB-serial source: reads the board line protocol (or a vendor format) from one or more ports.

    --source serial:auto                 scan for UWB boards, hot-plug tolerant
    --source serial:COM5                 Windows
    --source serial:/dev/ttyUSB0,/dev/ttyUSB1@115200
    --source serial:socket://127.0.0.1:5555     (pyserial URL: how the tests and the fake board drive this path)

Blocking pyserial reads run in one worker thread per port; messages are handed to the asyncio loop
with ``call_soon_threadsafe`` (works on Windows, where serial ports cannot be polled by asyncio).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time

from ..protocol import LineParser
from . import Source

log = logging.getLogger("uwbkit.source.serial")

# USB-UART bridges and native-USB chips found on UWB dev boards: (vid, pid or None) -> description
KNOWN_USB = {
    (0x10C4, 0xEA60): "CP210x (Makerfabs ESP32 UWB, many ESP32 dev boards)",
    (0x1A86, 0x7523): "CH340 (ESP32 dev boards)",
    (0x1A86, 0x55D4): "CH9102 (ESP32 dev boards, MaUWB)",
    (0x1A86, 0x55D3): "CH343 (ESP32-S3 dev boards)",
    (0x0403, 0x6001): "FTDI FT232",
    (0x0403, 0x6015): "FTDI FT231X",
    (0x303A, 0x1001): "Espressif native USB (ESP32-S3/C3 USB-Serial-JTAG)",
    (0x303A, None): "Espressif native USB",
    (0x1366, None): "SEGGER J-Link CDC (DWM1001-DEV, DWM3001CDK)",
    (0x0483, 0x5740): "STM32 virtual COM port",
    (0x2341, None): "Arduino",
}


def describe_usb(vid, pid):
    """Description for a USB id pair, or None if it is not a known UWB-board bridge."""
    if vid is None:
        return None
    return KNOWN_USB.get((vid, pid)) or KNOWN_USB.get((vid, None))


def candidate_ports() -> list:
    """Serial ports that look like UWB boards: known USB ids first, then any other USB serial port."""
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
    except Exception:
        return []
    known = [p.device for p in ports if describe_usb(p.vid, p.pid)]
    other = [p.device for p in ports if p.vid is not None and p.device not in known]
    return sorted(known) or sorted(other)


class _Port:
    """One serial port: a reader thread with automatic reopen."""

    def __init__(self, url: str, baud: int, proto: str, deliver, id_map=None):
        self.url, self.baud = url, baud
        self.parser = LineParser(proto, id_map)
        self.deliver = deliver          # callable(list_of_msgs), thread-safe
        self.open = False
        self.banner = None
        self.node_ids: set = set()
        self.last_line_t = None
        self.opens = 0
        self.error = None
        self._ser = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=f"uwbkit-serial-{url}", daemon=True)
        self._thread.start()

    def _open(self):
        import serial
        ser = serial.serial_for_url(self.url, baudrate=self.baud, timeout=0.2, do_not_open=True)
        for attr in ("dtr", "rts"):   # keep an ESP32 from being reset / held in the boot loader on open
            with contextlib.suppress(Exception):
                setattr(ser, attr, False)
        ser.open()
        return ser

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ser = self._open()
            except Exception as e:
                self.error = str(e)
                self._stop.wait(1.0)
                continue
            self._ser, self.open, self.error = ser, True, None
            self.opens += 1
            log.info("serial port %s open", self.url)
            try:
                with contextlib.suppress(Exception):
                    ser.write(b"?\n")       # ask for the banner
                while not self._stop.is_set():
                    data = ser.read(max(1, min(4096, getattr(ser, "in_waiting", 0) or 1)))
                    if not data:
                        continue
                    msgs = self.parser.feed(data)
                    if msgs:
                        self.last_line_t = time.time()
                        for m in msgs:
                            if m.get("type") == "node" and isinstance(m.get("id"), str):
                                self.node_ids.add(m["id"])
                                if m.get("banner"):
                                    self.banner = {k: v for k, v in m.items() if k not in ("type", "banner")}
                        self.deliver(msgs)
            except Exception as e:      # unplugged, permission lost, ...
                self.error = str(e)
                log.warning("serial port %s: %s; reopening", self.url, e)
            finally:
                self.open = False
                with contextlib.suppress(Exception):
                    ser.close()
                self._ser = None
            self._stop.wait(1.0)

    def write(self, line: str) -> bool:
        ser = self._ser
        if ser is None or not self.open:
            return False
        try:
            with self._lock:
                ser.write(line.encode("ascii", "replace") + b"\n")
            return True
        except Exception:
            return False

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):
            if self._ser is not None:
                self._ser.cancel_read()

    def info(self) -> dict:
        st = self.parser.stats
        return {"port": self.url, "open": self.open, "bytes": st["bytes"], "lines_ok": st["lines_ok"],
                "lines_bad": st["lines_bad"], "bad_checksum": st["bad_checksum"],
                "lines_other": st["lines_other"], "proto_seen": dict(st["proto_seen"]),
                "last_line_age_s": None if self.last_line_t is None else round(time.time() - self.last_line_t, 2),
                "banner": self.banner, "nodes": sorted(self.node_ids), "opens": self.opens, "error": self.error}


class SerialSource(Source):
    def __init__(self, port: str = "auto", baud: int = 115200, proto: str = "auto", id_map: dict | None = None):
        self.id_map = id_map
        self.spec = port or "auto"
        self.baud = int(baud)
        self.proto = proto
        self.auto = self.spec.lower() == "auto"
        self.name = f"serial:{self.spec}"
        self.ports: dict[str, _Port] = {}
        self._gone: dict = {}

    async def run(self, emit) -> None:
        loop = asyncio.get_running_loop()

        def on_loop(msgs):
            for m in msgs:
                emit(m)

        def deliver(msgs):
            with contextlib.suppress(RuntimeError):   # loop closed during shutdown
                loop.call_soon_threadsafe(on_loop, msgs)

        try:
            if not self.auto:
                for url in [p.strip() for p in self.spec.split(",") if p.strip()]:
                    self.ports[url] = _Port(url, self.baud, self.proto, deliver, self.id_map)
            while True:
                if self.auto:
                    found = await loop.run_in_executor(None, candidate_ports)
                    for url in found:
                        if url not in self.ports:
                            log.info("found serial port %s", url)
                            self.ports[url] = _Port(url, self.baud, self.proto, deliver, self.id_map)
                    for url in [u for u in self.ports if u not in found]:
                        self.ports.pop(url).stop()   # unplugged
                await asyncio.sleep(2.0)
        finally:
            for p in self.ports.values():
                p.stop()
            self.ports.clear()

    async def send_command(self, line: str, node: str | None = None) -> bool:
        targets = [p for p in self.ports.values() if node is None or node in p.node_ids]
        if node is not None and not targets:      # unknown node: a gateway may relay it
            targets = list(self.ports.values())
        ok = False
        for p in targets:
            ok = p.write(line) or ok
        return ok

    def info(self) -> dict:
        ports = [p.info() for p in self.ports.values()]
        return {"name": self.name,
                "lines_ok": sum(p["lines_ok"] for p in ports),
                "lines_bad": sum(p["lines_bad"] for p in ports),
                "bad_checksum": sum(p["bad_checksum"] for p in ports),
                "ports": ports}
