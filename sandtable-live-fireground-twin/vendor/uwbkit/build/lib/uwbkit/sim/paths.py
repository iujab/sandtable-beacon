"""Path engine for the uwbkit simulator: waypoints -> keyframes -> position/velocity at any sim time.

Pure Python (no numpy) because a scenario has only a handful of nodes and this runs every step.
"""
from __future__ import annotations

import math
from bisect import bisect_right


class PathError(ValueError):
    """Raised for impossible waypoint timing; the scenario loader prefixes the node location."""


def _dist(p, q) -> float:
    return math.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2)


def build_keyframes(node: dict, loop: bool):
    """Turn a normalised node spec into (times, points). times[0] == 0, non-decreasing.

    Waypoint timing: absolute ``t``; else ``speed`` (m/s) from the previous waypoint; else the node's
    default ``speed`` (1.0 m/s). ``hold_s`` stays at the waypoint's ``at`` (or the previous point when
    ``at`` is omitted). With ``loop`` (and node ``close_loop`` not false) a closing leg back to the first
    waypoint is appended at the speed of the last leg, so looping never jumps.
    """
    z0 = float(node.get("z", 1.0))

    def pt(at):
        return (float(at[0]), float(at[1]), float(at[2]) if len(at) > 2 else z0)

    if "path" not in node:
        return [0.0], [pt(node["at"])]
    wps = node["path"]
    default_speed = float(node.get("speed", 1.0))
    first = wps[0]
    p0 = pt(first["at"] if "at" in first else node["at"])
    times, pts = [0.0], [p0]
    t_first = float(first.get("t", 0.0))
    if t_first > 0:
        times.append(t_first)
        pts.append(p0)
    if float(first.get("hold_s", 0.0)) > 0:
        times.append(times[-1] + float(first["hold_s"]))
        pts.append(p0)
    last_speed = default_speed
    for i, wp in enumerate(wps[1:], 1):
        prev_t, prev_p = times[-1], pts[-1]
        if "at" not in wp:  # pure hold at the previous point
            times.append(prev_t + float(wp.get("hold_s", 0.0)))
            pts.append(prev_p)
            continue
        p = pt(wp["at"])
        dist = _dist(p, prev_p)
        if "t" in wp:
            ta = float(wp["t"])
            if ta < prev_t - 1e-9:
                raise PathError(
                    f"path[{i}]: t={ta:g} is earlier than the time the previous waypoint is reached "
                    f"({prev_t:.2f} s, after travel and holds); absolute \"t\" values must increase "
                    f"(or use \"speed\" instead of \"t\")")
            ta = max(ta, prev_t)
            if ta > prev_t and dist > 0:
                last_speed = dist / (ta - prev_t)
        else:
            sp = float(wp.get("speed", default_speed))
            last_speed = sp
            ta = prev_t + dist / sp
        times.append(ta)
        pts.append(p)
        hold = float(wp.get("hold_s", 0.0))
        if hold > 0:
            times.append(ta + hold)
            pts.append(p)
    if loop and node.get("close_loop", True) and len(pts) > 1:
        gap = _dist(pts[-1], pts[0])
        if gap > 1e-6:
            times.append(times[-1] + gap / max(last_speed, 1e-3))
            pts.append(pts[0])
    return times, pts


