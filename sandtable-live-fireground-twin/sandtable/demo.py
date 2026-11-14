"""Cross-platform launcher: starts this project's uwb-kit hub and the Sandtable server.

    python -m sandtable.demo                      simulated hub (3 anchors + 2 tags), board on http://127.0.0.1:1810
    python -m sandtable.demo --storyboard         same, and plays the two-minute exercise hands-free
    python -m sandtable.demo --hub serial:auto    real boards on USB (a gateway board)
    python -m sandtable.demo --hub udp:0.0.0.0:1801   real boards over Wi-Fi
    python -m sandtable.demo --hub url:ws://host:1800/stream   attach to a hub that is already running
    python -m sandtable.demo --tailnet            bind the board to this machine's tailnet address
    python -m sandtable.demo --no-beacon          do not start the Beacon module (the board relays its labelled sample)
    python -m sandtable.demo --beacon-url http://host:1885   use a Beacon server that is already running elsewhere

The Beacon module (phones; lane L16's work, vendored in vendor/beacon by scripts/vendor_beacon.py) is started on port 1885
as a subprocess when nothing answers there, and stopped on exit, like the hub.

Pure Python (no shell, no Linux-only calls): the same command works on Windows, macOS,
Linux x86_64 and the UNO Q (Debian aarch64).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tailnet_ip() -> str | None:
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        return out[0].strip() if out and out[0].strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def _get(url: str, timeout: float = 1.5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _post(url: str, body: dict, timeout: float = 5.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sandtable.demo", description="Sandtable demo launcher")
    ap.add_argument("--hub", default="sim", help="sim | url:ws://... | serial:PORT | udp:HOST:PORT | replay:FILE (default: sim)")
    ap.add_argument("--scenario", default="hardware/scenarios/sandtable_rig_3A2T.json", help="kit scenario file for --hub sim")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "1810")))
    ap.add_argument("--hub-port", type=int, default=None, help="default: port + 100")
    ap.add_argument("--profile", default=None, help="passed to the hub for real boards: 4A1T | 3A2T")
    ap.add_argument("--anchors", default=None, help="passed to the hub: JSON file with anchor coordinates to load at start")
    ap.add_argument("--tailnet", action="store_true", help="bind the board to the tailnet address (tailscale ip -4)")
    ap.add_argument("--storyboard", action="store_true", help="start the SIM storyboard autopilot once everything is up")
    ap.add_argument("--loop", action="store_true", help="with --storyboard: play it again and again")
    ap.add_argument("--no-beacon", action="store_true", help="do not start the vendored Beacon module")
    ap.add_argument("--live-beacon", action="store_true", help="with --storyboard: drive the live Beacon module instead of replaying the recorded run")
    ap.add_argument("--beacon-url", default=None, help="use this Beacon server instead of starting one (http://host:port)")
    ap.add_argument("--beacon-port", type=int, default=int(os.environ.get("BEACON_PORT", "1885")), help="port for the vendored Beacon module (default 1885)")
    args = ap.parse_args(argv)

    host = args.host
    if args.tailnet:
        ip = _tailnet_ip()
        if ip:
            host = ip
        else:
            print("[demo] tailscale address not found; staying on", host, flush=True)
    hub_port = args.hub_port or args.port + 100
    hub_proc = None
    if args.hub.startswith("url:"):
        os.environ["UWBKIT_URL"] = args.hub[4:]
        print(f"[demo] attaching to an existing hub at {args.hub[4:]}", flush=True)
    else:
        cmd = [sys.executable, "-m", "uwbkit", "hub", "--port", str(hub_port), "--host", "127.0.0.1"]
        if args.hub == "sim":
            cmd += ["--source", "sim", "--scenario", str((ROOT / args.scenario).resolve())]
        else:
            cmd += ["--source", args.hub]
        if args.profile:
            cmd += ["--profile", args.profile]
        if args.anchors:
            cmd += ["--anchors", str((ROOT / args.anchors).resolve())]
        env = dict(os.environ)
        vendored = ROOT / "vendor" / "uwbkit"
        if vendored.is_dir():                                # use the vendored kit even when it is not pip-installed
            env["PYTHONPATH"] = str(vendored) + os.pathsep + env.get("PYTHONPATH", "")
        try:
            other = _get(f"http://127.0.0.1:{hub_port}/healthz")
            print(f"[demo] port {hub_port} already has a hub (source {other.get('source')}). Stop it first (bash run_demo.sh --stop), "
                  f"or attach to it on purpose with --hub url:ws://127.0.0.1:{hub_port}/stream", flush=True)
            return 2
        except Exception:
            pass
        print("[demo] starting the uwb-kit hub:", " ".join(cmd[1:]), flush=True)
        hub_proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
        os.environ["UWBKIT_URL"] = f"ws://127.0.0.1:{hub_port}/stream"
        for _ in range(60):
            if hub_proc.poll() is not None:
                print("[demo] the hub exited at start-up; see its message above", flush=True)
                return 2
            try:
                h = _get(f"http://127.0.0.1:{hub_port}/healthz")
                print(f"[demo] hub up: source {h.get('source')}, simulated={h.get('simulated')}", flush=True)
                break
            except Exception:
                time.sleep(0.25)
        else:
            print("[demo] the hub did not answer on port", hub_port, flush=True)
            hub_proc.terminate()
            return 2
    beacon_proc = None if args.no_beacon else _start_beacon(args)
    os.environ["HOST"], os.environ["PORT"], os.environ["HUB_PORT"] = host, str(args.port), str(hub_port)

    if args.storyboard:
        def kick():
            base = f"http://{host}:{args.port}"
            while True:
                for _ in range(120):
                    try:
                        if _get(base + "/healthz").get("hub_connected"):
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)
                try:
                    _post(base + "/api/storyboard", {"action": "start", **({"beacon": "live"} if args.live_beacon else {})})
                    print("[demo] storyboard autopilot started", flush=True)
                except Exception as exc:
                    print("[demo] could not start the storyboard:", exc, flush=True)
                    return
                if not args.loop:
                    return
                time.sleep(5)
                while True:
                    try:
                        if not _get(base + "/api/state")["storyboard"]["running"]:
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                time.sleep(20)
        threading.Thread(target=kick, daemon=True).start()

    import signal
    import uvicorn
    print(f"[demo] Sandtable board: http://{host}:{args.port}/   (LLM backend: {os.environ.get('LLM_BACKEND', 'replay')}, data: {os.environ.get('SANDTABLE_DATA_MODE', 'fixture')})", flush=True)
    # The server runs in a thread so that this main thread owns the signals and always gets to stop the hub it started.
    server = uvicorn.Server(uvicorn.Config("sandtable.server:app", host=host, port=args.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def _stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        print("[demo] stopping", flush=True)
    finally:
        server.should_exit = True
        thread.join(8)
        for proc in (hub_proc, beacon_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return 0


def _start_beacon(args):
    """Point the board at a Beacon server: --beacon-url, or one already answering on the port, or the vendored copy started here.
    Returns the subprocess this launcher owns (or None). Any failure leaves the board on the labelled sample."""
    if args.beacon_url:
        os.environ["BEACON_URL"] = args.beacon_url.rstrip("/")
        print(f"[demo] Beacon module: using {os.environ['BEACON_URL']}", flush=True)
        return None
    url = f"http://127.0.0.1:{args.beacon_port}"
    os.environ["BEACON_URL"] = url
    try:
        h = _get(url + "/healthz")
        print(f"[demo] Beacon module already answering on {url} (source {h.get('source')}): using it", flush=True)
        return None
    except Exception:
        pass
    vendored = ROOT / "vendor" / "beacon"
    if not (vendored / "beacon" / "server.py").is_file():
        print("[demo] Beacon module not vendored (python scripts/vendor_beacon.py): the board relays the labelled SAMPLE", flush=True)
        return None
    env = dict(os.environ)
    env["PYTHONPATH"] = str(vendored) + os.pathsep + env.get("PYTHONPATH", "")
    env["BEACON_DATA_DIR"] = str(Path(os.environ.get("SANDTABLE_DATA_DIR") or (ROOT / "data")) / "beacon")
    for k in ("PORT", "HOST"):
        env.pop(k, None)
    cmd = [sys.executable, "-m", "beacon.demo", "--host", "127.0.0.1", "--port", str(args.beacon_port)]
    print("[demo] starting the Beacon module (vendor/beacon, lane L16's work):", " ".join(cmd[1:]), flush=True)
    try:
        proc = subprocess.Popen(cmd, cwd=str(vendored), env=env)
    except OSError as exc:
        print(f"[demo] the Beacon module did not start ({exc}): the board relays the labelled SAMPLE", flush=True)
        return None
    for _ in range(80):
        if proc.poll() is not None:
            print("[demo] the Beacon module exited at start-up (see above): the board relays the labelled SAMPLE", flush=True)
            return None
        try:
            h = _get(url + "/healthz")
            print(f"[demo] Beacon module up on {url}: source {h.get('source')}, simulated={h.get('simulated')}", flush=True)
            return proc
        except Exception:
            time.sleep(0.25)
    print(f"[demo] the Beacon module did not answer on {url}: stopping it; the board relays the labelled SAMPLE", flush=True)
    proc.terminate()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
