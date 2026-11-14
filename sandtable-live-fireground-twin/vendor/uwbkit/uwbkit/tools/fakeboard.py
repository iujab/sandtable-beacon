"""Fake-board emulator: prints exactly what the kit firmware prints, with no hardware.

It runs a simulator scenario and re-emits its ranges as board protocol lines (docs/CONTRACTS.md
section 4) over a real transport, so the hub's serial and UDP paths are exercised end to end:

    uwbkit fakeboard --udp 127.0.0.1:1801          # then: uwbkit hub --source udp:0.0.0.0:1801 --anchors a.json
    uwbkit fakeboard --tcp 5555                    # then: uwbkit hub --source serial:socket://127.0.0.1:5555
    uwbkit fakeboard --pty                         # POSIX: prints /dev/pts/N -> uwbkit hub --source serial:/dev/pts/N

Options: --scenario NAME|file (default office-walk), --profile mesh5 (= mesh-dance), --garbage
(boot noise, corrupted checksums, binary bursts), --dup (results reported twice, as relays do),
--emulate dwm1001|mauwb|example (vendor formats), --duration S.
THE DATA IS SIMULATED. The fake boards announce ``chip=FAKE`` in their banner, and the hub then sets
``hello.simulated = true`` (SIM badge). The vendor-format modes (--emulate) have no banner, so the hub
cannot tell: use those for testing the board path only, never for a demo.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import random
import sys
import time

from ..protocol import format_banner, format_event, format_kv, format_range, format_status

BOOT_NOISE = (b"\xff\xfe\x00\x1b\x80ets Jul 29 2019 12:21:46\r\n\r\nrst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)\r\n"
              b"configsip: 0, SPIWP:0xee\r\nmode:DIO, clock div:1\r\nload:0x3fff0030,len:1184\r\nentry 0x400805f0\r\n")


class FakeBoard:
    def __init__(self, node_id: str, role: str, index: int, chip: str = "FAKE"):
        self.id, self.role, self.chip = node_id, role, chip
        self.shown_id = node_id        # what the banner says (changed by the ID command)
        self.mac = f"F4CFA2{index:02X}{(index * 37) & 0xFF:02X}{(index * 91 + 17) & 0xFF:02X}"
        self.antdly = 16385
        self.ok = 0
        self.fail = 0
        self.vbat = None if role == "anchor" else round(3.72 + 0.07 * index, 2)
        self.t0 = time.time()

    def banner(self) -> str:
        return format_banner("1.0-fake", self.chip, self.role, self.shown_id, self.mac, 5, self.antdly)


class FakeNetwork:
    """A simulated UWB network that speaks the board line protocol.

    ``tick(now)`` returns ``{board_id: [lines]}``: what each board prints during this cycle.
    ``command(board_id, line)`` returns the reply lines of that board.
    """

    def __init__(self, scenario="office-walk", seed=None, garbage: bool = False, dup: bool = False,
                 emulate: str = "uwbkit", rate: float | None = None, chip: str = "FAKE"):
        from ..sim import Simulator
        self.sim = Simulator(scenario, seed=seed)
        self.rate = float(rate or self.sim.rate_hz)
        self.garbage, self.dup, self.emulate = garbage, dup, emulate
        self.rng = random.Random(seed if seed is not None else 1)
        # chip "FAKE" in the banner makes the hub label the stream simulated; tests of the
        # non-simulated code path pass another chip name
        self.boards = {nid: FakeBoard(nid, info.get("role", "tag"), i, chip)
                       for i, (nid, info) in enumerate(sorted(self.sim.nodes.items()))}
        self.bseq: dict = {}
        self.survey_until = 0.0
        self._last_status = 0.0
        self._last_banner = 0.0
        self._started = False
        self._seq = 0
        self._hex = {nid: f"{0x1150 + 0x2F * i:04X}" for i, nid in enumerate(sorted(self.sim.anchors))}

    # ------------------------------------------------------------------ helpers
    def _range_line(self, m: dict) -> str:
        a = m["a"]
        self.bseq[a] = (self.bseq.get(a, 0) + 1) % 65536
        line = format_range(self.bseq[a], a, m["b"], m["d"], m.get("raw"), m.get("rssi"), m.get("fp"), m.get("los"))
        if self.garbage and self.rng.random() < 0.02:     # corrupt the payload: checksum must catch it
            i = self.rng.randrange(2, len(line) - 3)
            line = line[:i] + ("7" if line[i] != "7" else "8") + line[i + 1:]
        return line

    def truth(self) -> dict:
        return self.sim.truth()

    # --------------------------------------------------------------------- tick
    def tick(self, now: float) -> dict:
        out: dict = {b: [] for b in self.boards}
        msgs = self.sim.step(now)
        if self.emulate != "uwbkit":       # vendor firmware prints no kit banner / status lines
            return self._tick_vendor(msgs, out)
        if not self._started:
            self._started = True
            self._last_banner = now
            for b in self.boards.values():
                out[b.id].append(b.banner())
        if now < self.survey_until:
            msgs = msgs + self.sim.survey(1)
        for m in msgs:
            if m["type"] == "range":
                a, b = m["a"], m["b"]
                line = self._range_line(m)
                out[a].append(line)
                self.boards[a].ok += 1
                if self.dup and b in out:
                    out[b].append(line)             # the responder relays the same result
            elif m["type"] == "event" and m["id"] in out:
                out[m["id"]].append(format_event(m["id"], m["name"], m.get("data")))
        if now - self._last_status >= 1.0:
            self._last_status = now
            for b in self.boards.values():
                up = now - b.t0
                if b.vbat is not None:
                    b.vbat = max(3.3, b.vbat - 0.00002)
                out[b.id].append(format_status(b.id, b.role, up, b.vbat, self.rate if b.ok else 0.0, b.ok, b.fail))
        if now - self._last_banner >= 10.0:
            self._last_banner = now
            for b in self.boards.values():
                out[b.id].append(b.banner())
        return out

    def _tick_vendor(self, msgs, out) -> dict:
        """Vendor stock-firmware formats: everything is printed by the tag's module."""
        by_tag: dict = {}
        for m in msgs:
            if m["type"] == "range" and m["b"] in self.sim.anchors:
                by_tag.setdefault(m["a"], {})[m["b"]] = m
        self._seq = (self._seq + 1) % 256
        anchors = sorted(self.sim.anchors)
        for tag, rs in by_tag.items():
            if self.emulate == "mauwb":
                tid = int(tag[1:]) - 1 if tag[1:].isdigit() else 0
                cm, rssi, ancid, mask = [], [], [], 0
                for i in range(8):
                    m = rs.get(anchors[i]) if i < len(anchors) else None
                    cm.append(str(int(round(m["d"] * 100))) if m else "0")
                    rssi.append(f"{m['rssi']:.2f}" if m else "0.00")
                    ancid.append(str(i) if m else "-1")
                    mask |= (1 << i) if m else 0
                out[tag].append(f"AT+RANGE=tid:{tid},mask:{mask:02X},seq:{self._seq},range:({','.join(cm)}),"
                                f"rssi:({','.join(rssi)}),ancid:({','.join(ancid)})")
            elif self.emulate == "dwm1001":
                parts = []
                for aid, m in sorted(rs.items()):
                    p = self.sim.anchors[aid]
                    parts.append(f"{self._hex[aid]}[{p['x']:.2f},{p['y']:.2f},{p['z']:.2f}]={m['d']:.2f}")
                t = self.sim.truth()["nodes"][tag]
                out[tag].append(" ".join(parts) + f" le_us=2576 est[{t['x']:.2f},{t['y']:.2f},{t['z']:.2f},95]")
            else:   # "example": Makerfabs DW1000 example sketch
                for aid, m in sorted(rs.items()):
                    out[tag].append(f"from: {self._hex[aid]}\t Range: {m['d']:.2f} m\t RX power: {m['rssi']:.2f} dBm")
        return out

    # ----------------------------------------------------------------- commands
    def command(self, board_id: str, line: str) -> list:
        b = self.boards.get(board_id)
        if b is None:
            return []
        parts = line.strip().split()
        if not parts:
            return []
        cmd = parts[0].upper()
        try:
            if cmd == "?":
                return [b.banner()]
            if cmd == "ANTDLY?":
                return [format_kv(b.id, "antdly", b.antdly)]
            if cmd == "ANTDLY":
                b.antdly = int(parts[1])
                return [format_kv(b.id, "antdly", b.antdly)]
            if cmd == "CAL":
                # one DW time unit is ~4.69 mm of range; pretend the board was 12 cm long
                b.antdly += int(round(0.12 / 0.004691 / 2))
                return [f"# CAL {parts[1]} true={float(parts[2]):.3f} m done", format_kv(b.id, "antdly", b.antdly)]
            if cmd == "SURVEY":
                self.survey_until = time.time() + float(parts[1])
                return [format_kv(b.id, "survey", int(float(parts[1])))]
            if cmd == "ROLE":
                b.role = parts[1].lower()
                return [format_kv(b.id, "role", b.role)]
            if cmd == "ID":
                b.shown_id = parts[1]      # banner only: the simulated network keeps its scenario ids
                return [format_kv(b.id, "id", parts[1])]
            if cmd == "RATE":
                self.rate = float(parts[1])
                return [format_kv(b.id, "rate", parts[1])]
            if cmd in ("SAVE", "REBOOT", "WIFI", "HUB"):
                return [format_kv(b.id, cmd.lower(), "ok")]
        except (IndexError, ValueError):
            return [f"# bad arguments: {line.strip()}"]
        return [f"# unknown command: {line.strip()}"]

    def command_all(self, line: str) -> list:
        """A command arriving on a gateway port: every board acts on it (SURVEY), the first replies."""
        out = []
        for i, bid in enumerate(self.boards):
            replies = self.command(bid, line)
            if i == 0 or line.strip().upper().startswith("?"):
                out.extend(replies)
        return out


