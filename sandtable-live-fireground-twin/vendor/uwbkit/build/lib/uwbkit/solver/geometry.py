"""Geometry helpers: canonical frame, classical MDS, weighted SMACOF, anchor layout solving.

Pure numpy, small fixed-size arrays (n <= ~10 nodes).
"""
from __future__ import annotations

import math
import re

import numpy as np


def natural_key(s) -> list:
    """Sort key so that N2 < N10."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", str(s))]


def _xyz(p) -> tuple[float, float, float]:
    z = p.get("z", 0.0)
    return float(p.get("x", 0.0)), float(p.get("y", 0.0)), float(0.0 if z is None else z)


def canon_array(P: np.ndarray, idx: list[int], dims: int) -> np.ndarray:
    """Return a copy of P (n, 3) in the canonical frame defined by the rows idx (N1, N2, N3[, N4]).

    dims 2: N1 at the origin (x, y), N2 on +x, N3 with y > 0; z is left unchanged (absolute height).
    dims 3: N1 at the origin, N2 on +x, N3 in the xy-plane with y > 0, N4 with z > 0.
    """
    Q = np.array(P, dtype=float, copy=True)
    if not idx:
        return Q
    if dims == 3:
        Q -= Q[idx[0]]
        if len(idx) < 2:
            return Q
        u = Q[idx[1]]
        nu = float(np.linalg.norm(u))
        e1 = u / nu if nu > 1e-9 else np.array([1.0, 0.0, 0.0])
        if len(idx) >= 3:
            v = Q[idx[2]]
            vp = v - float(v @ e1) * e1
        else:
            vp = np.zeros(3)
        nvp = float(np.linalg.norm(vp))
        if nvp < 1e-9:
            # any perpendicular direction
            trial = np.array([0.0, 0.0, 1.0]) if abs(e1[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
            vp = np.cross(trial, e1)
            vp = np.cross(e1, vp)
            nvp = float(np.linalg.norm(vp))
        e2 = vp / nvp
        e3 = np.cross(e1, e2)
        R = np.vstack([e1, e2, e3])
        Q = Q @ R.T
        if len(idx) >= 4 and Q[idx[3], 2] < 0:
            Q[:, 2] *= -1.0
        return Q
    # dims 2
    Q[:, :2] -= Q[idx[0], :2]
    if len(idx) >= 2:
        ux, uy = Q[idx[1], 0], Q[idx[1], 1]
        nu = math.hypot(ux, uy)
        if nu > 1e-9:
            c, s = ux / nu, uy / nu
            R = np.array([[c, s], [-s, c]])
            Q[:, :2] = Q[:, :2] @ R.T
    if len(idx) >= 3 and Q[idx[2], 1] < 0:
        Q[:, 1] *= -1.0
    return Q


def canonical_frame(points: dict, ids: list, dims: int = 2) -> dict:
    """Rigidly transform {id: {"x","y","z"}} into the canonical frame.

    The frame is defined by the first ids (in the given order) that are present in `points`:
    ids[0] at the origin, ids[1] on +x, ids[2] with y > 0 (3-D: in the xy-plane, ids[3] with z > 0).
    In 2-D only x/y are transformed; z (height) is kept as given.
    Every id in `points` is transformed (also ids not listed in `ids`).
    """
    keys = list(points.keys())
    if not keys:
        return {}
    P = np.array([_xyz(points[k]) for k in keys], dtype=float)
    pos = {k: i for i, k in enumerate(keys)}
    idx = []
    for i in ids:
        if i in pos and pos[i] not in idx:
            idx.append(pos[i])
    Q = canon_array(P, idx, 3 if dims == 3 else 2)
    return {k: {"x": float(Q[i, 0]), "y": float(Q[i, 1]), "z": float(Q[i, 2])} for k, i in pos.items()}


def complete_distances(D: np.ndarray, W: np.ndarray) -> np.ndarray:
    """Fill missing entries (W == 0) by shortest paths over known edges (Floyd-Warshall)."""
    n = len(D)
    G = np.where(W > 0, D, np.inf)
    np.fill_diagonal(G, 0.0)
    for k in range(n):
        G = np.minimum(G, G[:, k:k + 1] + G[k:k + 1, :])
    finite = np.isfinite(G)
    if not finite.all():
        fill = float(np.max(G[finite])) if finite.any() else 1.0
        G = np.where(finite, G, max(fill, 1.0))
    return G


def classical_mds(D: np.ndarray, dims: int) -> np.ndarray:
    """Classical (Torgerson) MDS of a full distance matrix -> (n, dims)."""
    n = len(D)
    J = np.eye(n) - 1.0 / n
    B = -0.5 * J @ (D * D) @ J
    w, V = np.linalg.eigh(B)
    order = np.argsort(w)[::-1][:dims]
    lam = np.clip(w[order], 0.0, None)
    X = V[:, order] * np.sqrt(lam)
    if X.shape[1] < dims:
        X = np.hstack([X, np.zeros((n, dims - X.shape[1]))])
    return X


def smacof(D: np.ndarray, W: np.ndarray, X: np.ndarray, iters: int = 30, tol: float = 1e-5) -> np.ndarray:
    """Weighted SMACOF stress majorisation starting from X (n, k). W symmetric, zero diagonal."""
    n = len(X)
    V = -W.copy()
    np.fill_diagonal(V, 0.0)
    np.fill_diagonal(V, -V.sum(axis=1))
    Vp = np.linalg.pinv(V)
    X = np.array(X, dtype=float, copy=True)
    eye = np.eye(n, dtype=bool)
    for _ in range(iters):
        diff = X[:, None, :] - X[None, :, :]
        dist = np.sqrt((diff * diff).sum(axis=-1))
        safe = np.where(dist > 1e-9, dist, 1.0)
        B = np.where(dist > 1e-9, -W * D / safe, 0.0)
        B[eye] = 0.0
        np.fill_diagonal(B, -B.sum(axis=1))
        Xn = Vp @ (B @ X)
        step = float(np.max(np.abs(Xn - X)))
        X = Xn
        if step < tol:
            break
    return X


def stress_rms(D: np.ndarray, W: np.ndarray, X: np.ndarray) -> float:
    diff = X[:, None, :] - X[None, :, :]
    dist = np.sqrt((diff * diff).sum(axis=-1))
    sw = float(W.sum())
    if sw <= 0:
        return 0.0
    return math.sqrt(float((W * (dist - D) ** 2).sum()) / sw)


def _split_key(key, idx: dict):
    if isinstance(key, (tuple, list)) and len(key) == 2:
        a, b = str(key[0]), str(key[1])
        return (a, b) if a in idx and b in idx else (None, None)
    if isinstance(key, str):
        for m in re.finditer("-", key):
            a, b = key[:m.start()], key[m.end():]
            if a in idx and b in idx:
                return a, b
    return None, None


def _median_value(val):
    if isinstance(val, (list, tuple)):
        vals = [float(v) for v in val if isinstance(v, (int, float)) and math.isfinite(float(v))]
        if not vals:
            return None
        vals.sort()
        if len(vals) >= 4:   # interquartile mean: robust like the median, less noisy
            q = len(vals) // 4
            vals = vals[q:len(vals) - q]
        return float(np.mean(vals))
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def solve_layout(pair_dists: dict, ids: list, dims: int = 2, z=None) -> dict:
    """Solve node coordinates from pairwise distances (anchor self-calibration).

    pair_dists: {(a, b): metres} or {"a-b": metres} (either order; a value may also be a list of samples,
    their interquartile mean is used). Missing pairs are fine while the graph stays rigid.
    ids: node ids; ids[0] -> origin, ids[1] -> +x, ids[2] -> y > 0 (3-D: ids[3] -> z > 0).
    z: None (-> 0.0), a float for all, or {id: height}. In 2-D with z given, distances are slant ranges.
    Returns {id: {"x","y","z"}} (plain floats, rounded to 4 decimals). Ids with no distances get no entry.
    """
    ids = [str(i) for i in ids]
    seen = set()
    ids = [i for i in ids if not (i in seen or seen.add(i))]
    d3 = dims == 3

    def zof(i):
        if z is None:
            return 0.0
        if isinstance(z, dict):
            v = z.get(i, 0.0)
            return float(v) if v is not None else 0.0
        return float(z)

    idx = {k: i for i, k in enumerate(ids)}
    n = len(ids)
    S = np.zeros((n, n))
    C = np.zeros((n, n))
    for key, val in (pair_dists or {}).items():
        a, b = _split_key(key, idx)
        if a is None or a == b:
            continue
        v = _median_value(val)
        if v is None or v < 0:
            continue
        if not d3 and z is not None:
            dz = zof(a) - zof(b)
            v = math.sqrt(max(v * v - dz * dz, 0.0))
        i, j = idx[a], idx[b]
        S[i, j] += v
        S[j, i] += v
        C[i, j] += 1
        C[j, i] += 1
    # keep only ids that have at least one distance (others cannot be placed)
    keep = [i for i in range(n) if C[i].sum() > 0]
    if not keep:
        return {ids[0]: {"x": 0.0, "y": 0.0, "z": zof(ids[0]) if not d3 else 0.0}} if n == 1 else {}
    S = S[np.ix_(keep, keep)]
    C = C[np.ix_(keep, keep)]
    kid = [ids[i] for i in keep]
    W = (C > 0).astype(float)
    D = np.where(C > 0, S / np.maximum(C, 1), 0.0)
    k = 3 if d3 else 2
    X0 = classical_mds(complete_distances(D, W), k)
    X = smacof(D, W, X0, iters=500, tol=1e-8)
    P = np.zeros((len(kid), 3))
    P[:, :k] = X
    Q = canon_array(P, list(range(min(len(kid), 4))), 3 if d3 else 2)
    out = {}
    for r, i in enumerate(kid):
        zz = float(Q[r, 2]) if d3 else zof(i)
        out[i] = {"x": round(float(Q[r, 0]), 4) + 0.0, "y": round(float(Q[r, 1]), 4) + 0.0, "z": round(zz, 4) + 0.0}
    return out
