"""``uwbkit flash``: put kit firmware on a board and give it an ID, on Windows or Linux.

    uwbkit flash --list                                   firmware variants, with their compile status
    uwbkit flash --print                                  the exact esptool / arduino-cli commands (copy-paste)
    uwbkit flash --variant dw3000 --port COM5 --id A1     flash the prebuilt binary, then set ROLE/ID and SAVE
    uwbkit flash --variant dw3000 --all 4A1T              guided: plug the boards in one at a time (A1..A4, T1)
    uwbkit flash --assign --port COM5 --id T1             no flashing: only set the ID (role follows the letter)

Prebuilt binaries come from ``firmware/bin/`` (listed in ``firmware/STATUS.json``; built with
arduino-cli, logs in ``firmware/build_logs/``). Flashing uses esptool (``pip install esptool``, pure
Python, works on Windows); without it the tool prints the arduino-cli command instead.
One binary fits every board of a kind: the role and ID are stored on the board by serial command.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

from ..protocol import LineParser

ROLE_OF = {"A": "anchor", "T": "tag", "N": "mesh"}
ALIASES = {"dw3000": "dw3000", "dw3k": "dw3000", "dw1000": "dw1000", "dw1k": "dw1000",
           "mauwb": "mauwb", "s3": "mauwb", "bridge": "mauwb"}


def firmware_dir(explicit=None) -> Path | None:
    cands = [Path(explicit)] if explicit else []
    here = Path(__file__).resolve()
    cands += [here.parents[2] / "firmware", Path.cwd() / "firmware", Path.cwd() / "uwb-kit" / "firmware"]
    for c in cands:
        if (c / "STATUS.json").exists():
            return c
    for c in cands:
        if c.is_dir():
            return c
    return None


def load_status(fw: Path | None) -> list:
    if fw is None or not (fw / "STATUS.json").exists():
        return []
    try:
        data = json.loads((fw / "STATUS.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("sketches", []) if isinstance(data, dict) else []


def pick(sketches: list, variant: str):
    key = ALIASES.get(variant.lower(), variant.lower())
    for s in sketches:
        if key in str(s.get("name", "")).lower():
            return s
    return None


def ids_for(profile: str) -> list:
    from ..hub import profile_nodes
    return list(profile_nodes(profile)) or ["A1", "A2", "A3", "A4", "T1"]


def esptool_cmd(port: str, bin_path: Path, baud: int) -> list:
    return [sys.executable, "-m", "esptool", "--chip", "auto", "--port", port, "--baud", str(baud),
            "write_flash", "0x0", str(bin_path)]


def print_commands(fw: Path | None, sketches: list, variant=None, port=None) -> None:
    print("Flashing commands (replace the port: Windows COM5, Linux /dev/ttyUSB0 or /dev/ttyACM0).")
    print("Hold nothing on these boards normally; if the upload does not start, hold BOOT while it connects.\n")
    for s in sketches:
        if variant and pick([s], variant) is None:
            continue
        name, fqbn = s.get("name"), s.get("fqbn")
        if "__" in str(name):
            continue                      # build-flag check variants of a sketch listed above
        folder = str(s.get("sketch") or f"{name}/x").rsplit("/", 1)[0]
        state = "compile-verified" if s.get("compiled") else "UNCOMPILED SKETCH"
        print(f"== {name}  ({s.get('board', '')}; {state}; never run on hardware) ==")
        binrel = s.get("bin")
        if binrel:
            for label, p in (("Windows", port or "COM5"), ("Linux  ", port or "/dev/ttyUSB0")):
                print(f"  {label}  python -m esptool --chip auto --port {p} --baud 460800 write_flash 0x0 "
                      f"firmware/{binrel}")
        print("  build + upload from source:")
        print(f"           arduino-cli compile --fqbn {fqbn} firmware/{folder}")
        print(f"           arduino-cli upload  --fqbn {fqbn} -p {port or '<port>'} firmware/{folder}")
        if "unoq" not in str(name):
            print(f"  then     uwbkit flash --assign --port {port or '<port>'} --id A1     (A1..A4 anchors, T1..T2 tags, N1..N5 mesh)")
        print()
    if not sketches:
        print("firmware/STATUS.json not found: run from the uwb-kit checkout, or pass --firmware-dir.")


def talk(port: str, lines: list, seconds: float = 2.0, baud: int = 115200) -> list:
    """Send command lines to a board and collect the node messages (banner / K replies) it prints."""
    import serial
    parser = LineParser("uwbkit")
    out = []
    ser = serial.serial_for_url(port, baudrate=baud, timeout=0.1, do_not_open=True)
    for attr in ("dtr", "rts"):
        with contextlib.suppress(Exception):
            setattr(ser, attr, False)
    ser.open()
    try:
        t_end = time.time() + 1.0
        while time.time() < t_end:                      # let a freshly reset board finish booting
            out += parser.feed(ser.read(256) or b"")
        for line in lines:
            ser.write(line.encode() + b"\n")
            t_end = time.time() + seconds / max(1, len(lines)) + 0.3
            while time.time() < t_end:
                out += parser.feed(ser.read(256) or b"")
    finally:
        ser.close()
    return [m for m in out if m.get("type") == "node"]


def assign(port: str, node_id: str, role: str | None = None) -> bool:
    role = role or ROLE_OF.get(node_id[:1].upper(), "tag")
    try:
        msgs = talk(port, [f"ROLE {role}", f"ID {node_id}", "SAVE", "?"], seconds=3.0)
    except Exception as e:
        print(f"  could not talk to {port}: {e}")
        return False
    banners = [m for m in msgs if m.get("banner")]
    if banners and banners[-1].get("id") == node_id and banners[-1].get("role") == role:
        b = banners[-1]
        print(f"  {port}: now id={b['id']} role={b['role']} chip={b.get('chip')} fw={b.get('fw')} (saved)")
        return True
    print(f"  {port}: the board did not confirm id={node_id} role={role}. "
          f"Last banner: {banners[-1] if banners else 'none'}. Is kit firmware running? (uwbkit identify --port {port})")
    return False


def flash_one(fw: Path, sketch: dict, port: str, baud: int, dry: bool) -> bool:
    binrel = sketch.get("bin")
    bin_path = fw / binrel if binrel else None
    if not sketch.get("compiled") or bin_path is None or not bin_path.exists():
        print(f"  no prebuilt binary for {sketch.get('name')} (compiled={sketch.get('compiled')}). Build it:\n"
              f"    arduino-cli compile --fqbn {sketch.get('fqbn')} --output-dir firmware/bin/{sketch.get('name')} firmware/{sketch.get('name')}")
        return False
    cmd = esptool_cmd(port, bin_path, baud)
    if importlib.util.find_spec("esptool") is None:
        print("  esptool is not installed in this Python. Either:\n"
              f"    {sys.executable} -m pip install esptool      and run this command again, or\n"
              f"    arduino-cli upload --fqbn {sketch.get('fqbn')} -p {port} --input-dir {bin_path.parent}")
        return False
    print("  " + " ".join(cmd))
    if dry:
        return True
    rc = subprocess.call(cmd)
    if rc != 0 and baud > 115200:
        print("  retrying at 115200 baud ...")
        rc = subprocess.call(esptool_cmd(port, bin_path, 115200))
    if rc != 0:
        print("  flashing failed. Hold the BOOT button while it says 'Connecting...', check the cable carries data, "
              "close any serial monitor, and try again.")
        return False
    time.sleep(1.5)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uwbkit flash", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="dw3000", help="dw3000 | dw1000 | mauwb (default dw3000)")
    ap.add_argument("--port")
    ap.add_argument("--id", help="node id to store on the board: A1..A4, T1..T2, N1..N5")
    ap.add_argument("--role", choices=["anchor", "tag", "mesh"], help="default: from the ID's letter")
    ap.add_argument("--baud", type=int, default=460800)
    ap.add_argument("--all", metavar="PROFILE", nargs="?", const="4A1T", help="guided loop over a profile's ids (default 4A1T)")
    ap.add_argument("--assign", action="store_true", help="do not flash, only set ROLE/ID and SAVE")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--print", action="store_true", dest="print_cmds")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--firmware-dir")
    a = ap.parse_args(argv)

    fw = firmware_dir(a.firmware_dir)
    sketches = load_status(fw)
    if a.list:
        if not sketches:
            print("firmware/STATUS.json not found (run from the uwb-kit checkout or pass --firmware-dir)")
            return 1
        for s in sketches:
            print(f"{s.get('name'):24s} {s.get('fqbn', ''):28s} "
                  f"{'compile-verified' if s.get('compiled') else 'UNCOMPILED':17s} "
                  f"{(str(s.get('size_bytes')) + ' B') if s.get('size_bytes') else '':>10s}  {s.get('board', '')}")
        print("None of these has run on real hardware yet (ran_on_hardware=false).")
        return 0
    if a.print_cmds:
        print_commands(fw, sketches, a.variant if "--variant" in (argv or sys.argv) else None, a.port)
        return 0
    if a.assign:
        if not a.port or not a.id:
            ap.error("--assign needs --port and --id")
        return 0 if assign(a.port, a.id, a.role) else 2
    sketch = pick(sketches, a.variant)
    if sketch is None:
        print(f"unknown variant {a.variant!r}; available: {[s.get('name') for s in sketches] or 'none (STATUS.json missing)'}")
        return 2
    if a.all:
        ids = ids_for(a.all)
        print(f"Guided flashing of {len(ids)} boards as {', '.join(ids)} with {sketch.get('name')}.")
        print("Label each board with its ID (tape + marker) as you go.\n")
        from .identify import list_ports
        done = []
        for nid in ids:
            before = {p["port"] for p in list_ports()}
            port = a.port
            if not port:
                input(f"[{nid}] Unplug the previous board, plug in the board that will be {nid}, then press Enter ... ")
                time.sleep(1.0)
                now = [p for p in list_ports() if p["vid"] is not None]
                new = [p["port"] for p in now if p["port"] not in before] or [p["port"] for p in now]
                if len(new) != 1:
                    port = input(f"    which port? {new or 'none found'}: ").strip()
                else:
                    port = new[0]
            ok = flash_one(fw, sketch, port, a.baud, a.dry_run) and (a.dry_run or assign(port, nid))
            done.append((nid, port, ok))
            if a.port:
                break
        print("\nSummary:")
        for nid, port, ok in done:
            print(f"  {nid:3s} {port:16s} {'ok' if ok else 'FAILED'}")
        return 0 if all(ok for _, _, ok in done) else 2
    if not a.port:
        ap.error("--port is required (see `uwbkit identify`), or use --all for the guided loop")
    if not flash_one(fw, sketch, a.port, a.baud, a.dry_run):
        return 2
    if a.id and not a.dry_run:
        return 0 if assign(a.port, a.id, a.role) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
