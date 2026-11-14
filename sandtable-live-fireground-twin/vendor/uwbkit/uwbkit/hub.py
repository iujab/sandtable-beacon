"""uwbkit hub: one asyncio process that turns a source (simulator, boards, replay) into the
INTERFACE.md v1 stream: WebSocket ``/stream``, SSE ``/events`` and the REST routes, with open CORS.

Pipeline per tick: raw ranges from the source -> validate / de-duplicate / bias-correct -> publish
``range`` -> ``Tracker.update`` -> publish ``pos`` -> ``EventDetector.update`` -> publish ``event``;
``status`` once per second.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import time
from collections import deque
from pathlib import Path

from aiohttp import WSMsgType, web

from . import KIT_NAME, WIRE_VERSION, __version__
from .sources import Source

log = logging.getLogger("uwbkit.hub")
STATIC_DIR = Path(__file__).parent / "static"
ONLINE_TIMEOUT_S = 2.5
MAX_RANGE_M = 150.0


def _dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False)


def _num(v):
    """Finite float or None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def role_of(node_id: str) -> str:
    c = node_id[:1].upper()
    return "anchor" if c == "A" else "mesh" if c == "N" else "tag"


def profile_nodes(profile: str) -> dict:
    """Default node table for a profile string: ``4A1T`` -> A1..A4 + T1, ``mesh5`` -> N1..N5."""
    p = (profile or "").strip()
    m = re.fullmatch(r"(\d+)A(\d+)T", p, re.I)
    if m:
        nodes = {f"A{i}": {"role": "anchor"} for i in range(1, int(m.group(1)) + 1)}
        nodes.update({f"T{i}": {"role": "tag"} for i in range(1, int(m.group(2)) + 1)})
        return nodes
    m = re.fullmatch(r"mesh(\d+)", p, re.I)
    if m:
        return {f"N{i}": {"role": "mesh"} for i in range(1, int(m.group(1)) + 1)}
    return {}


class _Client:
    """One connected stream consumer (WebSocket or SSE) with a bounded queue."""

    __slots__ = ("queue", "types", "kind", "lost")

    def __init__(self, kind: str, types=None, maxsize: int = 2000):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.types = types
        self.kind = kind
        self.lost = 0

    def push(self, text: str) -> None:
        q = self.queue
        if q.full():  # slow consumer: drop the oldest message, never block the hub
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            self.lost += 1
        q.put_nowait(text)


class _Link:
    __slots__ = ("count", "win", "last")

    def __init__(self):
        self.count = 0
        self.win: deque = deque(maxlen=400)
        self.last: dict = {}

    def add(self, m: dict) -> None:
        self.count += 1
        self.win.append((m["t"], m["d"]))
        self.last = m

    def stats(self, now: float, horizon: float = 5.0) -> dict:
        pts = [(t, d) for t, d in self.win if now - t <= horizon]
        out = {"count": self.count, "age_s": round(now - self.last.get("t", 0.0), 2) if self.last else None,
               "rate_hz": 0.0, "d_mean": None, "d_std": None,
               "d": self.last.get("d"), "rssi": self.last.get("rssi"), "fp": self.last.get("fp"),
               "los": self.last.get("los")}
        if pts:
            span = max(1.0, min(horizon, now - pts[0][0]))
            ds = [d for _, d in pts]
            mean = sum(ds) / len(ds)
            out["rate_hz"] = round(len(pts) / span, 2)
            out["d_mean"] = round(mean, 4)
            out["d_std"] = round(math.sqrt(sum((d - mean) ** 2 for d in ds) / len(ds)), 4)
        return out