# ============================================================================ transports
def _garble(net: FakeNetwork, data: bytes) -> bytes:
    if net.garbage and net.rng.random() < 0.01:
        return data + bytes(net.rng.randrange(256) for _ in range(24)) + b"\n"
    return data


async def run_udp(net: FakeNetwork, host: str, port: int, duration: float | None, quiet: bool) -> None:
    """One UDP socket per fake board, like boards on Wi-Fi."""
    loop = asyncio.get_running_loop()

    class Proto(asyncio.DatagramProtocol):
        def __init__(self, bid):
            self.bid, self.transport = bid, None

        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            for line in data.decode("ascii", "replace").splitlines():
                for reply in net.command(self.bid, line):
                    self.transport.sendto((reply + "\n").encode())

        def error_received(self, exc):
            pass

    socks = {}
    for bid in net.boards:
        transport, _ = await loop.create_datagram_endpoint(lambda bid=bid: Proto(bid), remote_addr=(host, port))
        socks[bid] = transport
    if not quiet:
        print(f"fakeboard: {len(socks)} boards sending UDP to {host}:{port} (SIMULATED data)", flush=True)
    try:
        if net.garbage:
            next(iter(socks.values())).sendto(BOOT_NOISE)
        await _pump(net, lambda bid, lines: socks[bid].sendto(_garble(net, ("\n".join(lines) + "\n").encode())),
                    duration)
    finally:
        for t in socks.values():
            t.close()


