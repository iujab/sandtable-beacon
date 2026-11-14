"""Scout mode: where the room no longer matches the twin (docs/SCOUT_CONTRACT.md 2.2).

After registration the pucks are in the twin frame, so the twin predicts every first-order wall
echo each pair should hear (image-source method). Two kinds of disagreement are reported:

* unexpected_reflector: measured static echoes that no wall predicts within `tol_m`, on at least
  three pairs, whose ellipses (foci = the two pucks, path = the echo) cross at one place inside the
  room, and on at least half of the pairs that should hear an object there. Debris, a collapsed
  ceiling section, a cabinet that is not in the twin (or a person: pass located victims as `known`).
* missing_wall: a twin wall whose predicted echo is absent on most of the pairs that should hear it
  clearly (well clear of the direct path, inside the echo window, not blended with another wall).

Resolution is about 0.3 m of path (one CIR tap at ~500 MHz), so nothing is flagged on fewer than
three independent pairs and every text states the resolution and tolerance. Compass words assume
+x is roughly east and +y roughly north (the fixture twin is north-up). Simulated until boards exist.
"""
from __future__ import annotations

import math

import numpy as np

from sandtable.scout.register import MIN_EXCESS_M, RES_M, _ceiling_path, _compass, _Echoes, _Room, _wall_paths

__all__ = ["find_mismatches"]

MAX_REFLECTORS = 3
MIN_FRAC = 0.5            # share of the links that should hear an object there that must hear it (0.65 with only 3 links)
RMS_FRAC_OF_TOL = 0.25    # the ellipses must cross tightly: fit RMS <= tol/4 (0.11 m at the default 0.45 m)
MAX_RAD_M = 0.8           # 1-sigma position radius of the crossing; wider = near-tangent ellipses, not a point
GRID_M = 0.1


def _predict(room: _Room, polygon, pos: np.ndarray, ech: _Echoes):
    """(Q, K) predicted wall paths (NaN = no specular reflection) and (Q, K, 2) specular points.
    Uses R1's geometry.image_paths when it imports; the vectorised copy in register.py otherwise."""
    Q, K = len(ech.pairs), room.K
    try:
        from sandtable.scout import geometry
        path = np.full((Q, K), np.nan)
        spec = np.zeros((Q, K, 2))
        for q, (i, j) in enumerate(ech.pairs):
            for p in geometry.image_paths(pos[i].tolist(), pos[j].tolist(), polygon):
                k = int(p["wall"])
                if 0 <= k < K:
                    path[q, k] = float(p["path_m"])
                    spec[q, k] = p.get("point", (np.nan, np.nan))[:2]
        return path, spec
    except ImportError:
        path, spec = _wall_paths(room, pos[None], ech.ii, ech.jj, occlusion=True)
        return path[0], spec[0]


def _wall_name(room: _Room, k: int) -> tuple[str, str]:
    """('north', ', the one at y = 8.0') — the qualifier only when two walls share a compass word."""
    names = [_compass(-room.n[m, 0], -room.n[m, 1]) for m in range(room.K)]
    name = names[k]
    qual = ""
    if names.count(name) > 1:
        a, b = room.a[k], room.b[k]
        if abs(b[1] - a[1]) < abs(b[0] - a[0]):
            qual = f", the one at y = {(a[1] + b[1]) / 2:.1f}"
        else:
            qual = f", the one at x = {(a[0] + b[0]) / 2:.1f}"
    return name, qual


def _seg_dists(room: _Room, p: np.ndarray) -> np.ndarray:
    e = room.b - room.a
    d = p[None] - room.a
    t = np.clip(np.sum(d * e, axis=1) / room.len ** 2, 0, 1)
    q = d - t[:, None] * e
    return np.hypot(q[:, 0], q[:, 1])


def _where_words(room: _Room, x: np.ndarray) -> str:
    d = _seg_dists(room, x)
    order = np.argsort(d)
    n1, q1 = _wall_name(room, int(order[0]))
    words = f"{d[order[0]]:.1f} m inside the {n1} wall{q1}"
    for k in order[1:]:
        n2, q2 = _wall_name(room, int(k))
        if n2 != n1 and abs(float(np.dot(room.n[k], room.n[order[0]]))) < 0.5:       # a perpendicular wall
            words += f", {d[k]:.1f} m from the {n2} wall{q2}"
            break
    return words


