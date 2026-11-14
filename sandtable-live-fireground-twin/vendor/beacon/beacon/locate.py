"""The Beacon locator: rings, signal-strength bands and bearings fused with the twin into honest geometry.

A grid Bayes posterior over the floor (0.25 m cells, numpy-vectorised) from:
  - UWB ranges (forward model `models.uwb_expected`: walls add bias and spread, a static per-link bias floors the sigma,
    the holder's body may block the link, rare positive outliers),
  - Bluetooth RSSI (`models.ble_expected_rssi`: log-distance path loss, wall loss, body loss, shadowing, and ONE common
    per-phone transmit offset shared by every puck),
  - the phone's bearing (a von Mises around `from_puck_deg` at that puck, plus a small blunder floor),
  - an optional room prior (the occupant says which suite they are in),
  - the twin: only cells inside the floor outline and not inside a wall; walls between each puck and each cell feed the
    forward models; a line-of-sight hint (los >= 0.8) down-weights cells behind walls from that puck.
Then `geometry_rules` turns the posterior into what the measurements honestly support: fix, candidates, ring, zone or none.
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict

import numpy as np

from . import models
from .twin import Twin, _pip

__all__ = ["Context", "estimate", "inside", "geometry_rules"]

geometry_rules: dict = {
    "hpd_mass": 0.90,
    "fix_sigma_max_m": {"uwb": 1.0, "uwb+bearing": 2.5, "ble": 2.5},
    "bearing_fix_max_arc_sigma_deg": 30.0,
    "max_candidates": 4,
    "min_component_mass": 0.03,
    "merge_components_m": 0.75,
    "ble_ring_max_sigma_m": 2.5,
    "ble_fix_min_pucks": 3,
    "collinear_tol_m": 0.3,
    "same_puck_m": 0.02,
    "los_threshold": 0.8,
    "los_wall_factor": 0.1,
    "los_body_factor": 0.1,
    "bearing_blunder_prob": 0.01,
    "wall_half_thickness_m": 0.075,
    "region_k_sigma": 2.0,
    "arc_bin_deg": 2.0,
    "prune_margin_m": 3.0,
    "prune_keep_m": 1.0,
    "impossible_log_penalty": 20.0,
    "text": [
        "Posterior: a grid Bayes posterior over cells of the floor (0.25 m). Twin on: only cells inside the floor outline and "
        "not inside a wall (0.075 m half thickness) are possible; walls between each puck and each cell are counted (door "
        "openings excepted) and feed the forward models. Twin off: free space, no rooms, no walls.",
        "UWB likelihood: Gaussian around models.uwb_expected (sigma never below the static per-link bias however many samples "
        "are averaged), a mixture with the body-blocked case (probability noise.uwb.body_block_prob) and a positive exponential "
        "outlier tail (outlier_prob; squared when the range is a median of 3 or more samples). Cells farther than max_range_m "
        "or behind more than max_walls walls are impossible (log penalty 20).",
        "Line-of-sight hint: when a UWB range carries los >= 0.8, cells with one or more walls between them and that puck are "
        "down-weighted by a factor 0.1, and the odds of a body block on that link by 0.1.",
        "Bluetooth likelihood: Gaussian in dB around models.ble_expected_rssi with the body loss moment-matched, and one "
        "common transmit offset (tx_spread_db) shared by all pucks (exact correlated Gaussian, Sherman-Morrison).",
        "Bearing: von Mises around from_puck_deg at the puck (kappa = 1 / sigma^2), mixed with a 1% uniform blunder floor.",
        "Take the 90% highest-density region (HPD) and split it into 8-connected components; components under 3% of the mass "
        "are ignored, components closer than 0.75 m are merged.",
        "fix: one component whose long-axis 1-sigma <= 1.0 m (UWB), <= 2.5 m when a bearing takes part (the bearing spreads "
        "the point along the ring; cov shows it) or for Bluetooth only. One UWB puck plus a bearing is a fix only when the "
        "posterior's angular 1-sigma around that puck is <= 30 degrees.",
        "candidates: 2 to 4 components that each pass the fix test; weights = their probability mass.",
        "Structure caps what the shape may claim: one UWB puck without a bearing is ALWAYS a ring (the twin may clip it to "
        "arcs but never makes it a point); Bluetooth only with fewer than 3 non-collinear pucks is a ring or a zone; "
        "otherwise the posterior decides (two pucks, or pucks in a line, give mirror candidates unless the twin removes one).",
        "ring: everything else with ranges: centre = the ranged puck with the smallest robust radial spread of the posterior; "
        "r_m = weighted median distance, sigma_m = 1.4826 x weighted MAD (never below the link's model sigma); arcs = the "
        "angular intervals (2-degree bins, CCW from +x, split at 0/360) covered by the HPD.",
        "zone: Bluetooth only and the ring's sigma_m > 2.5 m (and the twin is on): the most probable room, its probability, "
        "the signal band and the 5% to 95% distance quantiles from the strongest puck.",
        "none: heard but nothing usable (no usable ranges, or no possible cell).",
        "x and y are null unless geometry == 'fix'.",
        "confidence = posterior mass inside the published geometry: fix = the 2-sigma ellipse of cov (Mahalanobis <= 2); "
        "candidates = circles of 2 x sigma_m around each; ring = annulus r_m +- 2 sigma_m within the arcs; zone = the room. "
        "locate.inside(result, x, y) applies the same test to a point.",
        "hpd_area_m2 = area of the 90% HPD, always.",
        "pruned: a range-only estimate without the twin and without the bearing on the same cells; its compact components "
        "that no published point explains (within max(1 m, 2.5 sigma)) are listed with the reason the twin or bearing gives.",
        "debug.pair_bearing_deg: when exactly two pucks range the phone, the bearing (CCW from +x) of the phone from the "
        "midpoint of the two pucks on the side holding more mass; pair_bearing_alt_deg is its mirror across the baseline.",
    ],
}

_R = geometry_rules
_LOG2PI_HALF = 0.5 * math.log(2.0 * math.pi)
_HPD_VAR_CORR = 1.0 / 0.744      # variance of a 2-D Gaussian truncated to its 90% HPD is 0.744 of the full one
_WALL_CACHE_MAX = 64


# --- small vectorised geometry -------------------------------------------------------------------------------

def _pip_vec(X: np.ndarray, Y: np.ndarray, poly) -> np.ndarray:
    inside = np.zeros(X.shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[i - 1]
        if yi == yj:
            continue
        cond = (yi > Y) != (yj > Y)
        xint = (xj - xi) * (Y - yi) / (yj - yi) + xi
        inside ^= cond & (X < xint)
    return inside


def _seg_dist_vec(X, Y, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    if den == 0:
        return np.hypot(X - ax, Y - ay)
    t = np.clip(((X - ax) * dx + (Y - ay) * dy) / den, 0.0, 1.0)
    return np.hypot(X - (ax + t * dx), Y - (ay + t * dy))


def _merge_walls(walls: list[dict]) -> np.ndarray:
    """Merge collinear, overlapping axis-aligned wall segments (the twin lists shared walls once per room)."""
    horiz: dict[float, list] = {}
    vert: dict[float, list] = {}
    other = []
    for w in walls:
        (ax, ay), (bx, by) = w["a"], w["b"]
        if abs(ay - by) < 1e-6:
            horiz.setdefault(round(ay, 4), []).append((min(ax, bx), max(ax, bx)))
        elif abs(ax - bx) < 1e-6:
            vert.setdefault(round(ax, 4), []).append((min(ay, by), max(ay, by)))
        else:
            other.append((ax, ay, bx, by))

    def merge(iv):
        iv = sorted(iv)
        out = [list(iv[0])]
        for a, b in iv[1:]:
            if a <= out[-1][1] + 1e-6:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    segs = []
    for y, iv in horiz.items():
        segs += [(a, y, b, y) for a, b in merge(iv)]
    for x, iv in vert.items():
        segs += [(x, a, x, b) for a, b in merge(iv)]
    segs += other
    return np.array(segs, dtype=float).reshape(-1, 4)


def _slice_pad(arr: np.ndarray, j0: int, j1: int, i0: int, i1: int, fill) -> np.ndarray:
    ny, nx = arr.shape
    out = np.full((j1 - j0, i1 - i0), fill, dtype=arr.dtype)
    sj0, sj1, si0, si1 = max(j0, 0), min(j1, ny), max(i0, 0), min(i1, nx)
    if sj0 < sj1 and si0 < si1:
        out[sj0 - j0:sj1 - j0, si0 - i0:si1 - i0] = arr[sj0:sj1, si0:si1]
    return out


# --- context ---------------------------------------------------------------------------------------------------

class Context:
    """The grid of one floor, precomputed once (see module docstring)."""

    def __init__(self, twin, floor: str | None = None, cell_m: float = 0.25, use_twin: bool = True):
        if isinstance(twin, dict):
            twin = Twin(twin)
        if twin is None and use_twin:
            raise ValueError("use_twin=True needs a twin")
        self.twin: Twin | None = twin
        self.use_twin = bool(use_twin)
        self.cell = float(cell_m)
        if self.cell <= 0:
            raise ValueError("cell_m must be positive")
        self.floor = floor or (twin.exercise_floor if twin is not None else None)
        self.georef_bearing_deg = (twin.data.get("georef") or {}).get("bearing_deg") if twin is not None else None
        self.rooms: list[dict] = []
        self._wall_cache: OrderedDict = OrderedDict()
        if twin is not None:
            try:
                self.outline = twin.floor(self.floor)["outline"]
            except KeyError:
                raise ValueError(f"unknown floor {self.floor!r}") from None
            xs = [p[0] for p in self.outline]
            ys = [p[1] for p in self.outline]
            self.bbox = (min(xs), min(ys), max(xs), max(ys))
        else:
            self.outline = None
            self.bbox = (0.0, 0.0, 0.0, 0.0)
        self.x0, self.y0 = self.bbox[0], self.bbox[1]
        self.nx = max(1, int(math.ceil((self.bbox[2] - self.bbox[0]) / self.cell - 1e-9)))
        self.ny = max(1, int(math.ceil((self.bbox[3] - self.bbox[1]) / self.cell - 1e-9)))
        if not self.use_twin:
            self.valid = self.room_idx = self.X = self.Y = None
            self.wall_segs = np.zeros((0, 4))
            self.doors = np.zeros((0, 3))
            return
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.cell
        ys = self.y0 + (np.arange(self.ny) + 0.5) * self.cell
        self.X, self.Y = np.meshgrid(xs, ys)
        inside = _pip_vec(self.X, self.Y, self.outline)
        self.wall_segs = _merge_walls([w for w in twin.data.get("walls", []) if w.get("floor") == self.floor])
        self.doors = np.array([(d["at"][0], d["at"][1], d.get("width_m", 0.9) / 2.0)
                               for d in twin.data.get("doors", []) if d.get("floor") == self.floor], dtype=float).reshape(-1, 3)
        in_wall = np.zeros(self.X.shape, dtype=bool)
        for ax, ay, bx, by in self.wall_segs:
            in_wall |= _seg_dist_vec(self.X, self.Y, ax, ay, bx, by) < _R["wall_half_thickness_m"]
        for dx, dy, hw in self.doors:
            in_wall &= ~(np.hypot(self.X - dx, self.Y - dy) <= hw)
        self.valid = inside & ~in_wall
        self.rooms = [r for r in twin.data["rooms"] if r.get("floor") == self.floor]
        self.room_idx = np.full(self.X.shape, -1, dtype=np.int16)
        for k, r in enumerate(self.rooms):
            m = _pip_vec(self.X, self.Y, r["polygon"]) & (self.room_idx < 0)
            self.room_idx[m] = k
        self._room_k = {r["id"]: k for k, r in enumerate(self.rooms)}
        ri = self.room_idx[self.valid]
        self.room_cells = np.bincount(ri[ri >= 0], minlength=len(self.rooms))
        self.n_valid = int(self.valid.sum())
        # doors lying on each merged wall (door openings do not count as walls; margin 0.15 m as in Twin.walls_crossed)
        self._wall_doors = []
        for ax, ay, bx, by in self.wall_segs:
            near = [(dx, dy, hw + 0.15) for dx, dy, hw in self.doors
                    if _seg_dist_vec(np.array(dx), np.array(dy), ax, ay, bx, by) <= hw + 0.15]
            self._wall_doors.append(near)

    # walls between a puck and every cell of the floor grid (cached per puck position)
    def walls_from(self, px: float, py: float) -> np.ndarray:
        key = (round(float(px), 2), round(float(py), 2))
        hit = self._wall_cache.get(key)
        if hit is not None:
            self._wall_cache.move_to_end(key)
            return hit
        rx, ry = self.X - px, self.Y - py
        count = np.zeros(self.X.shape, dtype=np.int8)
        with np.errstate(divide="ignore", invalid="ignore"):
            for (ax, ay, bx, by), doors in zip(self.wall_segs, self._wall_doors):
                sx, sy = bx - ax, by - ay
                den = rx * sy - ry * sx
                qx, qy = ax - px, ay - py
                t = (qx * sy - qy * sx) / den
                u = (qx * ry - qy * rx) / den
                h = (np.abs(den) > 1e-12) & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)
                if doors and h.any():
                    hx, hy = px + t * rx, py + t * ry
                    for dx, dy, rad in doors:
                        h &= ~(np.hypot(hx - dx, hy - dy) <= rad)
                count += h
        self._wall_cache[key] = count
        if len(self._wall_cache) > _WALL_CACHE_MAX:
            self._wall_cache.popitem(last=False)
        return count

    def room_label(self, room_id: str | None) -> str | None:
        if room_id is None or self.twin is None:
            return None
        return self.twin.room(room_id)["label"]


# --- helpers ---------------------------------------------------------------------------------------------------

def _geo(ctx: Context, x: float, y: float):
    if ctx.twin is None:
        return None
    lat, lon = ctx.twin.to_geo(x, y)
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


def _wquantile(v: np.ndarray, w: np.ndarray, q):
    o = np.argsort(v)
    cw = np.cumsum(w[o])
    cw /= cw[-1]
    idx = np.clip(np.searchsorted(cw, q), 0, len(v) - 1)
    return v[o][idx]


def _label_runs(H: np.ndarray) -> tuple[np.ndarray, int]:
    """8-connected component labels of a boolean grid (run-length union-find; no scipy)."""
    ny, nx = H.shape
    pad = np.zeros((ny, nx + 2), dtype=np.int8)
    pad[:, 1:-1] = H
    d = np.diff(pad, axis=1)
    sr, sc = np.nonzero(d == 1)
    er, ec = np.nonzero(d == -1)          # end column exclusive
    n = len(sr)
    labels = np.full(H.shape, -1, dtype=np.int32)
    if n == 0:
        return labels, 0
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    row_start = np.searchsorted(sr, np.arange(ny + 1))
    for j in range(1, ny):
        a0, a1 = row_start[j - 1], row_start[j]
        b0, b1 = row_start[j], row_start[j + 1]
        if a0 == a1 or b0 == b1:
            continue
        ia = a0
        for ib in range(b0, b1):
            s, e = sc[ib], ec[ib]
            while ia < a1 and ec[ia] < s:      # previous-row run ends before s - 1 (8-connectivity: ec >= s touches)
                ia += 1
            k = ia
            while k < a1 and sc[k] <= e:
                ra, rb = find(k), find(ib)
                if ra != rb:
                    parent[rb] = ra
                k += 1
    roots = {}
    for r in range(n):
        root = find(r)
        lab = roots.setdefault(root, len(roots))
        labels[sr[r], sc[r]:ec[r]] = lab
    return labels, len(roots)


def _components(H, P, X, Y, cell):
    labels, k = _label_runs(H)
    if k == 0:
        return labels, []
    lab = labels[H]
    w = P[H]
    x = X[H]
    y = Y[H]
    m = np.bincount(lab, w, k)
    safe = np.where(m > 0, m, 1.0)
    mx = np.bincount(lab, w * x, k) / safe
    my = np.bincount(lab, w * y, k) / safe
    sxx = np.bincount(lab, w * x * x, k) / safe - mx * mx
    syy = np.bincount(lab, w * y * y, k) / safe - my * my
    sxy = np.bincount(lab, w * x * y, k) / safe - mx * my
    q = cell * cell / 12.0
    comps = []
    for i in range(k):
        C = np.array([[sxx[i], sxy[i]], [sxy[i], syy[i]]]) * _HPD_VAR_CORR + np.eye(2) * q
        C = (C + C.T) / 2
        ev = np.linalg.eigvalsh(C)
        comps.append({"label": i, "labels": [i], "mass": float(m[i]), "x": float(mx[i]), "y": float(my[i]), "cov": C,
                      "sigma_long": float(math.sqrt(max(ev[-1], 0.0)))})
    return labels, comps


def _merge_close(comps, labels, H, P, X, Y, cell):
    """Merge components whose centres are closer than merge_components_m."""
    n = len(comps)
    if n < 2:
        return comps
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    changed = False
    for a in range(n):
        for b in range(a + 1, n):
            if math.hypot(comps[a]["x"] - comps[b]["x"], comps[a]["y"] - comps[b]["y"]) < _R["merge_components_m"]:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
                    changed = True
    if not changed:
        return comps
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    q = cell * cell / 12.0
    for members in groups.values():
        if len(members) == 1:
            out.append(comps[members[0]])
            continue
        labs = [lb for i in members for lb in comps[i]["labels"]]
        sel = H & np.isin(labels, labs)
        w = P[sel]
        x, y = X[sel], Y[sel]
        m = w.sum()
        mx, my = (w * x).sum() / m, (w * y).sum() / m
        sxx = (w * (x - mx) ** 2).sum() / m
        syy = (w * (y - my) ** 2).sum() / m
        sxy = (w * (x - mx) * (y - my)).sum() / m
        C = np.array([[sxx, sxy], [sxy, syy]]) * _HPD_VAR_CORR + np.eye(2) * q
        ev = np.linalg.eigvalsh(C)
        out.append({"label": labs[0], "labels": labs, "mass": float(m), "x": float(mx), "y": float(my), "cov": C,
                    "sigma_long": float(math.sqrt(max(ev[-1], 0.0)))})
    return out


def _refine(comp, P, X, Y, H, labels, cell):
    """Moments of one component over every cell within Mahalanobis 3.5 of its HPD estimate (no truncation bias),
    excluding cells of other components' HPD."""
    C = comp["cov"]
    Ci = np.linalg.inv(C + np.eye(2) * 1e-9)
    dx, dy = X - comp["x"], Y - comp["y"]
    m2 = Ci[0, 0] * dx * dx + 2 * Ci[0, 1] * dx * dy + Ci[1, 1] * dy * dy
    sel = (m2 <= 3.5 ** 2) & ~(H & ~np.isin(labels, comp["labels"])) & (P > 0)
    w = P[sel]
    m = w.sum()
    if m <= 0:
        return comp["x"], comp["y"], C
    x, y = X[sel], Y[sel]
    mx, my = (w * x).sum() / m, (w * y).sum() / m
    sxx = (w * (x - mx) ** 2).sum() / m
    syy = (w * (y - my) ** 2).sum() / m
    sxy = (w * (x - mx) * (y - my)).sum() / m
    q = cell * cell / 12.0
    C2 = np.array([[sxx + q, sxy], [sxy, syy + q]])
    return float(mx), float(my), C2


