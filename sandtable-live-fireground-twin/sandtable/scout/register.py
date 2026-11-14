"""Scout mode: place a thrown puck constellation inside the twin's room (docs/SCOUT_CONTRACT.md 2.2).

Self-location (MDS on the puck-to-puck ranges) gives the pucks in their own frame: the right
distances, but an unknown rotation, mirror and offset. `register` finds that pose inside the
room the twin says the crew threw them into, from two things only:

* the door prior. The pucks were thrown from the door, so every puck lies inside the room
  (with a wall margin) and within `throw_max_m` of the door: a hard constraint. Nearer the
  door is likelier: a soft prior that only decides between poses the echoes fit about equally
  well, and the result says so in `text` when it did.
* the static wall echoes. Every pair of pucks hears each wall at the bistatic path length the
  room predicts (first-order image source). A pose is scored by how many measured echoes it
  explains, with a truncated quadratic: an echo with no predicted wall within the cap counts
  zero, it is never squared, so spurious peaks, debris and second bounces cannot drag the fit.
  A small second factor rewards poses whose predicted short wall echoes are actually heard.

A rectangular room has symmetric poses that wall echoes alone can never tell apart (half-turn
and both mirror images). The door prior usually breaks them; when it cannot, `ambiguity` is near
1 and `text` says so. `fit_box` is the no-twin fallback: a coarse rectangle in the puck frame.

Physics this respects: about 500 MHz of bandwidth, so about 0.3 m of path resolution; echoes
closer than that to the direct path are inside its main lobe and are not used. Everything here
runs on simulated echoes until real boards exist. Pure numpy, deterministic.
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np

__all__ = ["register", "fit_box", "RES_M"]

RES_M = 0.3                 # one CIR tap at ~500 MHz is ~1 ns, ~0.3 m of path
MIN_EXCESS_M = 0.3          # echoes this close to the direct path are inside its main lobe
COARSE_CAP_M = 0.6          # match cap during the grid search (grid steps cost up to ~0.5 m of path)
FINE_CAP_M = 0.35           # match cap for refinement, residual and the final scores
WALL_MARGIN_M = 0.1         # a puck centre is at least this far from a wall
FAR_RMS_M = 1.0             # two poses are "different" when the pucks move this much (RMS)
ROT_STEP_DEG = 4.0
GRID_STEP_M = 0.3
MIN_ECHOES = 4              # fewer usable echoes than this: no echo registration at all
MIN_SCORE = 2.5             # the winner must explain at least ~3 echoes well
TYPICAL_THROW_M = 3.0
N_CANDIDATES = 24           # distinct coarse poses refined (search breadth; ~10 ms each)


# --- room geometry --------------------------------------------------------------------

class _Room:
    """Walls of a polygon with inward normals, kept in the polygon's own vertex order so wall
    indices match geometry.image_paths (wall i runs from vertex i to vertex i+1)."""

    def __init__(self, polygon):
        P = np.asarray(polygon, float)[:, :2]
        if len(P) > 3 and np.allclose(P[0], P[-1]):
            P = P[:-1]
        if len(P) < 3:
            raise ValueError("room polygon needs three or more vertices")
        self.P = P
        self.a = P
        self.b = np.roll(P, -1, axis=0)
        d = self.b - self.a
        self.len = np.hypot(d[:, 0], d[:, 1])
        self.t = d / self.len[:, None]
        area2 = float(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1]))
        sign = 1.0 if area2 > 0 else -1.0                      # CCW: interior on the left of each edge
        self.n = sign * np.stack([-self.t[:, 1], self.t[:, 0]], axis=1)
        cr = d[:, 0] * np.roll(d[:, 1], -1) - d[:, 1] * np.roll(d[:, 0], -1)
        self.convex = bool(np.all(cr * sign >= -1e-9))
        self.K = len(P)
        self.lo, self.hi = P.min(axis=0), P.max(axis=0)
        self.centroid = P.mean(axis=0)

    def inside(self, pts: np.ndarray, margin: float = 0.0) -> np.ndarray:
        """Point-in-polygon for (M, 2) points, and at least `margin` from every wall."""
        x, y = pts[:, 0:1], pts[:, 1:2]
        ax, ay, bx, by = self.a[:, 0], self.a[:, 1], self.b[:, 0], self.b[:, 1]
        cond = (ay > y) != (by > y)
        den = np.where(by != ay, by - ay, 1.0)
        xint = (bx - ax) * (y - ay) / den + ax
        inside = (np.sum(cond & (x < xint), axis=1) % 2) == 1
        if margin > 0:
            inside &= self.wall_dist(pts) >= margin
        return inside

    def wall_dist(self, pts: np.ndarray) -> np.ndarray:
        d = pts[:, None, :] - self.a[None]
        e = self.b - self.a
        tt = np.clip(np.einsum("mkd,kd->mk", d, e) / (self.len ** 2), 0.0, 1.0)
        q = d - tt[..., None] * e[None]
        return np.min(np.hypot(q[..., 0], q[..., 1]), axis=1)


def _wall_paths(room: _Room, X: np.ndarray, ii: np.ndarray, jj: np.ndarray, occlusion: bool = True):
    """First-order specular wall paths, vectorised. X: (B, N, 2) puck positions; pairs (ii[q], jj[q]).
    Returns path (B, Q, K) with NaN where wall K gives no specular point for that pair, and the
    specular point (B, Q, K, 2). Same physics as geometry.image_paths (image-source method)."""
    rel = X[:, :, None, :] - room.a[None, None]                  # (B, N, K, 2)
    h = np.einsum("bnkd,kd->bnk", rel, room.n)                   # distance in front of the wall
    s = np.einsum("bnkd,kd->bnk", rel, room.t)                   # position along the wall
    hi, hj, si, sj = h[:, ii], h[:, jj], s[:, ii], s[:, jj]      # (B, Q, K)
    hs = hi + hj
    path = np.sqrt((si - sj) ** 2 + hs ** 2)
    sstar = si + (sj - si) * hi / np.where(hs > 1e-9, hs, 1.0)
    valid = (hi > 1e-6) & (hj > 1e-6) & (sstar >= 0.0) & (sstar <= room.len)
    spec = room.a + sstar[..., None] * room.t                     # (B, Q, K, 2)
    if occlusion and not room.convex:
        valid &= ~_occluded(room, X[:, ii][:, :, None, :], spec) & ~_occluded(room, spec, X[:, jj][:, :, None, :])
    return np.where(valid, path, np.nan), spec


def _occluded(room: _Room, p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """True where segment p->q (broadcast to (B, Q, K, 2)) properly crosses a wall other than wall K."""
    p, q = np.broadcast_arrays(p, q)
    r = q - p                                                     # (B, Q, K, 2)
    e = room.b - room.a                                           # (M, 2)
    ap = room.a[None, None, None] - p[..., None, :]               # (B, Q, K, M, 2)
    den = r[..., None, 0] * e[:, 1] - r[..., None, 1] * e[:, 0]   # (B, Q, K, M)
    ok = np.abs(den) > 1e-12
    dsafe = np.where(ok, den, 1.0)
    u = (ap[..., 0] * e[:, 1] - ap[..., 1] * e[:, 0]) / dsafe
    v = (ap[..., 0] * r[..., None, 1] - ap[..., 1] * r[..., None, 0]) / dsafe
    eps = 1e-6
    hit = ok & (u > eps) & (u < 1 - eps) & (v > eps) & (v < 1 - eps)
    K = room.K
    hit &= ~np.eye(K, dtype=bool)[None, None]                     # the reflecting wall itself
    return hit.any(axis=-1)


# --- echoes ----------------------------------------------------------------------------

class _Echoes:
    """Measured echoes per unordered pair, de-duplicated (tx->rx and rx->tx hear the same paths)."""

    def __init__(self, ids: list[str], pos: np.ndarray, echoes, merge_m: float = 0.15):
        self.ids = ids
        idx = {p: k for k, p in enumerate(ids)}
        self.pairs = list(combinations(range(len(ids)), 2))
        self.ii = np.array([p[0] for p in self.pairs], int)
        self.jj = np.array([p[1] for p in self.pairs], int)
        self.dij = np.hypot(*(pos[self.ii] - pos[self.jj]).T) if self.pairs else np.zeros(0)
        qof = {pr: q for q, pr in enumerate(self.pairs)}
        per: dict[int, list[tuple[float, float, dict]]] = {}
        for e in echoes or []:
            a, b = e.get("tx"), e.get("rx")
            if a not in idx or b not in idx or a == b:
                continue
            i, j = sorted((idx[a], idx[b]))
            q = qof[(i, j)]
            if e.get("excess_m") is not None:
                L = float(e["excess_m"]) + float(self.dij[q])     # consistent with the self-located distance
                exc = float(e["excess_m"])
            elif e.get("path_m") is not None:
                L = float(e["path_m"])
                exc = L - float(self.dij[q])
            else:
                continue
            if not math.isfinite(L) or exc < MIN_EXCESS_M - 0.05:
                continue
            snr = e.get("snr_db")
            w = 1.0 if snr is None else float(np.clip((float(snr) - 3.0) / 12.0, 0.3, 1.0))
            per.setdefault(q, []).append((L, w, e))
        rows = []
        for q, lst in per.items():
            lst.sort(key=lambda r: r[0])
            groups: list[list] = []
            for L, w, e in lst:
                if groups and L - groups[-1][-1][0] <= merge_m:
                    groups[-1].append((L, w, e))
                else:
                    groups.append([(L, w, e)])
            for g in groups:
                ws = np.array([r[1] for r in g])
                rows.append((q, float(np.average([r[0] for r in g], weights=ws)), float(ws.max()), [r[2] for r in g]))
        rows.sort(key=lambda r: (r[0], r[1]))
        self.q = np.array([r[0] for r in rows], int)
        self.L = np.array([r[1] for r in rows], float)
        self.w = np.array([r[2] for r in rows], float)
        self.raw = [r[3] for r in rows]
        self.E = len(rows)
        Q = len(self.pairs)
        mmax = max([int(np.sum(self.q == q)) for q in range(Q)] + [1])
        self.Lpad = np.full((Q, mmax), np.inf)
        self.window = np.zeros(Q)
        for q in range(Q):
            Ls = self.L[self.q == q]
            self.Lpad[q, :len(Ls)] = Ls
            self.window[q] = (Ls.max() + RES_M) if len(Ls) else 0.0


def _ceiling_path(dij, height_m, z=0.04):
    return np.sqrt(dij ** 2 + (2.0 * max(0.0, height_m - z)) ** 2)


def _score(room: _Room, X: np.ndarray, ech: _Echoes, cap: float, occlusion: bool, height_m: float | None = None):
    """Score B poses. Returns (score, inliers, rms_inlier_residual, residuals (B, E))."""
    B = X.shape[0]
    if ech.E == 0:
        z = np.zeros(B)
        return z, z, z, np.zeros((B, 0))
    path, _ = _wall_paths(room, X, ech.ii, ech.jj, occlusion)            # (B, Q, K)
    if height_m:
        ceil = np.broadcast_to(_ceiling_path(ech.dij, height_m)[None, :, None], (B, len(ech.pairs), 1))
        path = np.concatenate([path, ceil], axis=2)
    Pe = path[:, ech.q, :]                                                # (B, E, K)
    r = np.abs(Pe - ech.L[None, :, None])
    r = np.min(np.where(np.isnan(r), np.inf, r), axis=2)                  # (B, E)
    k = np.clip(1.0 - (r / cap) ** 2, 0.0, None)
    s_meas = (k * ech.w[None]).sum(axis=1)
    # recall: walls this pose predicts inside the pair's echo window, clear of the direct path
    wall_path = path[..., :room.K]
    cov = (~np.isnan(wall_path)) & (wall_path - ech.dij[None, :, None] >= MIN_EXCESS_M + 0.2) \
        & (wall_path <= ech.window[None, :, None])
    d = np.min(np.abs(np.where(cov, wall_path, 0.0)[..., None] - ech.Lpad[None, :, None, :]), axis=3)
    kc = np.clip(1.0 - (d / cap) ** 2, 0.0, None) * cov
    nv = cov.sum(axis=(1, 2))
    recall = np.where(nv > 0, kc.sum(axis=(1, 2)) / np.maximum(nv, 1), 0.5)
    score = s_meas * (0.5 + 0.5 * recall)
    inl = r < cap
    n_in = inl.sum(axis=1)
    rms = np.sqrt(np.where(inl, r ** 2, 0.0).sum(axis=1) / np.maximum(n_in, 1))
    return score, n_in, rms, r


# --- poses -----------------------------------------------------------------------------

def _place(Q0: np.ndarray, th, mir, cx, cy) -> np.ndarray:
    """Centred constellation Q0 (N, 2) -> (B, N, 2): mirror (y -> -y), rotate, put the centroid at (cx, cy)."""
    th, mir, cx, cy = (np.atleast_1d(np.asarray(v, float)) for v in (th, mir, cx, cy))
    y = Q0[None, :, 1] * np.where(mir > 0.5, -1.0, 1.0)[:, None]
    x = np.broadcast_to(Q0[None, :, 0], y.shape)
    c, s = np.cos(th)[:, None], np.sin(th)[:, None]
    return np.stack([c * x - s * y + cx[:, None], s * x + c * y + cy[:, None]], axis=2)


def _feasible(room: _Room, X: np.ndarray, door, throw_max: float) -> np.ndarray:
    B, N, _ = X.shape
    ok = room.inside(X.reshape(-1, 2), WALL_MARGIN_M).reshape(B, N).all(axis=1)
    if door is not None:
        ok &= (np.hypot(X[..., 0] - door[0], X[..., 1] - door[1]) <= throw_max).all(axis=1)
    return ok


def _prior(X: np.ndarray, door, throw_max: float) -> np.ndarray:
    """Soft door prior: thrown pucks land nearer the door. Only ever a tie-breaker in practice."""
    if door is None:
        return np.ones(X.shape[0])
    # flat over ordinary throws (a crew spreads the pucks 2-4 m in), decaying beyond: it must never overrule
    # the echoes between two plausible throws, only between a plausible one and a far mirror image.
    dbar = np.hypot(X[..., 0] - door[0], X[..., 1] - door[1]).mean(axis=1)
    plateau, sig = 0.5 * throw_max, max(0.5, 0.2 * throw_max)          # 3.25 m and 1.3 m at the default 6.5 m
    return np.exp(-0.5 * (np.maximum(0.0, dbar - plateau) / sig) ** 2)


def _transform(mu: np.ndarray, th: float, mir: bool, cx: float, cy: float) -> dict:
    """Contract transform on the raw relative frame: X = R(theta) * M * p + t, M = diag(1, -1) if mirrored
    (the same convention as sandtable.spatial.Registration with a = cos, b = sin)."""
    m = -1.0 if mir else 1.0
    c, s = math.cos(th), math.sin(th)
    mx, my = mu[0], m * mu[1]
    tx, ty = cx - (c * mx - s * my), cy - (s * mx + c * my)
    return {"rotation_deg": round(math.degrees(th) % 360.0, 2), "mirror": bool(mir), "tx": round(tx, 3), "ty": round(ty, 3)}


def _refine(room, Q0, pose, ech, door, throw_max, height_m, cap0, step_deg, step_m):
    th, mir, cx, cy = pose
    feas0 = _feasible(room, _place(Q0, th, mir, cx, cy), door, throw_max)[0]
    best = _score(room, _place(Q0, th, mir, cx, cy), ech, cap0, True, height_m)[0][0] if feas0 else -np.inf
    dth, dt = math.radians(step_deg), step_m
    g = np.array([-1.0, 0.0, 1.0])
    G = np.stack(np.meshgrid(g, g, g, indexing="ij"), -1).reshape(-1, 3)
    G = G[np.any(G != 0, axis=1)]
    for cap in (cap0, FINE_CAP_M):
        if cap != cap0:
            best = _score(room, _place(Q0, th, mir, cx, cy), ech, cap, True, height_m)[0][0] if feas0 else -np.inf
            dth, dt = math.radians(step_deg) / 2, step_m / 2
        for _ in range(80):
            T, Cx, Cy = th + G[:, 0] * dth, cx + G[:, 1] * dt, cy + G[:, 2] * dt
            X = _place(Q0, T, np.full(len(G), float(mir)), Cx, Cy)
            sc = _score(room, X, ech, cap, True, height_m)[0]
            sc = np.where(_feasible(room, X, door, throw_max), sc, -np.inf)
            k = int(np.argmax(sc))
            if sc[k] > best + 1e-9:
                best, th, cx, cy, feas0 = sc[k], T[k], Cx[k], Cy[k], True
            elif dt > 0.01 or dth > math.radians(0.1):
                dth, dt = dth / 2, dt / 2
            else:
                break
    return (th, mir, cx, cy)


def _rms_between(A: np.ndarray, B: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum((A - B) ** 2, axis=-1))))


# --- words -----------------------------------------------------------------------------

def _compass(vx: float, vy: float) -> str:
    """+x is roughly east and +y roughly north in the twin frame (the fixture twin is north-up)."""
    names = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]
    return names[int(round(math.degrees(math.atan2(vy, vx)) / 45.0)) % 8]


def _door_xy(room: dict | None):
    if not room:
        return None
    d = room.get("door")
    if d is None:
        return None
    if isinstance(d, dict):
        d = d.get("at")
    return np.asarray(d, float)[:2] if d is not None else None


def _throw_point(rm: _Room, door, throw_max: float) -> np.ndarray:
    if door is None:
        return rm.centroid.copy()
    k = int(np.argmin([_seg_dist(door, rm.a[i], rm.b[i]) for i in range(rm.K)]))      # the wall the door is in
    for dist in (min(TYPICAL_THROW_M, throw_max / 2), 2.0, 1.0, 0.5):
        p = door + dist * rm.n[k]
        if rm.inside(p[None], WALL_MARGIN_M)[0]:
            return p
    return rm.centroid.copy()


def _seg_dist(p, a, b) -> float:
    e = b - a
    t = float(np.clip(np.dot(p - a, e) / max(np.dot(e, e), 1e-12), 0, 1))
    return float(np.hypot(*(p - (a + t * e))))


def _pucks_dict(ids, X) -> dict:
    return {pid: [round(float(X[k, 0]), 3), round(float(X[k, 1]), 3)] for k, pid in enumerate(ids)}


# --- public ----------------------------------------------------------------------------

def register(pucks_rel: dict, echoes: list, room: dict | None, throw_max_m: float = 6.5) -> dict:
    """Place the self-located constellation in the twin's room. See the module docstring.

    `room`: {"polygon": [[x, y], ...], "door": [x, y] (or {"at": [x, y]}), optional "label", "height_m"}.
    `height_m` (additive): when given, the ceiling echo (pose-independent) is modelled so it is not
    counted as unexplained. Returns the contract dict plus additive keys: "matched" [inliers, echoes],
    "echo_ambiguity" (the same ratio without the door prior), "door_decided" (bool), "alternative"
    (the best clearly different pose: {"pucks", "transform", "score_ratio", "door_m"} or None),
    "confident" (bool), "runtime_s"."""
    import time
    t0 = time.perf_counter()
    ids = sorted(pucks_rel)
    rel = np.array([[float(pucks_rel[k][0]), float(pucks_rel[k][1])] for k in ids]) if ids else np.zeros((0, 2))
    base = {"ok": False, "method": "box-fit", "pucks": _pucks_dict(ids, rel),
            "transform": {"rotation_deg": 0.0, "mirror": False, "tx": 0.0, "ty": 0.0},
            "residual_m": 0.0, "ambiguity": 1.0, "text": "", "matched": [0, 0], "echo_ambiguity": 1.0,
            "door_decided": False, "alternative": None, "confident": False}
    if len(ids) < 2:
        base["text"] = "Fewer than two pucks: nothing to place."
        base["runtime_s"] = round(time.perf_counter() - t0, 3)
        return base
    mu = rel.mean(axis=0)
    Q0 = rel - mu
    ech = _Echoes(ids, rel, echoes)

    if room is None or not room.get("polygon"):
        box = fit_box(pucks_rel, echoes)
        base.update(residual_m=box.get("residual_m", 0.0), matched=[box.get("matched", 0), ech.E],
                    text="No twin for this room: puck positions are in the pucks' own frame (arbitrary rotation and "
                         "mirror, not north-up). " + box["text"])
        base["runtime_s"] = round(time.perf_counter() - t0, 3)
        return base

    rm = _Room(room["polygon"])
    door = _door_xy(room)
    label = room.get("label") or room.get("room_label") or "the room"
    height = room.get("height_m")
    throw_max = float(throw_max_m)

    def door_prior(why: str) -> dict:
        tp = _throw_point(rm, door, throw_max)
        best = None
        ths = np.radians(np.arange(0.0, 360.0, 10.0))
        offs = np.arange(-1.5, 1.51, 0.25)
        for mir in (0.0, 1.0):
            T, OX, OY = (a.ravel() for a in np.meshgrid(ths, offs, offs, indexing="ij"))
            X = _place(Q0, T, np.full(T.shape, mir), tp[0] + OX, tp[1] + OY)
            ok = _feasible(rm, X, door, throw_max)
            if ok.any():
                cost = np.hypot(OX, OY) + 0.01 * np.minimum(T, 2 * np.pi - T) + 0.5 * mir
                k = int(np.argmin(np.where(ok, cost, np.inf)))
                if best is None or cost[k] < best[0]:
                    best = (float(cost[k]), float(T[k]), bool(mir), float(tp[0] + OX[k]), float(tp[1] + OY[k]))
        fits = best is not None
        th, mir, cx, cy = (best[1:] if fits else (0.0, False, float(tp[0]), float(tp[1])))
        X = _place(Q0, th, mir, cx, cy)[0]
        sc, n_in, rms, r = _score(rm, X[None], ech, FINE_CAP_M, True, height)
        res = float(np.sqrt(np.mean(np.minimum(r[0], 1.0) ** 2))) if ech.E else 0.0
        out = dict(base)
        out.update(method="door-prior", pucks=_pucks_dict(ids, X), transform=_transform(mu, th, mir, cx, cy),
                   residual_m=round(res, 3), ambiguity=1.0, matched=[int(n_in[0]), ech.E], echo_ambiguity=1.0,
                   text=(f"GUESS, not a fix: {why} The constellation is drawn about {TYPICAL_THROW_M:g} m into {label} from the door, "
                         "orientation unknown." + ("" if fits else " It does not fit inside the room as measured: check the room "
                                                   "and the self-location.")))
        out["runtime_s"] = round(time.perf_counter() - t0, 3)
        return out

    if ech.E < MIN_ECHOES:
        return door_prior(f"only {ech.E} usable wall echo{'es' if ech.E != 1 else ''} (need {MIN_ECHOES} or more) to place the pucks.")

    # coarse grid: rotation x mirror x centroid offset; hard constraints first
    xs = np.arange(rm.lo[0] + 0.15, rm.hi[0], GRID_STEP_M)
    ys = np.arange(rm.lo[1] + 0.15, rm.hi[1], GRID_STEP_M)
    C = np.stack(np.meshgrid(xs, ys, indexing="ij"), -1).reshape(-1, 2)
    C = C[rm.inside(C, WALL_MARGIN_M)]
    if door is not None:
        C = C[np.hypot(C[:, 0] - door[0], C[:, 1] - door[1]) <= throw_max]
    ths = np.radians(np.arange(0.0, 360.0, ROT_STEP_DEG))
    cand = []
    for mir in (0.0, 1.0):
        for chunk in np.array_split(ths, max(1, len(ths) // 8)):
            T = np.repeat(chunk, len(C))
            CC = np.tile(C, (len(chunk), 1))
            X = _place(Q0, T, np.full(T.shape, mir), CC[:, 0], CC[:, 1])
            ok = _feasible(rm, X, door, throw_max)
            if not ok.any():
                continue
            sc = _score(rm, X[ok], ech, COARSE_CAP_M, False, height)[0]
            cand.append(np.column_stack([sc, T[ok], np.full(ok.sum(), mir), CC[ok]]))
    if not cand:
        return door_prior(f"the self-located constellation does not fit inside {label} within {throw_max:g} m of the door.")
    cand = np.concatenate(cand)
    order = np.argsort(-cand[:, 0])[:4000]
    kept: list[tuple[np.ndarray, tuple]] = []
    for k in order:
        _, th, mir, cx, cy = cand[k]
        X = _place(Q0, th, mir, cx, cy)[0]
        if all(_rms_between(X, Y) > 0.6 for Y, _ in kept):
            kept.append((X, (th, mir, cx, cy)))
            if len(kept) >= N_CANDIDATES:
                break

    refined = []
    for _, pose in kept:
        th, mir, cx, cy = _refine(rm, Q0, pose, ech, door, throw_max, height, COARSE_CAP_M, ROT_STEP_DEG / 2, GRID_STEP_M / 2)
        X = _place(Q0, th, mir, cx, cy)
        if not _feasible(rm, X, door, throw_max)[0]:
            continue
        sc, n_in, rms, _ = _score(rm, X, ech, FINE_CAP_M, True, height)
        refined.append({"pose": (th, bool(mir > 0.5), cx, cy), "X": X[0], "echo": float(sc[0]), "n_in": int(n_in[0]),
                        "rms": float(rms[0]), "prior": float(_prior(X, door, throw_max)[0])})
    if not refined:
        return door_prior(f"no pose of the constellation fits inside {label} within {throw_max:g} m of the door.")
    for r in refined:
        r["total"] = r["echo"] * r["prior"]
    win = max(refined, key=lambda r: r["total"])
    far = [r for r in refined if _rms_between(r["X"], win["X"]) > FAR_RMS_M]
    alt = max(far, key=lambda r: r["total"]) if far else None
    alt_echo = max(far, key=lambda r: r["echo"]) if far else None
    amb = min(1.0, alt["total"] / win["total"]) if (alt and win["total"] > 0) else 0.0
    echo_amb = min(1.0, alt_echo["echo"] / win["echo"]) if (alt_echo and win["echo"] > 0) else 0.0

    if win["echo"] < MIN_SCORE or win["n_in"] < 3:
        return door_prior(f"the wall echoes fit no pose well ({win['n_in']} of {ech.E} explained).")

    def dmean(X):
        return float(np.mean(np.hypot(X[:, 0] - door[0], X[:, 1] - door[1]))) if door is not None else 0.0

    door_decided = bool(alt_echo is not None and echo_amb >= 0.85 and amb < 0.85)
    th, mir, cx, cy = win["pose"]
    out = dict(base)
    out.update(ok=True, method="twin-echo-match", pucks=_pucks_dict(ids, win["X"]), transform=_transform(mu, th, mir, cx, cy),
               residual_m=round(win["rms"], 3), ambiguity=round(amb, 3), echo_ambiguity=round(echo_amb, 3),
               matched=[win["n_in"], ech.E], door_decided=door_decided, confident=bool(amb < 0.7 and win["n_in"] >= 5))
    if alt is not None:
        ath, amir, acx, acy = alt["pose"]
        out["alternative"] = {"pucks": _pucks_dict(ids, alt["X"]), "transform": _transform(mu, ath, amir, acx, acy),
                              "score_ratio": round(amb, 3), "door_m": round(dmean(alt["X"]), 1)}
    head = (f"Pucks placed in {label} by matching {win['n_in']} of {ech.E} wall echoes to the twin's outline "
            f"(path residual {win['rms']:.2f} m RMS; resolution about {RES_M:g} m).")
    if amb >= 0.85:
        kind = "a mirror image" if alt["pose"][1] != win["pose"][1] else "a turned pose"
        er = alt["echo"] / win["echo"] if win["echo"] > 0 else 1.0
        fit = "as well or better" if er >= 0.995 else f"{er:.0%} as well"
        why = (f"this one is kept because it lies nearer the door, mean {dmean(win['X']):.1f} m against {dmean(alt['X']):.1f} m"
               if alt["prior"] < win["prior"] - 0.02 else "the door does not separate them")
        tail = (f" CANNOT TELL APART: {kind} {_rms_between(alt['X'], win['X']):.1f} m away (RMS) fits the echoes {fit}; {why}. "
                "Treat puck and victim positions as possibly mirrored; the alternative is shown.")
    elif door_decided:
        tail = (f" The echoes fit a mirror-image or turned pose about as well ({echo_amb:.0%}); it was ruled out only because "
                f"the pucks were thrown from the door (it would put them {dmean(alt_echo['X']) - dmean(win['X']):.1f} m further "
                "from it on average).")
    elif amb >= 0.6:
        tail = f" Next-best pose scores {amb:.0%} of this one: likely, not certain."
    else:
        tail = f" Next-best pose scores {amb:.0%} of this one." if alt else " No other pose fits inside the room near the door."
    out["text"] = head + tail
    out["runtime_s"] = round(time.perf_counter() - t0, 3)
    return out


def fit_box(pucks_rel: dict, echoes: list) -> dict:
    """No twin: a coarse rectangle around the pucks, in the puck frame, from the wall echoes.

    Each wall echo of a pair is a line tangent to the ellipse with the two pucks as foci. For each
    orientation, every one of the four side directions is scanned for the offset that explains an
    echo on the most pairs (a Hough vote, one echo per pair per line); the orientation with the most
    support wins. A side seen on fewer than two pairs is drawn 1 m beyond the pucks and said so.
    Returns {"polygon", "confidence", "text"} plus additive "sides_seen", "size_m", "residual_m", "matched"."""
    ids = sorted(pucks_rel)
    rel = np.array([[float(pucks_rel[k][0]), float(pucks_rel[k][1])] for k in ids]) if ids else np.zeros((0, 2))
    ech = _Echoes(ids, rel, echoes) if len(ids) >= 2 else None
    if ech is None or ech.E == 0 or len(ids) < 2:
        lo, hi = (rel.min(axis=0) - 1.0, rel.max(axis=0) + 1.0) if len(ids) else (np.array([-1.0, -1.0]), np.array([1.0, 1.0]))
        poly = [[round(float(lo[0]), 2), round(float(lo[1]), 2)], [round(float(hi[0]), 2), round(float(lo[1]), 2)],
                [round(float(hi[0]), 2), round(float(hi[1]), 2)], [round(float(lo[0]), 2), round(float(hi[1]), 2)]]
        return {"polygon": poly, "confidence": 0.0, "sides_seen": 0, "size_m": [round(float(hi[0] - lo[0]), 1), round(float(hi[1] - lo[1]), 1)],
                "residual_m": 0.0, "matched": 0,
                "text": "Coarse box only: no wall echoes, so the outline is just the pucks plus 1 m. Not a map."}
    cap = 0.3
    best = None
    for phi in np.radians(np.arange(0.0, 90.0, 2.0)):
        u = np.array([math.cos(phi), math.sin(phi)])
        v = np.array([-u[1], u[0]])
        sides = []
        for n in (u, v, -u, -v):
            t = np.array([-n[1], n[0]])
            pn, pt = rel @ n, rel @ t
            cs = np.arange(pn.max() + 0.15, pn.max() + 12.0, 0.05)
            along = (pt[ech.ii] - pt[ech.jj]) ** 2                              # (Q,)
            perp = 2 * cs[None, :] - (pn[ech.ii] + pn[ech.jj])[:, None]          # (Q, C)
            pred = np.sqrt(along[:, None] + perp ** 2)                           # (Q, C)
            k = np.clip(1 - ((ech.L[:, None] - pred[ech.q]) / cap) ** 2, 0, None) * ech.w[:, None]   # (E, C)
            per_pair = np.zeros((len(ech.pairs), len(cs)))
            np.maximum.at(per_pair, ech.q, k)
            sup = per_pair.sum(axis=0)
            c = int(np.argmax(sup))
            sides.append((float(sup[c]), float(cs[c]), n, int((per_pair[:, c] > 0.25).sum())))
        tot = sum(s[0] for s in sides)
        if best is None or tot > best[0] + 1e-9:
            best = (tot, phi, sides)
    _, phi, sides = best
    seen = []
    offs = []
    for sup, c, n, npairs in sides:
        pn_max = float((rel @ n).max())
        ok = npairs >= 2
        seen.append(ok)
        offs.append(c if ok else pn_max + 1.0)
    u = np.array([math.cos(phi), math.sin(phi)])
    v = np.array([-u[1], u[0]])
    # sides: +u at offs[0], +v at offs[1], -u at offs[2], -v at offs[3]
    umax, vmax, umin, vmin = offs[0], offs[1], -offs[2], -offs[3]
    corners = [umin * u + vmin * v, umax * u + vmin * v, umax * u + vmax * v, umin * u + vmax * v]
    poly = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in corners]
    # how many echoes the box explains, and how well
    X = rel[None]
    box = _Room(poly)
    _, n_in, rms, _ = _score(box, X, ech, cap, True)
    n_seen = sum(seen)
    frac = float(n_in[0]) / ech.E
    conf = round(float(min(0.9, (n_seen / 4.0) * (0.3 + 0.7 * frac) * min(1.0, len(ech.pairs) / 3.0))), 2)
    size = [round(umax - umin, 1), round(vmax - vmin, 1)]
    names = ["+u", "+v", "-u", "-v"]
    unseen = [names[k] for k in range(4) if not seen[k]]
    text = (f"Coarse box from wall echoes, no twin: about {size[0]:.1f} x {size[1]:.1f} m, {n_seen} of 4 sides heard on two or "
            f"more links, {int(n_in[0])} of {ech.E} echoes explained. In the pucks' own frame (not north-up). A coarse outline, "
            "not a map of the room.")
    if unseen:
        text += f" {len(unseen)} side{'s' if len(unseen) > 1 else ''} not heard: drawn 1 m beyond the outermost puck."
    return {"polygon": poly, "confidence": conf, "sides_seen": n_seen, "size_m": size, "residual_m": round(float(rms[0]), 3),
            "matched": int(n_in[0]), "text": text}