def _refine_point(x, pos, ech, U, alive, tol_m):
    """Gauss-Newton on the crossing point with the supporting echoes (best one per pair).
    Returns (x, support columns into U, RMS path residual, 1-sigma position radius from the geometry)."""
    for _ in range(6):
        dx = np.hypot(*(pos - x).T)
        ex = dx[ech.ii] + dx[ech.jj]
        best: dict[int, tuple[float, int]] = {}
        for c, e in enumerate(U):
            if not alive[c]:
                continue
            r = abs(ex[ech.q[e]] - ech.L[e])
            q = int(ech.q[e])
            if r <= tol_m and (q not in best or r < best[q][0]):
                best[q] = (r, c)
        cols = [c for _, c in best.values()]
        if len(cols) < 2:
            return x, cols, float("inf"), float("inf")
        es = np.array([U[c] for c in cols])
        i, j = ech.ii[ech.q[es]], ech.jj[ech.q[es]]
        ui = (x - pos[i]) / np.maximum(np.hypot(*(x - pos[i]).T), 1e-6)[:, None]
        uj = (x - pos[j]) / np.maximum(np.hypot(*(x - pos[j]).T), 1e-6)[:, None]
        J = ui + uj                                                              # d(path)/dx
        res = ech.L[es] - (np.hypot(*(x - pos[i]).T) + np.hypot(*(x - pos[j]).T))
        JTJ = J.T @ J + 1e-6 * np.eye(2)
        step = np.linalg.solve(JTJ, J.T @ res)
        x = x + np.clip(step, -0.5, 0.5)
        if np.hypot(*step) < 1e-3:
            break
    rms = float(np.sqrt(np.mean(res ** 2)))
    cov = np.linalg.inv(JTJ) * max(0.1, rms) ** 2                                 # path noise floor 0.1 m
    rad = float(np.sqrt(np.trace(cov)))
    return x, cols, rms, rad