class Path:
    """Piecewise-linear or Catmull-Rom (time-parameterised cubic Hermite) path through keyframes.

    ``eval(t)`` returns ``(x, y, z, vx, vy, vz)``; the velocity is the exact derivative of the curve.
    Looping paths repeat with period ``T`` (the last keyframe time); non-looping paths stop at the end.
    Holds (two consecutive identical keyframes) stay put and force a zero spline tangent at both ends,
    so the node decelerates into a hold instead of overshooting.
    """

    def __init__(self, times, pts, mode: str = "linear", loop: bool = True):
        self.times = [float(t) for t in times]
        self.pts = [tuple(float(c) for c in p) for p in pts]
        self.loop = bool(loop)
        self.T = self.times[-1]
        self.spline = mode == "spline" and len(self.pts) > 2
        self._samples = None
        self.tan = self._tangents() if self.spline else None

    def _tangents(self):
        pts, times = self.pts, self.times
        k_n = len(pts)
        zero = (0.0, 0.0, 0.0)
        hold = [_dist(pts[k], pts[k + 1]) < 1e-9 for k in range(k_n - 1)]
        tan = []
        for k in range(k_n):
            if (k > 0 and hold[k - 1]) or (k < k_n - 1 and hold[k]):
                tan.append(zero)
                continue
            if 0 < k < k_n - 1:
                a, b, dt = pts[k - 1], pts[k + 1], times[k + 1] - times[k - 1]
            else:
                tan.append(zero)  # open ends start/stop at rest; periodic ends fixed below
                continue
            tan.append(tuple((b[c] - a[c]) / dt for c in range(3)) if dt > 0 else zero)
        periodic = self.loop and k_n >= 3 and _dist(pts[0], pts[-1]) < 1e-6
        if periodic and not hold[0] and not hold[-1]:
            a, b = pts[k_n - 2], pts[1]
            dt = (times[1] - times[0]) + (times[-1] - times[-2])
            if dt > 0:
                m = tuple((b[c] - a[c]) / dt for c in range(3))
                tan[0] = m
                tan[-1] = m
        return tan

    def local_time(self, t: float) -> float:
        if self.T <= 0:
            return 0.0
        if self.loop:
            return t % self.T
        return min(max(t, 0.0), self.T)

    def eval(self, t: float):
        pts, times = self.pts, self.times
        if len(pts) == 1 or self.T <= 0:
            p = pts[0]
            return (p[0], p[1], p[2], 0.0, 0.0, 0.0)
        tau = self.local_time(t)
        if tau <= times[0]:
            p = pts[0]
            return (p[0], p[1], p[2], 0.0, 0.0, 0.0)
        if tau >= times[-1]:
            p = pts[-1]
            return (p[0], p[1], p[2], 0.0, 0.0, 0.0)
        i = bisect_right(times, tau) - 1
        t0, t1 = times[i], times[i + 1]
        h = t1 - t0
        p0, p1 = pts[i], pts[i + 1]
        s = (tau - t0) / h
        if not self.spline or p0 == p1:
            return (p0[0] + s * (p1[0] - p0[0]), p0[1] + s * (p1[1] - p0[1]), p0[2] + s * (p1[2] - p0[2]),
                    (p1[0] - p0[0]) / h, (p1[1] - p0[1]) / h, (p1[2] - p0[2]) / h)
        m0, m1 = self.tan[i], self.tan[i + 1]
        s2 = s * s
        s3 = s2 * s
        h00, h10, h01, h11 = 2 * s3 - 3 * s2 + 1, s3 - 2 * s2 + s, -2 * s3 + 3 * s2, s3 - s2
        d00, d10, d01, d11 = 6 * s2 - 6 * s, 3 * s2 - 4 * s + 1, -6 * s2 + 6 * s, 3 * s2 - 2 * s
        out_p, out_v = [], []
        for c in range(3):
            a, b, ma, mb = p0[c], p1[c], m0[c] * h, m1[c] * h
            out_p.append(h00 * a + h10 * ma + h01 * b + h11 * mb)
            out_v.append((d00 * a + d10 * ma + d01 * b + d11 * mb) / h)
        return (out_p[0], out_p[1], out_p[2], out_v[0], out_v[1], out_v[2])

    def nearest_time(self, x: float, y: float, z: float) -> float:
        """Path-local time (0..T) of the sampled path point nearest to (x, y, z)."""
        if self.T <= 0:
            return 0.0
        if self._samples is None:
            n = max(2, min(20000, int(self.T / 0.05) + 1))
            self._samples = [(self.T * k / (n - 1), self.eval(self.T * k / (n - 1))) for k in range(n)]
            if self.loop:  # eval(T) wraps to 0 for loops; keep the endpoint consistent
                self._samples[-1] = (self.T, self.eval(self.T - 1e-9))
        best_t, best_d = 0.0, float("inf")
        for tau, p in self._samples:
            d = (p[0] - x) ** 2 + (p[1] - y) ** 2 + (p[2] - z) ** 2
            if d < best_d:
                best_t, best_d = tau, d
        return best_t