def _supersample_mass(P, X, Y, cell, pred) -> float:
    """Posterior mass inside a region, each cell split into 4 x 4 sub-points (uniform density within a cell)."""
    sel = P > P.max() * 1e-9
    if not sel.any():
        return 0.0
    w = P[sel]
    x, y = X[sel], Y[sel]
    off = ((np.arange(4) + 0.5) / 4.0 - 0.5) * cell
    ox, oy = np.meshgrid(off, off)
    xs = x[:, None] + ox.ravel()[None, :]
    ys = y[:, None] + oy.ravel()[None, :]
    frac = pred(xs, ys).mean(axis=1)
    return float((w * frac).sum())


def _in_arcs(theta_deg, arcs) -> np.ndarray:
    t = np.mod(theta_deg, 360.0)
    ok = np.zeros(np.shape(t), dtype=bool)
    for a0, a1 in arcs:
        ok |= (t >= a0) & (t <= a1)
    return ok


def _pred_for(result: dict):
    g = result.get("geometry")
    k = _R["region_k_sigma"]
    if g == "fix":
        c = result["cov"]
        C = np.array([[c[0], c[1]], [c[1], c[2]]])
        Ci = np.linalg.inv(C + np.eye(2) * 1e-12)
        x0, y0 = result["x"], result["y"]

        def pred(x, y):
            dx, dy = x - x0, y - y0
            return Ci[0, 0] * dx * dx + 2 * Ci[0, 1] * dx * dy + Ci[1, 1] * dy * dy <= k * k
        return pred
    if g == "candidates":
        cands = [(c["x"], c["y"], k * c["sigma_m"]) for c in result["candidates"]]

        def pred(x, y):
            ok = np.zeros(np.shape(x), dtype=bool)
            for cx, cy, rr in cands:
                ok |= np.hypot(x - cx, y - cy) <= rr
            return ok
        return pred
    if g == "ring":
        r = result["ring"]
        cx, cy, rm, sg, arcs = r["cx"], r["cy"], r["r_m"], r["sigma_m"], r["arcs"]

        def pred(x, y):
            d = np.hypot(x - cx, y - cy)
            th = np.degrees(np.arctan2(y - cy, x - cx))
            return (np.abs(d - rm) <= k * sg) & _in_arcs(th, arcs)
        return pred
    if g == "zone":
        poly = result["zone"]["polygon"]

        def pred(x, y):
            return _pip_vec(np.asarray(x, dtype=float), np.asarray(y, dtype=float), poly)
        return pred
    return None


