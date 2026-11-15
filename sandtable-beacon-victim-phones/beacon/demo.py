"""Cross-platform launcher for the Beacon server (pure Python: Windows, macOS, Linux, the UNO Q).

    python -m beacon.demo                         simulated world + real locator, board on http://127.0.0.1:1885
    python -m beacon.demo --story                 same, and plays the ~35 s walk-through once everything is up
    python -m beacon.demo --source fake           the hand-made sample replayed in phases (marked SAMPLE)
    python -m beacon.demo --source live --lan     real iPhones post to /api/ingest over the venue Wi-Fi (binds 0.0.0.0)
    python -m beacon.demo --tailnet               bind to this machine's tailnet address (and to 127.0.0.1)

Pattern copied from Sandtable's sandtable/demo.py (with credit). Addresses are discovered at run time and only printed,
never written to a file.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import threading


def _tailnet_ip() -> str | None:
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        return out[0].strip() if out and out[0].strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def _lan_ips() -> list[str]:
    """This machine's IPv4 addresses on the local network (no packet is sent: a UDP connect only picks a route)."""
    ips: list[str] = []
    for probe in ("10.255.255.255", "192.168.255.255", "172.31.255.255"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((probe, 1))
                ip = s.getsockname()[0]
            finally:
                s.close()
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        except OSError:
            continue
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def _bind(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m beacon.demo", description="Sandtable Beacon demo launcher")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "1885")))
    ap.add_argument("--source", default=os.environ.get("BEACON_SOURCE", "sim"), choices=["sim", "fake", "live", "kit"])
    ap.add_argument("--scenario", default=None, help="sim: load this scene at start (fixtures/beacon/scenarios/NAME.json)")
    ap.add_argument("--story", action="store_true", help="sim: play the scripted walk-through once everything is up")
    ap.add_argument("--tailnet", action="store_true", help="bind to the tailnet address (tailscale ip -4) and to 127.0.0.1")
    ap.add_argument("--lan", action="store_true", help="bind to 0.0.0.0 so phones on the same Wi-Fi can reach the server")
    args = ap.parse_args(argv)

    hosts = [args.host]
    if args.lan:
        hosts = ["0.0.0.0"]
    elif args.tailnet:
        ip = _tailnet_ip()
        if ip:
            hosts = [ip, "127.0.0.1"]
        else:
            print("[demo] tailscale address not found; staying on", args.host, flush=True)

    os.environ["PORT"] = str(args.port)
    os.environ["BEACON_SOURCE"] = args.source
    import uvicorn
    from . import server

    app = server.create_app(source=args.source, story=args.story, scenario=args.scenario)
    sockets = []
    try:
        for h in hosts:
            sockets.append(_bind(h, args.port))
    except OSError as exc:
        print(f"[demo] cannot bind port {args.port}: {exc}. Is a demo already running? (bash scripts/stop_demo.sh)", flush=True)
        for s in sockets:
            s.close()
        return 2

    shown = [h for h in hosts if h != "0.0.0.0"] or ["127.0.0.1"]
    print(f"[demo] Sandtable Beacon ({args.source}{', walk-through' if args.story else ''}):", flush=True)
    for h in shown:
        print(f"[demo]   board            http://{h}:{args.port}/", flush=True)
    if args.lan:
        ips = _lan_ips()
        for ip in ips:
            print(f"[demo]   phones (Wi-Fi)   http://{ip}:{args.port}   <- type this into the iOS app's server field", flush=True)
        if not ips:
            print("[demo]   no LAN address found: join the phones' Wi-Fi or hotspot, then restart", flush=True)
    if args.source in ("sim", "fake"):
        print("[demo]   everything shown is SIMULATED" + (" (hand-made SAMPLE)" if args.source == "fake" else ""), flush=True)

    config = uvicorn.Config(app, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, kwargs={"sockets": sockets}, daemon=True)
    thread.start()

    def _stop(signum, frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, OSError):
        pass
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        print("[demo] stopping", flush=True)
    finally:
        srv.should_exit = True
        thread.join(8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
