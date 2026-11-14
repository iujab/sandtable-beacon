"""Per-phone tracker: smooths one opt-in phone's observations and holds its published geometry steady.

- UWB: the median of the last UWB_WINDOW ranges per puck (a lone outlier never moves a median), with the median `los`.
- Bluetooth: the mean RSSI (dB) of the last BLE_WINDOW SOS samples per puck.
- A link with no sample for LINK_STALE_S is dropped; a bearing is used only while younger than BEARING_FRESH_S.
- One range per puck goes to the locator: UWB when that puck has a fresh UWB link, else Bluetooth.
- A thin UWB link (fewer than MIN_LINK_SAMPLES fresh readings) never joins solid links: a second puck's first reading
  does not turn a ring into a position. While NO link is solid (live: an iPhone reports each board once per turn,
  every 10 to 20 s) all links are used; the locator's likelihood carries the single-reading outlier tail.
- Ranges used together must describe the same moment (SYNC_S): a UWB link whose latest reading is more than SYNC_S
  older than the freshest link's is left out of the solution unless the phone is demonstrably still since that
  reading: (a) every fresh link that has a reading from at or before that time now reads within STILL_RANGE_M of it
  (and at least one such link exists), or (b) the latest report (within REPORT_FRESH_S) says `still: true` and its
  `still_s` covers that time, and no fresh link contradicts it (a fresh link whose range moved by more than
  STILL_RANGE_M since then always vetoes; so does a report of `still: false` made since then). A phone walked out of one
  puck's reach therefore loses that puck at once; a still phone taking turns between boards keeps every board.
  While the phone is known to be MOVING (a fresh `still: false` report, or its ranges changing), each UWB range is the
  median of that link's readings within MOVING_WINDOW_S before the freshest reading of any link, and a link with no
  reading in that window is left out: a median over older readings lags the phone by a different amount on each link,
  and two lagging ranges intersect in a confident wrong point. Left-out links are listed in `debug.left_out`.
- Hysteresis, asymmetric toward honesty: a WEAKER geometry (fix -> candidates -> ring -> zone -> none) on the same
  evidence (pucks, links used, bearing) replaces the published one after HOLD_N consecutive weaker estimates (one
  noisy update never demotes a fix, two do, whichever weaker geometries they are); on CHANGED evidence (a link lost
  or left out) at once. A STRONGER one needs HOLD_UP_N consecutive stronger estimates on the same evidence (HOLD_N
  when it changed), and what is then published is the weakest geometry of that run (a run of fix, candidates, fix
  publishes candidates). A claim is withdrawn faster than it is made. Estimates with the same geometry as the
  published one update it at once.
- A pruned point keeps its published reason text while that reason still applies to it (no flipping texts).
- `report` merges every report (and the compact report riding on SOS advertisements); `moving` comes from the
  phone's accelerometer (`report.still`) when it reported it within REPORT_FRESH_S, else from range changes.
"""
from __future__ import annotations

import math
from collections import deque

from . import locate, models

__all__ = ["PhoneTrack", "LINK_STALE_S", "BEARING_FRESH_S", "UWB_WINDOW", "BLE_WINDOW", "HOLD_N", "HOLD_UP_N",
           "HOLD_DOWN_N", "MIN_LINK_SAMPLES"]