def inside(result: dict, x: float, y: float) -> bool:
    """True when the point lies inside the published geometry, with the same regions `confidence` uses:
    fix = 2-sigma ellipse of cov; candidates = circles of 2 x sigma_m; ring = annulus r_m +- 2 sigma_m within arcs;
    zone = the room polygon; none = False."""
    pred = _pred_for(result)
    if pred is None:
        return False
    return bool(np.asarray(pred(np.array([float(x)]), np.array([float(y)])))[0])


def _arcs_from(theta_deg: np.ndarray, hw_deg: np.ndarray) -> list[list[float]]:
    bw = _R["arc_bin_deg"]
    nb = int(round(360.0 / bw))
    if theta_deg.size == 0:
        return []
    lo = np.floor((theta_deg - hw_deg) / bw).astype(int)
    hi = np.floor((theta_deg + hw_deg) / bw).astype(int)
    if np.any(hi - lo + 1 >= nb):
        return [[0.0, 360.0]]
    lo_m, hi_m = lo % nb, hi % nb
    wrap = lo_m > hi_m
    diff = np.zeros(nb + 2)
    np.add.at(diff, lo_m[~wrap], 1)
    np.add.at(diff, hi_m[~wrap] + 1, -1)
    np.add.at(diff, lo_m[wrap], 1)
    diff[0] += wrap.sum()
    np.add.at(diff, hi_m[wrap] + 1, -1)
    occ = np.cumsum(diff[:nb]) > 0
    occ = occ | (np.roll(occ, 1) & np.roll(occ, -1))          # fill single-bin gaps
    if occ.all():
        return [[0.0, 360.0]]
    arcs = []
    k = 0
    while k < nb:
        if occ[k]:
            s = k
            while k < nb and occ[k]:
                k += 1
            arcs.append([round(s * bw, 1), round(k * bw, 1)])
        else:
            k += 1
    return arcs


