"""Floor-plan geometry for Scout mode: specular wall echoes (image source), bistatic ellipses, polygon helpers.

Physical constants and the bistatic excess-path model are copied from lane L08's project
`tagless-uwb-echo-radar` (src/tagless/wire.py: C, FC_HZ, LAMBDA_M, TS_NS, M_PER_TAP, K0, bistatic_excess_m;
src/tagless/dsp/pipeline.py `_kappa_pts`: the near-surface body correction). The image-source wall model is new
here. Everything is floor-level 2-D geometry: walls are vertical, the pucks lie on the floor, so a reflection off a
wall keeps the height difference and the 2-D path is the 3-D path to within millimetres. Pure numpy, no I/O.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

# ---- physics (from L08 wire.py, unchanged) -------------------------------------------------------------
C = 299_792_458.0                 # m/s
FC_HZ = 6.4896e9                  # DW3000 channel 5 centre frequency
CARRIER_MHZ = FC_HZ / 1e6         # 6489.6
LAMBDA_M = C / FC_HZ              # 0.0462 m
BANDWIDTH_MHZ = 499.2             # channel 5 bandwidth
TS_NS = 1.0016                    # accumulator sample period: 1 / (2 * 499.2 MHz)
M_PER_TAP = C * TS_NS * 1e-9      # 0.3003 m of path length per CIR tap
K0 = 8                            # nominal index of the first path inside the frame window

# ---- Scout assumptions (new here; stated in RADAR_NOTES.md) -------------------------------------------
PUCK_Z_M = 0.04                   # antenna height of a puck resting on the floor
TARGET_Z_M = 0.25                 # chest of a person lying on the floor
BODY_RADIUS_M = 0.13              # DSP's assumed torso radius (L08 used 0.13 m for the "front" zone)

__all__ = ["image_paths", "ellipse_points", "ceiling_path", "bistatic_excess", "pip", "pip_grid", "poly_dist",
           "seg_dist", "mirror", "C", "FC_HZ", "LAMBDA_M", "TS_NS", "M_PER_TAP", "K0", "BANDWIDTH_MHZ",
           "CARRIER_MHZ", "PUCK_Z_M", "TARGET_Z_M", "BODY_RADIUS_M"]


# ---- polygon helpers ---------------------------------------------------------------------------------------
def pip(pt: Sequence[float], poly: Sequence[Sequence[float]]) -> bool:
    """Point in polygon (even-odd rule)."""
    x, y = float(pt[0]), float(pt[1])
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def pip_grid(X: np.ndarray, Y: np.ndarray, poly: Sequence[Sequence[float]]) -> np.ndarray:
    """Vectorised point in polygon for arrays of x and y (same shape)."""
    X = np.asarray(X, float)
    Y = np.asarray(Y, float)
    inside = np.zeros(X.shape, bool)
    n = len(poly)
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[i - 1][0], poly[i - 1][1]
        if yi == yj:
            continue
        cond = (yi > Y) != (yj > Y)
        xc = (xj - xi) * (Y - yi) / (yj - yi) + xi
        inside ^= cond & (X < xc)
    return inside


def seg_dist(p, a, b) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / den))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def poly_dist(p, poly) -> float:
    """Distance from a point to the polygon's boundary."""
    return min(seg_dist(p, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def mirror(p, a, b) -> tuple[float, float]:
    """Mirror image of point p across the infinite line through a and b."""
    ax, ay = a[0], a[1]
    dx, dy = b[0] - ax, b[1] - ay
    den = dx * dx + dy * dy
    if den == 0:
        return (2 * ax - p[0], 2 * ay - p[1])
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / den
    fx, fy = ax + t * dx, ay + t * dy
    return (2 * fx - p[0], 2 * fy - p[1])


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _proper_cross(p, q, a, b, eps: float = 1e-9) -> bool:
    """True when segments pq and ab cross at an interior point of both."""
    d1, d2 = _cross(a, b, p), _cross(a, b, q)
    d3, d4 = _cross(p, q, a), _cross(p, q, b)
    return ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps))