async def run_tcp(net: FakeNetwork, port: int, duration: float | None, quiet: bool, host: str = "127.0.0.1",
                  ready: asyncio.Event | None = None) -> None:
    """A gateway board on 'USB': every line of the network on one byte stream (pyserial socket:// URL)."""
    writers: set = set()

    async def handle(reader, writer):
        writers.add(writer)
        if net.garbage:
            writer.write(BOOT_NOISE)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if net.emulate != "uwbkit":
                    continue                    # vendor firmware ignores kit commands
                for reply in net.command_all(line.decode("ascii", "replace")):
                    writer.write((reply + "\r\n").encode())
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()

    server = await asyncio.start_server(handle, host, port)
    actual = server.sockets[0].getsockname()[1]
    net.tcp_port = actual
    if not quiet:
        print(f"fakeboard: gateway on tcp {host}:{actual}  ->  uwbkit hub --source serial:socket://{host}:{actual}"
              "  (SIMULATED data)", flush=True)
    if ready is not None:
        ready.set()

    def send(bid, lines):
        data = _garble(net, ("\r\n".join(lines) + "\r\n").encode())
        for w in list(writers):
            try:
                if net.garbage and len(data) > 8:      # split mid-line, as a UART does
                    cut = net.rng.randrange(1, len(data))
                    w.write(data[:cut])
                    w.write(data[cut:])
                else:
                    w.write(data)
            except Exception:
                writers.discard(w)

    try:
        await _pump(net, send, duration)
    finally:
        server.close()
        for w in list(writers):
            with contextlib.suppress(Exception):
                w.close()