# --- likelihoods -----------------------------------------------------------------------------------------------

def _uwb_ll(D, W, r, noise, puck_sigma, cell, use_walls):
    u = noise["uwb"]
    n = max(1, int(r.get("n") or 1))
    extra = puck_sigma ** 2 + cell * cell / 12.0
    m0, s0 = models.uwb_expected(D, W, noise, body=False, n_samples=n)
    m1, s1 = models.uwb_expected(D, W, noise, body=True, n_samples=n)
    s0 = np.sqrt(np.asarray(s0) ** 2 + extra)
    s1 = np.sqrt(np.asarray(s1) ** 2 + extra)
    los = r.get("los")
    los_hi = los is not None and float(los) >= _R["los_threshold"]
    pb = float(u.get("body_block_prob", 0.0))
    if los_hi and pb > 0:
        odds = pb / max(1e-12, 1 - pb) * _R["los_body_factor"]
        pb = odds / (1 + odds)
    dm = float(r["d_m"])
    z0 = (dm - m0) / s0
    lg = -0.5 * z0 * z0 - np.log(s0) - _LOG2PI_HALF + math.log(max(1e-12, 1 - pb))
    if pb > 0:
        z1 = (dm - m1) / s1
        lg = np.logaddexp(lg, -0.5 * z1 * z1 - np.log(s1) - _LOG2PI_HALF + math.log(pb))
    po = float(u["outlier_prob"])
    if n >= 3:
        po = po * po
    if po > 0:
        om = float(u["outlier_m"])
        ex = dm - m0
        lo = math.log(po / om) - np.clip(ex, 0.0, None) / om
        lo = np.where(ex < -2.0 * s0, -1e3, lo)
        L = np.logaddexp(math.log(1 - po) + lg, lo)
    else:
        L = lg
    pen = _R["impossible_log_penalty"]
    L = np.where(D > u["max_range_m"], L - pen, L)
    if use_walls:
        L = np.where(W > u["max_walls"], L - pen, L)
        if los_hi:
            L = np.where(W >= 1, L + math.log(_R["los_wall_factor"]), L)
    return L


def _ble_terms(D, W, r, noise):
    """Residual and per-link variance (without the common transmit offset) of one Bluetooth reading."""
    b = noise["ble"]
    n = max(1, int(r.get("n") or 1))
    tx = r.get("tx_dbm")
    pb = float(noise["uwb"].get("body_block_prob", 0.0))
    mean = models.ble_expected_rssi(D, W, noise, tx_dbm=tx, n_samples=n, body=False)[0] - pb * b["body_loss_db"]
    var = b["shadow_sigma_db"] ** 2 + b["fast_fading_sigma_db"] ** 2 / n + pb * (1 - pb) * b["body_loss_db"] ** 2
    return float(r["rssi"]) - mean, var


