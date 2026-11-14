"""``uwbkit identify``: which board is plugged in?

Lists every USB serial port (VID/PID -> the USB-UART chip family), then opens each candidate at
115200 baud, asks for the kit banner (``?``) and classifies what answers:

    kit firmware        banner with id / role / chip            -> ready, nothing to flash
    MaUWB AT firmware   AT+RANGE lines, or "OK" to "AT"         -> use as is (--proto mauwb) or flash the bridge
    DWM1001 PANS        "dwm>" shell / les / lec lines          -> use as is (--proto dwm1001)
    vendor example      "from: 1786  Range: 2.43 m ..."         -> works for one tag; flash kit firmware for more
    ESP32 boot log      "ets ..." / "rst:0x..." / "ESP-ROM:"    -> an ESP32 without kit firmware: flash it
    silent                                                      -> wrong baud, no firmware, or a charge-only cable

Usage:  uwbkit identify [--port COM5] [--reset] [--json] [--seconds 2.5]
``--reset`` pulses the ESP32 reset line (RTS) to capture the boot log (tells ESP32 from ESP32-S3).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time

from ..protocol import LineParser
from ..sources.serial_src import describe_usb

ADVICE = {
    "uwbkit": "kit firmware is running: nothing to flash",
    "mauwb": "Makerfabs MaUWB (AT firmware): the hub reads it as is (uwbkit hub --source serial:PORT), "
             "or flash firmware/uwbkit_mauwb_bridge for Wi-Fi + kit commands",
    "dwm1001": "DWM1001 with PANS firmware: type `les` in its shell, then uwbkit hub --source serial:PORT",
    "example": "vendor example sketch: ranges are readable (one tag only); flash kit firmware: uwbkit flash --port PORT",
    "esp32": "ESP32 without kit firmware: uwbkit flash --port PORT --variant dw3000   (or dw1000; see BRINGUP.md)",
    "esp32s3": "ESP32-S3 (MaUWB style) without kit firmware: uwbkit flash --port PORT --variant mauwb",
    "silent": "no output: try --reset; check the cable carries data; if it is an ESP32 board, flash it: uwbkit flash --port PORT",
    "text": "prints text the kit does not recognise (shown below): flash kit firmware: uwbkit flash --port PORT",
    "error": "could not open the port (in use by another program? close Arduino Serial Monitor / the hub)",
}


def list_ports() -> list:
    from serial.tools import list_ports as lp
    out = []
    for p in sorted(lp.comports(), key=lambda p: p.device):
        out.append({"port": p.device, "vid": p.vid, "pid": p.pid,
                    "usb": None if p.vid is None else f"{p.vid:04X}:{p.pid:04X}",
                    "bridge": describe_usb(p.vid, p.pid), "description": p.description,
                    "manufacturer": p.manufacturer, "serial": p.serial_number})
    return out


def classify(lines: list, parser: LineParser, banners: list) -> tuple:
    text = "\n".join(lines)
    if banners:
        b = banners[0]
        ids = ", ".join(f"{x.get('id')} ({x.get('role')}, {x.get('chip')})" for x in banners)
        return "uwbkit", f"id={b.get('id')} role={b.get('role')} chip={b.get('chip')} fw={b.get('fw')}" + \
            (f"  [also relays: {ids}]" if len(banners) > 1 else "")
    seen = parser.stats["proto_seen"]
    for proto in ("mauwb", "dwm1001", "example"):
        if seen.get(proto):
            return proto, f"{seen[proto]} lines in {proto} format"
    if seen.get("uwbkit"):
        return "uwbkit", "kit protocol lines, but no banner yet (old firmware?)"
    if "dwm>" in text:
        return "dwm1001", "PANS shell prompt"
    if "ESP-ROM:esp32s3" in text or "esp32s3" in text.lower():
        return "esp32s3", "ESP32-S3 boot log"
    if "ets " in text or "rst:0x" in text or "ESP-ROM:" in text:
        return "esp32", "ESP32 boot log"
    if any(ln.strip() == "OK" for ln in lines):
        return "mauwb", "answers OK to AT"
    if text.strip():
        return "text", f"{len(lines)} unrecognised lines"
    return "silent", "no data"


def probe(port: str, baud: int = 115200, seconds: float = 2.5, reset: bool = False) -> dict:
    """Open one port and work out what is talking on it."""
    import serial
    parser = LineParser("auto")
    banners, lines, raw = [], [], bytearray()

    def pump(ser, duration):
        t_end = time.time() + duration
        while time.time() < t_end:
            data = ser.read(max(1, min(4096, getattr(ser, "in_waiting", 0) or 1)))
            if not data:
                continue
            raw.extend(data)
            for m in parser.feed(data):
                if m.get("type") == "node" and m.get("banner"):
                    banners.append(m)
                elif m.get("type") == "log":
                    lines.append(m.get("text", ""))

    try:
        ser = serial.serial_for_url(port, baudrate=baud, timeout=0.1, do_not_open=True)
        for attr in ("dtr", "rts"):
            with contextlib.suppress(Exception):
                setattr(ser, attr, False)
        ser.open()
    except Exception as e:
        return {"port": port, "kind": "error", "detail": str(e), "lines": []}
    try:
        if reset:
            with contextlib.suppress(Exception):
                ser.dtr = False
                ser.rts = True      # EN low
                time.sleep(0.12)
                ser.rts = False     # release: the chip boots and prints its ROM log
            pump(ser, 1.2)
        pump(ser, 0.4)
        with contextlib.suppress(Exception):
            ser.write(b"?\n")
        pump(ser, seconds)
        if not banners and not parser.stats["lines_ok"]:
            with contextlib.suppress(Exception):
                ser.write(b"AT\r\n")           # MaUWB AT firmware answers OK
            pump(ser, 0.7)
        if not banners and not parser.stats["lines_ok"] and not any(ln.strip() == "OK" for ln in lines):
            with contextlib.suppress(Exception):
                ser.write(b"\r\r")             # DWM1001 PANS: double Enter opens the shell
            pump(ser, 0.8)
    finally:
        with contextlib.suppress(Exception):
            ser.close()
    kind, detail = classify(lines, parser, banners)
    return {"port": port, "kind": kind, "detail": detail, "bytes": len(raw),
            "banners": [{k: v for k, v in b.items() if k not in ("type", "banner")} for b in banners],
            "lines": lines[-6:], "stats": {k: parser.stats[k] for k in ("lines_ok", "lines_bad", "bad_checksum")}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uwbkit identify", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", action="append", help="probe only this port (repeatable; pyserial URLs work)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=2.5)
    ap.add_argument("--reset", action="store_true", help="pulse RTS to reset an ESP32 and read its boot log")
    ap.add_argument("--all", action="store_true", help="probe every serial port, not only USB ones")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    ports = list_ports()
    if a.port:
        known = {p["port"]: p for p in ports}
        targets = [known.get(p, {"port": p, "usb": None, "bridge": None, "description": ""}) for p in a.port]
    else:
        targets = [p for p in ports if a.all or p["vid"] is not None]
    results = []
    for p in targets:
        r = probe(p["port"], a.baud, a.seconds, a.reset)
        r.update({k: p.get(k) for k in ("usb", "bridge", "description", "serial")})
        if r["kind"] in ("silent", "text") and p.get("vid") == 0x1366:
            r["kind"], r["detail"] = "dwm1001", "SEGGER J-Link CDC port (DWM1001-DEV / DWM3001CDK): press Enter twice for the shell"
        r["advice"] = ADVICE.get(r["kind"], "").replace("PORT", p["port"])
        results.append(r)
    if a.json:
        print(json.dumps({"ports": ports, "results": results}, indent=1))
        return 0
    if not ports:
        print("No serial ports found. Plug a board in with a DATA cable. On Windows install the CP210x or CH340 "
              "driver if no COM port appears; on Linux add yourself to the dialout group.")
        return 1
    if not targets:
        print("No USB serial ports found (only built-in ports). Plug a board in, or use --all / --port.")
        return 1
    for r in results:
        print(f"{r['port']}")
        print(f"    usb     {r.get('usb') or '-'}  {r.get('bridge') or r.get('description') or ''}")
        print(f"    found   {r['kind'].upper()}: {r['detail']}")
        for b in r.get("banners", [])[:6]:
            print(f"            {b}")
        if r["kind"] in ("text", "silent") and r.get("lines"):
            for ln in r["lines"]:
                print(f"            | {ln[:100]}")
        print(f"    next    {r['advice']}")
    ready = sum(r["kind"] == "uwbkit" for r in results)
    print(f"\n{len(results)} port(s) probed, {ready} running kit firmware.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