class Hub:
    def __init__(self, source: Source, *, profile: str | None = None, rate_hz: float | None = None,
                 dims: int | None = None, anchors: dict | None = None, anchors_path: str | None = None,
                 tag_z: float = 1.0, record_dir: str = ".", solve: bool = True):
        self.source = source
        self.opt_profile = profile
        self.opt_rate = rate_hz
        self.opt_dims = dims
        self.opt_anchors = anchors
        self.anchors_path = anchors_path
        self.tag_z = tag_z
        self.record_dir = Path(record_dir)
        self.solve = solve

        self.clients: set[_Client] = set()
        self.hello: dict = {}
        self.anchors_msg: dict = {}
        self.room: dict = {}
        self.last_pos: dict = {}
        self.last_range: dict = {}
        self.status: dict = {}
        self.links: dict[str, _Link] = {}
        self.node_table: dict[str, dict] = {}
        self.node_seen: dict[str, float] = {}
        self.online: set[str] = set()
        self.logs: deque = deque(maxlen=200)
        self.bias: dict = {"offsets": {}, "scale": 1.0}

        self.tracker = None
        self.detector = None
        self.is_mesh = False
        self.seq = 0
        self.dropped = 0
        self.published = 0
        self._pending: list[dict] = []
        self._pending_keys: set = set()
        self._self_ticking = bool(getattr(source, "self_ticking", False))
        self._last_process = 0.0
        self._dedup: dict = {}
        self._last_status = 0.0
        self._pos_times: deque = deque(maxlen=600)
        self._range_times: deque = deque(maxlen=4000)
        self._rec = None
        self._rec_path = None
        self._rec_count = 0
        self._cal: dict | None = None
        self._fake_seen = False       # a fake-board emulator announced itself: its data is simulated
        self.last_calibration: dict | None = None
        self._err_t = 0.0
        self._tasks: list[asyncio.Task] = []
        self.started = time.time()
        self._configure(initial=True)

    @property
    def simulated(self) -> bool:
        """True for the simulator, replays of simulated recordings, and fake-board emulators."""
        return bool(self.source.simulated or self._fake_seen)

    # ------------------------------------------------------------------ config
    def _configure(self, initial: bool = False) -> None:
        """(Re)build hello / anchors / solver from the source description and CLI options."""
        d = self.source.describe() or {}
        profile = self.opt_profile or d.get("profile")
        if not profile:
            # real boards, no --profile: mesh firmware announces itself through its N* ids
            roles = {v.get("role") or role_of(k) for k, v in self.node_table.items()}
            n = len(self.node_table)
            profile = ("mesh5" if n <= 5 else f"mesh{n}") if roles == {"mesh"} else "4A1T"
        rate = float(self.opt_rate or d.get("rate_hz") or 10.0)
        rate = int(rate) if rate == int(rate) else rate
        dims = int(self.opt_dims or d.get("dims") or 2)
        nodes = {k: dict(v) for k, v in (d.get("nodes") or profile_nodes(profile)).items()}
        for nid, info in self.node_table.items():  # discovered boards
            nodes.setdefault(nid, {"role": info.get("role") or role_of(nid)})
        nodes = dict(sorted(nodes.items()))
        self.is_mesh = profile.lower().startswith("mesh") or (
            bool(nodes) and all(v.get("role") == "mesh" for v in nodes.values()))
        derived = self.source.simulated or getattr(self.source, "passthrough", False)
        if not derived and not initial and self.anchors_msg.get("anchors"):
            # real boards: a node appearing must not throw away the current calibration
            anchors, calibrated = self.anchors_msg["anchors"], self.anchors_msg.get("calibrated", "manual")
        elif self.opt_anchors is not None and (initial or not derived):
            anchors, calibrated = self.opt_anchors, "manual"
        else:
            anchors = d.get("anchors") or {}
            calibrated = d.get("calibrated") or ("manual" if anchors else "none")
        anchors = {k: {"x": float(v.get("x", 0.0)), "y": float(v.get("y", 0.0)), "z": float(v.get("z", 0.0))}
                   for k, v in anchors.items()}
        self.room = d.get("room") or {}
        hello = {"type": "hello", "kit": KIT_NAME, "v": WIRE_VERSION,
                 "simulated": self.simulated, "source": self.source.name,
                 "profile": profile, "rate_hz": rate, "nodes": nodes,
                 "frame": d.get("frame") or ("mesh" if self.is_mesh else "site"), "dims": dims,
                 "kit_version": __version__}
        if self.room:
            hello["room"] = self.room
        if self.source.simulated:
            hello["sim"] = {"scenario": d.get("scenario"), "noise": d.get("noise"),
                            "description": d.get("description")}
        elif self._fake_seen:
            hello["sim"] = {"scenario": None, "noise": None,
                            "description": "fake-board emulator (uwbkit fakeboard): simulated ranges over the real board path"}
        if d.get("recorded_source"):
            hello["recorded_source"] = d["recorded_source"]
        self.hello = hello
        self.anchors_msg = {"type": "anchors", "frame": hello["frame"], "anchors": anchors,
                            "calibrated": calibrated}
        self.last_pos, self.last_range = {}, {}
        self._build_solver()

    def _build_solver(self) -> None:
        self.tracker = None
        self.detector = None
        if not self.solve or getattr(self.source, "passthrough", False):
            return  # replay passes recorded pos through untouched
        try:
            from .solver import EventDetector, Tracker
        except Exception as e:  # pragma: no cover - solver missing
            log.error("solver unavailable (%s): publishing ranges only", e)
            return
        h = self.hello
        nodes = {k: dict(v) for k, v in h["nodes"].items()}
        for v in nodes.values():
            if v.get("role") == "tag":
                v.setdefault("z", self.tag_z)
        anchors = self.anchors_msg["anchors"]
        try:
            if self.is_mesh or anchors:
                self.tracker = Tracker(h["profile"], None if self.is_mesh else anchors, dims=h["dims"],
                                       nodes=nodes, rate_hz=h["rate_hz"])
            self.detector = EventDetector()
        except Exception:
            log.exception("could not create the solver")

    def _broadcast_config(self) -> None:
        self.publish(self.hello)
        self.publish(self.anchors_msg)

    # ----------------------------------------------------------------- publish
    def publish(self, msg: dict) -> None:
        try:
            text = _dumps(msg)
        except (ValueError, TypeError):
            self.dropped += 1
            return
        typ = msg.get("type")
        if typ == "pos":
            self.last_pos[msg.get("id")] = msg
        elif typ == "range":
            self.last_range[f"{msg.get('a')}-{msg.get('b')}"] = msg
        elif typ == "status":
            self.status = msg
        self.published += 1
        if self._rec is not None:
            try:
                self._rec.write(text + "\n")
                self._rec_count += 1
            except OSError:
                log.exception("recording failed; stopping it")
                self._stop_record()
        for c in self.clients:
            if c.types is None or typ in c.types:
                c.push(text)

    # ------------------------------------------------------------------ ingest
    def on_raw(self, msg: dict) -> None:
        """Called by the source for every raw message (on the event loop thread)."""
        typ = msg.get("type")
        if typ == "range":
            if "t" not in msg:
                msg["t"] = time.time()
            self._on_range(msg)
        elif typ == "tick":
            self.process(msg.get("t") or time.time())
        elif typ == "event":
            ev = {"type": "event", "t": round(_num(msg.get("t")) or time.time(), 3),
                  "id": str(msg.get("id", "hub")), "name": str(msg.get("name", "?")),
                  "data": msg.get("data") if isinstance(msg.get("data"), dict) else {}}
            self._touch(ev["id"], ev["t"])
            self.publish(ev)
        elif typ == "node":
            self._on_node(msg)
        elif typ == "log":
            self.logs.append({"t": round(time.time(), 2), "text": str(msg.get("text", ""))[:300]})
        elif typ == "_pub":
            m = msg.get("msg") or {}
            if m.get("type") == "anchors":
                self.anchors_msg = m
            if m.get("type") == "range" and _num(m.get("d")) is not None and _num(m.get("t")) is not None:
                self.links.setdefault(f"{m.get('a')}-{m.get('b')}", _Link()).add(m)
            self.publish(m)
        elif typ == "_reconfig":
            self._configure()
            self.links.clear()
            self._pending.clear()
            self._pending_keys.clear()
            self._pos_times.clear()
            self.node_seen.clear()
            self.online.clear()
            self._broadcast_config()
        elif typ == "anchors_hint":
            anchors = msg.get("anchors") or {}
            if anchors and not self.anchors_msg.get("anchors"):
                self.set_anchors(anchors, "auto")

    def _on_node(self, msg: dict) -> None:
        nid = msg.get("id")
        if not isinstance(nid, str) or not nid:
            return
        entry = self.node_table.setdefault(nid, {})
        new = not entry
        for k, v in msg.items():
            if k not in ("type", "id") and v is not None:
                if k == "kv" and isinstance(v, dict):
                    entry.setdefault("kv", {}).update(v)
                else:
                    entry[k] = v
        entry["last_t"] = round(time.time(), 2)
        self._touch(nid, time.time())
        fake = str(msg.get("chip", "")).upper() == "FAKE" and not self._fake_seen
        if fake:
            self._fake_seen = True     # honesty: emulated boards are labelled simulated
            log.warning("node %s is a fake-board emulator: hello.simulated is now true", nid)
        if fake or (new and nid not in self.hello.get("nodes", {})):
            self._configure()
            self._broadcast_config()

    def _touch(self, nid: str, t: float) -> None:
        if nid and nid != "hub":
            self.node_seen[nid] = t

    def _clean_range(self, r: dict):
        a, b = r.get("a"), r.get("b")
        d = _num(r.get("d"))
        t = _num(r.get("t")) or time.time()
        if not isinstance(a, str) or not isinstance(b, str) or not a or not b or a == b \
                or d is None or d < -1.0 or d > MAX_RANGE_M:
            self.dropped += 1
            return None
        # canonical direction, whoever reported it: the mobile node first ("T1-A2"), else id order
        nodes = self.hello.get("nodes", {})
        ra = nodes.get(a, {}).get("role") or role_of(a)
        rb = nodes.get(b, {}).get("role") or role_of(b)
        if (ra == "anchor" and rb != "anchor") or (ra == rb and b < a):
            a, b = b, a
        bseq = r.get("bseq")
        if bseq is not None:  # the same exchange may arrive over serial and UDP, or via a relay
            key = (a, b, bseq)
            seen = self._dedup.get(key)
            if seen is not None and t - seen < 2.0:
                return None
            self._dedup[key] = t
            if len(self._dedup) > 4000:
                cutoff = t - 2.0
                self._dedup = {k: v for k, v in self._dedup.items() if v >= cutoff}
        raw = _num(r.get("raw"))
        off = self.bias["offsets"]
        corr = off.get(f"{a}-{b}", off.get(f"{b}-{a}", 0.0)) + off.get(a, 0.0) + off.get(b, 0.0)
        scale = self.bias.get("scale", 1.0)
        if corr or scale != 1.0:
            if raw is None:
                raw = d
            d = d * scale - corr
        self.seq += 1
        out = {"type": "range", "t": round(t, 4), "seq": self.seq, "a": a, "b": b, "d": round(max(0.0, d), 4)}
        if raw is not None:
            out["raw"] = round(raw, 4)
        for k in ("rssi", "fp"):
            v = _num(r.get(k))
            if v is not None:
                out[k] = round(v, 1)
        v = _num(r.get("los"))
        if v is not None:
            out["los"] = round(min(1.0, max(0.0, v)), 2)
        return out

    def _on_range(self, raw: dict) -> None:
        """A range arrived: validate, publish at once, queue for the solver. For board sources the
        solver runs when a measurement cycle completes (the same link shows up again), so every
        solve sees whole cycles instead of whatever happened to arrive within a fixed tick."""
        m = self._clean_range(raw)
        if m is None:
            return
        a, b = m["a"], m["b"]
        nodes = self.hello["nodes"]
        new_node = False
        for nid in (a, b):
            self._touch(nid, m["t"])
            if nid not in nodes and nid not in self.node_table:
                self.node_table[nid] = {"role": role_of(nid), "discovered": True}
                new_node = True
        if new_node:
            self._configure()
            self._broadcast_config()
            nodes = self.hello["nodes"]
        key = f"{a}-{b}"
        if not self._self_ticking and key in self._pending_keys:
            self.process(m["t"])
        self.links.setdefault(key, _Link()).add(m)
        self._range_times.append(m["t"])
        self.publish(m)
        anchors = self.anchors_msg.get("anchors", {})
        a_anchor = a in anchors or nodes.get(a, {}).get("role") == "anchor"
        b_anchor = b in anchors or nodes.get(b, {}).get("role") == "anchor"
        if a_anchor and b_anchor and not self.is_mesh:
            if self._cal is not None:      # anchor-to-anchor ranges feed self-calibration only
                self._cal["pairs"].setdefault(tuple(sorted((a, b))), []).append(m["d"])
        else:
            self._pending.append(m)
            self._pending_keys.add(key)

    def process(self, t: float) -> None:
        """One solver tick: solve from the ranges queued since the last tick, detect events, status."""
        solver_in, self._pending = self._pending, []
        self._pending_keys.clear()
        self._last_process = t
        pos = []
        if self.tracker is not None:
            try:
                pos = self.tracker.update(solver_in, t) or []
            except Exception:
                self._log_error("solver update failed")
        for p in pos:
            self._pos_times.append(t)
            self.publish(p)
        if self.detector is not None:
            try:
                for e in self.detector.update(pos, solver_in, t) or []:
                    self.publish(e)
            except Exception:
                self._log_error("event detector failed")
        self._presence(t)
        if t - self._last_status >= 1.0:
            self._last_status = t
            self.publish(self._make_status(t))

    def _log_error(self, what: str) -> None:
        now = time.time()
        if now - self._err_t > 5.0:
            self._err_t = now
            log.exception(what)

    def _presence(self, t: float) -> None:
        now_online = {n for n, seen in self.node_seen.items() if t - seen <= ONLINE_TIMEOUT_S}
        for n in sorted(now_online - self.online):
            self.publish({"type": "event", "t": round(t, 3), "id": n, "name": "node_online", "data": {}})
        for n in sorted(self.online - now_online):
            self.publish({"type": "event", "t": round(t, 3), "id": n, "name": "node_offline", "data": {}})
        self.online = now_online

    def _make_status(self, t: float) -> dict:
        def per_second(times, streams):
            recent = [x for x in times if t - x <= 2.0]
            if len(recent) < 2 or recent[-1] <= recent[0]:
                return 0.0
            # events per second per stream, measured between the first and last event of the window
            return max(0.0, (len(recent) - streams) / (recent[-1] - recent[0]) / streams)
        tracked = sum(1 for p in self.last_pos.values() if t - p.get("t", 0) <= 2.0)
        rate = per_second(self._pos_times, max(1, tracked))
        if not rate:
            live = sum(1 for l in self.links.values() if l.last and t - l.last.get("t", 0) <= 2.0)
            rate = per_second(self._range_times, max(1, live))
        battery = {n: v["battery"] for n, v in self.node_table.items()
                   if isinstance(v.get("battery"), (int, float))}
        src = {}
        with contextlib.suppress(Exception):
            src = self.source.info() or {}
        bad = int(src.get("lines_bad", 0) or 0) + int(src.get("bad_checksum", 0) or 0)
        st = {"type": "status", "t": round(t, 3), "rate_hz": round(rate, 1),
              "nodes_online": sorted(self.online), "dropped": self.dropped + bad, "battery": battery,
              "clients": len(self.clients), "uptime_s": round(t - self.started, 1)}
        if self._rec is not None:
            st["recording"] = self._rec_path
        if self.source.simulated and hasattr(self.source, "sim"):
            st["sim"] = {"paused": bool(self.source.sim.paused), "speed": float(self.source.sim.speed),
                         "t_sim": round(float(self.source.sim.t_sim), 2)}
        return st

    # ----------------------------------------------------------------- anchors
    def set_anchors(self, anchors: dict, calibrated: str = "manual", save: bool = True) -> dict:
        clean = {}
        for k, v in anchors.items():
            x, y, z = _num(v.get("x")), _num(v.get("y")), _num(v.get("z", 0.0))
            if x is None or y is None:
                raise ValueError(f"anchor {k}: x and y must be numbers")
            clean[str(k)] = {"x": round(x, 4), "y": round(y, 4), "z": round(z or 0.0, 4)}
        self.anchors_msg = {"type": "anchors", "frame": self.hello.get("frame", "site"),
                            "anchors": clean, "calibrated": calibrated}
        added = False
        for k in clean:
            if k not in self.hello["nodes"]:
                self.node_table.setdefault(k, {"role": "anchor"})
                self.hello["nodes"][k] = {"role": "anchor"}
                added = True
        if added:
            self.hello["nodes"] = dict(sorted(self.hello["nodes"].items()))
            self.publish(self.hello)
        if self.tracker is not None and not self.is_mesh and clean:
            try:
                self.tracker.set_anchors(clean)
            except Exception:
                log.exception("tracker.set_anchors failed; rebuilding the solver")
                self._build_solver()
        else:
            self._build_solver()
        self.publish(self.anchors_msg)
        if save and self.anchors_path:
            try:
                tmp = f"{self.anchors_path}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.anchors_msg, f, indent=1)
                os.replace(tmp, self.anchors_path)
            except OSError:
                log.exception("could not save anchors to %s", self.anchors_path)
        return self.anchors_msg

    async def _calibrate(self, duration: float, z) -> None:
        """Anchor self-calibration: anchors range each other, the kit solves the layout."""
        ids = sorted(n for n, v in self.hello["nodes"].items() if v.get("role") == "anchor")
        result = {"ok": False}
        try:
            if self.source.simulated and hasattr(self.source, "sim"):
                pairs: dict = {}
                for m in self.source.sim.survey(30):
                    pairs.setdefault(tuple(sorted((m["a"], m["b"]))), []).append(m["d"])
            else:
                self._cal = {"pairs": {}}
                await self.source.send_command(f"SURVEY {int(math.ceil(duration))}")
                await asyncio.sleep(duration + 1.0)
                pairs, self._cal = self._cal["pairs"], None
            if len(ids) < 3:
                raise ValueError(f"need at least 3 anchors, have {ids}")

            def med(v):
                s = sorted(v)
                return s[len(s) // 2]
            dists = {k: med(v) for k, v in pairs.items() if len(v) >= 3 and k[0] in ids and k[1] in ids}
            need = len(ids) * (len(ids) - 1) // 2
            if len(dists) < max(3, need - 1):
                raise ValueError(f"only {len(dists)} of {need} anchor pairs measured: "
                                 "check that every anchor is powered and in SURVEY mode")
            old = self.anchors_msg.get("anchors", {})
            if isinstance(z, dict):
                zmap = {i: float(z.get(i, 0.0)) for i in ids}
            elif z is not None:
                zmap = {i: float(z) for i in ids}
            else:
                zmap = {i: float(old.get(i, {}).get("z", 0.0)) for i in ids}
            from .solver import solve_layout
            layout = solve_layout(dists, ids, dims=2, z=zmap)
            for i in ids:
                layout[i]["z"] = zmap[i]
            # residual of the solved layout against the measured distances
            sq = []
            for (a, b), d in dists.items():
                pa, pb = layout[a], layout[b]
                est = math.dist((pa["x"], pa["y"], pa["z"]), (pb["x"], pb["y"], pb["z"]))
                sq.append((est - d) ** 2)
            rms = math.sqrt(sum(sq) / len(sq))
            self.set_anchors(layout, "auto")
            result = {"ok": True, "rms_m": round(rms, 4), "pairs": len(dists), "anchors": ids}
        except Exception as e:
            self._cal = None
            result = {"ok": False, "error": str(e)}
            log.warning("calibration failed: %s", e)
        self.last_calibration = dict(result, t=round(time.time(), 3))
        self.publish({"type": "event", "t": round(time.time(), 3), "id": "hub", "name": "calibration",
                      "data": result})

    # --------------------------------------------------------------- recording
    def _start_record(self, path: str) -> str:
        p = Path(path)
        target = (p if p.is_absolute() else self.record_dir / p).resolve()
        root = self.record_dir.resolve()
        if root != target.parent and root not in target.parents:
            raise ValueError("recording path must be inside the hub's record directory")
        self._stop_record()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._rec = open(target, "w", encoding="utf-8", buffering=1 << 16)
        self._rec_path = str(target)
        self._rec_count = 0
        for m in (self.hello, self.anchors_msg):
            self._rec.write(_dumps(m) + "\n")
            self._rec_count += 1
        return self._rec_path

    def _stop_record(self) -> dict:
        info = {"path": self._rec_path, "messages": self._rec_count}
        if self._rec is not None:
            with contextlib.suppress(OSError):
                self._rec.close()
        self._rec, self._rec_path = None, None
        return info

    # ------------------------------------------------------------------- state
    def state(self) -> dict:
        return {"hello": self.hello, "anchors": self.anchors_msg, "pos": self.last_pos,
                "ranges": self.last_range, "status": self.status}

    def diag(self) -> dict:
        now = time.time()
        nodes = {}
        for nid in sorted(set(self.hello["nodes"]) | set(self.node_table) | set(self.node_seen)):
            e = dict(self.hello["nodes"].get(nid, {}))
            e.update({k: v for k, v in self.node_table.get(nid, {}).items()})
            seen = self.node_seen.get(nid)
            e["online"] = nid in self.online
            e["age_s"] = round(now - seen, 2) if seen else None
            nodes[nid] = e
        src = {}
        with contextlib.suppress(Exception):
            src = self.source.info() or {}
        return {"t": round(now, 3), "simulated": self.simulated, "source": src,
                "profile": self.hello["profile"], "nominal_rate_hz": self.hello["rate_hz"],
                "calibrated": self.anchors_msg.get("calibrated"), "nodes": nodes,
                "links": {k: v.stats(now) for k, v in sorted(self.links.items())},
                "dropped": self.dropped, "published": self.published, "clients": len(self.clients),
                "bias": self.bias, "calibration": self.last_calibration, "logs": list(self.logs)[-40:]}

    # ---------------------------------------------------------------- lifecycle
    async def _source_runner(self) -> None:
        while True:
            try:
                await self.source.run(self.on_raw)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("source crashed; restarting in 1 s")
                await asyncio.sleep(1.0)

    async def _ticker(self) -> None:
        """Fallback tick for board sources: normally a completed measurement cycle triggers the
        solver; this keeps coasting, presence and status alive when ranges stop or are sparse."""
        while True:
            period = 1.0 / max(1.0, float(self.hello["rate_hz"]))
            now = time.time()
            if now - self._last_process >= 1.5 * period:
                self.process(now)
            await asyncio.sleep(period / 2)

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._source_runner(), name="uwbkit-source"))
        if not getattr(self.source, "self_ticking", False):
            self._tasks.append(asyncio.create_task(self._ticker(), name="uwbkit-ticker"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()
        self._stop_record()


# ============================================================================ HTTP
def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=_dumps(data), status=status, content_type="application/json")


def _err(message: str, status: int = 400) -> web.Response:
    return _json({"ok": False, "error": message}, status)


async def _body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=_dumps({"ok": False, "error": "body must be JSON"}),
                                 content_type="application/json")
    return data if isinstance(data, dict) else {}


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Private-Network": "true",
    "Access-Control-Max-Age": "86400",
}


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=CORS_HEADERS)
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        e.headers.update(CORS_HEADERS)
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:     # a handler bug must never take the hub down or leak a bare 500 page
        log.exception("request %s %s failed", request.method, request.path)
        resp = _json({"ok": False, "error": f"internal error: {type(e).__name__}: {e}"}, 500)
    if not resp.prepared:
        resp.headers.update(CORS_HEADERS)
    return resp


