"""EventDetector: motion_start / motion_stop and pairwise proximity enter / exit, with hysteresis."""
from __future__ import annotations

import math

from .geometry import natural_key

STALE_S = 2.0          # a node without pos for this long is considered gone (moving -> motion_stop)
DIRECT_WINDOW_S = 1.0  # a direct range newer than this is preferred over position distance


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _ev(t, nid, name, data):
    return {"type": "event", "t": round(float(t), 4), "id": nid, "name": name, "data": data}


class _Node:
    __slots__ = ("hist", "t", "pos", "moving")

    def __init__(self):
        self.hist = []
        self.t = None
        self.pos = None
        self.moving = False


class EventDetector:
    """motion_start / motion_stop from the pos velocity averaged over the last motion_hold_s (vector mean, so
    zero-mean jitter of a still tag cancels): start when it exceeds motion_start_mps, stop when it drops below
    motion_stop_mps (hysteresis). proximity: enter when d < proximity_m, exit when
    d > proximity_m + hysteresis_m; d = median of the direct ranges of the last second (up to 3), else the 3-D
    distance between the latest positions. Non-anchor nodes = nodes that appear in pos messages."""

    def __init__(self, proximity_m: float = 1.0, hysteresis_m: float = 0.15,
                 motion_start_mps: float = 0.25, motion_stop_mps: float = 0.10, motion_hold_s: float = 0.7):
        self.proximity_m = float(proximity_m)
        self.hysteresis_m = float(hysteresis_m)
        self.start_mps = float(motion_start_mps)
        self.stop_mps = float(motion_stop_mps)
        self.hold_s = float(motion_hold_s)
        self.nodes: dict[str, _Node] = {}
        self.direct: dict[tuple, list] = {}     # (lo, hi) -> [(t, d), ...]
        self.inside: dict[tuple, tuple] = {}    # (lo, hi) -> (t, d) of the last data while inside

    def update(self, pos_msgs: list, range_msgs: list, t: float) -> list:
        t = float(t)
        out = []
        for p in pos_msgs or []:
            if not isinstance(p, dict) or p.get("type", "pos") != "pos" or p.get("id") is None:
                continue
            nid = str(p["id"])
            x, y = _num(p.get("x")), _num(p.get("y"))
            if x is None or y is None:
                continue
            z = _num(p.get("z")) or 0.0
            vx, vy = _num(p.get("vx")), _num(p.get("vy"))
            if vx is None or vy is None:
                sp = _num(p.get("speed")) or 0.0
                vx, vy = sp, 0.0          # no vector: fall back to scalar speed
            nd = self.nodes.get(nid)
            if nd is None:
                nd = self.nodes[nid] = _Node()
            nd.t = t
            nd.pos = (x, y, z)
            # mean velocity vector over the last hold_s: zero-mean noise averages out, real motion does not
            hist = nd.hist
            hist.append((t, vx, vy))
            while hist and t - hist[0][0] > self.hold_s + 1e-6:
                del hist[0]
            if not hist or t - hist[0][0] < 0.75 * self.hold_s:
                continue                  # not enough history yet
            mvx = sum(h[1] for h in hist) / len(hist)
            mvy = sum(h[2] for h in hist) / len(hist)
            s_ = math.hypot(mvx, mvy)
            if (not nd.moving and s_ > self.start_mps) or (nd.moving and s_ < self.stop_mps):
                nd.moving = not nd.moving
                out.append(_ev(t, nid, "motion_start" if nd.moving else "motion_stop", {"speed": round(s_, 3)}))
        # nodes that vanished while moving
        for nid, nd in self.nodes.items():
            if nd.moving and nd.t is not None and t - nd.t > STALE_S:
                nd.moving = False
                nd.hist = []
                out.append(_ev(t, nid, "motion_stop", {"speed": 0.0, "reason": "lost"}))
        # direct ranges between non-anchor nodes
        for r in range_msgs or []:
            if not isinstance(r, dict):
                continue
            a, b, d = r.get("a"), r.get("b"), _num(r.get("d"))
            if a is None or b is None or d is None or d < 0:
                continue
            a, b = str(a), str(b)
            if a == b or a not in self.nodes or b not in self.nodes:
                continue
            key = (a, b) if natural_key(a) <= natural_key(b) else (b, a)
            lst = self.direct.setdefault(key, [])
            rt = _num(r.get("t"))
            lst.append((t if rt is None else min(rt, t), d))
            if len(lst) > 3:
                del lst[0]
        # proximity per pair
        ids = sorted(self.nodes, key=natural_key)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = (ids[i], ids[j])
                d = self._distance(key, t)
                if d is None:
                    if key in self.inside and t - self.inside[key][0] > STALE_S:
                        last_d = self.inside.pop(key)[1]
                        out.append(_ev(t, key[0], "proximity",
                                       {"other": key[1], "d": round(last_d, 3), "state": "exit"}))
                    continue
                if key in self.inside:
                    self.inside[key] = (t, d)
                    if d > self.proximity_m + self.hysteresis_m:
                        del self.inside[key]
                        out.append(_ev(t, key[0], "proximity", {"other": key[1], "d": round(d, 3), "state": "exit"}))
                elif d < self.proximity_m:
                    self.inside[key] = (t, d)
                    out.append(_ev(t, key[0], "proximity", {"other": key[1], "d": round(d, 3), "state": "enter"}))
        return out

    def _distance(self, key, t):
        lst = self.direct.get(key)
        if lst:
            recent = [d for (rt, d) in lst if t - rt <= DIRECT_WINDOW_S]
            if recent:
                recent.sort()
                return recent[len(recent) // 2]
        na, nb = self.nodes[key[0]], self.nodes[key[1]]
        if na.pos is None or nb.pos is None or t - na.t > DIRECT_WINDOW_S or t - nb.t > DIRECT_WINDOW_S:
            return None
        return math.dist(na.pos, nb.pos)