def _loglik(ctx, X, Y, pk, ranges, noise, walls_of, use_walls):
    L = np.zeros(X.shape)
    ble_A = ble_B = None
    ble_c = 0.0
    tau2 = noise["ble"]["tx_spread_db"] ** 2
    for r in ranges:
        p = pk[r["puck"]]
        D = np.hypot(X - p["x"], Y - p["y"])
        W = walls_of(p) if use_walls else 0
        if r["kind"] == "uwb":
            L += _uwb_ll(D, W, r, noise, p["sigma_m"], ctx.cell, use_walls)
        else:
            e, var = _ble_terms(D, W, r, noise)
            ble_A = e * e / var if ble_A is None else ble_A + e * e / var
            ble_B = e / var if ble_B is None else ble_B + e / var
            ble_c += 1.0 / var
            L -= 0.5 * math.log(var)
    if ble_A is not None:
        L += -0.5 * (ble_A - tau2 * ble_B * ble_B / (1.0 + tau2 * ble_c)) - 0.5 * math.log(1.0 + tau2 * ble_c)
    return L


def _hpd(P: np.ndarray, mass: float) -> np.ndarray:
    flat = P.ravel()
    nz = np.flatnonzero(flat > flat.max() * 1e-12)
    o = nz[np.argsort(-flat[nz], kind="stable")]
    cs = np.cumsum(flat[o])
    k = int(np.searchsorted(cs, mass * cs[-1])) + 1
    H = np.zeros(flat.shape, dtype=bool)
    H[o[:k]] = True
    return H.reshape(P.shape)


def _collinear(pts: list[tuple[float, float]]) -> bool:
    if len(pts) < 3:
        return True
    a = np.array(pts, dtype=float)
    a = a - a.mean(axis=0)
    s = np.linalg.svd(a, compute_uv=False)
    # RMS distance from the best-fit line
    return float(s[-1] / math.sqrt(len(pts))) < _R["collinear_tol_m"]


