"""Per-phone tracker: smooths one opt-in phone's observations and holds its published geometry steady.

- UWB: the median of the last UWB_WINDOW ranges per puck (a lone outlier never moves a median), with the median `los`.
- Bluetooth: the mean RSSI (dB) of the last BLE_WINDOW SOS samples per puck.
- A link with no sample for LINK_STALE_S is dropped; a bearing is used only while younger than BEARING_FRESH_S.
- One range per puck goes to the locator: UWB when that puck has a fresh UWB link, else Bluetooth.
- Hysteresis: a published geometry is replaced only after HOLD_N consecutive estimates agree on the new geometry
  (so one noisy update never demotes a fix); estimates with the same geometry as the published one update it at once.
- `report` merges every report (and the compact report riding on SOS advertisements); `moving` comes from the
  phone's accelerometer (`report.still`) when it reported it within REPORT_FRESH_S, else from range changes.
"""
from __future__ import annotations

import math
from collections import deque

from . import locate, models

__all__ = ["PhoneTrack", "LINK_STALE_S", "BEARING_FRESH_S", "UWB_WINDOW", "BLE_WINDOW", "HOLD_N"]

LINK_STALE_S = 5.0
BEARING_FRESH_S = 3.0
UWB_WINDOW = 7
BLE_WINDOW = 10
HOLD_N = 2
REPORT_FRESH_S = 10.0
MOVE_WINDOW_S = 3.0          # range history looked at for `moving`
MOVE_RANGE_M = 0.6           # a smoothed range changing by more than this within MOVE_WINDOW_S means the phone moves


def _median(v: list[float]) -> float:
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