# ---- echoes ------------------------------------------------------------------------------------------------------
def image_paths(tx_xy, rx_xy, polygon) -> list[dict]:
    """First-order specular wall reflections between two points inside a room (image-source method).

    For wall i (polygon[i] -> polygon[i+1]) the receiver is mirrored across the wall's line; the reflection exists
    when the straight line from the transmitter to that image meets the wall segment itself, both points are on the
    same (inner) side of the wall, and neither leg is cut by another wall (concave rooms).
    Returns [{"wall": i, "path_m": total tx -> wall -> rx length, "point": specular point [x, y]}], ordered by wall.
    Door openings are not cut out of the walls (a 0.9 m door is a small part of a wall)."""
    tx = (float(tx_xy[0]), float(tx_xy[1]))
    rx = (float(rx_xy[0]), float(rx_xy[1]))
    n = len(polygon)
    out = []
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        s_tx, s_rx = _cross(a, b, tx), _cross(a, b, rx)
        if s_tx * s_rx <= 0:                       # on the wall line or on opposite sides: no specular path off this face
            continue
        img = mirror(rx, a, b)
        dx, dy = img[0] - tx[0], img[1] - tx[1]
        ex, ey = b[0] - a[0], b[1] - a[1]
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((a[0] - tx[0]) * ey - (a[1] - tx[1]) * ex) / den
        u = ((a[0] - tx[0]) * dy - (a[1] - tx[1]) * dx) / den
        if not (0.0 < t < 1.0 and 0.0 <= u <= 1.0):
            continue
        hit = (tx[0] + t * dx, tx[1] + t * dy)
        blocked = False
        for j in range(n):
            if j == i:
                continue
            c, d = polygon[j], polygon[(j + 1) % n]
            if _proper_cross(tx, hit, c, d) or _proper_cross(hit, rx, c, d):
                blocked = True
                break
        if blocked:
            continue
        out.append({"wall": i, "path_m": math.hypot(dx, dy), "point": [hit[0], hit[1]]})
    return out


def ceiling_path(tx_xy, rx_xy, height_m: float, z_m: float = PUCK_Z_M) -> dict:
    """Specular ceiling bounce between two pucks at height z under a flat ceiling at height_m.
    Returns {"path_m", "elevation_deg"} (elevation of the ray as it leaves the puck)."""
    d = math.hypot(tx_xy[0] - rx_xy[0], tx_xy[1] - rx_xy[1])
    v = 2.0 * max(height_m - z_m, 0.0)
    return {"path_m": math.hypot(d, v), "elevation_deg": math.degrees(math.atan2(v, max(d, 1e-9)))}


def ellipse_points(f1, f2, path_m: float, n: int = 64) -> list[list[float]]:
    """Points of the ellipse {p : |p - f1| + |p - f2| = path_m} (an echo's locus for one pair of pucks).
    A path no longer than the focal distance gives the degenerate ellipse: the segment f1-f2."""
    f1 = np.asarray(f1[:2], float)
    f2 = np.asarray(f2[:2], float)
    c = float(np.linalg.norm(f2 - f1)) / 2.0
    a = float(path_m) / 2.0
    th = np.linspace(0.0, 2 * math.pi, int(n), endpoint=False)
    if a <= c + 1e-9:
        s = 0.5 * (1 - np.cos(th))
        pts = f1[None, :] + s[:, None] * (f2 - f1)[None, :]
        return [[float(x), float(y)] for x, y in pts]
    b = math.sqrt(a * a - c * c)
    ctr = (f1 + f2) / 2.0
    ang = math.atan2(f2[1] - f1[1], f2[0] - f1[0]) if c > 0 else 0.0
    ca, sa = math.cos(ang), math.sin(ang)
    ex, ey = a * np.cos(th), b * np.sin(th)
    xs = ctr[0] + ca * ex - sa * ey
    ys = ctr[1] + sa * ex + ca * ey
    return [[float(x), float(y)] for x, y in zip(xs, ys)]


def bistatic_excess(x, y, a_xy, b_xy, z_t: float = TARGET_Z_M, z_p: float = PUCK_Z_M, rho: float = BODY_RADIUS_M):
    """Excess path (m) over the direct path of an echo off a body of radius rho centred at (x, y, z_t), for two
    pucks at height z_p. Vectorised over x, y. The near-surface term -2 rho cos(beta/2) is L08's cylinder model."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    dxa, dya = x - a_xy[0], y - a_xy[1]
    dxb, dyb = x - b_xy[0], y - b_xy[1]
    ha = np.maximum(np.hypot(dxa, dya), 1e-6)
    hb = np.maximum(np.hypot(dxb, dyb), 1e-6)
    dz = z_t - z_p
    r1 = np.sqrt(ha ** 2 + dz ** 2)
    r2 = np.sqrt(hb ** 2 + dz ** 2)
    cos_half = 0.5 * np.hypot(dxa / ha + dxb / hb, dya / ha + dyb / hb)
    d = math.hypot(a_xy[0] - b_xy[0], a_xy[1] - b_xy[1])
    return r1 + r2 - 2.0 * rho * cos_half - d