def _types_filter(request: web.Request):
    q = request.query.get("types")
    return {x.strip() for x in q.split(",") if x.strip()} if q else None


def make_app(hub: Hub) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])

    def sim():
        s = getattr(hub.source, "sim", None)
        if not hub.source.simulated or s is None:
            raise web.HTTPConflict(text=_dumps({"ok": False, "error": "not a sim source"}),
                                   content_type="application/json")
        return s

    # ---- streams
    async def stream(request: web.Request):
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=1 << 20)
        ws.headers.update({"Access-Control-Allow-Origin": "*"})
        await ws.prepare(request)
        client = _Client("ws", _types_filter(request))
        client.push(_dumps(hub.hello))        # hello first, then anchors, on every connection
        client.push(_dumps(hub.anchors_msg))
        hub.clients.add(client)

        async def writer():
            with contextlib.suppress(ConnectionError, RuntimeError):
                while True:
                    text = await client.queue.get()
                    await ws.send_str(text)

        wtask = asyncio.create_task(writer())
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data == "ping":
                    await ws.send_str('{"type":"pong"}')
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        except (asyncio.CancelledError, ConnectionError):
            pass
        finally:
            hub.clients.discard(client)
            wtask.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await wtask
        return ws

    async def events(request: web.Request):
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no", **CORS_HEADERS})
        await resp.prepare(request)
        client = _Client("sse", _types_filter(request))
        try:
            client.push(_dumps(hub.hello))
            client.push(_dumps(hub.anchors_msg))
            hub.clients.add(client)
            while True:
                try:
                    text = await asyncio.wait_for(client.queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    await resp.write(b": keep-alive\n\n")
                    continue
                parts = [text]
                while not client.queue.empty() and len(parts) < 64:
                    parts.append(client.queue.get_nowait())
                await resp.write("".join(f"data: {p}\n\n" for p in parts).encode())
        except (asyncio.CancelledError, ConnectionError, RuntimeError):
            pass
        finally:
            hub.clients.discard(client)
        return resp

    # ---- snapshots
    async def get_state(request):
        return _json(hub.state())

    async def healthz(request):
        return _json({"ok": True, "kit": KIT_NAME, "v": WIRE_VERSION, "simulated": hub.hello["simulated"],
                      "source": hub.hello["source"], "kit_version": __version__})

    async def get_diag(request):
        return _json(hub.diag())

    async def get_room(request):
        return _json({"room": hub.room, "anchors": hub.anchors_msg.get("anchors", {}),
                      "dims": hub.hello["dims"], "profile": hub.hello["profile"]})

    # ---- anchors / calibration
    async def post_anchors(request):
        body = await _body(request)
        anchors = body.get("anchors", body if body and "type" not in body else None)
        if not isinstance(anchors, dict) or not anchors or not all(isinstance(v, dict) for v in anchors.values()):
            return _err('body must be an anchors message: {"anchors":{"A1":{"x":0,"y":0,"z":2.0},...}}')
        try:
            msg = hub.set_anchors(anchors, "manual")
        except ValueError as e:
            return _err(str(e))
        return _json({"ok": True, "anchors": msg})

    async def post_calibrate(request):
        body = await _body(request)
        if hub.is_mesh:
            return _err("mesh profiles have no anchors to calibrate", 409)
        if hub._cal is not None:
            return _err("calibration already running", 409)
        duration = min(30.0, max(1.0, float(_num(body.get("duration_s")) or 5.0)))
        if hub.source.simulated:
            duration = 0.0             # the simulator surveys instantly
        asyncio.create_task(hub._calibrate(duration, body.get("z")))
        return _json({"ok": True, "started": True, "duration_s": duration,
                      "note": "result arrives as a new anchors message and a 'calibration' event"})

    async def post_bias(request):
        body = await _body(request)
        offsets = body.get("offsets")
        if offsets is not None:
            if not isinstance(offsets, dict) or any(_num(v) is None for v in offsets.values()):
                return _err('offsets must map "T1-A1" or node ids to metres')
            hub.bias["offsets"] = {str(k): float(v) for k, v in offsets.items()}
        if body.get("scale") is not None:
            s = _num(body.get("scale"))
            if s is None or not 0.5 < s < 1.5:
                return _err("scale must be between 0.5 and 1.5")
            hub.bias["scale"] = s
        return _json({"ok": True, "bias": hub.bias})

    async def post_command(request):
        body = await _body(request)
        line = str(body.get("line", "")).strip()
        if not line or len(line) > 120 or "\n" in line:
            return _err('body: {"line":"ANTDLY 16385","node":"A1"}')
        ok = await hub.source.send_command(line, body.get("node"))
        return _json({"ok": bool(ok)}, 200 if ok else 409)

    # ---- sim
    async def sim_scenarios(request):
        from .sim import list_scenarios
        names = list_scenarios()
        details = {}
        with contextlib.suppress(Exception):
            from .sim import load_scenario
            for n in names:
                sc = load_scenario(n)
                details[n] = {"profile": sc.get("profile"), "dims": sc.get("dims", 2),
                              "description": sc.get("description", "")}
        current = getattr(getattr(hub.source, "sim", None), "name", None)
        return _json({"scenarios": names, "current": current, "details": details,
                      "simulated": bool(hub.source.simulated)})

    async def sim_scenario(request):
        sim()
        body = await _body(request)
        spec = body.get("scenario") if isinstance(body.get("scenario"), dict) else body.get("name")
        if not spec:
            return _err('body: {"name":"office-walk"} or {"scenario":{...}}')
        try:
            hub.source.set_scenario(spec)
        except (ValueError, OSError, KeyError, TypeError) as e:
            return _err(f"bad scenario: {e}")
        hub.source._changed = False
        hub.on_raw({"type": "_reconfig"})
        return _json({"ok": True, "name": hub.source.sim.name, "hello": hub.hello})

    async def sim_control(request):
        s = sim()
        body = await _body(request)
        cmd = body.get("cmd")
        if cmd not in ("pause", "resume", "restart", "speed"):
            return _err("cmd must be pause | resume | restart | speed")
        value = _num(body.get("value"))
        if cmd == "speed" and (value is None or not 0.0 < value <= 20.0):
            return _err("speed needs a value between 0 and 20")
        s.control(cmd, value)
        if cmd == "restart" and hub.tracker is not None:
            with contextlib.suppress(Exception):
                hub.tracker.reset()
        return _json({"ok": True, "paused": bool(s.paused), "speed": float(s.speed),
                      "t_sim": round(float(s.t_sim), 3)})

    async def sim_teleport(request):
        s = sim()
        body = await _body(request)
        nid = body.get("id")
        if not isinstance(nid, str) or nid not in s.nodes:
            return _err(f"unknown node {nid!r}; nodes: {sorted(s.nodes)}")
        if body.get("release"):
            s.release(nid)
            return _json({"ok": True, "id": nid, "released": True})
        x, y, z = _num(body.get("x")), _num(body.get("y")), _num(body.get("z"))
        if x is None or y is None:
            return _err('body: {"id":"T1","x":1.0,"y":2.0}')
        if max(abs(x), abs(y), abs(z or 0.0)) > 1000.0:
            return _err("x, y, z must be within 1000 m of the origin")
        before = (s.truth().get("nodes") or {}).get(nid)
        s.teleport(nid, x, y, z)
        if before and hub.tracker is not None and math.hypot(x - before["x"], y - before["y"]) > 1.0:
            with contextlib.suppress(Exception):
                hub.tracker.reset(nid)
        return _json({"ok": True, "id": nid, "x": x, "y": y})

    async def sim_truth(request):
        s = sim()
        truth = dict(s.truth())
        truth["t"] = round(time.time(), 3)
        truth["note"] = "ground truth is for error metrics in results/, never for a demo UI"
        if hub.is_mesh:
            with contextlib.suppress(Exception):
                from .solver import canonical_frame
                ids = sorted(truth["nodes"])
                truth["nodes_rel"] = canonical_frame(truth["nodes"], ids, hub.hello["dims"])
        return _json(truth)

    # ---- record
    async def post_record(request):
        body = await _body(request)
        cmd = body.get("cmd")
        if cmd == "start":
            path = str(body.get("path") or time.strftime("session-%Y%m%d-%H%M%S.ndjson"))
            try:
                full = hub._start_record(path)
            except (ValueError, OSError) as e:
                return _err(str(e))
            return _json({"ok": True, "recording": True, "path": full})
        if cmd == "stop":
            info = hub._stop_record()
            return _json({"ok": True, "recording": False, **info})
        if cmd == "status":
            return _json({"ok": True, "recording": hub._rec is not None, "path": hub._rec_path,
                          "messages": hub._rec_count})
        return _err("cmd must be start | stop | status")

    # ---- static
    async def index(request):
        f = STATIC_DIR / "index.html"
        if f.exists():
            return web.FileResponse(f, headers={"Cache-Control": "no-cache"})
        h = hub.hello
        badge = '<b style="background:#f5a524;color:#111;padding:2px 8px;border-radius:4px">SIM</b> ' \
            if h["simulated"] else ""
        return web.Response(content_type="text/html", text=(
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>uwb-kit hub</title><body style='font:16px system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem'>"
            f"<h1>uwb-kit hub {badge}</h1><p>source <code>{h['source']}</code>, profile <code>{h['profile']}</code>.</p>"
            "<p>Stream: <code>/stream</code> (WebSocket), <code>/events</code> (SSE). Snapshots: "
            "<a href=/state>/state</a>, <a href=/diag>/diag</a>, <a href=/healthz>/healthz</a>, "
            "<a href=/sim/scenarios>/sim/scenarios</a>.</p><p>The web console is not installed in this build.</p>"))

    async def client_js(request):
        f = STATIC_DIR / "uwbkit-client.js"
        if not f.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Cache-Control": "no-cache", **CORS_HEADERS})

    r = app.router
    r.add_get("/stream", stream)
    r.add_get("/events", events)
    r.add_get("/state", get_state)
    r.add_get("/healthz", healthz)
    r.add_get("/diag", get_diag)
    r.add_get("/room", get_room)
    r.add_post("/anchors", post_anchors)
    r.add_post("/calibrate", post_calibrate)
    r.add_post("/bias", post_bias)
    r.add_post("/command", post_command)
    r.add_get("/sim/scenarios", sim_scenarios)
    r.add_post("/sim/scenario", sim_scenario)
    r.add_post("/sim/control", sim_control)
    r.add_post("/sim/teleport", sim_teleport)
    r.add_get("/sim/truth", sim_truth)
    r.add_post("/record", post_record)
    r.add_get("/", index)
    r.add_get("/uwbkit-client.js", client_js)
    if STATIC_DIR.exists():
        r.add_static("/static/", STATIC_DIR, show_index=False)

    async def on_startup(app):
        await hub.start()

    async def on_cleanup(app):
        await hub.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


async def serve(hub: Hub, host: str = "127.0.0.1", port: int = 1800) -> web.AppRunner:
    """Start the hub's HTTP server; returns the runner (call ``await runner.cleanup()`` to stop)."""
    runner = web.AppRunner(make_app(hub), access_log=None, handle_signals=False)
    await runner.setup()
    try:
        for h in [x.strip() for x in str(host).split(",") if x.strip()] or ["127.0.0.1"]:
            await web.TCPSite(runner, h, port).start()   # several hosts: "127.0.0.1,<tailnet ip>"
    except BaseException:
        await runner.cleanup()
        raise
    return runner
