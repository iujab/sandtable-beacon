"""Command line of the puck host: `python -m beacon.host --help`."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from . import __version__
from .core import Host, Poster
from .parse import LEARN_SAMPLE, MAX_M, MIN_M
from .sources import FileFollowSource, ReplaySource, RttTelnetSource, SerialSource, parse_hostport
from .twr import TWR_SAMPLE, BaselineEstimator

PUCK_IDS = ("P1", "P2", "P3", "P4", "P5")

DESCRIPTION = """\
Beacon puck host (v{v}). Runs next to ONE puck (a Qorvo DWM3001CDK) on the Arduino UNO Q or a laptop, and talks to
the Beacon server's POST /api/ingest.

What it always does: a heartbeat every 5 s ({{"kind": "host", ...}}), so the command board shows the puck as hosted.
It retries quietly while the server is away.

With the stock Qorvo Nearby Interaction firmware a puck needs NO host: the iPhone app measures and reports every
range. A host is optional. Written from documentation and forum reports; never run against a real board.

EXPERIMENTAL range readers (the stock firmware's log format is NOT confirmed; use --learn and send a sample back):
  --rtt HOST:PORT   SEGGER RTT telnet stream, served by J-Link software on port 19021 while JLinkExe or
                    JLinkGDBServer is connected to the board through its J-Link port (J9).
  --rtt-log FILE    follow a file written by JLinkRTTLogger.
  --serial PORT     a USB serial port (needs pyserial). Only the SDK's CLI firmware prints there (JSON with D_cm,
                    port J20, 115200 baud); the stock NI firmware prints nothing on any serial port.
Each line goes through one tolerant parser; a distance with an OK status, between --min-m and --max-m, becomes
{{"kind": "uwb_puck", "puck", "d_m"}}, at most 5 per second.

BOARD TO BOARD (our SS-TWR firmware, hardware/twr_firmware/; written, not run on boards with this host):
  --baseline-to P2  the board this host reads is the TWR INITIATOR at puck --puck; the responder is at P2. Its
                    R,<seq>,<dist_m>,... lines (read with --rtt or --rtt-log) become a robust spacing (median of the
                    window, outliers beyond 3.5 robust sigma dropped) posted to POST /api/pucks/baseline. A
                    board-to-board range is never sent as a phone range.

OPTIONAL, OFF by default, NEVER RUN BY ITS AUTHORS:
  --cold-scan       passive Bluetooth listening (needs bleak) in 10 s windows; posts only three counts
                    (near / mid / far). Addresses exist only as salted hashes in memory for one window; nothing is
                    written to disk; nothing but the counts is sent. Devices, not people.
""".format(v=__version__)

EPILOG = """\
examples:
  python -m beacon.host --server http://127.0.0.1:1885 --puck P1 --name uno-q
  python -m beacon.host --server http://192.168.1.20:1885 --puck P1 --name uno-q --rtt 127.0.0.1:19021 --learn
  python -m beacon.host --server http://127.0.0.1:1885 --puck P2 --name laptop --serial auto
  python -m beacon.host --puck P1 --dry-run --no-post        # no hardware, no server: see what would be sent
  python -m beacon.host --server http://127.0.0.1:1885 --puck P1 --baseline-to P2 --rtt 127.0.0.1:19021
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m beacon.host", description=DESCRIPTION, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", default="http://127.0.0.1:1885",
                   help="Beacon server base URL (default http://127.0.0.1:1885)")
    p.add_argument("--puck", required=True, help="the puck this host sits next to: P1..P5 (as placed on the board)")
    p.add_argument("--name", default="uno-q", help="host name shown on the board: uno-q | laptop | battery | android ... (default uno-q)")
    p.add_argument("--rtt", metavar="HOST:PORT", help="EXPERIMENTAL: read the SEGGER RTT telnet stream (J-Link default port 19021)")
    p.add_argument("--rtt-log", metavar="FILE", help="EXPERIMENTAL: follow a file written by JLinkRTTLogger")
    p.add_argument("--serial", metavar="PORT|auto", help="EXPERIMENTAL: read a USB serial port (COM5, /dev/ttyACM0, auto); needs pyserial")
    p.add_argument("--baud", type=int, default=115200, help="serial speed (default 115200, the CLI firmware's)")
    p.add_argument("--cold-scan", action="store_true", help="OPTIONAL, never run by its authors: count Bluetooth advertisers (needs bleak)")
    p.add_argument("--cold-window", type=float, default=10.0, help="cold-scan window in seconds (default 10)")
    p.add_argument("--learn", action="store_true", help="print every raw line and what the parser made of it")
    p.add_argument("--unitless", choices=["auto", "m", "cm", "mm"], default="auto",
                   help="unit of a distance printed with no unit (auto: decimals are metres, integers are dropped)")
    p.add_argument("--min-m", type=float, default=MIN_M, help=f"drop ranges below this (default {MIN_M})")
    p.add_argument("--max-m", type=float, default=MAX_M, help=f"drop ranges above this (default {MAX_M})")
    p.add_argument("--max-hz", type=float, default=5.0, help="at most this many ranges per second (default 5)")
    p.add_argument("--heartbeat-s", type=float, default=5.0, help="heartbeat period (default 5 s)")
    p.add_argument("--baseline-to", metavar="P2", choices=PUCK_IDS,
                   help="board to board: the TWR responder's puck id; posts the spacing --puck to it")
    p.add_argument("--baseline-window", type=int, default=50, help="ranges in the baseline window (default 50, about 10 s)")
    p.add_argument("--baseline-min", type=int, default=20, help="fewest inlier ranges before a baseline is posted (default 20)")
    p.add_argument("--twr-offset-m", type=float, default=0.0,
                   help="subtract this constant from every board-to-board range (the bias found by the antenna-delay "
                        "check in hardware/twr_firmware/README.md; default 0: uncalibrated)")
    p.add_argument("--dry-run", action="store_true",
                   help="no hardware: replay built-in SAMPLE lines (the research's examples, not measurements) instead of "
                        "reading a board; every observation carries \"dry_run\": true; prints what it sends")
    p.add_argument("--no-post", action="store_true", help="print the observations instead of sending them (no server needed)")
    p.add_argument("--seconds", type=float, default=None,
                   help="stop after this many seconds (default: run until Ctrl-C; 15 with --dry-run)")
    p.add_argument("--version", action="version", version=f"beacon.host {__version__}")
    return p


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), "host:", msg, file=sys.stderr, flush=True)