def find_mismatches(pucks: dict, echoes: list, polygon, tol_m: float = 0.45, *, door=None, height_m: float | None = None,
                    known: list | None = None) -> list[dict]:
    """See the module docstring. Additive keyword arguments: `door` [x, y] (echoes whose specular point
    falls in the door opening are not expected), `height_m` (model the ceiling echo so it is not called
    unexpected), `known` [{"at": [x, y]}, ...] (objects already accounted for, e.g. located victims:
    reflectors within 1 m of one are not reported). Each item also carries "tol_m" and "links_expected"."""
    ids = [k for k in sorted(pucks)]
    if len(ids) < 2 or not polygon:
        return []
    pos = np.array([[float(pucks[k][0]), float(pucks[k][1])] for k in ids])
    room = _Room(polygon)
    ech = _Echoes(ids, pos, echoes)
    if ech.E == 0:
        return []
    path, spec = _predict(room, polygon, pos, ech)
    door_xy = None
    if door is not None:
        door_xy = np.asarray(door.get("at") if isinstance(door, dict) else door, float)[:2]
    allp = path if not height_m else np.concatenate([path, _ceiling_path(ech.dij, height_m)[:, None]], axis=1)
    r = np.abs(allp[ech.q] - ech.L[:, None])
    r = np.min(np.where(np.isnan(r), np.inf, r), axis=1)                        # (E,)
    explained = r <= tol_m
    window = float(ech.L.max()) + RES_M
    out: list[dict] = []
    res_words = f"resolution about {RES_M:g} m of path, tolerance {tol_m:g} m"

    # --- unexpected reflectors -------------------------------------------------------------
    U = np.where(~explained)[0]
    if len(U) >= 3:
        xs = np.arange(room.lo[0] + GRID_M / 2, room.hi[0], GRID_M)
        ys = np.arange(room.lo[1] + GRID_M / 2, room.hi[1], GRID_M)
        G = np.stack(np.meshgrid(xs, ys, indexing="ij"), -1).reshape(-1, 2)
        G = G[room.inside(G, 0.05)]
        dG = np.hypot(G[:, None, 0] - pos[None, :, 0], G[:, None, 1] - pos[None, :, 1])      # (M, N)
        sig = max(RES_M, 0.67 * tol_m)
        alive = np.ones(len(U), bool)
        ell = dG[:, ech.ii[ech.q[U]]] + dG[:, ech.jj[ech.q[U]]] - ech.L[U][None]            # (M, |U|)
        suppressed = np.zeros(len(G), bool)
        known_xy = [np.asarray(k.get("at") if isinstance(k, dict) else k, float)[:2] for k in (known or [])]
        found = 0
        for _ in range(8):
            if found >= MAX_REFLECTORS:
                break
            k = np.clip(1 - (ell / sig) ** 2, 0, None) * alive[None]
            per = np.zeros((len(G), len(ech.pairs)))
            for col, e in enumerate(U):
                np.maximum(per[:, ech.q[e]], k[:, col], out=per[:, ech.q[e]])
            V = np.where(suppressed, 0.0, per.sum(axis=1))
            g = int(np.argmax(V))
            if V[g] < 2.0:
                break
            near = np.hypot(*(G - G[g]).T) <= 0.4
            wts = V[near] ** 2
            x = (G[near] * wts[:, None]).sum(axis=0) / wts.sum()
            x, sup_cols, rms, rad = _refine_point(x, pos, ech, U, alive, tol_m)
            dx = np.hypot(*(pos - x).T)
            ex = dx[ech.ii] + dx[ech.jj]                                             # reflector path per pair
            sup_pairs = sorted({int(ech.q[U[c]]) for c in sup_cols})
            wallp = np.where(np.isnan(path), np.inf, path)
            hidden = np.min(np.abs(wallp - ex[:, None]), axis=1) <= tol_m           # would read as a wall echo
            has_echo = np.isin(np.arange(len(ech.pairs)), ech.q)
            expected = (ex - ech.dij >= MIN_EXCESS_M + 0.1) & (ex <= window) & ~hidden & has_echo
            n_exp = int(max(expected.sum(), len(sup_pairs)))
            n_sup = len(sup_pairs)
            suppressed |= np.hypot(*(G - x).T) <= 0.6
            enough = n_sup >= 3 and (n_sup >= 0.65 * n_exp or (n_sup >= 4 and n_sup >= MIN_FRAC * n_exp))
            if not (enough and rms <= RMS_FRAC_OF_TOL * tol_m and rad <= MAX_RAD_M):
                continue
            for c in sup_cols:
                alive[c] = False
            if any(math.dist(x, kx) <= 1.0 for kx in known_xy):
                continue
            radius = round(float(np.clip(2.0 * rad, RES_M, 1.2)), 2)
            frac = n_sup / n_exp
            conf = round(float(min(0.95, frac * (1 - 0.6 ** (n_sup - 2)))), 2)     # 3 links 0.4, 4: 0.64, 5: 0.78, 7: 0.92
            lead = "Possible static echo" if n_sup == 3 else "Static echo"
            caveat = (" Only three links: three ellipses can cross by chance, so check it." if n_sup == 3 else "")
            found += 1
            out.append({"id": "", "kind": "unexpected_reflector", "confidence": conf,
                        "where": {"kind": "point", "at": [round(float(x[0]), 2), round(float(x[1]), 2)], "radius_m": radius},
                        "pairs": n_sup, "links_expected": n_exp, "tol_m": tol_m, "fit_rms_m": round(rms, 3),
                        "text": (f"{lead} {_where_words(room, x)}, where the twin shows open floor: an object that is not "
                                 f"in the twin (debris, furniture, or a person lying still). Heard on {n_sup} of {n_exp} links that should hear it, "
                                 f"within about {radius:g} m ({res_words}).{caveat}")})

    # --- missing walls ------------------------------------------------------------------------
    if ech.E >= 4:
        has_echo = np.isin(np.arange(len(ech.pairs)), ech.q)
        flagged = []
        for k in range(room.K):
            pk = path[:, k]
            others = np.delete(path, k, axis=1)
            o = np.where(np.isnan(others), np.inf, others)
            clear = np.min(np.abs(o - pk[:, None]), axis=1) > RES_M if o.shape[1] else np.ones(len(pk), bool)
            exp = (~np.isnan(pk)) & (pk - ech.dij >= 0.6) & (pk <= window) & clear & has_echo
            if door_xy is not None:
                exp &= np.hypot(spec[:, k, 0] - door_xy[0], spec[:, k, 1] - door_xy[1]) > 0.75
            n_exp = int(exp.sum())
            if n_exp < 3:
                continue
            heard = 0
            for q in np.where(exp)[0]:
                Ls = ech.L[ech.q == q]
                heard += int(len(Ls) > 0 and np.min(np.abs(Ls - pk[q])) <= tol_m)
            if heard <= n_exp / 3.0:
                flagged.append((k, n_exp, heard))
        if len(flagged) <= room.K // 2:          # most walls silent = a receiver problem, not a building one
            for k, n_exp, heard in flagged:
                name, qual = _wall_name(room, k)
                miss = n_exp - heard
                conf = round(float(min(0.95, (miss / n_exp) * min(1.0, n_exp / 5.0))), 2)
                a, b = room.a[k], room.b[k]
                out.append({"id": "", "kind": "missing_wall", "confidence": conf,
                            "where": {"kind": "segment", "a": [round(float(a[0]), 2), round(float(a[1]), 2)],
                                      "b": [round(float(b[0]), 2), round(float(b[1]), 2)]},
                            "pairs": miss, "links_expected": n_exp, "tol_m": tol_m,
                            "text": (f"No echo from the twin's {name} wall{qual} ({room.len[k]:.1f} m) on {miss} of {n_exp} links "
                                     "that should hear it clearly: it may be down or open (collapse, a breach), or something "
                                     f"in front of it absorbs. Do not rely on it as a boundary ({res_words}).")})
    for n, m in enumerate(out, 1):
        m["id"] = f"M{n}"
    return out
