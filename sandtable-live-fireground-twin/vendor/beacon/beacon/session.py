"""BeaconSession: the state behind the Beacon feed (docs/BEACON_FEED.md sections 2, 3 and 7; CONTRACTS 3.3).

    s = BeaconSession(twin_dict, site_dict, labels=None, emit=None)
    s.set_pucks([{"id": "P1", "x": 7.9, "y": 6.7}], room="r310", floor="f3")
    s.ingest(obs)             # one observation (CONTRACTS section 2); validated, queued, used at the next step
    s.step(now) -> dict       # locator + tracker; returns the Beacons object (and emits it when it changed)
    s.beacons() -> dict       # the latest Beacons object
    s.brief() -> dict         # discrete summary for agent prompts (also: brief_from_beacons(beacons))
    s.reset()

What it does:
  - pucks: positions are an input (Scout's self-location, the operator's click, or the simulator). Pucks heard by a
    phone before they are placed (live mode: by Bluetooth accessory name) are listed in `unplaced_pucks` and not used.
  - one tracker (`beacon.track.PhoneTrack`) per opt-in phone. A phone's rotating ephemeral id (eid) is linked to the
    same phone through the report's `prev_eid`, so the public id (B1, B2, ...) survives a rotation.
  - cold aggregation: per puck, the latest cold scan in the window becomes one `presence[]` entry (see COUNT_RANGE_RULE).
  - stale and retention: a phone not heard for STALE_S keeps its last geometry with a growing sigma; it is dropped
    (everything about it forgotten) after RETENTION_S. A `bye` observation drops it at once.
  - `safe_outside`: the phone is published with geometry "none" and counted as accounted for.
  - honesty: x, y, sigma_m, cov, geo, node are null unless geometry == "fix", whatever the locator returned.
  - texts: built only from whole or half metres, wall names (west/east/north/south, from the room polygon and the
    twin's georeference) and room names. The occupant's typed text never goes into `text` (prompt-safe).

Threading: `ingest` only validates and appends to a queue (safe from any thread); `step`, `set_pucks`, `reset` and
`set_rig_scale` take one lock. The server runs `step` in a worker thread; `emit` must be thread-safe.

If `beacon.locate`/`beacon.track` cannot be imported, a crude built-in stub locator is used and the Beacons object
says so in `limits` (the server prefers its SAMPLE engine in that case; the stub exists for tests and for `live`).
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from . import config
from .twin import Twin, _pip

log = logging.getLogger("beacon.session")

SCHEMA = "sandtable.beacons/1"
LINK_STALE_S = 5.0          # a range or RSSI older than this is not "hearing" the phone (ack, presence of a link)
BEARING_FRESH_S = 3.0
STATUSES = ("trapped", "injured", "sheltering", "ok", "unknown")
KINDS = ("cold", "sos", "uwb", "bearing", "report", "bye", "host", "uwb_puck")
NO_EID_KINDS = ("cold", "host", "uwb_puck")
HOST_SILENT_S = 20.0        # a puck host silent this long is dropped from its puck (BEACON_FEED A7)
PUCK_UWB_ATTRIB_S = 30.0    # a puck-side range goes to the phone that last ranged that puck within this many seconds
EID_RE = re.compile(r"^[0-9a-fA-F]{8}$")
REPORT_FIELDS = ("status", "text", "people", "can_move", "battery_pct", "floor_reported", "baro_hpa", "still", "still_s",
                 "platform", "app", "home_suite", "safe_outside", "model", "chip", "network")

COUNT_RANGE_RULE = (
    "Cold count band: count = devices in the near and mid bands of one puck (signal at least -75 dBm: a few metres in line of "
    "sight; attributed to the puck's room); "
    "lower bound = half the count rounded down, but never fewer than the opt-in phones the feed places in that room "
    "(they advertise too); upper bound = the count plus half the far-band devices rounded up (some far devices are in "
    "the room behind furniture or bodies). Over-count causes: address rotation and several advertising sets per device. "
    "Under-count causes: idle phones that do not advertise, bodies. Entries from several pucks in one room hear the same "
    "devices: do not add them up.")

LIMITS_BASE = [
    "A phone with no app cannot be ranged or identified: cold presence is a band of advertising devices per puck, never a "
    "position or an identity. Devices are not people: watches, earbuds and laptops advertise too, and some phones do not.",
    COUNT_RANGE_RULE,
    "A ring stays a ring: x and y are published only when the geometry is a fix. One puck gives a ring, two pucks two "
    "mirror-image candidates, three pucks not in a line (or one puck plus the phone's own bearing) one fix.",
    "Bluetooth signal strength gives metres, not centimetres: a Bluetooth-only phone is a band or a room, never a precise point.",
    "The twin prunes positions (outside the building, inside a wall, the wrong room for a line-of-sight link). This twin is a "
    "synthetic interior fitted to the real footprint, so pruning is only as good as its invented walls.",
    "Floors come from the occupant's own report when given; otherwise the pucks' floor is assumed.",
]

PRIVACY_TEXT = (
    "Only phones whose owners installed the app and started sharing are identified, by a rotating id that is not a name, a "
    "number or a hardware address. Devices with no app are counted per signal-strength band and never identified or "
    "stored. A phone that stops sharing is forgotten at once; everything else is deleted {m:g} minutes after the phone was "
    "last heard.")


# ============================================================================ small helpers
def _finite(v, name: str, lo: float | None = None, hi: float | None = None, open_lo: bool = False) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(f):
        raise ValueError(f"{name} must be finite")
    if lo is not None and (f <= lo if open_lo else f < lo):
        raise ValueError(f"{name} out of range ({f})")
    if hi is not None and f > hi:
        raise ValueError(f"{name} out of range ({f})")
    return f


def half(d: float | None) -> float | None:
    """Round to the nearest half metre."""
    if d is None:
        return None
    return round(float(d) * 2.0) / 2.0


def half_text(d: float) -> str:
    v = half(d)
    if v < 0.5:
        return "less than 0.5 m"
    return f"{v:g} m"


def _round(v, nd=2):
    return None if v is None else round(float(v), nd)


def _people(n) -> str:
    if n is None:
        return ""
    n = int(n)
    return "1 person" if n == 1 else f"{n} people"


def _ccw(poly) -> bool:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) > 0


def _seg_dist(p, a, b) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / den))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


# ============================================================================ the stub locator (fallback only)
class _StubContext:
    def __init__(self, twin: Twin, floor: str | None = None):
        self.twin = twin
        self.floor = floor or twin.exercise_floor


class _StubTrack:
    """Minimal tracker used only when beacon.track is missing: medians/means of the last samples, no hysteresis."""

    def __init__(self, eid: str, georef_bearing_deg: float | None = None):
        self.eid = eid
        self.georef_bearing_deg = georef_bearing_deg
        self._uwb: dict[str, deque] = {}
        self._ble: dict[str, deque] = {}
        self._bearing: dict | None = None
        self.report: dict = {}
        self.last_seen: float | None = None
        self.moving = False

    def add(self, obs: dict) -> None:
        t = float(obs.get("t") or time.time())
        self.last_seen = max(self.last_seen or t, t)
        k = obs["kind"]
        if k == "uwb":
            self._uwb.setdefault(obs["puck"], deque(maxlen=7)).append((t, float(obs["d_m"]), obs.get("los")))
        elif k == "sos":
            self._ble.setdefault(obs["puck"], deque(maxlen=7)).append((t, float(obs["rssi"]), obs.get("tx_dbm")))
            if obs.get("report"):
                self.report.update(obs["report"])
        elif k == "bearing":
            self._bearing = dict(obs)
        elif k == "report":
            self.report.update(obs.get("report") or {})

    def inputs(self, now: float):
        ranges = []
        for pid, dq in self._uwb.items():
            fresh = [s for s in dq if now - s[0] <= LINK_STALE_S]
            if fresh:
                ds = sorted(s[1] for s in fresh)
                los = [s[2] for s in fresh if s[2] is not None]
                ranges.append({"puck": pid, "kind": "uwb", "d_m": ds[len(ds) // 2], "los": (sum(los) / len(los)) if los else None, "n": len(fresh)})
        have = {r["puck"] for r in ranges}
        for pid, dq in self._ble.items():
            fresh = [s for s in dq if now - s[0] <= LINK_STALE_S]
            if fresh and pid not in have:
                ranges.append({"puck": pid, "kind": "ble", "rssi": sum(s[1] for s in fresh) / len(fresh), "tx_dbm": fresh[-1][2], "n": len(fresh)})
        bearing = None
        b = self._bearing
        if b and now - float(b.get("t") or 0) <= BEARING_FRESH_S:
            g = 90.0 if self.georef_bearing_deg is None else self.georef_bearing_deg
            bearing = {"puck": b["puck"], "from_puck_deg": (g - float(b["to_puck_compass_deg"]) + 180.0) % 360.0,
                       "sigma_deg": b.get("sigma_deg") or 25.0, "source": b.get("source")}
        return ranges, bearing

    def estimate(self, ctx, pucks: list[dict], now: float, noise: dict | None = None) -> dict:
        ranges, bearing = self.inputs(now)
        return _stub_estimate(ctx, pucks, ranges, bearing, noise)


def _stub_estimate(ctx, pucks, ranges, bearing, noise) -> dict:
    """Crude geometry: ring from one UWB range, circle intersections from two, least squares from three or more,
    room-level zone from Bluetooth only. No twin pruning except the floor outline. Labelled as the stub in `debug`."""
    tw: Twin = ctx.twin
    pk = {p["id"]: p for p in pucks}
    out = {"method": "ble-presence", "geometry": "none", "x": None, "y": None, "sigma_m": None, "cov": None, "confidence": None,
           "room": None, "room_label": None, "room_prob": None, "node": None, "ring": None, "candidates": [], "pruned": [],
           "zone": None, "hpd_area_m2": None, "debug": {"stub": True}}
    uwb = [r for r in ranges if r["kind"] == "uwb" and r["puck"] in pk]
    ble = [r for r in ranges if r["kind"] == "ble" and r["puck"] in pk]
    outline = tw.floor(ctx.floor)["outline"]

    def fix(x, y, sig, method):
        loc = tw.locate(x, y, ctx.floor)
        out.update({"method": method, "geometry": "fix", "x": round(x, 2), "y": round(y, 2), "sigma_m": round(sig, 2),
                    "cov": [round(sig * sig, 4), 0.0, round(sig * sig, 4)], "confidence": 0.68, "room": loc["room"],
                    "room_label": loc["room_label"], "room_prob": 0.8, "node": loc["node"], "hpd_area_m2": round(math.pi * (2.15 * sig) ** 2, 2)})
        return out

    if len(uwb) >= 3:
        x = sum(pk[r["puck"]]["x"] for r in uwb) / len(uwb)
        y = sum(pk[r["puck"]]["y"] for r in uwb) / len(uwb) + 0.5
        for _ in range(30):
            jtj = [[0.0, 0.0], [0.0, 0.0]]
            jtr = [0.0, 0.0]
            for r in uwb:
                p = pk[r["puck"]]
                dx, dy = x - p["x"], y - p["y"]
                d = math.hypot(dx, dy) or 1e-6
                res = d - r["d_m"]
                jx, jy = dx / d, dy / d
                jtj[0][0] += jx * jx; jtj[0][1] += jx * jy; jtj[1][1] += jy * jy
                jtr[0] += jx * res; jtr[1] += jy * res
            jtj[1][0] = jtj[0][1]
            det = jtj[0][0] * jtj[1][1] - jtj[0][1] * jtj[1][0]
            if abs(det) < 1e-9:
                break
            sx = (jtj[1][1] * jtr[0] - jtj[0][1] * jtr[1]) / det
            sy = (-jtj[1][0] * jtr[0] + jtj[0][0] * jtr[1]) / det
            x, y = x - sx, y - sy
            if math.hypot(sx, sy) < 1e-4:
                break
        rms = math.sqrt(sum((math.hypot(x - pk[r["puck"]]["x"], y - pk[r["puck"]]["y"]) - r["d_m"]) ** 2 for r in uwb) / len(uwb))
        return fix(x, y, max(0.2, rms), "uwb-twr")
    if len(uwb) == 1 and bearing and bearing.get("puck") == uwb[0]["puck"] and bearing.get("from_puck_deg") is not None:
        p, r = pk[uwb[0]["puck"]], uwb[0]["d_m"]
        a = math.radians(float(bearing["from_puck_deg"]))
        return fix(p["x"] + r * math.cos(a), p["y"] + r * math.sin(a), max(0.3, r * math.sin(math.radians(float(bearing.get("sigma_deg") or 25)))), "uwb-twr+bearing")
    if len(uwb) == 2:
        p1, p2 = pk[uwb[0]["puck"]], pk[uwb[1]["puck"]]
        r1, r2 = uwb[0]["d_m"], uwb[1]["d_m"]
        d = math.hypot(p2["x"] - p1["x"], p2["y"] - p1["y"])
        if 1e-6 < d < r1 + r2 and d > abs(r1 - r2):
            a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
            h = math.sqrt(max(0.0, r1 * r1 - a * a))
            mx, my = p1["x"] + a * (p2["x"] - p1["x"]) / d, p1["y"] + a * (p2["y"] - p1["y"]) / d
            pts = [(mx + h * (p2["y"] - p1["y"]) / d, my - h * (p2["x"] - p1["x"]) / d),
                   (mx - h * (p2["y"] - p1["y"]) / d, my + h * (p2["x"] - p1["x"]) / d)]
            keep = [q for q in pts if _pip(q, outline)]
            out["pruned"] = [{"x": round(q[0], 2), "y": round(q[1], 2), "reason": "outside the building outline"} for q in pts if not _pip(q, outline)]
            if len(keep) == 1 or (len(keep) == 2 and math.dist(*keep) < 0.6):
                q = keep[0] if len(keep) == 1 else ((keep[0][0] + keep[1][0]) / 2, (keep[0][1] + keep[1][1]) / 2)
                return fix(q[0], q[1], 0.4, "uwb-twr")
            if len(keep) == 2:
                cands = []
                for q in keep:
                    loc = tw.locate(q[0], q[1], ctx.floor)
                    cands.append({"x": round(q[0], 2), "y": round(q[1], 2), "sigma_m": 0.4, "weight": 0.5, "room": loc["room"], "room_label": loc["room_label"]})
                out.update({"method": "uwb-twr", "geometry": "candidates", "candidates": cands, "confidence": 0.6, "hpd_area_m2": round(2 * math.pi * 0.86 ** 2, 2)})
                if cands[0]["room"] == cands[1]["room"]:
                    out.update({"room": cands[0]["room"], "room_label": cands[0]["room_label"], "room_prob": 0.8})
                return out
        uwb = [min(uwb, key=lambda r: r["d_m"])]
    if len(uwb) == 1:
        p, r = pk[uwb[0]["puck"]], uwb[0]["d_m"]
        out.update({"method": "uwb-twr", "geometry": "ring", "confidence": 0.68,
                    "ring": {"puck": p["id"], "cx": p["x"], "cy": p["y"], "r_m": round(r, 2), "sigma_m": 0.15, "arcs": [[0.0, 360.0]], "kind": "uwb"},
                    "hpd_area_m2": round(2 * math.pi * r * 0.5, 2)})
        return out
    if ble:
        best = max(ble, key=lambda r: r["rssi"])
        p = pk[best["puck"]]
        d, lo, hi = _ble_distance(best["rssi"], best.get("tx_dbm"), noise)
        room = p.get("room") or tw.locate(p["x"], p["y"], ctx.floor)["room"]
        band = "near" if d < 2 else "mid" if d < 6 else "far"
        prob = 0.7 if band != "far" else 0.4
        rr = tw.room(room) if room else None
        out.update({"method": "ble-rssi", "geometry": "zone", "confidence": prob, "room": room, "room_label": rr["label"] if rr else None,
                    "room_prob": prob,
                    "zone": {"room": room, "room_label": rr["label"] if rr else None, "prob": prob, "band": band, "puck": p["id"],
                             "r_min_m": round(lo, 1), "r_max_m": round(hi, 1), "polygon": rr["polygon"] if rr else None}})
        return out
    return out


def _ble_distance(rssi: float, tx: float | None, noise: dict | None) -> tuple[float, float, float]:
    try:
        from . import models
        return models.ble_distance(rssi, noise, tx_dbm=tx)
    except Exception:
        tx = -62.0 if tx is None else float(tx)
        d = 10 ** ((tx - rssi) / 25.0)
        return d, d * 10 ** (-8.4 / 25.0), d * 10 ** (8.4 / 25.0)


def _load_locator(choice: str):
    """(locate module or None, PhoneTrack class, note or None)."""
    if choice == "stub":
        return None, _StubTrack, "Locator: the crude built-in stub was chosen (tests)."
    try:
        from . import locate, track
        return locate, track.PhoneTrack, None
    except Exception as exc:
        note = (f"Locator NOT available ({type(exc).__name__}: {exc}): a crude built-in stub is in use "
                "(no twin pruning, no hysteresis). Positions from it are rough.")
        log.error(note)
        return None, _StubTrack, note


def _mid_reach_m(noise) -> float:
    """Line-of-sight distance at which a nominal phone drops out of the mid band (models.COLD_BANDS_DBM), half metres."""
    try:
        from . import models
        d = models.ble_distance(models.COLD_BANDS_DBM["mid"], noise)[0]
    except Exception:
        d = 6.0
    return max(0.5, round(d * 2) / 2)


def _noise():
    try:
        from . import models
        return models.DEFAULT_NOISE
    except Exception:
        return None


# ============================================================================ per-phone state
class _Phone:
    def __init__(self, pid: str, eid: str, track, now: float):
        self.id = pid
        self.eid = eid
        self.eids: list[str] = [eid]
        self.track = track
        self.first_seen = now
        self.last_seen = now
        self.updates = 0
        self.report: dict = {}
        self.report_t: float | None = None
        self.links: dict[tuple[str, str], dict] = {}       # (puck, kind) -> latest obs
        self.bearing: dict | None = None
        self.injected = False
        self.device: dict = {}
        self.last_spatial: dict | None = None
        self.last_spatial_t: float | None = None
        self.published = False
        self.was_fix = False
        self.status: str | None = None
        self.safe: bool = False
        self.stale = False
        self.last_xy: tuple[float, float, float] | None = None
        self.speed = 0.0
        self.err_logged = False
        self.phone_uwb_t: dict[str, float] = {}             # puck -> last UWB range reported by the phone itself


# ============================================================================ the session
class BeaconSession:
    def __init__(self, twin, site: dict | None = None, labels: dict | None = None, emit: Callable[[dict], None] | None = None, *,
                 source: str = "sim", simulated: bool | None = None, rungs: dict | None = None, noise: dict | None = None,
                 stale_s: float | None = None, retention_s: float | None = None, locator: str = "auto",
                 persist_path: str | Path | None = None, rig_scale: float | None = None, use_roster: bool = True):
        self.twin = twin if isinstance(twin, Twin) else Twin(twin)
        self.twin_raw = self.twin.data
        self.site = site if site is not None else {}
        self.labels = labels
        self._emit = emit
        self.source = source
        self.simulated = (source in ("sim", "fake")) if simulated is None else bool(simulated)
        self.rungs = dict(rungs or {"cold": True, "ble": True, "uwb": True, "bearing": True})
        self.noise = noise if noise is not None else _noise()
        self.stale_s = float(config.STALE_S if stale_s is None else stale_s)
        self.retention_s = float(config.RETENTION_S if retention_s is None else retention_s)
        self.rig_scale = float(config.RIG_SCALE if rig_scale is None else rig_scale)
        self.persist_path = Path(persist_path) if persist_path else None
        self._locate, self._Track, self._locator_note = _load_locator(locator)
        self._roster = None
        self._roster_note = None
        if use_roster:
            try:
                from . import roster
                self._roster = roster
            except Exception as exc:
                self._roster_note = f"Building record module not available ({type(exc).__name__}): no accountability table."
                log.warning(self._roster_note)
        self._lock = threading.RLock()
        self._pending: deque = deque()
        self._ctx = None
        self._ctx_floor = None
        self._georef_b = float((self.twin_raw.get("georef") or {}).get("bearing_deg", 90.0))
        self._clear()
        self._load_persisted()
        self._latest = self._build(time.time(), [], [])

    # ---- state
    def _clear(self) -> None:
        self.pucks: list[dict] = []
        self.room: str | None = None
        self.floor: str | None = None
        self._accessories: dict[str, str] = {}
        self._unplaced: dict[str, dict] = {}
        self._phones: dict[str, _Phone] = {}
        self._eid_to_id: dict[str, str] = {}
        self._next_phone = 1
        self._cold: dict[str, dict] = {}
        self._hosts: dict[str, dict] = {}
        self._presence_ids: dict[tuple, str] = {}
        self._acc_key: str | None = None
        self._acc: dict | None = None
        self.seq = 0
        self._emit_key: str | None = None
        self._emit_t = 0.0

    def _persist(self) -> None:
        if not self.persist_path:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"pucks": [{k: p.get(k) for k in ("id", "x", "y", "sigma_m", "accessory", "host", "floor")} for p in self.pucks],
                                       "room": self.room, "floor": self.floor, "accessories": self._accessories,
                                       "rig": {"scale": self.rig_scale}}, indent=1), encoding="utf-8")
            tmp.replace(self.persist_path)
        except OSError as exc:
            log.warning("could not save the puck table: %s", exc)

    def _load_persisted(self) -> None:
        if not self.persist_path or not self.persist_path.is_file():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            self.rig_scale = float((data.get("rig") or {}).get("scale") or self.rig_scale)
            if data.get("pucks"):
                self._set_pucks_locked(data["pucks"], data.get("room"), data.get("floor"), False, now=time.time(), quiet=True)
            for acc, pid in (data.get("accessories") or {}).items():
                self._accessories[str(acc)] = str(pid)
            log.info("restored %d pucks from %s", len(self.pucks), self.persist_path.name)
        except Exception as exc:
            log.warning("could not restore the puck table: %s", exc)

    def emit(self, msg: dict) -> None:
        if self._emit is not None:
            try:
                self._emit(msg)
            except Exception:
                log.exception("emit")

    def _event(self, name: str, pid: str | None, text: str, now: float | None = None) -> None:
        self.emit({"type": "beacon_event", "t": round(now or time.time(), 3), "name": name, "id": pid, "text": text,
                   **({"simulated": True} if self.simulated else {})})

    # ---- pucks
    def set_pucks(self, pucks: list[dict], room: str | None = None, floor: str | None = None, merge: bool = False) -> dict:
        with self._lock:
            res = self._set_pucks_locked(pucks, room, floor, merge, now=time.time())
            self._persist()
            return res

    def _set_pucks_locked(self, pucks, room, floor, merge, now, quiet=False) -> dict:
        if not isinstance(pucks, list):
            raise ValueError("pucks must be a list")
        clean = []
        for p in pucks:
            if not isinstance(p, dict):
                raise ValueError("each puck must be an object")
            pid = p.get("id")
            if not isinstance(pid, str) or not re.match(r"^[A-Za-z0-9_-]{1,16}$", pid):
                raise ValueError(f"bad puck id {pid!r}")
            x = _finite(p.get("x"), f"{pid}.x", -1000, 1000)
            y = _finite(p.get("y"), f"{pid}.y", -1000, 1000)
            sig = _finite(p.get("sigma_m", 0.08) if p.get("sigma_m") is not None else 0.08, f"{pid}.sigma_m", 0, 50)
            acc = p.get("accessory")
            if acc is not None and (not isinstance(acc, str) or not acc or len(acc) > 80):
                raise ValueError(f"bad accessory name for {pid}")
            host = p.get("host")
            if host is not None and host not in ("uno-q", "battery", "android", "laptop"):
                host = str(host)[:16]
            clean.append({"id": pid, "x": x, "y": y, "sigma_m": sig, "accessory": acc, "host": host, "floor": p.get("floor"),
                          "radios": p.get("radios") if isinstance(p.get("radios"), list) else None})
        if room is not None:
            self.twin.room(room)
        if floor is not None:
            self.twin.floor(floor)
        table = {p["id"]: p for p in self.pucks} if merge else {}
        for p in clean:
            old = table.get(p["id"]) or {}
            fl = p["floor"] or floor or old.get("floor") or self.floor or (self.twin.room(room)["floor"] if room else self.twin.exercise_floor)
            loc = self.twin.locate(p["x"], p["y"], fl)
            lat, lon = self.twin.to_geo(p["x"], p["y"])
            acc = p["accessory"] if p["accessory"] is not None else old.get("accessory")
            table[p["id"]] = {"id": p["id"], "x": round(p["x"], 3), "y": round(p["y"], 3), "floor": fl, "sigma_m": round(p["sigma_m"], 3),
                              "room": loc["room"], "radios": p["radios"] or old.get("radios") or ["ble", "uwb"],
                              "geo": {"lat": round(lat, 7), "lon": round(lon, 7)}, "accessory": acc,
                              "host": p["host"] if p["host"] is not None else old.get("host"), "simulated": self.simulated}
            if acc:
                for a, i in list(self._accessories.items()):
                    if i == p["id"] and a != acc:
                        del self._accessories[a]
                self._accessories[acc] = p["id"]
                self._unplaced.pop(acc, None)
        self.pucks = sorted(table.values(), key=lambda q: q["id"])
        if room is None and self.pucks and (not merge or self.room is None):
            rooms = [q["room"] for q in self.pucks if q["room"]]
            room = max(set(rooms), key=rooms.count) if rooms else None
        if room is not None or not merge:
            self.room = room
        self.floor = floor or (self.twin.room(self.room)["floor"] if self.room else (self.pucks[0]["floor"] if self.pucks else None))
        if not quiet:
            label = self.twin.room(self.room)["label"] if self.room else "no room"
            ids = ", ".join(q["id"] for q in self.pucks) or "none"
            self._event("pucks_changed", None, f"Pucks: {ids} ({label})", now)
        return {"pucks": copy.deepcopy(self.pucks), "room": self.room, "floor": self.floor}

    def set_rig_scale(self, scale: float) -> None:
        s = _finite(scale, "scale", 0.0, 100.0, open_lo=True)
        with self._lock:
            self.rig_scale = s
            self._persist()

    # ---- observations
    def ingest(self, obs: dict, origin: str | None = None) -> dict:
        """Validate one observation (CONTRACTS section 2, plus `bye`) and queue it for the next step. ValueError when bad."""
        o = self.validate(obs)
        self._pending.append((o, origin))
        return o

    def validate(self, obs: Any, now: float | None = None) -> dict:
        if not isinstance(obs, dict):
            raise ValueError("an observation must be an object")
        kind = obs.get("kind")
        if kind not in KINDS:
            raise ValueError(f"unknown observation kind {kind!r}")
        now = time.time() if now is None else now
        o: dict = {"kind": kind}
        t = obs.get("t")
        o["t"] = now if t is None else _finite(t, "t")
        if kind not in NO_EID_KINDS:
            eid = obs.get("eid")
            if not isinstance(eid, str) or not EID_RE.match(eid):
                raise ValueError(f"eid must be 8 hex digits, got {eid!r}")
            o["eid"] = eid.lower()
        if kind in ("cold", "sos", "uwb", "bearing", "host", "uwb_puck"):
            pid, acc = obs.get("puck"), obs.get("accessory")
            if pid is None and acc is None:
                raise ValueError(f"a {kind} observation needs puck or accessory")
            if pid is not None:
                if not isinstance(pid, str) or not re.match(r"^[A-Za-z0-9_-]{1,16}$", pid):
                    raise ValueError(f"bad puck id {pid!r}")
                o["puck"] = pid
            if acc is not None:
                if not isinstance(acc, str) or not acc or len(acc) > 80:
                    raise ValueError("accessory must be a non-empty string of at most 80 characters")
                o["accessory"] = acc
        if kind == "cold":
            o["window_s"] = _finite(obs.get("window_s", 10.0), "window_s", 0, 3600, open_lo=True)
            for b in ("near", "mid", "far"):
                v = obs.get(b, 0)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or v > 100000:
                    raise ValueError(f"{b} must be a count")
                o[b] = int(v)
        elif kind == "host":
            h = obs.get("host")
            if not isinstance(h, str) or not h or len(h) > 24:
                raise ValueError("host must be a short name")
            o["host"] = h
            if obs.get("uptime_s") is not None:
                o["uptime_s"] = _finite(obs["uptime_s"], "uptime_s", 0.0, 1e9)
            for k in ("rtt", "cold_scan"):
                if obs.get(k) is not None:
                    o[k] = bool(obs[k])
            if obs.get("version") is not None:
                o["version"] = str(obs["version"])[:16]
        elif kind in ("uwb", "uwb_puck"):
            o["d_m"] = _finite(obs.get("d_m"), "d_m", 0.0, 150.0, open_lo=True)
            if o["d_m"] >= 150.0:
                raise ValueError("d_m out of range")
            for k, lo, hi in (("rssi", -150.0, 20.0), ("fp", -150.0, 20.0), ("los", 0.0, 1.0)):
                if obs.get(k) is not None:
                    o[k] = _finite(obs[k], k, lo, hi)
        elif kind == "sos":
            o["rssi"] = _finite(obs.get("rssi"), "rssi", -130.0, 10.0)
            if obs.get("tx_dbm") is not None:
                o["tx_dbm"] = _finite(obs["tx_dbm"], "tx_dbm", -120.0, 20.0)
            if obs.get("report") is not None:
                o["report"] = self._clean_report(obs["report"])
        elif kind == "bearing":
            o["to_puck_compass_deg"] = _finite(obs.get("to_puck_compass_deg"), "to_puck_compass_deg", -720.0, 720.0) % 360.0
            o["sigma_deg"] = _finite(obs.get("sigma_deg", 25.0), "sigma_deg", 0.0, 180.0, open_lo=True)
            src = obs.get("source", "phone-aoa+compass")
            o["source"] = str(src)[:40]
        elif kind == "report":
            prev = obs.get("prev_eid")
            if prev is not None:
                if not isinstance(prev, str) or not EID_RE.match(prev):
                    raise ValueError("prev_eid must be 8 hex digits or null")
                o["prev_eid"] = prev.lower()
            else:
                o["prev_eid"] = None
            o["report"] = self._clean_report(obs.get("report") or {})
        return o

    @staticmethod
    def _clean_report(rep: Any) -> dict:
        if not isinstance(rep, dict):
            raise ValueError("report must be an object")
        out: dict = {}
        for k in REPORT_FIELDS:
            if k not in rep:
                continue
            v = rep[k]
            if v is None:
                out[k] = None
                continue
            if k == "status":
                out[k] = v if v in STATUSES else "unknown"
            elif k == "text":
                out[k] = str(v)[:120]
            elif k in ("people", "battery_pct", "floor_reported"):
                f = _finite(v, k, -1000, 100000)
                out[k] = int(round(f))
                if k == "battery_pct":
                    out[k] = max(0, min(100, out[k]))
                if k == "people":
                    out[k] = max(0, min(999, out[k]))
            elif k in ("baro_hpa", "still_s"):
                out[k] = round(_finite(v, k, 0, 100000), 2)
            elif k in ("can_move", "still", "safe_outside"):
                out[k] = bool(v)
            else:
                out[k] = str(v)[:40]
        return out

    def ingest_batch(self, body: Any, origin: str = "http", now: float | None = None) -> dict:
        """POST /api/ingest: {"device": {...}, "obs": [...]}, a list of observations, or one observation.
        All-or-nothing: one bad observation rejects the batch with ValueError. Times more than 120 s from the server
        clock are replaced by the server's receive time (a phone clock that is off must not make every link stale)."""
        now = time.time() if now is None else now
        device: dict = {}
        if isinstance(body, dict) and "obs" in body:
            device = body.get("device") or {}
            if not isinstance(device, dict):
                raise ValueError("device must be an object")
            items = body["obs"]
        elif isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            items = [body]
        else:
            raise ValueError("send an observation, a list of observations, or {device, obs}")
        if not isinstance(items, list):
            raise ValueError("obs must be a list")
        if len(items) > 500:
            raise ValueError("at most 500 observations per batch")
        dev_eid = device.get("eid")
        if dev_eid is not None and (not isinstance(dev_eid, str) or not EID_RE.match(dev_eid)):
            raise ValueError("device.eid must be 8 hex digits")
        dev_info = {}
        for k in ("platform", "model", "chip", "app", "network"):
            if device.get(k) is not None:
                dev_info[k] = str(device[k])[:40]
        clean = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"obs[{i}]: an observation must be an object")
            it = dict(item)
            if dev_eid and it.get("kind") not in NO_EID_KINDS and "eid" not in it:
                it["eid"] = dev_eid
            try:
                o = self.validate(it, now)
            except ValueError as exc:
                raise ValueError(f"obs[{i}]: {exc}")
            if abs(o["t"] - now) > 120.0:
                o["t"] = now
            if dev_info and o["kind"] == "report":
                o["report"] = {**dev_info, **o["report"]}
            clean.append(o)
        unknown = []
        for o in clean:
            acc = o.get("accessory")
            if acc and o.get("puck") is None and acc not in self._accessories and acc not in unknown:
                unknown.append(acc)
            self._pending.append((o, origin))
        if dev_info and dev_eid:
            self._pending.append(({"kind": "_device", "eid": dev_eid.lower(), "t": now, "device": dev_info}, origin))
        eid = (dev_eid or next((o.get("eid") for o in clean if o.get("eid")), None))
        phone = self.phone_by_eid(eid.lower()) if eid else None
        return {"ok": True, "accepted": len(clean), "unknown_accessories": unknown, "ack": self.ack(phone)}

    # ---- lookups
    def phone_by_eid(self, eid: str | None) -> dict | None:
        if not eid:
            return None
        pid = self._eid_to_id.get(eid.lower())
        if pid is None:
            return None
        for p in self._latest.get("phones") or []:
            if p["id"] == pid:
                return p
        return None

    @staticmethod
    def ack(phone: dict | None) -> dict:
        return make_ack(phone)

    def beacons(self) -> dict:
        return self._latest

    def brief(self) -> dict:
        return brief_from_beacons(self._latest)

    # ---- reset
    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
            self._clear()
            self._persist()
            self._latest = self._build(time.time(), [], [])
        self.emit({"type": "reset"})
        self._event("reset", None, "Beacon reset: phones, presence and pucks cleared")
        self.emit(self._latest)

    # ---- step
    def _context(self):
        floor = self.floor or self.twin.exercise_floor
        if self._ctx is None or self._ctx_floor != floor:
            if self._locate is not None:
                self._ctx = self._locate.Context(self.twin, floor=floor)
            else:
                self._ctx = _StubContext(self.twin, floor)
            self._ctx_floor = floor
        return self._ctx

    def _puck_of(self, o: dict, now: float) -> str | None:
        pid = o.get("puck")
        if pid is not None:
            return pid
        acc = o.get("accessory")
        if acc is None:
            return None
        if acc in self._accessories:
            return self._accessories[acc]
        u = self._unplaced.setdefault(acc, {"accessory": acc, "first_seen": now, "last_seen": now, "eids": set()})
        u["last_seen"] = max(u["last_seen"], o["t"])
        if o.get("eid"):
            u["eids"].add(o["eid"])
        return None

    def _new_phone(self, eid: str, now: float) -> _Phone:
        pid = f"B{self._next_phone}"
        self._next_phone += 1
        ph = _Phone(pid, eid, self._Track(eid, georef_bearing_deg=self._georef_b), now)
        self._phones[pid] = ph
        self._eid_to_id[eid] = pid
        return ph

    def _drop(self, ph: _Phone, text: str, now: float) -> None:
        self._phones.pop(ph.id, None)
        for e in ph.eids:
            if self._eid_to_id.get(e) == ph.id:
                del self._eid_to_id[e]
        for u in self._unplaced.values():
            u["eids"] -= set(ph.eids)
        if ph.published:
            self._event("phone_lost", ph.id, text, now)

    def _drain(self, now: float) -> None:
        while self._pending:
            o, origin = self._pending.popleft()
            kind = o["kind"]
            if kind == "cold":
                pid = self._puck_of(o, now)
                if pid is not None:
                    prev = self._cold.get(pid)
                    if prev is None or o["t"] >= prev["t"]:
                        self._cold[pid] = o
                continue
            if kind == "host":
                pid = self._puck_of(o, now)
                if pid is not None:
                    self._hosts[pid] = o
                continue
            if kind == "uwb_puck":
                self._uwb_puck(o, now)
                continue
            eid = o["eid"]
            if kind == "bye":
                pid = self._eid_to_id.get(eid)
                if pid and pid in self._phones:
                    self._drop(self._phones[pid], f"Phone {pid} stopped sharing", now)
                continue
            if kind == "_device":
                pid = self._eid_to_id.get(eid)
                if pid:
                    self._phones[pid].device.update(o["device"])
                continue
            pid = self._eid_to_id.get(eid)
            if kind == "report" and o.get("prev_eid"):
                old = self._eid_to_id.get(o["prev_eid"])
                if old and old in self._phones and old != pid:
                    if pid and pid in self._phones:                    # the new eid was seen first: merge into the old phone
                        new = self._phones.pop(pid)
                        for e in new.eids:
                            self._eid_to_id[e] = old
                            if e not in self._phones[old].eids:
                                self._phones[old].eids.append(e)
                    self._eid_to_id[eid] = old
                    ph_old = self._phones[old]
                    if eid not in ph_old.eids:
                        ph_old.eids.append(eid)
                    ph_old.eid = eid
                    if hasattr(ph_old.track, "eid"):
                        try:
                            ph_old.track.eid = eid
                        except Exception:
                            pass
                    pid = old
            ph = self._phones.get(pid) if pid else None
            if ph is None:
                ph = self._new_phone(eid, o["t"])
            if origin == "http" and self.source == "sim":
                ph.injected = True
            to_track = dict(o)
            if kind in ("sos", "uwb", "bearing"):
                puck = self._puck_of(o, now)
                if puck is None:
                    continue                                          # heard by a puck that is not placed yet
                to_track["puck"] = puck
                to_track.pop("accessory", None)
                if kind == "uwb" and self.source == "live" and self.rig_scale != 1.0:
                    to_track["d_m"] = o["d_m"] * self.rig_scale
                ph.links[(puck, kind)] = to_track
                if kind == "uwb":
                    ph.phone_uwb_t[puck] = o["t"]
            if kind == "bearing":
                ph.bearing = to_track
            if kind == "report" or (kind == "sos" and o.get("report")):
                rep = o.get("report") or {}
                ph.report.update(rep)
                if kind == "report" or ph.report_t is None:
                    ph.report_t = o["t"]
            ph.eid = eid
            ph.last_seen = max(ph.last_seen, o["t"])
            ph.updates += 1
            try:
                ph.track.add(to_track)
            except Exception:
                log.exception("tracker rejected an observation for %s", ph.id)

    def _uwb_puck(self, o: dict, now: float) -> None:
        """A range read on the puck side (no phone id): attribute it to the phone that last reported a UWB range to that
        puck within PUCK_UWB_ATTRIB_S; ignored when there is none (BEACON_FEED A7, experimental)."""
        puck = self._puck_of(o, now)
        if puck is None:
            return
        best, best_t = None, None
        for ph in self._phones.values():
            t = ph.phone_uwb_t.get(puck)
            if t is not None and o["t"] - t <= PUCK_UWB_ATTRIB_S and (best_t is None or t > best_t):
                best, best_t = ph, t
        if best is None:
            return
        d = o["d_m"] * (self.rig_scale if self.source == "live" else 1.0)
        obs = {"kind": "uwb", "t": o["t"], "puck": puck, "eid": best.eid, "d_m": d, "reporter": "puck"}
        best.links[(puck, "uwb")] = obs
        best.last_seen = max(best.last_seen, o["t"])
        best.updates += 1
        try:
            best.track.add(obs)
        except Exception:
            log.exception("tracker rejected a puck-side range for %s", best.id)

    def step(self, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        with self._lock:
            self._drain(now)
            ctx = self._context()
            pucks = [{"id": p["id"], "x": p["x"], "y": p["y"], "sigma_m": p["sigma_m"]} for p in self.pucks]
            phones = []
            for ph in list(self._phones.values()):
                age = now - ph.last_seen
                if age > self.retention_s:
                    self._drop(ph, f"Phone {ph.id} dropped: not heard for {self.retention_s / 60:g} minutes", now)
                    continue
                phones.append(self._phone(ph, ctx, pucks, now))
            presence = self._presence(now, phones)
            b = self._build(now, phones, presence)
            self._latest = b
            key = json.dumps({k: v for k, v in b.items() if k not in ("seq", "t")}, sort_keys=True, default=str)
            due = key != self._emit_key or now - self._emit_t >= 2.0
            if due:
                self._emit_key, self._emit_t = key, now
        if due:
            self.emit(b)
        return b

    # ---- one phone
    def _phone(self, ph: _Phone, ctx, pucks: list[dict], now: float) -> dict:
        age = now - ph.last_seen
        rep = self._report(ph, now)
        safe = bool(rep.get("safe_outside"))
        stale = age > self.stale_s
        spatial = None
        if safe:
            spatial = _none_spatial()
        elif stale and ph.last_spatial is not None:
            spatial = _grow(ph.last_spatial, (now - (ph.last_spatial_t or now)) * config.STALE_SIGMA_GROWTH_MPS)
        else:
            if pucks:
                try:
                    spatial = ph.track.estimate(ctx, pucks, now, noise=self.noise)
                except Exception:
                    if not ph.err_logged:
                        log.exception("locator failed for %s", ph.id)
                        ph.err_logged = True
                    spatial = None
            if spatial is None:
                spatial = _none_spatial()
            if spatial.get("geometry") not in (None, "none"):
                ph.last_spatial, ph.last_spatial_t = copy.deepcopy(spatial), now
        spatial = _honest(spatial)
        geom = spatial["geometry"]
        ranges = self._ranges(ph, now)
        method = spatial.get("method") or ("uwb-twr" if any(r["kind"] == "uwb" for r in ranges) else "ble-rssi" if ranges else "ble-presence")
        if geom == "none" and method not in ("ble-presence",) and not ranges:
            method = "ble-presence"
        floor, floor_source = self._floor_of(rep)
        phone = {
            "id": ph.id, "eid": ph.eid, "opt_in": True, "simulated": self.simulated,
            "method": method, "geometry": geom,
            "x": spatial.get("x"), "y": spatial.get("y"), "floor": floor, "floor_source": floor_source,
            "sigma_m": spatial.get("sigma_m"), "cov": spatial.get("cov"), "confidence": _round(spatial.get("confidence")),
            "room": spatial.get("room"), "room_label": spatial.get("room_label"), "room_prob": _round(spatial.get("room_prob")),
            "node": spatial.get("node"), "geo": None,
            "ring": spatial.get("ring"), "candidates": spatial.get("candidates") or [], "pruned": spatial.get("pruned") or [],
            "zone": spatial.get("zone"),
            "ranges": spatial["ranges"] if isinstance(spatial.get("ranges"), list) else ranges,
            "bearing": spatial["bearing"] if "bearing" in spatial else self._bearing(ph, now),
            "report": rep, "track": self._track_info(ph, spatial, now),
            "stale": stale, "hpd_area_m2": spatial.get("hpd_area_m2"),
        }
        for r in phone["ranges"]:
            lk = ph.links.get((r.get("puck"), "uwb"))
            if r.get("kind") == "uwb" and lk is not None and lk.get("reporter") == "puck":
                r["reporter"] = "puck"
        if ph.injected:
            phone["injected"] = True
        if safe:
            phone.update({"room": None, "room_label": None, "room_prob": None, "confidence": None})
        self._add_geo(phone)
        phone["text"] = phone_text(phone, self.twin, self._georef_b, stale_age=age if stale else None)
        self._events_for(ph, phone, now)
        return phone

    def _report(self, ph: _Phone, now: float) -> dict:
        r = {"status": "unknown", "text": None, "people": None, "can_move": None, "battery_pct": None, "floor_reported": None,
             "baro_hpa": None, "still": None, "still_s": None, "platform": None, "app": None, "home_suite": None,
             "safe_outside": False, "model": None, "chip": None, "network": None}
        for k, v in ph.device.items():
            r[k] = v
        for k, v in ph.report.items():
            if k in r and (v is not None or k not in ph.device):
                r[k] = v
        r["safe_outside"] = bool(r.get("safe_outside"))
        if r.get("status") not in STATUSES:
            r["status"] = "unknown"
        if r.get("platform") is None and self.source == "sim" and not ph.injected:
            r["platform"] = "sim"
        r["age_s"] = round(now - ph.report_t, 1) if ph.report_t is not None else None
        return r

    def _ranges(self, ph: _Phone, now: float) -> list[dict]:
        smoothed = {}
        try:
            rs, _b = ph.track.inputs(now)
            for r in rs or []:
                smoothed[(r.get("puck"), r.get("kind"))] = r
        except Exception:
            pass
        out = {}
        for (puck, kind), o in ph.links.items():
            age = now - o["t"]
            if age > self.stale_s:
                continue
            item = {"puck": puck, "kind": "uwb" if kind == "uwb" else "ble", "d_m": None, "sigma_m": None,
                    "rssi": o.get("rssi"), "los": o.get("los") if kind == "uwb" else None, "age_s": round(max(0.0, age), 1)}
            s = smoothed.get((puck, item["kind"]))
            n = int((s or {}).get("n") or 1)
            if kind == "uwb":
                d = float((s or {}).get("d_m") or o["d_m"])
                item["d_m"] = round(d, 2)
                item["sigma_m"] = round(self._uwb_sigma(d, o.get("los"), n), 2)
            else:
                rssi = float((s or {}).get("rssi") or o["rssi"])
                d, lo, hi = _ble_distance(rssi, o.get("tx_dbm"), self.noise)
                item["d_m"] = round(d, 1)
                item["sigma_m"] = round((hi - lo) / 2.0, 1)
                item["rssi"] = round(rssi, 1)
            prev = out.get(puck)
            if prev is None or (item["kind"] == "uwb" and (prev["kind"] != "uwb" or item["age_s"] <= LINK_STALE_S)):
                if prev is not None and prev["kind"] == "uwb" and prev["age_s"] <= LINK_STALE_S and item["kind"] == "ble":
                    continue
                if prev is not None and prev["kind"] == "uwb" and item["kind"] == "ble":
                    continue
                out[puck] = item
            elif prev["kind"] == "uwb" and prev["age_s"] > LINK_STALE_S and item["kind"] == "ble" and item["age_s"] <= LINK_STALE_S:
                out[puck] = item
        return [out[k] for k in sorted(out)]

    def _uwb_sigma(self, d: float, los, n: int) -> float:
        try:
            from . import models
            walls = 0 if (los is None or los >= 0.5) else 1
            return float(models.uwb_expected(d, walls, self.noise, n_samples=n)[1])
        except Exception:
            return 0.12

    def _bearing(self, ph: _Phone, now: float) -> dict | None:
        b = ph.bearing
        if not b or now - b["t"] > BEARING_FRESH_S:
            return None
        try:
            from . import models
            deg = models.compass_to_bldg_deg(b["to_puck_compass_deg"], self._georef_b)
        except Exception:
            deg = (self._georef_b - b["to_puck_compass_deg"] + 180.0) % 360.0
        return {"puck": b["puck"], "from_puck_deg": round(deg, 1), "sigma_deg": b.get("sigma_deg"), "source": b.get("source"),
                "age_s": round(now - b["t"], 1)}

    def _track_info(self, ph: _Phone, spatial: dict, now: float) -> dict:
        tr = spatial.get("track") if isinstance(spatial.get("track"), dict) else {}
        moving = tr.get("moving")
        if moving is None:
            moving = bool(getattr(ph.track, "moving", False))
        speed = tr.get("speed")
        if speed is None:
            if spatial.get("geometry") == "fix" and spatial.get("x") is not None:
                if ph.last_xy is not None and now > ph.last_xy[2]:
                    dt = now - ph.last_xy[2]
                    inst = math.hypot(spatial["x"] - ph.last_xy[0], spatial["y"] - ph.last_xy[1]) / dt
                    ph.speed = 0.7 * ph.speed + 0.3 * inst
                ph.last_xy = (spatial["x"], spatial["y"], now)
            speed = ph.speed
        return {"first_seen_s": round(now - ph.first_seen, 1), "last_seen_s": round(max(0.0, now - ph.last_seen), 1),
                "updates": ph.updates, "moving": bool(moving), "speed": round(float(speed or 0.0), 2)}

    def _floor_of(self, rep: dict) -> tuple[str | None, str]:
        fr = rep.get("floor_reported")
        if fr is not None:
            for f in self.twin.data["floors"]:
                if str(f.get("label")) == str(fr):
                    return f["id"], "self-report"
        return (self.floor or self.twin.exercise_floor), "puck"

    def _add_geo(self, phone: dict) -> None:
        def g(x, y):
            lat, lon = self.twin.to_geo(float(x), float(y))
            return {"lat": round(lat, 7), "lon": round(lon, 7)}
        if phone["geometry"] == "fix" and phone["x"] is not None:
            phone["geo"] = g(phone["x"], phone["y"])
        if phone.get("ring"):
            phone["ring"] = dict(phone["ring"])
            phone["ring"]["geo"] = g(phone["ring"]["cx"], phone["ring"]["cy"])
        if phone.get("candidates"):
            phone["candidates"] = [{**c, "geo": g(c["x"], c["y"])} for c in phone["candidates"]]
        if phone.get("zone"):
            z = dict(phone["zone"])
            poly = z.get("polygon") or (self.twin.room(z["room"])["polygon"] if z.get("room") else None)
            if poly:
                z["polygon"] = poly
                z["geo"] = g(sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
            phone["zone"] = z

    def _events_for(self, ph: _Phone, phone: dict, now: float) -> None:
        rep = phone["report"]
        if not ph.published:
            ph.published = True
            ph.status, ph.safe = rep.get("status"), rep.get("safe_outside")
            what = ", ".join(x for x in (_people(rep.get("people")), rep.get("status") if rep.get("status") != "unknown" else None) if x)
            self._event("phone_new", ph.id, f"Phone {ph.id} heard" + (f": {what}" if what else "") + f" ({phone['method']})", now)
        else:
            if rep.get("status") != ph.status or bool(rep.get("safe_outside")) != bool(ph.safe):
                txt = f"Phone {ph.id}: says safe outside" if rep.get("safe_outside") and not ph.safe else f"Phone {ph.id}: status {ph.status} -> {rep.get('status')}"
                ph.status, ph.safe = rep.get("status"), rep.get("safe_outside")
                self._event("status_changed", ph.id, txt, now)
        is_fix = phone["geometry"] == "fix" and not phone["stale"]
        if is_fix and not ph.was_fix:
            self._event("phone_fix", ph.id, phone["text"], now)
        ph.was_fix = is_fix
        if phone["stale"] and not ph.stale:
            self._event("phone_lost", ph.id, f"Phone {ph.id} not heard for {self.stale_s:g} s; keeping its last position with growing uncertainty", now)
        ph.stale = phone["stale"]

    # ---- cold presence
    def _presence(self, now: float, phones: list[dict]) -> list[dict]:
        optin_in_room: dict[str, int] = {}
        for p in phones:
            if p["room"] and not p["stale"] and not p["report"].get("safe_outside"):
                optin_in_room[p["room"]] = optin_in_room.get(p["room"], 0) + 1
        out = []
        pk = {p["id"]: p for p in self.pucks}
        for pid in sorted(self._cold):
            o = self._cold[pid]
            if pid not in pk or now - o["t"] > config.COLD_WINDOW_S:
                continue
            puck = pk[pid]
            room = puck.get("room")
            key = (pid, room)
            if key not in self._presence_ids:
                self._presence_ids[key] = f"C{len(self._presence_ids) + 1}"
            near, mid, far = o["near"], o["mid"], o["far"]
            count = near + mid
            lo = max(count // 2, optin_in_room.get(room, 0))
            hi = max(lo, count + math.ceil(far / 2))
            label = self.twin.room(room)["label"] if room else None
            poly = self.twin.room(room)["polygon"] if room else None
            if poly:
                cx, cy = sum(q[0] for q in poly) / len(poly), sum(q[1] for q in poly) / len(poly)
            else:
                cx, cy = puck["x"], puck["y"]
            lat, lon = self.twin.to_geo(cx, cy)
            where = f" in {label}" if label else ""
            reach = _mid_reach_m(self.noise)
            out.append({"id": self._presence_ids[key], "method": "ble-presence", "opt_in": False, "simulated": self.simulated,
                        "puck": pid, "floor": puck.get("floor"), "room": room, "room_label": label,
                        "count": count, "count_range": [lo, hi], "zones": {"near": near, "mid": mid, "far": far},
                        "window_s": o["window_s"], "confidence": 0.5, "age_s": round(now - o["t"], 1),
                        "geo": {"lat": round(lat, 7), "lon": round(lon, 7)},
                        "text": (f"About {count} device{'s' if count != 1 else ''} advertising within about {reach:g} m of puck {pid}{where} "
                                 f"(between {lo} and {hi}); {far} more farther away or through a wall. Devices, not people; no identities kept")})
        return out

    # ---- the Beacons object
    def _build(self, now: float, phones: list[dict], presence: list[dict]) -> dict:
        self.seq += 1
        limits = list(LIMITS_BASE)
        if self.simulated:
            limits.insert(0, "SIMULATED: every number on this feed comes from the simulator's stated noise model (hello.noise), not from a measurement.")
        limits.append(f"A phone not heard for {self.stale_s:g} s is marked stale and its uncertainty grows by "
                      f"{config.STALE_SIGMA_GROWTH_MPS:g} m per second; it is dropped after {self.retention_s / 60:g} minutes.")
        if self.source == "live" and self.rig_scale != 1.0:
            limits.append(f"Exercise scale 1 : {self.rig_scale:g}: every UWB range is multiplied by {self.rig_scale:g} before the locator.")
        for note in (self._locator_note, self._roster_note):
            if note:
                limits.append(note)
        label = self.twin.room(self.room)["label"] if self.room else None
        unplaced = []
        used = {p["id"] for p in self.pucks}
        for acc in sorted(self._unplaced, key=lambda a: self._unplaced[a]["first_seen"]):
            u = self._unplaced[acc]
            k = 1
            while f"P{k}" in used:
                k += 1
            used.add(f"P{k}")
            unplaced.append({"accessory": acc, "suggested_id": f"P{k}", "heard_by": len(u["eids"]),
                             "last_seen_s": round(max(0.0, now - u["last_seen"]), 1)})
        pucks = copy.deepcopy(self.pucks)
        for p in pucks:
            h = self._hosts.get(p["id"])
            if h is not None and now - h["t"] <= HOST_SILENT_S:
                p["host"] = h["host"]
                p["host_seen_s"] = round(max(0.0, now - h["t"]), 1)
        for pid in [k for k, h in self._hosts.items() if now - h["t"] > HOST_SILENT_S]:
            del self._hosts[pid]
        b = {"type": "beacons", "schema": SCHEMA, "seq": self.seq, "t": round(now, 3), "simulated": self.simulated,
             "source": self.source, "floor": self.floor, "room": self.room, "room_label": label, "rungs": dict(self.rungs),
             "rig": {"scale": self.rig_scale}, "pucks": pucks, "unplaced_pucks": unplaced,
             "phones": sorted(phones, key=lambda p: int(p["id"][1:]) if p["id"][1:].isdigit() else 0),
             "presence": presence, "limits": limits,
             "privacy": {"opt_in_only": True, "cold_identities_kept": False, "retention_s": self.retention_s,
                         "text": PRIVACY_TEXT.format(m=self.retention_s / 60)}}
        acc = self._accountability(b)
        if acc is not None:
            b["accountability"] = acc
        return b

    def _accountability(self, b: dict) -> dict | None:
        if self._roster is None:
            return None
        key = json.dumps([[(p["id"], p["geometry"], p["room"], p.get("x"), p.get("y"), p["stale"], p["report"]) for p in b["phones"]],
                          [(c["id"], c["count"], c["count_range"], c["room"]) for c in b["presence"]],
                          [(p["id"], p["x"], p["y"], p["room"]) for p in b["pucks"]], b["room"]], sort_keys=True, default=str)
        if key == self._acc_key and self._acc is not None:
            return self._acc
        try:
            acc = self._roster.accountability(self.site, self.twin, b["floor"] or self.twin.exercise_floor, b["phones"], b["presence"],
                                              b["pucks"], labels=self.labels, now_local_hour=None, focus_room=b["room"])
        except Exception:
            log.exception("roster.accountability failed")
            return None
        self._acc_key, self._acc = key, acc
        return acc

    # ---- debug
    def heat(self, phone_id: str, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        with self._lock:
            ph = self._phones.get(phone_id)
            if ph is None:
                raise KeyError(phone_id)
            if self._locate is None:
                raise NotImplementedError("the heat grid needs beacon.locate (the stub locator has none)")
            ranges, bearing = ph.track.inputs(now)
            if not ranges or not self.pucks:
                return None
            if bearing and "from_puck_deg" not in bearing and "to_puck_compass_deg" in bearing:
                bearing = self._bearing(ph, now)
            pucks = [{"id": p["id"], "x": p["x"], "y": p["y"], "sigma_m": p["sigma_m"]} for p in self.pucks]
            est = self._locate.estimate(self._context(), pucks, ranges, bearing=bearing, noise=self.noise, want_heat=True)
        heat = est.get("heat")
        if heat is None:
            return None
        return {"id": phone_id, "geometry": est.get("geometry"), "simulated": self.simulated, **heat}


# ============================================================================ pure helpers (also used by the server)
def _none_spatial() -> dict:
    return {"method": None, "geometry": "none", "x": None, "y": None, "sigma_m": None, "cov": None, "confidence": None,
            "room": None, "room_label": None, "room_prob": None, "node": None, "ring": None, "candidates": [], "pruned": [],
            "zone": None, "hpd_area_m2": None}


def _honest(sp: dict) -> dict:
    """Whatever the locator returned: x, y, sigma_m, cov, geo, node only for a fix; the parts that match the geometry."""
    sp = dict(sp or {})
    geom = sp.get("geometry") or "none"
    if geom not in ("fix", "candidates", "ring", "zone", "none"):
        geom = "none"
    if geom == "fix" and (sp.get("x") is None or sp.get("y") is None):
        geom = "none"
    if geom == "ring" and not sp.get("ring"):
        geom = "none"
    if geom == "candidates" and not sp.get("candidates"):
        geom = "none"
    if geom == "zone" and not sp.get("zone"):
        geom = "none"
    sp["geometry"] = geom
    if geom != "fix":
        for k in ("x", "y", "sigma_m", "cov", "geo", "node"):
            sp[k] = None
    else:
        sp["x"], sp["y"] = round(float(sp["x"]), 2), round(float(sp["y"]), 2)
        if sp.get("sigma_m") is not None:
            sp["sigma_m"] = round(float(sp["sigma_m"]), 2)
        if sp.get("cov") is not None:
            sp["cov"] = [round(float(c), 4) for c in sp["cov"]]
    if geom != "ring":
        sp["ring"] = None
    if geom != "candidates":
        sp["candidates"] = []
    if geom != "zone":
        sp["zone"] = None
    if geom in ("ring", "none"):
        sp["room"], sp["room_label"], sp["room_prob"] = None, None, None
    if geom == "candidates":
        rooms = {c.get("room") for c in sp["candidates"]}
        if len(rooms) != 1:
            sp["room"], sp["room_label"], sp["room_prob"] = None, None, None
    return sp


def _grow(sp: dict, extra: float) -> dict:
    sp = copy.deepcopy(sp)
    extra = max(0.0, float(extra))
    if sp.get("sigma_m") is not None:
        s = float(sp["sigma_m"]) + extra
        if sp.get("cov"):
            f = (s / max(float(sp["sigma_m"]), 1e-6)) ** 2
            sp["cov"] = [c * f for c in sp["cov"]]
        sp["sigma_m"] = s
    if sp.get("ring"):
        sp["ring"] = dict(sp["ring"])
        sp["ring"]["sigma_m"] = round(float(sp["ring"].get("sigma_m") or 0.0) + extra, 2)
    if sp.get("candidates"):
        sp["candidates"] = [{**c, "sigma_m": round(float(c.get("sigma_m") or 0.0) + extra, 2)} for c in sp["candidates"]]
    return sp


def _walls(twin: Twin, room_id: str, georef_b: float) -> list[tuple[str, list, list]]:
    poly = twin.room(room_id)["polygon"]
    ccw = _ccw(poly)
    out = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        nx, ny = (dy, -dx) if ccw else (-dy, dx)
        theta = math.degrees(math.atan2(ny, nx))
        compass = (georef_b - theta) % 360.0
        name = ("north", "east", "south", "west")[int(round(compass / 90.0)) % 4]
        out.append((name, a, b))
    return out


def describe_point(twin: Twin, x: float, y: float, room_id: str | None, georef_b: float) -> str:
    """'2.5 m from the west wall and 2 m from the south wall' (half metres, wall names from the room polygon)."""
    if not room_id:
        return ""
    try:
        walls = _walls(twin, room_id, georef_b)
    except ValueError:
        return ""
    best: dict[str, float] = {}
    for name, a, b in walls:
        d = _seg_dist((x, y), a, b)
        if d < best.get(name, float("inf")):
            best[name] = d
    if not best:
        return ""
    first = min(best, key=best.get)
    perp = {"north": ("east", "west"), "south": ("east", "west"), "east": ("north", "south"), "west": ("north", "south")}[first]
    others = [n for n in perp if n in best]
    if not others:
        return f"{half_text(best[first])} from the {first} wall"
    second = min(others, key=best.get)
    return f"{half_text(best[first])} from the {first} wall and {half_text(best[second])} from the {second} wall"


def _report_words(rep: dict) -> str:
    parts = []
    if rep.get("people") is not None:
        parts.append(_people(rep["people"]))
    if rep.get("can_move") is False:
        parts.append("cannot move")
    elif rep.get("can_move") is True:
        parts.append("can move")
    if rep.get("status") and rep["status"] != "unknown":
        parts.append(rep["status"])
    return ", ".join(parts)


def phone_text(phone: dict, twin: Twin, georef_b: float, stale_age: float | None = None) -> str:
    """One readable line, built only from whole or half metres, wall names and room names (no typed text, no ids but ours)."""
    rep = phone.get("report") or {}
    head = f"Phone {phone['id']}"
    words = _report_words(rep)
    if rep.get("safe_outside"):
        return f"{head}: says safe outside (I am out). Accounted for, not a victim"
    head = f"{head}: {words}." if words else f"{head}:"
    geom = phone.get("geometry")
    pre = f"Not heard for {int(stale_age)} s; last known: " if stale_age is not None else ""
    if geom == "fix":
        where = describe_point(twin, phone["x"], phone["y"], phone.get("room"), georef_b)
        kind = "UWB fix" if str(phone.get("method", "")).startswith("uwb") else "Bluetooth fix (coarse)"
        if phone.get("method") == "uwb-twr+bearing":
            kind = "UWB fix with the phone's bearing"
        sig = phone.get("sigma_m") or 0.0
        within = f"within {max(0.5, half(sig)):g} m"
        room = phone.get("room_label") or "an unknown room"
        return f"{head} {pre}{kind} in {room}" + (f", {where}" if where else "") + f", {within}"
    if geom == "candidates":
        cands = phone.get("candidates") or []
        descs = []
        for c in cands:
            d = describe_point(twin, c["x"], c["y"], c.get("room"), georef_b)
            descs.append((f"in {c.get('room_label')}" if c.get("room_label") else "outside any room") + (f", {d}" if d else ""))
        n = len(cands)
        word = {2: "Two", 3: "Three", 4: "Four"}.get(n, str(n))
        return f"{head} {pre}{word} possible spots: " + "; or ".join(descs)
    if geom == "ring":
        r = phone.get("ring") or {}
        kind = "UWB" if r.get("kind") == "uwb" else "Bluetooth"
        clipped = (r.get("arcs") or [[0, 360]]) != [[0.0, 360.0]] and (r.get("arcs") or [[0, 360]]) != [[0, 360]]
        approx = "" if kind == "UWB" else "about "
        return (f"{head} {pre}{kind} range only: {approx}{half_text(r.get('r_m') or 0)} from puck {r.get('puck')}"
                + (", on the part of that circle the twin leaves possible" if clipped else ", somewhere on that circle")
                + "; no position yet")
    if geom == "zone":
        z = phone.get("zone") or {}
        return (f"{head} {pre}Bluetooth only: in {z.get('room_label') or 'an unknown room'} (probability {float(z.get('prob') or 0):.1f}), "
                f"{half(z.get('r_min_m') or 0):g} to {half(z.get('r_max_m') or 0):g} m from puck {z.get('puck')}")
    return f"{head} {pre}heard, no position yet"


def make_ack(phone: dict | None) -> dict:
    """What the occupant's app shows back (the simulated phone page shows the same)."""
    if phone is None:
        return {"heard_by": [], "geometry": None, "sigma_m": None,
                "text": "No rescue beacon hears you yet. Keep the app open and the phone uncovered."}
    rep = phone.get("report") or {}
    heard = sorted({r["puck"] for r in phone.get("ranges") or [] if (r.get("age_s") or 0.0) <= LINK_STALE_S})
    n = len(heard)
    geom = phone.get("geometry")
    sig = phone.get("sigma_m") if geom == "fix" else None
    beacons = f"{n} rescue beacon{'s' if n != 1 else ''} hear{'s' if n == 1 else ''} you"
    if rep.get("safe_outside"):
        text = "You are marked safe outside. Thank you: crews will not search for you."
    elif n == 0:
        text = "No rescue beacon hears you right now. Keep the app open and the phone uncovered."
    elif geom == "fix":
        text = f"{beacons}; they know where you are to within {max(0.5, half(sig or 0)):g} m"
    elif geom == "candidates":
        text = f"{beacons}; they have narrowed you down to {len(phone.get('candidates') or [])} spots"
    elif geom == "ring":
        r = (phone.get("ring") or {}).get("r_m")
        text = f"{beacons}; {'it knows' if n == 1 else 'they know'} how far you are ({half_text(r or 0)}), not yet where"
    elif geom == "zone":
        text = f"{beacons}; they know which room you are in ({(phone.get('zone') or {}).get('room_label') or 'nearby'})"
    else:
        text = f"{beacons}"
    return {"heard_by": heard, "geometry": geom, "sigma_m": sig, "text": text}


def _r05(v):
    return None if v is None else round(float(v) * 2) / 2


def _r1(v):
    return None if v is None else round(float(v), 1)


def brief_from_beacons(b: dict) -> dict:
    """DISCRETE summary for the command agent: no timestamps, no eids, positions to 0.5 m, confidences to 0.1,
    room ids and labels, the accountability table and priorities, the limits. Same situation -> same brief."""
    b = b or {}
    phones = []
    for p in sorted(b.get("phones") or [], key=lambda q: q["id"]):
        rep = p.get("report") or {}
        e = {"id": p["id"], "method": p.get("method"), "geometry": p.get("geometry"), "stale": bool(p.get("stale")),
             "room": p.get("room"), "room_label": p.get("room_label"), "confidence": _r1(p.get("confidence")),
             "report": {"status": rep.get("status"), "people": rep.get("people"), "can_move": rep.get("can_move"),
                        "battery_pct": None if rep.get("battery_pct") is None else int(round(rep["battery_pct"] / 10.0) * 10),
                        "still": rep.get("still"), "floor_reported": rep.get("floor_reported"), "home_suite": rep.get("home_suite"),
                        "safe_outside": bool(rep.get("safe_outside"))},
             "text": p.get("text")}
        if p.get("geometry") == "fix":
            e.update({"x": _r05(p.get("x")), "y": _r05(p.get("y")), "sigma_m": max(0.5, _r05(p.get("sigma_m")) or 0.5)})
        if p.get("ring"):
            r = p["ring"]
            e["ring"] = {"puck": r.get("puck"), "r_m": _r05(r.get("r_m")), "sigma_m": max(0.5, _r05(r.get("sigma_m")) or 0.5), "kind": r.get("kind"),
                         "arcs": [[round(a0 / 5) * 5, round(a1 / 5) * 5] for a0, a1 in (r.get("arcs") or [])]}
        if p.get("candidates"):
            e["candidates"] = [{"x": _r05(c["x"]), "y": _r05(c["y"]), "weight": _r1(c.get("weight")), "room": c.get("room")} for c in p["candidates"]]
        if p.get("zone"):
            z = p["zone"]
            e["zone"] = {"room": z.get("room"), "room_label": z.get("room_label"), "band": z.get("band"), "puck": z.get("puck"), "prob": _r1(z.get("prob"))}
        phones.append(e)
    out = {"schema": "sandtable.beacons.brief/1", "simulated": bool(b.get("simulated")), "source": b.get("source"),
           "floor": b.get("floor"), "room": b.get("room"), "room_label": b.get("room_label"), "rungs": b.get("rungs"),
           "rig_scale": (b.get("rig") or {}).get("scale", 1.0),
           "pucks": [{"id": p["id"], "x": _r05(p.get("x")), "y": _r05(p.get("y")), "room": p.get("room")} for p in sorted(b.get("pucks") or [], key=lambda q: q["id"])],
           "unplaced_pucks": len(b.get("unplaced_pucks") or []),
           "phones": phones,
           "presence": [{"id": c["id"], "puck": c.get("puck"), "room": c.get("room"), "room_label": c.get("room_label"),
                         "count": c.get("count"), "count_range": c.get("count_range"), "zones": c.get("zones")}
                        for c in sorted(b.get("presence") or [], key=lambda q: q["id"])],
           "limits": b.get("limits") or []}
    if b.get("sample"):
        out["sample"] = True
    acc = b.get("accountability")
    if acc:
        keep = ("suite", "room", "room_label", "tenant", "industry", "vacant", "life_hazard", "expected", "app_registered", "safe_outside",
                "phones_opt_in", "people_reported", "located", "cold_devices", "cold_range", "unaccounted", "state", "priority", "why")
        out["accountability"] = {"source": acc.get("source"), "floor": acc.get("floor"),
                                 "suites": [{k: s.get(k) for k in keep if k in s} for s in acc.get("suites") or []],
                                 "totals": acc.get("totals"), "priorities": acc.get("priorities"),
                                 "next_puck": acc.get("next_puck"), "record_checks": acc.get("record_checks"), "text": acc.get("text")}
    return out
