"""Robust single-epoch multilateration (Gauss-Newton, Cauchy IRLS, leave-one-out outlier rejection).

Ranges are always treated as 3-D slant distances: in 2-D the tag height is fixed and only x, y are solved,
so a range shorter than the height difference just produces a residual (never a sqrt of a negative number).
"""
from __future__ import annotations

import math

import numpy as np

CAUCHY_K = 2.5      # in sigmas
INLIER_K = 4.0      # normalised residual above this = outlier


def _linear_init(A: np.ndarray, d: np.ndarray, D: int, z: float) -> np.ndarray | None:
    """Closed-form linearised solution (mean-subtracted sphere equations)."""
    n = len(d)
    if n < D + 1:
        return None
    if D == 2:
        r2 = np.maximum(d * d - (A[:, 2] - z) ** 2, 0.0)
        Ah = A[:, :2]
    else:
        r2 = d * d
        Ah = A
    a2 = (Ah * Ah).sum(axis=1)
    M = -2.0 * (Ah - Ah.mean(axis=0))
    b = (r2 - r2.mean()) - (a2 - a2.mean())
    try:
        sol, _, rank, _ = np.linalg.lstsq(M, b, rcond=1e-6)
    except np.linalg.LinAlgError:
        return None
    if rank < D or not np.all(np.isfinite(sol)):
        return None
    p = np.empty(3)
    p[:D] = sol
    if D == 2:
        p[2] = z
    return p


def _gn(A, d, sig, p, D, zp, zsig, iters=12, plain_iters=3):
    """Gauss-Newton with Cauchy IRLS. Returns (p, robust_cost)."""
    p = p.copy()
    I = np.eye(D)
    for k in range(iters):
        diff = p - A
        r = np.sqrt((diff * diff).sum(axis=1))
        r = np.maximum(r, 1e-6)
        res = r - d
        e = res / sig
        kk = 1e3 if k < plain_iters else CAUCHY_K
        w = 1.0 / (1.0 + (e / kk) ** 2) / (sig * sig)
        J = diff[:, :D] / r[:, None]
        JW = J.T * w
        H = JW @ J + 1e-6 * I
        g = JW @ res
        if D == 3 and zsig is not None:
            H[2, 2] += 1.0 / (zsig * zsig)
            g[2] += (p[2] - zp) / (zsig * zsig)
        try:
            dp = -np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        nrm = float(np.sqrt(dp @ dp))
        if not math.isfinite(nrm):
            break
        if nrm > 3.0:
            dp *= 3.0 / nrm
        p[:D] += dp
        if nrm < 1e-4 and k >= plain_iters:
            break
    diff = p - A
    r = np.sqrt((diff * diff).sum(axis=1))
    e = (r - d) / sig
    cost = float(np.log1p((e / CAUCHY_K) ** 2).sum())
    if D == 3 and zsig is not None:
        cost += 0.5 * ((p[2] - zp) / zsig) ** 2
    return p, cost


def robust_fix(A: np.ndarray, d: np.ndarray, sig: np.ndarray, D: int, z: float,
               p_prev: np.ndarray | None = None, zsig: float | None = None):
    """Solve one position from n ranges.

    A (n, 3) anchor positions, d (n,) slant ranges, sig (n,) 1-sigma per range, D = 2 (z fixed at `z`)
    or 3 (z free, regularised toward `z` with sigma `zsig`).
    Returns dict(p=(3,), cov=(D, D), inlier=(n,) bool, rms=float metres, n=int) or None.
    """
    n = len(d)
    if n < 3:
        return None
    zp = z
    cands = []
    lin = _linear_init(A, d, D, z)
    if lin is not None:
        cands.append(lin)
    if p_prev is not None and np.all(np.isfinite(p_prev)):
        q = np.array(p_prev, dtype=float)
        if D == 2:
            q[2] = z
        cands.append(q)
    c = A.mean(axis=0).copy()
    c[2] = z
    cands.append(c)
    if D == 3 and lin is None:
        # coplanar anchors: start from the 2-D linear solution at the prior height
        l2 = _linear_init(A, d, 2, z)
        if l2 is not None:
            cands.insert(0, l2)
    best, best_cost = None, math.inf
    for c0 in cands:
        p, cost = _gn(A, d, sig, c0, D, zp, zsig)
        if np.all(np.isfinite(p)) and cost < best_cost:
            best, best_cost = p, cost
        if best_cost < 0.2 * n:   # good enough, skip other starts
            break
    if best is None:
        return None
    p = best
    diff = p - A
    e = (np.sqrt((diff * diff).sum(axis=1)) - d) / sig
    inl = np.abs(e) < INLIER_K
    # leave-one-out when something is clearly inconsistent and there is redundancy
    if not inl.all() or float(np.max(np.abs(e))) > INLIER_K:
        if n - 1 >= D + 1:
            bestj, bestc, bestp = None, math.inf, None
            for j in range(n):
                m = np.ones(n, dtype=bool)
                m[j] = False
                pj, cj = _gn(A[m], d[m], sig[m], p, D, zp, zsig, iters=8, plain_iters=2)
                if np.all(np.isfinite(pj)) and cj < bestc:
                    bestj, bestc, bestp = j, cj, pj
            if bestp is not None:
                diff = bestp - A
                ej = (np.sqrt((diff * diff).sum(axis=1)) - d) / sig
                if np.sum(np.abs(ej) < INLIER_K) >= np.sum(inl):
                    p, e = bestp, ej
                    inl = np.abs(e) < INLIER_K
    if inl.sum() < 3:
        return None
    # final plain weighted refinement on inliers
    if not inl.all():
        p, _ = _gn(A[inl], d[inl], sig[inl], p, D, zp, zsig, iters=5, plain_iters=5)
    diff = p - A
    r = np.maximum(np.sqrt((diff * diff).sum(axis=1)), 1e-6)
    res = r - d
    e = res / sig
    inl = np.abs(e) < INLIER_K
    if inl.sum() < 3 or not np.all(np.isfinite(p)):
        return None
    J = diff[inl, :D] / r[inl, None]
    w = 1.0 / sig[inl] ** 2
    H = (J.T * w) @ J + 1e-6 * np.eye(D)
    if D == 3 and zsig is not None:
        H[2, 2] += 1.0 / (zsig * zsig)
    try:
        cov = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return None
    dof = max(1, int(inl.sum()) - D)
    s2 = max(1.0, float((e[inl] ** 2).sum()) / dof)
    cov = cov * s2
    rms = float(np.sqrt(np.mean(res[inl] ** 2)))
    if not (np.all(np.isfinite(cov)) and math.isfinite(rms)):
        return None
    return {"p": p, "cov": cov, "inlier": inl, "rms": rms, "n": int(inl.sum()),
            "enorm": float(np.sqrt(np.mean(e[inl] ** 2)))}
