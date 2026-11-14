"""uwbkit client: ONE file, Python standard library only (copy it into your project to vendor it).

    from uwbkit.client import Stream            # or: from vendor.uwbkit.client import Stream
    for msg in Stream("ws://127.0.0.1:1800/stream"):      # reconnects automatically
        if msg["type"] == "hello": print("SIMULATED" if msg["simulated"] else "real boards")
        if msg["type"] == "pos":   print(msg["id"], msg["x"], msg["y"], msg["q"])

    async for msg in AsyncStream(url): ...      # asyncio
    Kit("http://127.0.0.1:1800").teleport("T1", 1.0, 2.0)   # REST helpers

The URL defaults to the ``UWBKIT_URL`` environment variable, else ``ws://127.0.0.1:1800/stream``.
``http://host:port`` and bare ``host:port`` are accepted too. A URL whose path ends in ``/events``
(or ``transport="sse"``) uses Server-Sent Events instead of WebSocket.
Wire format: INTERFACE.md (v1). Unknown message types and fields are passed through untouched.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import http.client
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

__all__ = ["Stream", "AsyncStream", "Kit", "KitError", "DEFAULT_URL"]

DEFAULT_URL = "ws://127.0.0.1:1800/stream"
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_SILENCE_S = 30.0   # the hub sends status every second and a ping every 20 s


class KitError(Exception):
    """The hub answered with an error, or could not be reached."""


def _resolve(url=None, transport=None):
    """-> (transport, secure, host, port, path, http_base)"""
    url = (url or os.environ.get("UWBKIT_URL") or DEFAULT_URL).strip()
    if "://" not in url:
        url = "ws://" + url
    u = urlsplit(url)
    scheme = u.scheme.lower()
    secure = scheme in ("wss", "https")
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if secure else (1800 if scheme in ("ws", "wss") else 80))
    path = u.path or ""
    if path in ("", "/"):
        path = "/events" if transport == "sse" else "/stream"
    if transport is None:
        transport = "sse" if path.rstrip("/").endswith("/events") else "ws"
    if u.query:
        path += "?" + u.query
    shown = f"[{host}]" if ":" in host else host
    base = f"{'https' if secure else 'http'}://{shown}:{port}"
    return transport, secure, host, port, path, base


def _mask(payload: bytes, key: bytes) -> bytes:
    n = len(payload)
    if not n:
        return b""
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(k, "big")).to_bytes(n, "big")


def _frame(opcode: int, payload: bytes = b"") -> bytes:
    """One masked client frame (RFC 6455)."""
    key = os.urandom(4)
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
    return head + key + _mask(payload, key)


def _handshake_request(host: str, port: int, path: str):
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: uwbkit-client\r\n\r\n")
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
    return req.encode(), accept


def _check_handshake(head: bytes, accept: str) -> None:
    lines = head.decode("latin-1").split("\r\n")
    status = lines[0].split(" ", 2)
    if len(status) < 2 or status[1] != "101":
        raise ConnectionError(f"WebSocket handshake refused: {lines[0]!r}")
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        headers[k.strip().lower()] = v.strip()
    if headers.get("sec-websocket-accept") != accept:
        raise ConnectionError("WebSocket handshake failed: bad Sec-WebSocket-Accept")


class _Frames:
    """Sans-I/O frame decoder: feed bytes, get ('text', str) | ('ping', bytes) | ('close', bytes)."""

    def __init__(self):
        self.buf = bytearray()
        self._parts = None

    def feed(self, data: bytes) -> list:
        self.buf += data
        out = []
        buf = self.buf
        while True:
            if len(buf) < 2:
                break
            b0, b1 = buf[0], buf[1]
            n = b1 & 0x7F
            pos = 2
            if n == 126:
                if len(buf) < 4:
                    break
                n = struct.unpack_from("!H", buf, 2)[0]
                pos = 4
            elif n == 127:
                if len(buf) < 10:
                    break
                n = struct.unpack_from("!Q", buf, 2)[0]
                pos = 10
            masked = b1 & 0x80
            if masked:
                if len(buf) < pos + 4:
                    break
                key = bytes(buf[pos:pos + 4])
                pos += 4
            if len(buf) < pos + n:
                break
            payload = bytes(buf[pos:pos + n])
            del buf[:pos + n]
            if masked:
                payload = _mask(payload, key)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            if opcode == 0x8:
                out.append(("close", payload))
            elif opcode == 0x9:
                out.append(("ping", payload))
            elif opcode == 0xA:
                pass
            elif opcode in (0x0, 0x1, 0x2):
                if opcode != 0x0:
                    self._parts = [payload]
                elif self._parts is not None:
                    self._parts.append(payload)
                else:
                    continue  # continuation without a start: ignore
                if fin:
                    data_all = b"".join(self._parts)
                    self._parts = None
                    out.append(("text", data_all.decode("utf-8", "replace")))
        return out


class _Base:
    def __init__(self, url=None, types=None, reconnect=True, timeout=None, transport=None):
        (self.transport, self._secure, self._host, self._port, self._path,
         self.http_base) = _resolve(url, transport)
        scheme = ("wss" if self._secure else "ws") if self.transport == "ws" else \
            ("https" if self._secure else "http")
        self.url = f"{scheme}://{self._host}:{self._port}{self._path}"
        self.types = set([types] if isinstance(types, str) else types) if types else None
        self.reconnect = reconnect
        self.timeout = _SILENCE_S if timeout is None else (timeout or None)
        self.hello = None
        self.anchors = None
        self.simulated = None
        self.connected = False
        self.reconnects = 0
        self._closed = False
        self._backoff = 0.2

    def _decode(self, text: str):
        """JSON text -> message dict to yield, or None to skip."""
        try:
            msg = json.loads(text)
        except ValueError:
            return None
        if not isinstance(msg, dict):
            return None
        typ = msg.get("type")
        if typ == "hello":
            self.hello = msg
            self.simulated = bool(msg.get("simulated"))
        elif typ == "anchors":
            self.anchors = msg
        if self.types is not None and typ not in self.types:
            return None
        return msg

    def _next_backoff(self) -> float:
        b = self._backoff
        self._backoff = min(2.0, b * 1.8)
        return b


class Stream(_Base):
    """Synchronous iterator over hub messages. Reconnects for ever unless ``reconnect=False``."""

    def __init__(self, url=None, types=None, reconnect=True, timeout=None, transport=None):
        super().__init__(url, types, reconnect, timeout, transport)
        self._sock = None
        self._frames = None
        self._queue = []
        self._resp = None
        self._conn = None

    # -- connection -------------------------------------------------------
    def _connect(self) -> None:
        if self.transport == "sse":
            cls = http.client.HTTPSConnection if self._secure else http.client.HTTPConnection
            conn = cls(self._host, self._port, timeout=self.timeout or None)
            conn.request("GET", self._path, headers={"Accept": "text/event-stream"})
            resp = conn.getresponse()
            if resp.status != 200:
                conn.close()
                raise ConnectionError(f"SSE refused: HTTP {resp.status}")
            self._conn, self._resp = conn, resp
        else:
            sock = socket.create_connection((self._host, self._port), timeout=10.0)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                if self._secure:
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self._host)
                req, accept = _handshake_request(self._host, self._port, self._path)
                sock.sendall(req)
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("connection closed during the WebSocket handshake")
                    head += chunk
                    if len(head) > 65536:
                        raise ConnectionError("oversized handshake response")
                head, _, rest = head.partition(b"\r\n\r\n")
                _check_handshake(head, accept)
                sock.settimeout(self.timeout or None)
            except BaseException:
                sock.close()
                raise
            self._sock = sock
            self._frames = _Frames()
            self._queue = self._frames.feed(rest) if rest else []
        self.connected = True
        self._backoff = 0.2

    def _disconnect(self) -> None:
        self.connected = False
        for obj in (self._sock, self._resp, self._conn):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        self._sock = self._resp = self._conn = self._frames = None
        self._queue = []

    def _recv_text(self):
        """Next text payload, or None when the connection is gone."""
        if self.transport == "sse":
            data = []
            while True:
                line = self._resp.readline()
                if not line:
                    return None
                line = line.rstrip(b"\r\n")
                if not line:
                    if data:
                        return b"\n".join(data).decode("utf-8", "replace")
                    continue
                if line.startswith(b"data:"):
                    data.append(line[5:].lstrip())
        while True:
            while self._queue:
                kind, payload = self._queue.pop(0)
                if kind == "text":
                    return payload
                if kind == "ping":
                    self._sock.sendall(_frame(0xA, payload))
                elif kind == "close":
                    try:
                        self._sock.sendall(_frame(0x8, payload[:2]))
                    except OSError:
                        pass
                    return None
            chunk = self._sock.recv(65536)
            if not chunk:
                return None
            self._queue = self._frames.feed(chunk)

    # -- iteration --------------------------------------------------------
    def __iter__(self):
        return self

    def __next__(self) -> dict:
        while not self._closed:
            if not self.connected:
                try:
                    self._connect()
                except (OSError, ConnectionError, http.client.HTTPException) as e:
                    self._disconnect()
                    if not self.reconnect:
                        raise ConnectionError(f"cannot connect to {self.url}: {e}") from e
                    time.sleep(self._next_backoff())
                    continue
            try:
                text = self._recv_text()
            except (OSError, ConnectionError, http.client.HTTPException, ValueError):
                text = None
            if text is None:
                self._disconnect()
                if not self.reconnect:
                    raise StopIteration
                self.reconnects += 1
                time.sleep(self._next_backoff())
                continue
            msg = self._decode(text)
            if msg is not None:
                return msg
        raise StopIteration

    def close(self) -> None:
        self._closed = True
        if self._sock is not None:
            try:
                self._sock.sendall(_frame(0x8, struct.pack("!H", 1000)))
            except OSError:
                pass
        self._disconnect()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self._disconnect()
        except Exception:
            pass


class AsyncStream(_Base):
    """``async for msg in AsyncStream(url): ...`` with the same semantics as :class:`Stream`."""

    def __init__(self, url=None, types=None, reconnect=True, timeout=None, transport=None):
        super().__init__(url, types, reconnect, timeout, transport)
        self._reader = None
        self._writer = None
        self._frames = None
        self._queue = []
        self._sse_data = []

    async def _connect(self) -> None:
        ctx = ssl.create_default_context() if self._secure else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port, ssl=ctx, limit=1 << 22), 10.0)
        try:
            if self.transport == "sse":
                writer.write((f"GET {self._path} HTTP/1.1\r\nHost: {self._host}:{self._port}\r\n"
                              "Accept: text/event-stream\r\nConnection: close\r\n\r\n").encode())
                await writer.drain()
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10.0)
                status_line = head.split(b"\r\n", 1)[0]
                if b" 200" not in status_line:
                    raise ConnectionError(f"SSE refused: {status_line!r}")
            else:
                req, accept = _handshake_request(self._host, self._port, self._path)
                writer.write(req)
                await writer.drain()
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10.0)
                _check_handshake(head[:-4], accept)
        except BaseException:
            writer.close()
            raise
        self._reader, self._writer = reader, writer
        self._frames = _Frames()
        self._queue = []
        self._sse_data = []
        self.connected = True
        self._backoff = 0.2

    async def _disconnect(self) -> None:
        self.connected = False
        w, self._writer, self._reader = self._writer, None, None
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    async def _read(self, coro):
        return await (asyncio.wait_for(coro, self.timeout) if self.timeout else coro)

    async def _recv_text(self):
        if self.transport == "sse":
            # chunked transfer framing lines (hex sizes) never start with "data:", so they are skipped
            while True:
                line = await self._read(self._reader.readline())
                if not line:
                    return None
                line = line.rstrip(b"\r\n")
                if line.startswith(b"data:"):
                    self._sse_data.append(line[5:].lstrip())
                elif not line and self._sse_data:
                    data, self._sse_data = b"\n".join(self._sse_data), []
                    return data.decode("utf-8", "replace")
        while True:
            while self._queue:
                kind, payload = self._queue.pop(0)
                if kind == "text":
                    return payload
                if kind == "ping":
                    self._writer.write(_frame(0xA, payload))
                elif kind == "close":
                    try:
                        self._writer.write(_frame(0x8, payload[:2]))
                    except Exception:
                        pass
                    return None
            chunk = await self._read(self._reader.read(65536))
            if not chunk:
                return None
            self._queue = self._frames.feed(chunk)

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        while not self._closed:
            if not self.connected:
                try:
                    await self._connect()
                except (OSError, ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                        asyncio.LimitOverrunError) as e:
                    await self._disconnect()
                    if not self.reconnect:
                        raise ConnectionError(f"cannot connect to {self.url}: {e}") from e
                    await asyncio.sleep(self._next_backoff())
                    continue
            try:
                text = await self._recv_text()
            except (OSError, ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
                text = None
            if text is None:
                await self._disconnect()
                if not self.reconnect:
                    raise StopAsyncIteration
                self.reconnects += 1
                await asyncio.sleep(self._next_backoff())
                continue
            msg = self._decode(text)
            if msg is not None:
                return msg
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self._closed = True
        await self._disconnect()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


class Kit:
    """REST helpers for the hub (urllib only). ``Kit()`` uses ``UWBKIT_URL`` / the default hub."""

    def __init__(self, base_url=None, timeout: float = 5.0):
        self.base = _resolve(base_url)[5]
        self.timeout = timeout

    def _call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                text = json.loads(text).get("error", text)
            except (ValueError, AttributeError):
                pass
            raise KitError(f"{method} {path} -> HTTP {e.code}: {text}") from None
        except (urllib.error.URLError, OSError) as e:
            raise KitError(f"hub not reachable at {self.base}: {e}") from None

    def get(self, path: str):
        return self._call("GET", path)

    def post(self, path: str, body=None):
        return self._call("POST", path, body if body is not None else {})

    def state(self):
        return self.get("/state")

    def healthz(self):
        return self.get("/healthz")

    def diag(self):
        return self.get("/diag")

    def scenarios(self) -> list:
        r = self.get("/sim/scenarios")
        return r.get("scenarios", []) if isinstance(r, dict) else r

    def set_scenario(self, name_or_dict):
        key = "scenario" if isinstance(name_or_dict, dict) else "name"
        return self.post("/sim/scenario", {key: name_or_dict})

    def sim_control(self, cmd: str, value=None):
        body = {"cmd": cmd}
        if value is not None:
            body["value"] = value
        return self.post("/sim/control", body)

    def teleport(self, id: str, x: float, y: float, z=None):
        body = {"id": id, "x": x, "y": y}
        if z is not None:
            body["z"] = z
        return self.post("/sim/teleport", body)

    def release(self, id: str):
        return self.post("/sim/teleport", {"id": id, "release": True})

    def set_anchors(self, anchors: dict, calibrated: str = "manual"):
        return self.post("/anchors", {"type": "anchors", "frame": "site", "anchors": anchors,
                                      "calibrated": calibrated})

    def calibrate(self, **options):
        return self.post("/calibrate", options)

    def record(self, cmd: str, path=None):
        body = {"cmd": cmd}
        if path:
            body["path"] = path
        return self.post("/record", body)

    def truth(self):
        return self.get("/sim/truth")


def main(argv=None) -> int:
    """``python -m uwbkit.client [url] [--types pos,event] [--count N]``: print the stream."""
    import argparse
    ap = argparse.ArgumentParser(prog="uwbkit.client", description="Print messages from a uwb-kit hub.")
    ap.add_argument("url", nargs="?", default=None)
    ap.add_argument("--types", default=None, help="comma list, e.g. pos,event")
    ap.add_argument("--count", type=int, default=0, help="stop after N messages")
    ap.add_argument("--no-reconnect", action="store_true")
    a = ap.parse_args(argv)
    types = set(a.types.split(",")) if a.types else None
    n = 0
    stream = Stream(a.url, types=types, reconnect=not a.no_reconnect)
    try:
        for msg in stream:
            print(json.dumps(msg, separators=(",", ":")), flush=True)
            n += 1
            if a.count and n >= a.count:
                break
    except KeyboardInterrupt:
        pass
    except ConnectionError as e:
        print(f"uwbkit.client: {e}", file=sys.stderr)
        return 1
    finally:
        stream.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
