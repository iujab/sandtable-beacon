"""``uwbkit`` command line: hub, scenarios, listen, fakeboard, identify, flash, doctor, anchors, eval."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import os
import sys

from . import DEFAULT_PORT, __version__

TOOLS = {
    "fakeboard": ("uwbkit.tools.fakeboard", "emulate UWB boards over UDP / TCP / pty (no hardware needed)"),
    "identify": ("uwbkit.tools.identify", "tell which board is plugged in (USB VID/PID + serial banner)"),
    "flash": ("uwbkit.tools.flash", "flash kit firmware to a board (wraps esptool / arduino-cli)"),
    "doctor": ("uwbkit.tools.doctor", "check every node is ranging: rates and a per-link table"),
    "anchors": ("uwbkit.tools.anchors", "set anchor coordinates by tape measure, or run self-calibration"),
    "eval": ("uwbkit.tools.evaluate", "solver accuracy against simulator ground truth"),
    "unoq-matrix": ("uwbkit.tools.unoq_matrix", "show hub status on the Arduino UNO Q LED matrix"),
}


def _load_anchors(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    anchors = data.get("anchors", data) if isinstance(data, dict) else None
    if not isinstance(anchors, dict) or not all(isinstance(v, dict) for v in anchors.values()):
        raise ValueError(f"{path}: expected an anchors message or {{\"A1\":{{\"x\":0,\"y\":0,\"z\":2}}, ...}}")
    return anchors


def cmd_hub(args) -> int:
    from .hub import Hub, serve
    from .sources import make_source

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    scenario, profile = args.scenario, args.profile
    is_sim = args.source.split(":", 1)[0].strip().lower() == "sim"
    if is_sim and profile:
        # the scenario defines the profile; --profile without --scenario picks a matching built-in
        from .sim import list_scenarios, load_scenario
        try:
            if scenario is None and ":" not in args.source:
                match = [n for n in list_scenarios() if str(load_scenario(n).get("profile", "")).lower() == profile.lower()]
                preferred = {"4a1t": "office-walk", "3a2t": "two-tags", "mesh5": "mesh-dance"}.get(profile.lower())
                if match:
                    scenario = preferred if preferred in match else match[0]
                else:
                    print(f"uwbkit: no built-in scenario has profile {profile!r}; using office-walk "
                          "(write a scenario file for other layouts)", file=sys.stderr)
        except ValueError:
            pass
        profile = None
    try:
        id_map = None
        if args.id_map:
            id_map = {}
            for item in args.id_map.split(","):
                k, eq, v = item.partition("=")
                if not eq or not k.strip() or not v.strip():
                    raise ValueError(f"--id-map: expected VENDOR_ID=KIT_ID pairs like 1786=A1,1787=A2, got {item!r}")
                k = k.strip()
                id_map[k.lower() if k.lower() in ("tag", "self", "peer") else k.upper()] = v.strip()
        source = make_source(args.source, scenario=scenario, seed=args.seed, loop=args.loop,
                             speed=args.speed, proto=args.proto, id_map=id_map)
    except (ValueError, OSError) as e:
        print(f"uwbkit: {e}", file=sys.stderr)
        return 2
    anchors = None
    anchors_path = args.anchors
    real = not source.simulated and not getattr(source, "passthrough", False)
    if anchors_path is None and real:
        anchors_path = "uwbkit_anchors.json"   # real boards: calibration survives a hub restart
    if anchors_path and os.path.exists(anchors_path):
        try:
            anchors = _load_anchors(anchors_path)
        except (ValueError, OSError) as e:
            print(f"uwbkit: {e}", file=sys.stderr)
            return 2
    hub = Hub(source, profile=profile, rate_hz=args.rate, dims=args.dims, anchors=anchors,
              anchors_path=anchors_path, tag_z=args.tag_z, record_dir=args.record_dir,
              solve=not args.no_solve)

    async def main() -> None:
        try:
            runner = await serve(hub, args.host, args.port)
        except OSError as e:
            raise SystemExit(f"uwbkit: cannot listen on {args.host}:{args.port} ({e}). "
                             f"Is another hub running? Pick another port with --port.")
        if args.record:
            print(f"recording to {hub._start_record(args.record)}")
        first = args.host.split(",")[0].strip()
        shown = "localhost" if first in ("127.0.0.1", "0.0.0.0", "") else first
        h = hub.hello
        print(f"uwbkit hub {__version__}  source={h['source']}"
              f"{' (SIMULATED)' if h['simulated'] else ''}  profile={h['profile']}  rate={h['rate_hz']:g} Hz\n"
              f"  console  http://{shown}:{args.port}/\n"
              f"  stream   ws://{shown}:{args.port}/stream     sse  http://{shown}:{args.port}/events\n"
              f"  state    http://{shown}:{args.port}/state      diag http://{shown}:{args.port}/diag",
              flush=True)
        if real and not hub.anchors_msg["anchors"] and not hub.is_mesh:
            print("  anchors are not set: run `uwbkit anchors` (tape measure) or POST /calibrate", flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    if args.pidfile:
        with open(args.pidfile, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        if args.pidfile:
            with contextlib.suppress(OSError):
                os.remove(args.pidfile)
    return 0


def cmd_vendor(args) -> int:
    """Copy the kit into a project so its repo stands alone (bench rule: copy, not symlink)."""
    import shutil
    import time
    from pathlib import Path
    pkg = Path(__file__).resolve().parent
    kit_root = pkg.parent
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    if args.client_only:
        (dest / "__init__.py").touch()
        shutil.copy2(pkg / "client.py", dest / "client.py")
        copied.append("client.py")
    else:
        target = dest / "uwbkit"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(pkg, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copied.append("uwbkit/ (hub, simulator, solver, clients, tools)")
        for name in ("pyproject.toml", "INTERFACE.md", "READY"):
            if (kit_root / name).exists() and (kit_root / "uwbkit").is_dir():
                shutil.copy2(kit_root / name, dest / name)
                copied.append(name)
        if not (dest / "pyproject.toml").exists():   # vendoring from an installed (non-editable) kit
            (dest / "pyproject.toml").write_text(
                '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n\n'
                f'[project]\nname = "uwbkit"\nversion = "{__version__}"\nrequires-python = ">=3.10"\n'
                'dependencies = ["aiohttp>=3.10", "numpy>=2.1", "pyserial>=3.5"]\n\n'
                '[project.scripts]\nuwbkit = "uwbkit.cli:main"\n\n'
                '[tool.setuptools.packages.find]\ninclude = ["uwbkit*"]\n\n'
                '[tool.setuptools.package-data]\nuwbkit = ["static/*", "static/**/*", "sim/scenarios/*.json"]\n',
                encoding="utf-8")
            copied.append("pyproject.toml (generated)")
    js = pkg / "static" / "uwbkit-client.js"
    if js.exists():
        shutil.copy2(js, dest / "uwbkit-client.js")
        copied.append("uwbkit-client.js")
    (dest / "VENDORED.txt").write_text(
        f"uwbkit {__version__} vendored {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"({'client only' if args.client_only else 'full kit'}).\n"
        "Source: the bench's shared uwb-kit. Wire contract: INTERFACE.md v1.\n"
        + ("" if args.client_only else
           "Run the hub from here:  PYTHONPATH=<this dir> python -m uwbkit hub --source sim --scenario <file.json> --port <port>\n"
           "  (needs: pip install aiohttp numpy pyserial)   or:  pip install <this dir>\n"), encoding="utf-8")
    print(f"vendored into {dest}: " + ", ".join(copied))
    return 0


def cmd_scenarios(args) -> int:
    from .sim import list_scenarios, load_scenario
    for name in list_scenarios():
        sc = load_scenario(name)
        print(f"{name:16s} {str(sc.get('profile', '')):7s} {sc.get('description', '')}")
    return 0


def cmd_listen(args) -> int:
    from . import client
    argv = [args.url] if args.url else []
    if args.types:
        argv += ["--types", args.types]
    if args.count:
        argv += ["--count", str(args.count)]
    return int(client.main(argv) or 0)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="uwbkit", description="Shared UWB kit: hub, simulator, solver, bring-up tools.")
    p.add_argument("--version", action="version", version=f"uwbkit {__version__}")
    sub = p.add_subparsers(dest="cmd")

    h = sub.add_parser("hub", help="run the hub (WebSocket /stream, SSE /events, REST, web console)")
    h.add_argument("--source", default="sim",
                   help="sim | serial:<port|auto>[@baud] | udp:<host>:<port> | replay:<file.ndjson> "
                        "(join several with +, e.g. serial:COM5+udp:0.0.0.0:1801)")
    h.add_argument("--scenario", default=None, help="sim: built-in name or path to a scenario .json (default office-walk)")
    h.add_argument("--profile", default=None, help="4A1T | 3A2T | mesh5 | free string (default: from the scenario, else 4A1T)")
    h.add_argument("--port", type=int, default=int(os.environ.get("UWBKIT_PORT", DEFAULT_PORT)))
    h.add_argument("--host", default=os.environ.get("UWBKIT_HOST", "127.0.0.1"),
                   help="bind address; several separated by commas (default 127.0.0.1; 0.0.0.0 = all interfaces)")
    h.add_argument("--rate", type=float, default=None, help="solver tick rate in Hz for real boards (default 10)")
    h.add_argument("--dims", type=int, choices=(2, 3), default=None)
    h.add_argument("--anchors", default=None, help="JSON file with anchor coordinates (loaded at start, saved on change)")
    h.add_argument("--tag-z", type=float, default=1.0, help="assumed tag height in metres for 2-D solving (default 1.0)")
    h.add_argument("--loop", action="store_true", help="replay: loop the file")
    h.add_argument("--speed", type=float, default=1.0, help="sim/replay speed factor")
    h.add_argument("--seed", type=int, default=None, help="sim: override the scenario seed")
    h.add_argument("--proto", default="auto", help="board line protocol: auto | uwbkit | dwm1001 | mauwb | example")
    h.add_argument("--id-map", default=None, metavar="VENDOR=KIT,...",
                   help="vendor firmware only: map its device ids to kit ids, e.g. 1786=A1,1787=A2,self=T1 "
                        "(Makerfabs example short addresses, DWM1001 hex ids; tag=T2 renames a DWM1001 tag)")
    h.add_argument("--record", default=None, help="record every message to this NDJSON file from the start")
    h.add_argument("--record-dir", default=".", help="directory that POST /record paths are relative to (default: cwd)")
    h.add_argument("--no-solve", action="store_true", help="publish ranges only")
    h.add_argument("--pidfile", default=None)
    h.add_argument("--log-level", default="info")
    h.set_defaults(func=cmd_hub)

    s = sub.add_parser("scenarios", help="list built-in simulator scenarios")
    s.set_defaults(func=cmd_scenarios)

    v = sub.add_parser("vendor", help="copy the kit (or just the clients) into a project directory")
    v.add_argument("dest", help="e.g. ../my-project/vendor/uwbkit")
    v.add_argument("--client-only", action="store_true", help="only client.py + uwbkit-client.js")
    v.set_defaults(func=cmd_vendor)

    li = sub.add_parser("listen", help="print messages from a hub (smoke test)")
    li.add_argument("url", nargs="?", default=None)
    li.add_argument("--types", default=None, help="comma list, e.g. pos,event")
    li.add_argument("--count", type=int, default=0)
    li.set_defaults(func=cmd_listen)

    for name, (_, help_text) in TOOLS.items():
        sub.add_parser(name, help=help_text, add_help=False)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in TOOLS:  # tools own their argument parsing
        mod_name = TOOLS[argv[0]][0]
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as e:
            print(f"uwbkit {argv[0]}: not available in this build ({e})", file=sys.stderr)
            return 2
        return int(mod.main(argv[1:]) or 0)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
