"""``uwbkit unoq-matrix``: show hub status on the Arduino UNO Q's 13 x 8 LED matrix.

Runs on the UNO Q's Linux side next to the hub. It follows the hub's stream, renders a tiny
picture and pushes it to the MCU sketch ``firmware/uwbkit_unoq_matrix`` about five times a
second through the Arduino router (MessagePack-RPC on the unix socket
``/var/run/arduino-router.sock``; method ``uwbkit_frame(w0, w1, w2, w3)``).

    rows 0-6   mini map: anchors = steady dots, tags / mesh nodes = blinking dots
    row 7      one dot per node online (left), heartbeat (right-most dot toggles every frame)
    "SIM"      flashed for one second out of every five while hello.simulated is true
    no hub     the MCU shows its own "no hub" cross when frames stop for 3 s

    uwbkit unoq-matrix                      # on the UNO Q (hub on 127.0.0.1:1800)
    uwbkit unoq-matrix --ascii              # anywhere: draw the frames in the terminal instead

Stdlib only. The router protocol is implemented from the public description of arduino-router
(MessagePack-RPC request ``[0, msgid, method, params]``); it has not been run on a real UNO Q.
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import threading
import time

from ..client import Stream

W, H = 13, 8
ROUTER_SOCKET = "/var/run/arduino-router.sock"

# 3x5 glyphs (rows top to bottom, bits left to right) for the SIM marker
GLYPHS = {"S": ["111", "100", "111", "001", "111"], "I": ["1", "1", "1", "1", "1"],
          "M": ["10001", "11011", "10101", "10001", "10001"]}


# ----------------------------------------------------------------------------- msgpack
def mp_uint(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x100:
        return b"\xcc" + bytes([n])
    if n < 0x10000:
        return b"\xcd" + struct.pack(">H", n)
    return b"\xce" + struct.pack(">I", n & 0xFFFFFFFF)


def mp_str(s: str) -> bytes:
    b = s.encode()
    if len(b) < 32:
        return bytes([0xA0 | len(b)]) + b
    return b"\xd9" + bytes([len(b)]) + b


def mp_array(items: list) -> bytes:
    assert len(items) < 16
    return bytes([0x90 | len(items)]) + b"".join(items)


def rpc_request(msgid: int, method: str, params: list) -> bytes:
    """MessagePack-RPC request: [0, msgid, method, [params...]] (params: unsigned ints)."""
    return mp_array([mp_uint(0), mp_uint(msgid), mp_str(method), mp_array([mp_uint(p) for p in params])])


def mp_decode(buf: bytes, pos: int = 0):
    """Decode one MessagePack value (the subset an RPC response uses). -> (value, next_pos)."""
    b = buf[pos]
    if b < 0x80:
        return b, pos + 1
    if 0x90 <= b <= 0x9F:
        out, pos = [], pos + 1
        for _ in range(b & 0x0F):
            v, pos = mp_decode(buf, pos)
            out.append(v)
        return out, pos
    if 0xA0 <= b <= 0xBF:
        n = b & 0x1F
        return buf[pos + 1:pos + 1 + n].decode("utf-8", "replace"), pos + 1 + n
    if b == 0xC0:
        return None, pos + 1
    if b in (0xC2, 0xC3):
        return b == 0xC3, pos + 1
    if b == 0xCC:
        return buf[pos + 1], pos + 2
    if b == 0xCD:
        return struct.unpack_from(">H", buf, pos + 1)[0], pos + 3
    if b == 0xCE:
        return struct.unpack_from(">I", buf, pos + 1)[0], pos + 5
    if b == 0xD0:
        return struct.unpack_from(">b", buf, pos + 1)[0], pos + 2
    if b == 0xD1:
        return struct.unpack_from(">h", buf, pos + 1)[0], pos + 3
    if b == 0xD2:
        return struct.unpack_from(">i", buf, pos + 1)[0], pos + 5
    if b == 0xD9:
        n = buf[pos + 1]
        return buf[pos + 2:pos + 2 + n].decode("utf-8", "replace"), pos + 2 + n
    if b >= 0xE0:
        return b - 0x100, pos + 1
    raise ValueError(f"unsupported msgpack type 0x{b:02x}")


class Router:
    """Minimal MessagePack-RPC client for the UNO Q's arduino-router."""

    def __init__(self, path: str = ROUTER_SOCKET, timeout: float = 0.5):
        self.path, self.timeout = path, timeout
        self.sock = None
        self.msgid = 0
        self.error = None

    def call(self, method: str, params: list) -> bool:
        try:
            if self.sock is None:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(self.timeout)
                s.connect(self.path)
                self.sock = s
            self.msgid = (self.msgid + 1) & 0x7FFFFFFF
            self.sock.sendall(rpc_request(self.msgid, method, params))
            buf = b""
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("router closed the connection")
                buf += chunk
                try:
                    resp, _ = mp_decode(buf)
                except (IndexError, struct.error):
                    continue            # response not complete yet
                if isinstance(resp, list) and len(resp) == 4 and resp[0] == 1 and resp[1] == self.msgid:
                    self.error = resp[2]
                    return resp[2] is None
                buf = b""
        except (OSError, ValueError, ConnectionError, AttributeError) as e:
            self.error = str(e)
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None
            return False