LINK_STALE_S = 5.0
BEARING_FRESH_S = 3.0
UWB_WINDOW = 7
BLE_WINDOW = 10
HOLD_N = 2               # consecutive estimates before a weaker geometry (or one on changed evidence) is published
MIN_LINK_SAMPLES = 3     # a UWB link joins a multi-puck solution once it has this many fresh readings
# Promotion is slower than demotion: with an unchanged set of pucks, links and bearing, a STRONGER geometry (ring ->
# candidates -> fix) is published only after HOLD_UP_N consecutive estimates, so that a borderline posterior does not
# flicker a dot onto the board; a weaker one needs HOLD_N. (Round 1 of this file held a published fix for 6 weaker
# estimates instead: that kept a confident dot on the board after the evidence had withdrawn it.)
HOLD_UP_N = 3
HOLD_DOWN_N = HOLD_N     # kept for importers: demotion uses HOLD_N
STICKY_REASON_M = 1.0    # a pruned point within this distance of a published one keeps its text while it still applies
PUCK_MOVED_M = 0.30      # a puck that `estimate` sees more than this from where it was last time has moved: forget its ranges
MOVING_WINDOW_S = 1.0    # a moving phone: each UWB range is the median of its readings in this window before the freshest one
SYNC_S = 1.5             # links whose latest readings are further apart than this do not describe the same moment ...
STILL_RANGE_M = 0.3      # ... unless the phone is still: a fresh link's range moved by no more than this since then
RAW_HISTORY = 512        # UWB readings kept per link for the stillness test (25 s at up to 20 Hz)
_RANK = {"none": 0, "zone": 1, "ring": 2, "candidates": 3, "fix": 4}
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
        self._ignore_before: dict[str, float] = {}      # per puck: samples timestamped up to this are from before the puck moved
        self._puck_pos: dict[str, tuple[float, float]] = {}   # where each puck was at the last estimate
        self.georef_bearing_deg = georef_bearing_deg
        self._uwb: dict[str, deque] = {}
        self._ble: dict[str, deque] = {}
        self._bearing: dict | None = None
        self._hist: dict[str, deque] = {}       # (t, smoothed UWB range) per puck, for `moving`
        self._raw: dict[str, deque] = {}        # (t, d) every UWB reading per puck, for the stillness test (SYNC_S)
        self.report: dict = {}
        self.report_t: float | None = None
        self.first_seen: float | None = None
        self.last_seen: float | None = None
        self.updates = 0
        self.published: dict | None = None
        self._published_sig = None
        self._down_n = 0                         # consecutive estimates weaker than the published geometry
        self._up_run: list[dict] = []            # consecutive estimates stronger than the published geometry
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
        if kind in ("uwb", "sos", "bearing") and t <= self._ignore_before.get(obs.get("puck"), float("-inf")):
            if kind == "sos" and obs.get("report"):
                self._merge_report(obs["report"], t)     # the status is still good; the range is not
            return                                       # measured before that puck moved: it no longer fits the map
        if kind == "uwb":
            d = obs.get("d_m")
            if d is None or not math.isfinite(float(d)):
                return                                   # dropout
            self._uwb.setdefault(obs["puck"], deque(maxlen=UWB_WINDOW)).append(
                (t, float(d), obs.get("los"), obs.get("rssi")))
            self._raw.setdefault(obs["puck"], deque(maxlen=RAW_HISTORY)).append((t, float(d)))
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

    def forget_puck(self, puck_id: str, now: float, guard_s: float = 0.3) -> None:
        """The puck moved, was removed, or its ranges changed meaning (exercise scale): what is buffered for it no longer
        describes the geometry. Drop it, ignore anything timestamped up to `now + guard_s` for that puck, and publish
        afresh: a geometry computed with the old position is never held over (no dot from stale ranges)."""
        self._uwb.pop(puck_id, None)
        self._ble.pop(puck_id, None)
        self._hist.pop(puck_id, None)
        self._raw.pop(puck_id, None)
        if self._bearing is not None and self._bearing.get("puck") == puck_id:
            self._bearing = None
        # Observation times come from the observer's clock (a phone's may run behind the server's): the cut-off is the
        # newest observation time this tracker has seen when it is earlier than `now`, so a phone clock running behind
        # does not make fresh ranges look old (a server-time cut-off would drop them for the whole skew).
        ref = float(now) if self.last_seen is None else min(float(now), float(self.last_seen))
        self._ignore_before[puck_id] = max(self._ignore_before.get(puck_id, float("-inf")), ref + float(guard_s))
        self.published = None
        self._published_sig = None
        self._down_n = 0
        self._up_run = []
        self._last_fix = None

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
        # a puck that moved since the last estimate: its buffered ranges were measured against the old position (the
        # session also calls forget_puck; this catches any caller that does not)
        for p in pucks or []:
            xy = (float(p["x"]), float(p["y"]))
            old = self._puck_pos.get(p["id"])
            if old is not None and math.hypot(xy[0] - old[0], xy[1] - old[1]) > PUCK_MOVED_M:
                self.forget_puck(p["id"], now)
            self._puck_pos[p["id"]] = xy
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
        # A thin UWB link (fewer than MIN_LINK_SAMPLES readings) never joins solid ones: a second puck's first reading
        # must not turn a ring into a position. While no UWB link is solid (sparse live readings) every link is used.
        solid = [r for r in ranges if r["kind"] != "uwb" or int(r.get("n") or 0) >= MIN_LINK_SAMPLES]
        use = solid if any(r["kind"] == "uwb" for r in solid) else list(ranges)
        # Ranges used together must describe the same moment (SYNC_S), unless the phone is demonstrably still.
        use, left_out = self._synchronise(use, now)
        use_bearing = bearing if (bearing is not None and any(r["puck"] == bearing["puck"] for r in use)) else None
        est = locate.estimate(ctx, pucks, use, use_bearing, prior, noise, want_heat=want_heat)
        if left_out:
            est["debug"] = dict(est.get("debug") or {}, left_out=left_out)
        g = est["geometry"]
        by_id = {p["id"]: p for p in pucks or []}
        # the evidence the locator actually used: pucks (where they are), link kinds, bearing
        sig = (frozenset((r["puck"], r["kind"], round(float(by_id[r["puck"]]["x"]), 1), round(float(by_id[r["puck"]]["y"]), 1)) for r in use),
               use_bearing["puck"] if use_bearing else None)
        same_evidence = sig == self._published_sig
        if self.published is None or g == self.published["geometry"]:
            self._publish(est, sig)
            held = False
        elif _RANK.get(g, 0) < _RANK.get(self.published["geometry"], 0):
            self._up_run = []
            self._down_n += 1
            held = not (not same_evidence or self._down_n >= HOLD_N)     # changed evidence: at once
            if not held:
                self._publish(est, sig)
        else:
            self._down_n = 0
            self._up_run.append(est)
            held = len(self._up_run) < (HOLD_UP_N if same_evidence else HOLD_N)
            if not held:
                # publish the weakest geometry the whole run agrees on (fix, candidates, fix -> candidates)
                low = min(_RANK.get(e["geometry"], 0) for e in self._up_run)
                pick = next(e for e in reversed(self._up_run) if _RANK.get(e["geometry"], 0) == low)
                if _RANK.get(pick["geometry"], 0) > _RANK.get(self.published["geometry"], 0):
                    self._publish(pick, sig)
                else:
                    self._up_run = []
        out = dict(self.published)
        out["debug"] = dict(out.get("debug") or {}, held=held, latest_geometry=g)
        if out["geometry"] == "fix" and not held:
            if self._last_fix is not None and now > self._last_fix[2]:
                v = math.hypot(out["x"] - self._last_fix[0], out["y"] - self._last_fix[1]) / (now - self._last_fix[2])
                self.speed = 0.7 * self.speed + 0.3 * v
            self._last_fix = (out["x"], out["y"], now)
        return self._finish(out, ranges, bearing, now, noise)

    def _publish(self, est: dict, sig) -> None:
        """Make `est` the published geometry. A pruned point near a previously published one keeps the earlier reason
        text while that reason is still among the reasons that apply (stable texts; the locator's are deterministic)."""
        prev = (self.published or {}).get("pruned") or []
        if prev and est.get("pruned"):
            kept = []
            for q in est["pruned"]:
                old = min(prev, key=lambda o: math.hypot(o["x"] - q["x"], o["y"] - q["y"]))
                if (math.hypot(old["x"] - q["x"], old["y"] - q["y"]) <= STICKY_REASON_M and old.get("reason") != q.get("reason")
                        and old.get("reason") in (q.get("reasons") or [])):
                    q = dict(q, reason=old["reason"])
                kept.append(q)
            est = dict(est, pruned=kept)
        self.published = est
        self._published_sig = sig
        self._down_n = 0
        self._up_run = []

    def _moving_now(self, now: float) -> bool:
        """Known to be moving: a fresh report says `still: false`, or (no fresh accelerometer report) the ranges move."""
        still = self.report.get("still")
        if still is not None and self.report_t is not None and now - self.report_t <= REPORT_FRESH_S:
            return not bool(still)
        return self._range_moving

    def _still_since(self, t0: float, fresh: list[str], now: float) -> tuple[bool, str]:
        """Is the phone demonstrably still since t0? (see the module docstring, SYNC_S). Returns (still, why)."""
        rep = self.report
        if rep.get("still") is False and self.report_t is not None and self.report_t >= t0 - 0.5 \
                and now - self.report_t <= REPORT_FRESH_S:
            return False, "the phone reports it moved since then"     # e.g. walking around a puck keeps that range constant
        vouched = False
        for q in fresh:
            hist = self._raw.get(q) or ()
            before = [d for t, d in hist if t <= t0 + 1e-6]
            if not before:
                continue                                  # this link cannot speak for that moment
            last_t = max(t for t, _d in hist)
            recent = [d for t, d in hist if t >= last_t - SYNC_S]
            moved = abs(_median(recent) - _median(before[-3:]))
            if moved > STILL_RANGE_M:
                return False, f"the range to {q} changed by {moved:.1f} m since then"
            vouched = True
        if vouched:
            return True, "fresh links unchanged since then"
        if rep.get("still") is True and self.report_t is not None and now - self.report_t <= REPORT_FRESH_S:
            still_s = rep.get("still_s")
            if still_s is None or self.report_t - float(still_s) <= t0 + 0.5:
                return True, "the phone reports it is still"
        return False, "the phone is not known to be still since then"

    def _synchronise(self, use: list[dict], now: float) -> tuple[list[dict], list[dict]]:
        """Leave out UWB links whose latest reading is more than SYNC_S older than the freshest link's, unless the
        phone is demonstrably still since that reading."""
        last = {r["puck"]: max(t for t, *_ in self._uwb[r["puck"]]) for r in use if r["kind"] == "uwb" and self._uwb.get(r["puck"])}
        if len(last) < 2:
            return use, []
        t_fresh = max(last.values())
        if self._moving_now(now):
            # a moving phone: ranges from the same short window only (see the module docstring)
            out, left = [], []
            for r in use:
                if r["kind"] != "uwb" or r["puck"] not in last:
                    out.append(r)
                    continue
                win = [s for s in self._uwb[r["puck"]] if s[0] >= t_fresh - MOVING_WINDOW_S]
                if not win:
                    left.append({"puck": r["puck"], "age_s": round(now - last[r["puck"]], 2),
                                 "why": f"the phone is moving and {r['puck']} has no reading from the last {MOVING_WINDOW_S:g} s"})
                    continue
                out.append(dict(r, d_m=round(_median([s[1] for s in win]), 3), n=len(win)))
            return out, left
        aged = sorted((p for p, t in last.items() if t_fresh - t > SYNC_S), key=str)
        fresh = sorted((p for p in last if p not in aged), key=str)
        out, left = [], []
        drop = set()
        for p in aged:
            ok, why = self._still_since(last[p], fresh, now)
            if not ok:
                drop.add(p)
                left.append({"puck": p, "age_s": round(now - last[p], 2), "why": why})
        out = [r for r in use if not (r["kind"] == "uwb" and r["puck"] in drop)]
        return out, left

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