def _echo(batch: list) -> None:
    for obs in batch:
        print(json.dumps(obs, separators=(", ", ": ")), flush=True)


def _learn(text: str) -> None:
    print(text, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_m <= 0 or args.max_m <= args.min_m:
        print("error: need 0 < --min-m < --max-m", file=sys.stderr)
        return 2
    readers = [x for x in (args.rtt, args.rtt_log, args.serial) if x]
    if args.baseline_to and (args.puck not in PUCK_IDS or args.baseline_to == args.puck):
        print("error: --baseline-to needs --puck and --baseline-to to be two different ids among P1..P5", file=sys.stderr)
        return 2
    if not 1 <= args.baseline_min <= args.baseline_window:
        print("error: need 1 <= --baseline-min <= --baseline-window", file=sys.stderr)
        return 2
    if args.dry_run and readers:
        print("error: --dry-run replaces the readers; drop --rtt / --rtt-log / --serial", file=sys.stderr)
        return 2

    poster = Poster(args.server, _log, post=not args.no_post,
                    echo=_echo if (args.dry_run or args.no_post) else None)
    host = Host(server=args.server, puck=args.puck, name=args.name, poster=poster, log=_log,
                learn_out=_learn if args.learn else None, dry_run=args.dry_run, heartbeat_s=args.heartbeat_s,
                max_hz=args.max_hz, min_m=args.min_m, max_m=args.max_m, unitless=args.unitless,
                cold_scan=args.cold_scan, baseline_to=args.baseline_to,
                estimator=BaselineEstimator(window=args.baseline_window, min_samples=args.baseline_min),
                twr_offset_m=args.twr_offset_m)
    stop = host.stop_event

    if args.dry_run:
        sample = TWR_SAMPLE + LEARN_SAMPLE if args.baseline_to else LEARN_SAMPLE
        host.add_source(ReplaySource(sample, host.line_handler("dry-run"), stop, _log))
    if args.rtt:
        try:
            h, port = parse_hostport(args.rtt)
        except ValueError:
            print(f"error: --rtt needs HOST:PORT (for example 127.0.0.1:19021), got {args.rtt!r}", file=sys.stderr)
            return 2
        host.add_source(RttTelnetSource(h, port, host.line_handler("rtt"), stop, _log))
    if args.rtt_log:
        host.add_source(FileFollowSource(args.rtt_log, host.line_handler("rtt-log"), stop, _log))
    if args.serial:
        host.add_source(SerialSource(args.serial, host.line_handler("serial"), stop, _log, baud=args.baud))

    cold_thread = None
    if args.cold_scan:
        from .cold import run_cold_scan
        cold_thread = threading.Thread(target=run_cold_scan, name="beacon-host-cold", daemon=True,
                                       args=(args.puck, poster.put, stop, args.cold_window, _log))

    seconds = args.seconds if args.seconds is not None else (15.0 if args.dry_run else 0.0)
    what = ", ".join(s.describe() for s in host.sources) or "heartbeat only"
    dest = "printing only (--no-post)" if args.no_post else f"posting to {poster.url}"
    _log(f"puck {args.puck} hosted by '{args.name}'; {what}; {dest}"
         + ("; DRY RUN: sample lines, not measurements" if args.dry_run else "")
         + (f"; stops after {seconds:g} s" if seconds else "; Ctrl-C to stop"))
    if cold_thread is not None:
        cold_thread.start()
    host.run(seconds)
    _log(f"done: {host.lines_seen} lines, {host.ranges_sent} ranges, {poster.posted} observations posted, "
         f"{poster.failed} failed, {poster.dropped} dropped"
         + (f"; board to board: {host.twr['ranges']} ranges, {host.twr['timeouts']} timeouts, "
            f"{host.baselines_sent} baselines posted" if args.baseline_to else ""))
    return 0
