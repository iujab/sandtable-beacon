"""Restart a puck's board, so a phone whose UWB session to it dropped redoes the Nearby Interaction handshake.

Seen on the owner's boards (2026-11-15): one board's UWB distance stopped while its Bluetooth link stayed up, so the
phone was reduced to one ring. Resetting that board (its app reconnects within about 10 s) brought the UWB distance back.

How a puck is found: its Bluetooth name is "DWM3001CDK (XXXXXXXX)" (a suffix such as "#1900" may follow), and the eight
hex digits are the low 32 bits of the nRF52833's FICR DEVICEADDR. Each board's on-board J-Link (USB vendor 0x1366)
is read over SWD with OpenOCD to match a J-Link serial to that address; the matching board is then reset with
`reset run`. This runs on the machine the boards are wired to (the Arduino UNO Q). It needs OpenOCD with the J-Link
driver and write access to the J-Link USB device (root, or a udev rule such as hardware/99-segger-jlink.rules).
Nothing here touches a board unless `restart(..., dry_run=False)` is called.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time

JLINK_VENDOR = "1366"
FICR_DEVICEADDR0 = 0x100000A4
_NAME_ADDR = re.compile(r"\(([0-9A-Fa-f]{8})\)")
_MDW = re.compile(r"0x100000a4:\s*([0-9A-Fa-f]{8})", re.I)
_CACHE_S = 600.0
_cache: dict = {"t": 0.0, "serials": (), "map": {}}


def addr_from_accessory(name: str | None) -> str | None:
    """'DWM3001CDK (1BDB5E43)#1900' -> '1BDB5E43'; None when the name carries no address."""
    m = _NAME_ADDR.search(name or "")
    return m.group(1).upper() if m else None


def jlink_serials(sys_root: str = "/sys/bus/usb/devices") -> list[str]:
    """Serial numbers of the J-Links on USB (one per DWM3001CDK), from sysfs; works while OpenOCD holds a probe."""
    out = set()
    for d in glob.glob(os.path.join(sys_root, "*")):
        try:
            with open(os.path.join(d, "idVendor")) as f:
                if f.read().strip().lower() != JLINK_VENDOR:
                    continue
            with open(os.path.join(d, "serial")) as f:
                serial = f.read().strip()
        except OSError:
            continue
        if serial.isdigit():
            out.add(serial)
    return sorted(out)


def _openocd(serial: str, commands: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    exe = shutil.which("openocd") or "/usr/bin/openocd"
    args = [exe, "-c", "adapter driver jlink", "-c", f"adapter serial {serial}", "-c", "transport select swd",
            "-f", "target/nrf52.cfg", "-c", "init"]
    for c in commands:
        args += ["-c", c]
    args += ["-c", "exit"]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def read_device_addr(serial: str, run=_openocd) -> str | None:
    """The board's DEVICEADDR[0] as 8 upper-case hex digits, read over SWD without halting it; None if unreadable."""
    r = run(serial, [f"mdw {FICR_DEVICEADDR0:#x} 1"])
    m = _MDW.search((r.stdout or "") + (r.stderr or ""))
    return m.group(1).upper() if m else None


def board_map(serials: list[str] | None = None, run=_openocd, now: float | None = None) -> dict[str, str]:
    """{DEVICEADDR: J-Link serial} for every attached board, cached for 10 minutes while the set of J-Links is unchanged."""
    serials = jlink_serials() if serials is None else serials
    now = time.time() if now is None else now
    if _cache["map"] and tuple(serials) == _cache["serials"] and now - _cache["t"] < _CACHE_S:
        return dict(_cache["map"])
    found = {}
    for s in serials:
        addr = read_device_addr(s, run=run)
        if addr:
            found[addr] = s
    _cache.update(t=now, serials=tuple(serials), map=found)
    return dict(found)


def restart(accessory: str, *, dry_run: bool = False, run=_openocd, serials: list[str] | None = None) -> dict:
    """Reset the board whose Bluetooth name is `accessory`. Raises ValueError when the name carries no address or no
    attached board has it; OSError / subprocess.TimeoutExpired when OpenOCD cannot run or reach the probe."""
    want = addr_from_accessory(accessory)
    if not want:
        raise ValueError(f"the board name {accessory!r} carries no address: expected 'DWM3001CDK (XXXXXXXX)'")
    serials = jlink_serials() if serials is None else serials
    if not serials:
        raise ValueError("no J-Link (DWM3001CDK) is attached to this machine over USB")
    mapping = board_map(serials, run=run)
    serial = mapping.get(want)
    if serial is None:
        _cache["map"] = {}                                    # a board may have been swapped: rescan next time
        raise ValueError(f"no attached board has the address {want} (found: {', '.join(sorted(mapping)) or 'none readable'})")
    if dry_run:
        return {"ok": True, "dry_run": True, "address": want, "jlink": serial}
    r = run(serial, ["reset run"])
    ok = r.returncode == 0
    detail = "" if ok else ((r.stderr or r.stdout or "").strip().splitlines() or ["OpenOCD failed"])[-1][:200]
    return {"ok": ok, "dry_run": False, "address": want, "jlink": serial, **({"detail": detail} if detail else {})}