def _distinct(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        if all(math.hypot(p[0] - q[0], p[1] - q[1]) > _R["same_puck_m"] for q in out):
            out.append(p)
    return out


# --- the estimate ----------------------------------------------------------------------------------------------

def _validate(ctx, pucks, ranges, bearing, prior, noise):
    pk = {}
    for p in pucks or []:
        try:
            pid = p["id"]
            x, y = float(p["x"]), float(p["y"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"bad puck {p!r}") from None
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(f"bad puck position {p!r}")
        s = p.get("sigma_m")
        pk[pid] = {"id": pid, "x": x, "y": y,
                   "sigma_m": float(s) if s is not None else float(noise["puck"]["position_sigma_m"])}
    rs = []
    for r in ranges or []:
        if r.get("puck") not in pk:
            raise ValueError(f"range references unknown puck {r.get('puck')!r}")
        kind = r.get("kind")
        if kind == "uwb":
            d = r.get("d_m")
            if d is None or not math.isfinite(float(d)) or float(d) < 0:
                raise ValueError(f"bad UWB range {r!r}")
        elif kind == "ble":
            v = r.get("rssi")
            if v is None or not math.isfinite(float(v)):
                raise ValueError(f"bad Bluetooth reading {r!r}")
            if r.get("n") is not None and int(r["n"]) <= 0:
                continue                      # heard, no usable sample
        else:
            raise ValueError(f"unknown range kind {kind!r}")
        rs.append(r)
    if bearing is not None:
        if bearing.get("puck") not in pk:
            raise ValueError(f"bearing references unknown puck {bearing.get('puck')!r}")
        if bearing.get("from_puck_deg") is None or not math.isfinite(float(bearing["from_puck_deg"])):
            raise ValueError(f"bad bearing {bearing!r}")
    if prior is not None and ctx.use_twin:
        if prior.get("room") not in getattr(ctx, "_room_k", {}):
            raise ValueError(f"prior references unknown room {prior.get('room')!r} on floor {ctx.floor!r}")
    return pk, rs


def _empty(method: str, why: str, t0: float) -> dict:
    return {"method": method, "geometry": "none", "x": None, "y": None, "sigma_m": None, "cov": None, "confidence": 0.0,
            "room": None, "room_label": None, "room_prob": None, "node": None, "geo": None,
            "ring": None, "candidates": [], "pruned": [], "zone": None, "hpd_area_m2": None,
            "debug": {"n_components": 0, "reason": why, "ms": round((time.perf_counter() - t0) * 1000, 2)}}


def estimate(ctx: Context, pucks: list[dict], ranges: list[dict], bearing: dict | None = None, prior: dict | None = None,
             noise: dict | None = None, want_heat: bool = False) -> dict:
    """Grid Bayes estimate of one phone; returns the spatial part of a BEACON_FEED Phone (see geometry_rules)."""
    t0 = time.perf_counter()
    noise = models.noise_or_default(noise)
    pk, ranges = _validate(ctx, pucks, ranges, bearing, prior, noise)
    uwb = [r for r in ranges if r["kind"] == "uwb"]
    ble = [r for r in ranges if r["kind"] == "ble"]
    if not ranges:
        return _empty("ble-presence", "no usable range", t0)
    method = ("uwb-twr+bearing" if bearing is not None else "uwb-twr") if uwb else "ble-rssi"
    cell = ctx.cell
    u = noise["uwb"]

    # ---- region: cells within reach of every measurement (true distance <= measured + 5 sigma for UWB: biases are positive)
    lo_x, lo_y, hi_x, hi_y = -math.inf, -math.inf, math.inf, math.inf
    for r in ranges:
        p = pk[r["puck"]]
        if r["kind"] == "uwb":
            reach = float(r["d_m"]) + 5.0 * math.sqrt(models.uwb_link_sigma(noise, r.get("n") or 1) ** 2 + p["sigma_m"] ** 2) + cell
            reach = min(reach, u["max_range_m"] + cell)
        else:
            _, _, dhi = models.ble_distance(float(r["rssi"]), noise, r.get("tx_dbm"), r.get("n") or 1)
            sig_db = models.ble_expected_rssi(1.0, 0, noise, r.get("tx_dbm"), r.get("n") or 1)[1]
            reach = min(dhi * 10.0 ** (2.5 * sig_db / (10.0 * noise["ble"]["n_los"])), 60.0)
        lo_x, lo_y = max(lo_x, p["x"] - reach), max(lo_y, p["y"] - reach)
        hi_x, hi_y = min(hi_x, p["x"] + reach), min(hi_y, p["y"] + reach)
    if lo_x >= hi_x or lo_y >= hi_y:          # inconsistent beyond 5 sigma: fall back to the union of the pucks' reach
        xs = [pk[r["puck"]]["x"] for r in ranges]
        ys = [pk[r["puck"]]["y"] for r in ranges]
        R = u["max_range_m"]
        lo_x, lo_y, hi_x, hi_y = min(xs) - R, min(ys) - R, max(xs) + R, max(ys) + R
    if ctx.use_twin:
        # cells outside the building matter only for listing pruned points (several pucks, or a bearing)
        n_pk = len({r["puck"] for r in ranges})
        mg = _R["prune_margin_m"] if (n_pk >= 2 or bearing is not None) else 0.0
        lo_x, lo_y = max(lo_x, ctx.bbox[0] - mg), max(lo_y, ctx.bbox[1] - mg)
        hi_x, hi_y = min(hi_x, ctx.bbox[2] + mg), min(hi_y, ctx.bbox[3] + mg)
    else:
        R = u["max_range_m"]
        lo_x = max(lo_x, min(p["x"] for p in pk.values()) - R)
        lo_y = max(lo_y, min(p["y"] for p in pk.values()) - R)
        hi_x = min(hi_x, max(p["x"] for p in pk.values()) + R)
        hi_y = min(hi_y, max(p["y"] for p in pk.values()) + R)
    i0 = int(math.floor((lo_x - ctx.x0) / cell))
    i1 = max(i0 + 1, int(math.ceil((hi_x - ctx.x0) / cell)))
    j0 = int(math.floor((lo_y - ctx.y0) / cell))
    j1 = max(j0 + 1, int(math.ceil((hi_y - ctx.y0) / cell)))
    xs = ctx.x0 + (np.arange(i0, i1) + 0.5) * cell
    ys = ctx.y0 + (np.arange(j0, j1) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    if ctx.use_twin:
        valid = _slice_pad(ctx.valid, j0, j1, i0, i1, False)
        room = _slice_pad(ctx.room_idx, j0, j1, i0, i1, -1)

        def walls_of(p):
            return _slice_pad(ctx.walls_from(p["x"], p["y"]), j0, j1, i0, i1, 0)
    else:
        valid = np.ones(X.shape, dtype=bool)
        room = None
        walls_of = None

    # ---- posterior
    L = _loglik(ctx, X, Y, pk, ranges, noise, walls_of, ctx.use_twin)
    if bearing is not None:
        L = L + _bearing_ll(X, Y, pk, bearing, noise)
    if prior is not None and ctx.use_twin and ctx.n_valid > 0:
        k = ctx._room_k[prior["room"]]
        wgt = min(max(float(prior.get("weight", 0.7)), 0.0), 1.0)
        n_in = max(1, int(ctx.room_cells[k]))
        n_out = max(1, ctx.n_valid - n_in)
        pin, pout = max(wgt / n_in, 1e-300), max((1 - wgt) / n_out, 1e-300)
        L = L + np.where(room == k, math.log(pin), math.log(pout))
    if not valid.any():
        return _empty(method, "no possible cell within reach of the measurements", t0)
    Lv = np.where(valid, L, -np.inf)
    mx = Lv.max()
    if not np.isfinite(mx):
        return _empty(method, "no possible cell within reach of the measurements", t0)
    P = np.exp(Lv - mx)
    P /= P.sum()
    H = _hpd(P, _R["hpd_mass"])
    hpd_area = float(H.sum()) * cell * cell
    room_mass = None
    if ctx.use_twin:
        ri = room[valid]
        room_mass = np.bincount(ri[ri >= 0], P[valid][ri >= 0], minlength=len(ctx.rooms))

    # ---- structure: what the measurement geometry can support at most
    uwb_pts = _distinct([(pk[r["puck"]]["x"], pk[r["puck"]]["y"]) for r in uwb])
    ble_pts = _distinct([(pk[r["puck"]]["x"], pk[r["puck"]]["y"]) for r in ble])
    n_uwb, n_ble = len(uwb_pts), len(ble_pts)
    has_bearing = bearing is not None
    if n_uwb >= 2 or (n_uwb >= 1 and has_bearing):
        point_ok, cap = True, "points"
    elif n_uwb == 1:
        point_ok, cap = False, "ring: one UWB puck without a bearing"
    elif has_bearing:
        point_ok, cap = True, "points"
    elif n_ble >= _R["ble_fix_min_pucks"] and not _collinear(ble_pts):
        point_ok, cap = True, "points"
    else:
        point_ok, cap = False, "ring or zone: Bluetooth only from fewer than 3 non-collinear pucks"
    if has_bearing:
        fix_max = _R["fix_sigma_max_m"]["uwb+bearing"]
    elif n_uwb:
        fix_max = _R["fix_sigma_max_m"]["uwb"]
    else:
        fix_max = _R["fix_sigma_max_m"]["ble"]

    labels, comps = _components(H, P, X, Y, cell)
    sig = [c for c in comps if c["mass"] >= _R["min_component_mass"]]
    sig = _merge_close(sig, labels, H, P, X, Y, cell)
    sig.sort(key=lambda c: -c["mass"])
    compact = bool(sig) and all(c["sigma_long"] <= fix_max for c in sig)
    debug = {"n_components": len(sig), "cap": cap, "n_uwb_pucks": n_uwb, "n_ble_pucks": n_ble, "bearing": has_bearing,
             "cells": int(X.size), "fix_sigma_max_m": fix_max,
             "components": [{"x": round(c["x"], 2), "y": round(c["y"], 2), "mass": round(c["mass"], 3),
                             "sigma_long_m": round(c["sigma_long"], 2)} for c in sig[:6]]}
    if room_mass is not None:
        top = np.argsort(-room_mass)[:5]
        debug["room_mass"] = {ctx.rooms[i]["id"]: round(float(room_mass[i]), 3) for i in top if room_mass[i] > 0.005}

    geometry = None
    if point_ok and compact and len(sig) == 1:
        geometry = "fix"
        if n_uwb == 1 and has_bearing and n_uwb < 2:
            up = pk[uwb[0]["puck"]]
            sel = H & np.isin(labels, sig[0]["labels"])
            th = np.arctan2(Y[sel] - up["y"], X[sel] - up["x"])
            w = P[sel]
            Rlen = math.hypot((w * np.cos(th)).sum(), (w * np.sin(th)).sum()) / w.sum()
            ang_sd = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(max(Rlen, 1e-12)))) * _HPD_VAR_CORR ** 0.5)
            debug["arc_sigma_deg"] = round(ang_sd, 1)
            if ang_sd > _R["bearing_fix_max_arc_sigma_deg"]:
                geometry = None
                debug["reason"] = "bearing leaves an arc wider than the fix limit"
    elif point_ok and compact and 2 <= len(sig) <= _R["max_candidates"]:
        geometry = "candidates"

    out = {"method": method, "geometry": None, "x": None, "y": None, "sigma_m": None, "cov": None, "confidence": 0.0,
           "room": None, "room_label": None, "room_prob": None, "node": None, "geo": None,
           "ring": None, "candidates": [], "pruned": [], "zone": None, "hpd_area_m2": round(hpd_area, 2), "debug": debug}

    def room_of(rid_mass):
        if room_mass is None or not len(room_mass) or room_mass.max() <= 0:
            return None, None, None
        k = int(np.argmax(rid_mass))
        rid = ctx.rooms[k]["id"]
        return rid, ctx.rooms[k]["label"], round(float(rid_mass[k]), 3)

    published_pts = []
    if geometry == "fix":
        x, y, C = _refine(sig[0], P, X, Y, H, labels, cell)
        ev = np.linalg.eigvalsh(C)
        out.update({"geometry": "fix", "x": round(x, 2), "y": round(y, 2), "sigma_m": round(math.sqrt(max(ev[-1], 0.0)), 2),
                    "cov": [round(float(C[0, 0]), 4), round(float(C[0, 1]), 4), round(float(C[1, 1]), 4)]})
        out["room"], out["room_label"], out["room_prob"] = room_of(room_mass)
        if ctx.twin is not None:
            out["node"] = ctx.twin.locate(x, y, ctx.floor)["node"]
            out["geo"] = _geo(ctx, x, y)
        published_pts.append((x, y, out["sigma_m"]))
    elif geometry == "candidates":
        cands = []
        tot = sum(c["mass"] for c in sig)
        for c in sig:
            x, y, C = _refine(c, P, X, Y, H, labels, cell)
            ev = np.linalg.eigvalsh(C)
            s = math.sqrt(max(ev[-1], 0.0))
            rid = rl = None
            if room is not None:
                sel = H & np.isin(labels, c["labels"]) & (room >= 0)
                if sel.any():
                    rm = np.bincount(room[sel], P[sel], minlength=len(ctx.rooms))
                    rid, rl = ctx.rooms[int(np.argmax(rm))]["id"], ctx.rooms[int(np.argmax(rm))]["label"]
            cands.append({"x": round(x, 2), "y": round(y, 2), "sigma_m": round(s, 2), "weight": round(c["mass"] / tot, 3),
                          "room": rid, "room_label": rl, "geo": _geo(ctx, x, y)})
            published_pts.append((x, y, s))
        out.update({"geometry": "candidates", "candidates": cands})
        rooms_c = {c["room"] for c in cands}
        if len(rooms_c) == 1 and None not in rooms_c and room_mass is not None:
            rid = rooms_c.pop()
            out["room"], out["room_label"] = rid, ctx.room_label(rid)
            out["room_prob"] = round(float(room_mass[ctx._room_k[rid]]), 3)
    else:
        # ---- ring or zone
        cand_pucks = uwb if uwb else ble
        best = None
        sel = P > P.max() * 1e-9
        w = P[sel]
        for r in cand_pucks:
            p = pk[r["puck"]]
            d = np.hypot(X[sel] - p["x"], Y[sel] - p["y"])
            med = float(_wquantile(d, w, 0.5))
            mad = float(_wquantile(np.abs(d - med), w, 0.5)) * 1.4826
            if best is None or mad < best[2]:
                best = (r, med, mad)
        r, rm, sr = best
        p = pk[r["puck"]]
        if r["kind"] == "uwb":
            sr = max(sr, math.sqrt(models.uwb_link_sigma(noise, r.get("n") or 1) ** 2 + p["sigma_m"] ** 2))
        if not uwb and ctx.use_twin and sr > _R["ble_ring_max_sigma_m"]:
            # zone: strongest puck
            rb = max(ble, key=lambda q: float(q["rssi"]))
            pb_ = pk[rb["puck"]]
            d = np.hypot(X[sel] - pb_["x"], Y[sel] - pb_["y"])
            q05, q95 = _wquantile(d, w, [0.05, 0.95])
            rid, rl, rp = room_of(room_mass)
            poly = ctx.twin.room(rid)["polygon"] if rid else []
            cx, cy = ctx.twin.room_centre(rid) if rid else (pb_["x"], pb_["y"])
            out.update({"geometry": "zone", "room": rid, "room_label": rl, "room_prob": rp,
                        "zone": {"room": rid, "room_label": rl, "prob": rp, "band": models.cold_band(float(rb["rssi"])),
                                 "puck": rb["puck"], "r_min_m": round(float(q05), 1), "r_max_m": round(float(q95), 1),
                                 "polygon": [list(map(float, q)) for q in poly], "geo": _geo(ctx, cx, cy)}})
        else:
            Hs = H
            d = np.hypot(X[Hs] - p["x"], Y[Hs] - p["y"])
            th = np.degrees(np.arctan2(Y[Hs] - p["y"], X[Hs] - p["x"])) % 360.0
            keep = d > 0.5 * cell
            hw = np.degrees(np.arctan2(0.75 * cell, np.maximum(d[keep], 1e-6)))
            arcs = _arcs_from(th[keep], hw)
            if (~keep).any():
                arcs = [[0.0, 360.0]]
            out.update({"geometry": "ring",
                        "ring": {"puck": r["puck"], "cx": round(p["x"], 2), "cy": round(p["y"], 2), "r_m": round(rm, 2),
                                 "sigma_m": round(sr, 2), "arcs": arcs, "kind": r["kind"], "geo": _geo(ctx, p["x"], p["y"])}})
    # ---- confidence = posterior mass inside the published geometry
    if out["geometry"] == "zone":
        out["confidence"] = out["room_prob"] or 0.0
    else:
        pred = _pred_for(out)
        out["confidence"] = round(min(1.0, _supersample_mass(P, X, Y, cell, pred)), 3) if pred else 0.0

    # ---- pruned: range-only, twin-free, bearing-free solution; its points that nothing published explains
    if out["geometry"] in ("fix", "candidates") and (n_uwb >= 2 or n_ble >= 3 or has_bearing):
        Lf = _loglik(ctx, X, Y, pk, ranges, noise, None, False)
        Pf = np.exp(Lf - Lf.max())
        Pf /= Pf.sum()
        Hf = _hpd(Pf, _R["hpd_mass"])
        labf, compf = _components(Hf, Pf, X, Y, cell)
        compf = [c for c in compf if c["mass"] >= _R["min_component_mass"]]
        compf = _merge_close(compf, labf, Hf, Pf, X, Y, cell)
        for c in sorted(compf, key=lambda c: -c["mass"]):
            if c["sigma_long"] > max(fix_max, _R["fix_sigma_max_m"]["uwb"]):
                continue
            explained = any(math.hypot(c["x"] - x, c["y"] - y) <= max(_R["prune_keep_m"], 2.5 * c["sigma_long"], 2.5 * s)
                            for x, y, s in published_pts)
            if not explained:
                out["pruned"].append({"x": round(c["x"], 2), "y": round(c["y"], 2),
                                      "reason": _prune_reason(ctx, pk, ranges, bearing, prior, c["x"], c["y"], noise)})

    # ---- two pucks: bearing of the phone from their midpoint (evaluation of short baselines)
    rp_ids = list(dict.fromkeys(r["puck"] for r in ranges))
    pts2 = _distinct([(pk[i]["x"], pk[i]["y"]) for i in rp_ids])
    if len(pts2) == 2:
        (ax, ay), (bx, by) = pts2
        mxm, mym = (ax + bx) / 2, (ay + by) / 2
        beta = math.atan2(by - ay, bx - ax)
        side = (bx - ax) * (Y - mym) - (by - ay) * (X - mxm)
        th = np.arctan2(Y - mym, X - mxm)
        m_pos, m_neg = float(P[side > 0].sum()), float(P[side < 0].sum())
        msk = side > 0 if m_pos >= m_neg else side < 0
        w = P[msk]
        if w.sum() > 0:
            c_, s_ = (w * np.cos(th[msk])).sum() / w.sum(), (w * np.sin(th[msk])).sum() / w.sum()
            main = math.degrees(math.atan2(s_, c_)) % 360.0
            Rl = math.hypot(c_, s_)
            debug["pair_bearing_deg"] = round(main, 1)
            debug["pair_bearing_alt_deg"] = round((2 * math.degrees(beta) - main) % 360.0, 1)
            debug["pair_bearing_sigma_deg"] = round(math.degrees(math.sqrt(max(0.0, -2 * math.log(max(Rl, 1e-12))))), 1)
            debug["pair_side_mass"] = round(max(m_pos, m_neg), 3)
            debug["pair_baseline_m"] = round(math.hypot(bx - ax, by - ay), 3)

    if want_heat:
        out["heat"] = _heat(P, xs, ys, cell)
    debug["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


def _bearing_ll(X, Y, pk, bearing, noise):
    p = pk[bearing["puck"]]
    sig = math.radians(float(bearing.get("sigma_deg") or noise["bearing"]["sigma_deg"]))
    kappa = 1.0 / max(sig * sig, 1e-6)
    th = np.arctan2(Y - p["y"], X - p["x"])
    dth = th - math.radians(float(bearing["from_puck_deg"]))
    eps = _R["bearing_blunder_prob"]
    vm = (1 - eps) * math.sqrt(kappa / (2 * math.pi)) * np.exp(kappa * (np.cos(dth) - 1.0))
    return np.log(vm + eps / (2 * math.pi))


def _heat(P, xs, ys, cell, k: int = 2) -> dict:
    ny, nx = P.shape
    py, px = (-ny) % k, (-nx) % k
    Q = np.pad(P, ((0, py), (0, px)))
    Q = Q.reshape(Q.shape[0] // k, k, Q.shape[1] // k, k).sum(axis=(1, 3))
    m = Q.max()
    Q = Q / m if m > 0 else Q
    return {"origin": [round(float(xs[0] - cell / 2), 3), round(float(ys[0] - cell / 2), 3)], "cell_m": cell * k,
            "nx": int(Q.shape[1]), "ny": int(Q.shape[0]), "values": [round(float(v), 3) for v in Q.ravel()]}


def _wall_between(ctx, x, y):
    """Labels of the rooms on both sides of the wall nearest to (x, y), and the distance to it."""
    best = None
    for ax, ay, bx, by in ctx.wall_segs:
        d = float(_seg_dist_vec(np.array(x), np.array(y), ax, ay, bx, by))
        if best is None or d < best[0]:
            best = (d, ax, ay, bx, by)
    if best is None:
        return None, None, math.inf
    d, ax, ay, bx, by = best
    L = math.hypot(bx - ax, by - ay) or 1.0
    nx_, ny_ = -(by - ay) / L, (bx - ax) / L
    labels = []
    for s in (1, -1):
        qx, qy = x + s * 0.3 * nx_, y + s * 0.3 * ny_
        if not _pip((qx, qy), ctx.outline):
            labels.append(None)
            continue
        labels.append(ctx.twin.locate(qx, qy, ctx.floor)["room_label"])
    return labels[0], labels[1], d


def _prune_reason(ctx, pk, ranges, bearing, prior, x, y, noise) -> str:
    u = noise["uwb"]
    if ctx.use_twin:
        if not _pip((x, y), ctx.outline):
            return "outside the building"
        a, b, dw = _wall_between(ctx, x, y)
        if dw < 0.2:
            if a and b and a != b:
                return f"inside the wall between {a} and {b}"
            if a or b:
                return f"inside the outer wall of {a or b}"
            return "inside a wall"
        label = ctx.twin.locate(x, y, ctx.floor)["room_label"]
        for r in ranges:
            if r["kind"] != "uwb":
                continue
            p = pk[r["puck"]]
            if math.hypot(x - p["x"], y - p["y"]) > u["max_range_m"]:
                return f"beyond UWB range of {p['id']}"
            nw = ctx.twin.walls_crossed((p["x"], p["y"]), (x, y), ctx.floor)
            if nw > u["max_walls"]:
                return f"behind {nw} walls from {p['id']}: UWB does not range through more than {u['max_walls']}"
        for r in ranges:
            if r["kind"] == "uwb" and r.get("los") is not None and float(r["los"]) >= _R["los_threshold"]:
                p = pk[r["puck"]]
                if ctx.twin.walls_crossed((p["x"], p["y"]), (x, y), ctx.floor) >= 1:
                    return f"behind the wall to {label}: the link to {p['id']} is line-of-sight"
    if bearing is not None:
        p = pk[bearing["puck"]]
        th = math.degrees(math.atan2(y - p["y"], x - p["x"]))
        dd = abs((th - float(bearing["from_puck_deg"]) + 180.0) % 360.0 - 180.0)
        sg = float(bearing.get("sigma_deg") or noise["bearing"]["sigma_deg"])
        if dd > 2.0 * sg:
            return f"against the phone's bearing from {p['id']} ({round(dd)} degrees off)"
    if prior is not None and ctx.use_twin:
        here = ctx.twin.locate(x, y, ctx.floor)["room"]
        if here != prior.get("room"):
            return f"outside {ctx.room_label(prior['room'])}, where the occupant says they are"
    if ctx.use_twin:
        for r in ranges:
            p = pk[r["puck"]]
            if ctx.twin.walls_crossed((p["x"], p["y"]), (x, y), ctx.floor) >= 1:
                return f"behind a wall from {p['id']}: the ranges fit worse once the wall is counted"
    return "less likely once the twin is applied"