async def run_pty(net: FakeNetwork, duration: float | None, quiet: bool) -> None:
    """POSIX pseudo-terminal: looks like a real serial device to pyserial."""
    if os.name != "posix":
        raise SystemExit("--pty needs a POSIX system; use --tcp PORT with serial:socket://127.0.0.1:PORT")
    import tty
    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    name = os.ttyname(slave)
    net.pty_path = name
    print(f"fakeboard: gateway on {name}  ->  uwbkit hub --source serial:{name}  (SIMULATED data)", flush=True)
    loop = asyncio.get_running_loop()
    pending = bytearray()

    def on_readable():
        try:
            data = os.read(master, 1024)
        except OSError:
            return
        pending.extend(data)
        while b"\n" in pending:
            line, _, rest = bytes(pending).partition(b"\n")
            pending[:] = rest
            for reply in net.command_all(line.decode("ascii", "replace")):
                with contextlib.suppress(OSError):
                    os.write(master, (reply + "\r\n").encode())

    loop.add_reader(master, on_readable)

    def send(bid, lines):
        with contextlib.suppress(OSError):     # nobody reading: the pty buffer is full
            os.write(master, _garble(net, ("\r\n".join(lines) + "\r\n").encode()))

    try:
        if net.garbage:
            os.write(master, BOOT_NOISE)
        await _pump(net, send, duration)
    finally:
        loop.remove_reader(master)
        os.close(master)
        os.close(slave)


async def _pump(net: FakeNetwork, send, duration: float | None) -> None:
    t_end = None if not duration else time.time() + duration
    next_t = time.monotonic()
    while t_end is None or time.time() < t_end:
        for bid, lines in net.tick(time.time()).items():
            if lines:
                send(bid, lines)
        next_t += 1.0 / net.rate
        delay = next_t - time.monotonic()
        if delay < -1.0:
            next_t, delay = time.monotonic(), 0.0
        await asyncio.sleep(max(0.0, delay))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uwbkit fakeboard", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--udp", metavar="HOST:PORT", help="send datagrams to a hub's udp: source")
    ap.add_argument("--tcp", metavar="PORT", type=int, help="listen; the hub uses serial:socket://127.0.0.1:PORT")
    ap.add_argument("--pty", action="store_true", help="POSIX pseudo-terminal (prints the device path)")
    ap.add_argument("--scenario", default=None, help="built-in name or scenario file (default office-walk)")
    ap.add_argument("--profile", default=None, help="mesh5 = shortcut for --scenario mesh-dance")
    ap.add_argument("--rate", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--garbage", action="store_true", help="inject boot noise, corrupted lines and binary bursts")
    ap.add_argument("--dup", action="store_true", help="report every result twice (as relaying boards do)")
    ap.add_argument("--emulate", default="uwbkit", choices=["uwbkit", "dwm1001", "mauwb", "example"],
                    help="print a vendor's stock-firmware format instead of the kit protocol")
    ap.add_argument("--duration", type=float, default=None, help="stop after this many seconds")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    scenario = a.scenario or ("mesh-dance" if (a.profile or "").lower().startswith("mesh") else "office-walk")
    try:
        net = FakeNetwork(scenario, seed=a.seed, garbage=a.garbage, dup=a.dup, emulate=a.emulate, rate=a.rate)
    except ValueError as e:
        print(f"uwbkit fakeboard: {e}", file=sys.stderr)
        return 2
    try:
        if a.udp:
            host, _, port = a.udp.rpartition(":")
            asyncio.run(run_udp(net, host or "127.0.0.1", int(port), a.duration, a.quiet))
        elif a.tcp is not None:
            asyncio.run(run_tcp(net, a.tcp, a.duration, a.quiet))
        elif a.pty:
            asyncio.run(run_pty(net, a.duration, a.quiet))
        else:
            ap.error("pick a transport: --udp HOST:PORT | --tcp PORT | --pty")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