# ------------------------------------------------------------------------------ render
def to_words(pixels: set) -> list:
    """{(x, y)} with y = 0 at the top -> 4 words for Arduino_LED_Matrix::loadFrame."""
    words = [0, 0, 0, 0]
    for x, y in pixels:
        if 0 <= x < W and 0 <= y < H:
            p = y * W + x
            words[p // 32] |= 1 << (31 - p % 32)
    return words


def render(state: dict, tick: int) -> set:
    """State (hello, anchors, pos, status) -> set of lit pixels."""
    px = set()
    hello = state.get("hello") or {}
    if hello.get("simulated") and tick % 25 < 5:          # "SIM" for 1 s out of every 5 (at 5 fps)
        x = 1
        for ch in "SIM":
            for row, bits in enumerate(GLYPHS[ch]):
                for col, bit in enumerate(bits):
                    if bit == "1":
                        px.add((x + col, 1 + row))
            x += len(GLYPHS[ch][0]) + 1
        return px
    anchors = (state.get("anchors") or {}).get("anchors") or {}
    pos = state.get("pos") or {}
    pts = [(a["x"], a["y"]) for a in anchors.values()] + [(p["x"], p["y"]) for p in pos.values()]
    if pts:
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        sx, sy = max(x1 - x0, 0.5), max(y1 - y0, 0.5)

        def cell(x, y):
            return (int(round((x - x0) / sx * (W - 1))), int(round((1.0 - (y - y0) / sy) * (H - 2))))
        for a in anchors.values():
            px.add(cell(a["x"], a["y"]))
        if tick % 3 != 2:                                   # tags blink
            for p in pos.values():
                px.add(cell(p["x"], p["y"]))
    online = len((state.get("status") or {}).get("nodes_online") or [])
    for i in range(min(online, W - 2)):
        px.add((i, H - 1))
    if tick % 2 == 0:
        px.add((W - 1, H - 1))                              # heartbeat
    return px


def ascii_frame(pixels: set) -> str:
    return "\n".join("".join("#" if (x, y) in pixels else "." for x in range(W)) for y in range(H))


def follow(url, state: dict, stop: threading.Event) -> None:
    for msg in Stream(url, timeout=5):
        if stop.is_set():
            return
        t = msg.get("type")
        if t == "hello":
            state["hello"], state["pos"] = msg, {}
        elif t == "anchors":
            state["anchors"] = msg
        elif t == "pos":
            state["pos"][msg["id"]] = msg
        elif t == "status":
            state["status"] = msg
        state["last"] = time.time()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uwbkit unoq-matrix", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None, help="hub URL (default $UWBKIT_URL or ws://127.0.0.1:1800/stream)")
    ap.add_argument("--socket", default=ROUTER_SOCKET, help="arduino-router unix socket")
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--ascii", action="store_true", help="print frames instead of sending them to the MCU")
    ap.add_argument("--frames", type=int, default=0, help="stop after N frames (testing)")
    a = ap.parse_args(argv)
    state: dict = {"pos": {}, "last": 0.0}
    stop = threading.Event()
    threading.Thread(target=follow, args=(a.url, state, stop), daemon=True).start()
    router = None if a.ascii else Router(a.socket)
    tick, warned = 0, 0.0
    try:
        while not a.frames or tick < a.frames:
            time.sleep(1.0 / a.fps)
            tick += 1
            if time.time() - state["last"] > 3.0:
                if a.ascii:
                    print("(no data from the hub: the MCU shows its own 'no hub' picture)\n")
                continue                 # send nothing: the MCU falls back to the "no hub" cross
            pixels = render(state, tick)
            if a.ascii:
                print(ascii_frame(pixels) + "\n")
            elif not router.call("uwbkit_frame", to_words(pixels)) and time.time() - warned > 10:
                warned = time.time()
                print(f"uwbkit unoq-matrix: cannot reach the MCU through {a.socket}: {router.error} "
                      "(is the uwbkit_unoq_matrix sketch flashed? is this a UNO Q?)", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