class PhoneTrack:
    """One opt-in phone. Feed it observations (`add`), ask for smoothed `inputs` or a held `estimate`."""

    def __init__(self, eid: str, georef_bearing_deg: float | None = None):
        self.eid = eid
        self.eids = [eid]
        # Seconds a link's samples stay usable. The session raises it (to 25 s) for live phones that take turns on a
        # board, because the stock puck firmware serves one phone at a time and a still phone's range does not age fast.
        self.link_stale_s = LINK_STALE_S
        self.georef_bearing_deg = georef_bearing_deg
        self._uwb: dict[str, deque] = {}
        self._ble: dict[str, deque] = {}
        self._bearing: dict | None = None
        self._hist: dict[str, deque] = {}       # (t, smoothed UWB range) per puck, for `moving`
        self.report: dict = {}
        self.report_t: float | None = None
        self.first_seen: float | None = None
        self.last_seen: float | None = None
        self.updates = 0
        self.published: dict | None = None
        self._pending_geom: str | None = None
        self._pending_n = 0
        self._last_fix: tuple[float, float, float] | None = None
        self.speed = 0.0
        self._range_moving = False

    # ---- input
    def add(self, obs: dict) -> None:
        kind = obs.get("kind")
        t = float(obs.get("t", 0.0))
        if kind not in ("sos", "uwb", "bearing", "report"):
            raise ValueError(f"PhoneTrack takes sos | uwb | bearing | report observations, not {kind!r}")
        eid = obs.get("eid")
        if eid and eid != self.eid:                      # a rotated id (the session links it through prev_eid)
            if eid not in self.eids:
                self.eids.append(eid)
            self.eid = eid
        self.first_seen = t if self.first_seen is None else min(self.first_seen, t)
        self.last_seen = t if self.last_seen is None else max(self.last_seen, t)
        self.updates += 1
        if kind == "uwb":
            d = obs.get("d_m")
            if d is None or not math.isfinite(float(d)):
                return                                   # dropout
            self._uwb.setdefault(obs["puck"], deque(maxlen=UWB_WINDOW)).append(
                (t, float(d), obs.get("los"), obs.get("rssi")))
        elif kind == "sos":
            if obs.get("rssi") is not None:
                self._ble.setdefault(obs["puck"], deque(maxlen=BLE_WINDOW)).append((t, float(obs["rssi"]), obs.get("tx_dbm")))
            if obs.get("report"):
                self._merge_report(obs["report"], t)
        elif kind == "bearing":
            self._bearing = dict(obs)
        elif kind == "report":
            if obs.get("report"):
                self._merge_report(obs["report"], t)

    def _merge_report(self, rep: dict, t: float) -> None:
        if self.report_t is None or t >= self.report_t:
            self.report.update({k: v for k, v in rep.items() if k != "age_s"})
            self.report_t = t

    # ---- smoothed inputs
    def inputs(self, now: float, georef_bearing_deg: float | None = None) -> tuple[list[dict], dict | None]:
        ranges: list[dict] = []
        uwb_pucks = set()
        for puck, dq in self._uwb.items():
            fresh = [s for s in dq if now - s[0] <= self.link_stale_s]
            if not fresh:
                continue
            d = _median([s[1] for s in fresh])
            los = [float(s[2]) for s in fresh if s[2] is not None]
            rssi = [float(s[3]) for s in fresh if s[3] is not None]
            r = {"puck": puck, "kind": "uwb", "d_m": round(d, 3), "n": len(fresh), "age_s": round(now - fresh[-1][0], 2)}
            if los:
                r["los"] = round(_median(los), 2)
            if rssi:
                r["rssi"] = round(sum(rssi) / len(rssi), 1)
            ranges.append(r)
            uwb_pucks.add(puck)
        for puck, dq in self._ble.items():
            if puck in uwb_pucks:
                continue
            fresh = [s for s in dq if now - s[0] <= self.link_stale_s]
            if not fresh:
                continue
            r = {"puck": puck, "kind": "ble", "rssi": round(sum(s[1] for s in fresh) / len(fresh), 1), "n": len(fresh),
                 "age_s": round(now - fresh[-1][0], 2)}
            tx = [s[2] for s in fresh if s[2] is not None]
            if tx:
                r["tx_dbm"] = float(tx[-1])
            ranges.append(r)
        ranges.sort(key=lambda r: r["puck"])
        bearing = None
        b = self._bearing
        if b is not None and now - float(b.get("t", 0.0)) <= BEARING_FRESH_S:
            deg = b.get("from_puck_deg")
            if deg is None and b.get("to_puck_compass_deg") is not None:
                g = georef_bearing_deg if georef_bearing_deg is not None else self.georef_bearing_deg
                if g is not None:
                    deg = models.compass_to_bldg_deg(float(b["to_puck_compass_deg"]), float(g))
            if deg is not None:
                bearing = {"puck": b["puck"], "from_puck_deg": round(float(deg), 1),
                           "sigma_deg": float(b.get("sigma_deg") or models.DEFAULT_NOISE["bearing"]["sigma_deg"]),
                           "source": b.get("source", "phone-aoa+compass"), "age_s": round(now - float(b.get("t", 0.0)), 2)}
        self._update_motion(now, ranges)
        return ranges, bearing

    def _update_motion(self, now: float, ranges: list[dict]) -> None:
        moving = False
        for r in ranges:
            if r["kind"] != "uwb":
                continue
            h = self._hist.setdefault(r["puck"], deque(maxlen=64))
            if not h or h[-1][0] != now:
                h.append((now, r["d_m"]))
            recent = [d for t, d in h if now - t <= MOVE_WINDOW_S]
            if len(recent) >= 2 and max(recent) - min(recent) > MOVE_RANGE_M:
                moving = True
        self._range_moving = moving

    # ---- state
    @property
    def moving(self) -> bool:
        still = self.report.get("still")
        if still is not None and self.report_t is not None and self.last_seen is not None \
                and self.last_seen - self.report_t <= REPORT_FRESH_S:
            return not bool(still)
        return self._range_moving

    def report_age(self, now: float) -> float | None:
        return None if self.report_t is None else round(now - self.report_t, 2)

    def track(self, now: float) -> dict:
        return {"first_seen_s": round(now - self.first_seen, 1) if self.first_seen is not None else None,
                "last_seen_s": round(now - self.last_seen, 1) if self.last_seen is not None else None,
                "updates": self.updates, "moving": self.moving, "speed": round(self.speed, 2)}

    # ---- estimate with hysteresis
    def estimate(self, ctx: locate.Context, pucks: list[dict], now: float, noise: dict | None = None,
                 prior: dict | None = None, want_heat: bool = False) -> dict:
        noise = models.noise_or_default(noise)
        known = {p["id"] for p in pucks or []}
        ranges, bearing = self.inputs(now, getattr(ctx, "georef_bearing_deg", None))
        ranges = [r for r in ranges if r["puck"] in known]          # pucks can be reset or removed
        if bearing is not None and bearing["puck"] not in known:
            bearing = None
        if not ranges:
            if self.published is None:
                est = locate.estimate(ctx, pucks, [], None, None, noise)
                est["debug"]["held"] = False
            else:
                est = dict(self.published)
                est["debug"] = dict(est.get("debug") or {}, held=True, reason="no fresh link: last geometry kept")
            return self._finish(est, ranges, bearing, now, noise)
        est = locate.estimate(ctx, pucks, ranges, bearing, prior, noise, want_heat=want_heat)
        g = est["geometry"]
        if self.published is None or g == self.published["geometry"]:
            self.published = est
            self._pending_geom, self._pending_n = None, 0
            held = False
        else:
            if g == self._pending_geom:
                self._pending_n += 1
            else:
                self._pending_geom, self._pending_n = g, 1
            if self._pending_n >= HOLD_N:
                self.published = est
                self._pending_geom, self._pending_n = None, 0
                held = False
            else:
                held = True
        out = dict(self.published)
        out["debug"] = dict(out.get("debug") or {}, held=held, latest_geometry=g)
        if out["geometry"] == "fix" and not held:
            if self._last_fix is not None and now > self._last_fix[2]:
                v = math.hypot(out["x"] - self._last_fix[0], out["y"] - self._last_fix[1]) / (now - self._last_fix[2])
                self.speed = 0.7 * self.speed + 0.3 * v
            self._last_fix = (out["x"], out["y"], now)
        return self._finish(out, ranges, bearing, now, noise)

    def _finish(self, est: dict, ranges: list[dict], bearing: dict | None, now: float, noise: dict) -> dict:
        feed_ranges = []
        for r in ranges:
            if r["kind"] == "uwb":
                fr = {"puck": r["puck"], "kind": "uwb", "d_m": r["d_m"],
                      "sigma_m": round(models.uwb_link_sigma(noise, r.get("n") or 1), 3),
                      "rssi": r.get("rssi"), "los": r.get("los"), "age_s": r["age_s"]}
            else:
                d, lo, hi = models.ble_distance(r["rssi"], noise, r.get("tx_dbm"), r.get("n") or 1)
                fr = {"puck": r["puck"], "kind": "ble", "d_m": round(d, 2), "sigma_m": round((hi - lo) / 2, 2),
                      "rssi": r["rssi"], "los": None, "age_s": r["age_s"]}
            feed_ranges.append(fr)
        est["ranges"] = feed_ranges
        est["bearing"] = bearing
        est["track"] = self.track(now)
        return est
