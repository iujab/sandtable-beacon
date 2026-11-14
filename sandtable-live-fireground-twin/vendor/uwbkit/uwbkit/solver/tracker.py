"""Tracker: ranges in, INTERFACE `pos` messages out.

Anchor profiles: per tag, robust LSQ (init / fallback, src "lsq") + constant-velocity EKF over raw slant ranges
(src "ekf") with Huber-style down-weighting, 4-sigma innovation gating, per-link adaptive measurement noise,
LOS-based noise inflation, re-initialisation from LSQ after >= 3 consecutive bad ticks, and coasting for at most
1 s without accepted ranges.
Anchor-free (mesh): per-pair alpha-beta distance filters with gating, classical MDS (cold start) + weighted SMACOF
(warm start from the previous tick), canonical frame with flip hysteresis.
"""
from __future__ import annotations

import math

import numpy as np

from .geometry import canon_array, classical_mds, complete_distances, natural_key, smacof, stress_rms
from .multilat import robust_fix

SIGMA0 = 0.10          # base range sigma (m) for a LOS range
ACCEL_SIGMA = 0.8      # CV-model process noise (m/s^2) while the model fits (NIS ~ 1)
ACCEL_ADAPT_MAX = 16.0  # process-noise variance is scaled by the smoothed NIS, up to this factor (manoeuvres)
NIS_ALPHA = 0.5
INIT_POS_SIGMA = 0.1
INIT_VEL_SIGMA = 0.3
FLIP_MARGIN = 0.1     # mesh: keep the previous reflection while N3 (3-D: N4) is this close to the axis/plane
GATE = 4.0             # innovation gate (sigmas)
HUBER_K = 2.0          # down-weight innovations above this (sigmas)
MAX_COAST_S = 1.0      # stop publishing a track after this long without accepted ranges
BAD_TICKS_REINIT = 3   # consecutive bad ticks -> re-initialise from LSQ
HEADING_MIN_SPEED = 0.15


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _los(r) -> float:
    v = _num(r.get("los"))
    if v is None:
        return 1.0
    return min(1.0, max(0.0, v))


def range_sigma(los: float) -> float:
    return SIGMA0 * (1.0 + 2.5 * (1.0 - los))


def _r4(v) -> float:
    return round(float(v), 4) + 0.0   # + 0.0 turns -0.0 into 0.0


def make_pos(t, nid, p, v, cov2, n, q, src):
    """Build an INTERFACE `pos` dict, or None if anything is not finite."""
    vals = [t, p[0], p[1], p[2], v[0], v[1], v[2], cov2[0], cov2[1], cov2[2], q]
    for x in vals:
        if not math.isfinite(float(x)):
            return None
    speed = math.sqrt(float(v[0]) ** 2 + float(v[1]) ** 2 + float(v[2]) ** 2)
    msg = {"type": "pos", "t": _r4(t), "id": str(nid),
           "x": _r4(p[0]), "y": _r4(p[1]), "z": _r4(p[2]),
           "vx": _r4(v[0]), "vy": _r4(v[1]), "vz": _r4(v[2]), "speed": _r4(speed)}
    if speed >= HEADING_MIN_SPEED:
        msg["heading"] = _r4(math.atan2(float(v[1]), float(v[0])))
    sxx = max(_r4(cov2[0]), 0.0001)
    syy = max(_r4(cov2[2]), 0.0001)
    sxy = _r4(max(-math.sqrt(sxx * syy), min(math.sqrt(sxx * syy), float(cov2[1]))))
    msg["cov"] = [sxx, sxy, syy]
    msg["n"] = int(n)
    msg["q"] = _r4(min(1.0, max(0.0, float(q))))
    msg["src"] = src
    return msg


def _hdop(Hh: np.ndarray) -> float:
    """Horizontal DOP from (m, 2) unit-ish direction rows."""
    if len(Hh) < 2:
        return math.inf
    a = float(Hh[:, 0] @ Hh[:, 0])
    b = float(Hh[:, 0] @ Hh[:, 1])
    c = float(Hh[:, 1] @ Hh[:, 1])
    det = a * c - b * b
    if det < 1e-6:
        return math.inf
    return math.sqrt((a + c) / det)


def quality(n_used, n_meas, hdop, rms, los_mean, pos_var, age) -> float:
    q_geom = min(1.0, 1.6 / (hdop + 0.6)) if math.isfinite(hdop) else 0.15
    q_res = 1.0 / (1.0 + (rms / 0.2) ** 2)
    q_n = min(1.0, (n_used + 1) / 5.0)
    q_los = 0.4 + 0.6 * los_mean
    q_rej = math.sqrt(n_used / n_meas) if n_meas > 0 else 1.0
    q_cov = 1.0 / (1.0 + pos_var / 0.35 ** 2)
    coast = max(0.0, 1.0 - 0.85 * min(1.0, age / MAX_COAST_S))
    return q_geom * q_res * q_n * q_los * q_rej * q_cov * coast


class _TagTrack:
    """One tag: robust LSQ init + constant-velocity EKF on slant ranges."""

    def __init__(self, tid: str, D: int, z0: float):
        self.id = tid
        self.D = D
        self.z0 = z0
        self.x = None          # state [pos(D), vel(D)]
        self.P = None
        self.t = None          # time of the state
        self.t_meas = None     # time of the last accepted update
        self.bad = 0
        self.mult = {}         # per-anchor adaptive R multiplier (EWMA of NIS)
        self.qbase = 0.0
        self.qs = None         # displayed quality: qbase smoothed over ~0.3 s so UIs do not flicker
        self.n_last = 0
        self.nis = 1.0         # smoothed normalised innovation squared (drives adaptive process noise)

    # ---- helpers
    def _p3(self) -> np.ndarray:
        if self.D == 3:
            return self.x[:3]
        return np.array([self.x[0], self.x[1], self.z0])

    def _init_from(self, fix, t):
        D = self.D
        self.x = np.zeros(2 * D)
        self.x[:D] = fix["p"][:D]
        self.P = np.zeros((2 * D, 2 * D))
        cov = fix["cov"]
        # generous position variance, modest velocity variance: the next epochs correct position first instead
        # of reading the single-epoch LSQ error as a fast velocity (adaptive Q still lets a moving tag converge)
        self.P[:D, :D] = cov + np.eye(D) * INIT_POS_SIGMA ** 2
        self.P[D:, D:] = np.eye(D) * INIT_VEL_SIGMA ** 2
        self.t = t
        self.t_meas = t
        self.bad = 0
        self.mult = {}
        self.nis = 1.0

    def drop(self):
        self.x = None
        self.P = None
        self.bad = 0
        self.mult = {}

    def _predict(self, dt):
        if dt <= 0:
            return
        D = self.D
        x, P = self.x, self.P
        x[:D] += x[D:] * dt
        # P <- F P F^T with F = [[I, dt I], [0, I]] (block form, no matrix build)
        P[:D, :] += dt * P[D:, :]
        P[:, :D] += dt * P[:, D:]
        qa = ACCEL_SIGMA ** 2 * min(ACCEL_ADAPT_MAX, max(1.0, self.nis))
        q11, q12, q22 = qa * dt ** 4 / 4.0, qa * dt ** 3 / 2.0, qa * dt * dt
        for i in range(D):
            P[i, i] += q11
            P[i, D + i] += q12
            P[D + i, i] += q12
            P[D + i, D + i] += q22

    def _ekf_update(self, meas, zsig):
        """Sequential scalar updates. Returns (n_used, n_gated, rows_used, los_used)."""
        D = self.D
        n_used = n_gated = 0
        rows, los_used, used_idx = [], [], []
        zsum = 0.0
        for k, (aid, a, d, los) in enumerate(meas):
            p3 = self._p3()
            diff = p3 - a
            r = math.sqrt(float(diff @ diff))
            if r < 1e-3:
                continue
            H = np.zeros(2 * D)
            H[:D] = diff[:D] / r
            nu = d - r
            PHt = self.P @ H
            hph = float(H @ PHt)
            r0 = range_sigma(los) ** 2
            m = self.mult.get(aid, 1.0)
            S = hph + r0 * m
            z = nu / math.sqrt(S)
            z0sq = min(nu * nu / (hph + r0), GATE * GATE * 1.5)
            self.mult[aid] = min(16.0, max(1.0, 0.9 * m + 0.1 * z0sq))
            az = abs(z)
            if az > GATE:
                n_gated += 1
                zsum += GATE * GATE
                continue
            zsum += z * z
            if az > HUBER_K:
                S = hph + r0 * m * az / HUBER_K
            K = PHt / S
            self.x += K * nu
            self.P -= np.outer(K, PHt)
            n_used += 1
            rows.append(H[:2].copy())
            los_used.append(los)
            used_idx.append(k)
        if D == 3 and zsig is not None:
            H = np.zeros(6)
            H[2] = 1.0
            PHt = self.P @ H
            S = float(PHt[2]) + zsig * zsig
            K = PHt / S
            self.x += K * (self.z0 - self.x[2])
            self.P -= np.outer(K, PHt)
        self.P = 0.5 * (self.P + self.P.T)
        if n_used + n_gated:
            self.nis += NIS_ALPHA * (zsum / (n_used + n_gated) - self.nis)
        return n_used, n_gated, rows, los_used, used_idx

    def _lsq(self, meas, zsig, p_prev=None):
        A = np.array([m[1] for m in meas])
        d = np.array([m[2] for m in meas])
        sig = np.array([range_sigma(m[3]) for m in meas])
        return robust_fix(A, d, sig, self.D, self.z0, p_prev=p_prev, zsig=zsig)

    def _fix_quality(self, fix, meas, t):
        inl = fix["inlier"]
        A = np.array([m[1] for m in meas])[inl]
        diff = fix["p"] - A
        r = np.maximum(np.sqrt((diff * diff).sum(axis=1)), 1e-6)
        hd = _hdop(diff[:, :2] / r[:, None])
        los_m = float(np.mean([m[3] for m, ok in zip(meas, inl) if ok]))
        return quality(fix["n"], len(meas), hd, fix["rms"], los_m, 0.0, 0.0)   # cov term applied in _emit

    # ---- main step
    def step(self, meas, t, zsig):
        """meas: list of (anchor_id, anchor_xyz(3,), d, los). Returns a pos dict or None."""
        src = "ekf"
        n_used = 0
        if self.x is not None:
            if t - self.t_meas > MAX_COAST_S or not np.all(np.isfinite(self.x)):
                self.drop()
        if self.x is not None:
            dt = min(max(t - self.t, 0.0), MAX_COAST_S)
            self._predict(dt)
            self.t = max(self.t, t)
            if meas:
                n_used, n_gated, rows, los_used, used_idx = self._ekf_update(meas, zsig)
                bad = len(meas) >= 2 and 2 * n_gated >= len(meas) or (len(meas) == 1 and n_gated == 1)
                self.bad = self.bad + 1 if bad else 0
                if self.bad >= BAD_TICKS_REINIT and len(meas) >= 3:
                    fix = self._lsq(meas, zsig)
                    if fix is not None:
                        self._init_from(fix, t)
                        self.qbase = self._fix_quality(fix, meas, t)
                        self.n_last = fix["n"]
                        return self._emit(t, "lsq", fix["n"], self.qbase)
                if n_used > 0:
                    self.t_meas = t
                    p3 = self._p3()
                    res = [float(np.linalg.norm(p3 - meas[k][1]) - meas[k][2]) for k in used_idx]
                    rms = math.sqrt(sum(r * r for r in res) / len(res))
                    hd = _hdop(np.array(rows))
                    self.qbase = quality(n_used, len(meas), hd, rms, sum(los_used) / n_used, 0.0, 0.0)
            if n_used == 0:
                # coasting: damp velocity (time constant 1 s)
                self.x[self.D:] *= math.exp(-dt)
            # divergence guard
            pv = float(self.P[0, 0] + self.P[1, 1])
            if not (math.isfinite(pv) and pv < 25.0 and np.all(np.isfinite(self.x))):
                self.drop()
        if self.x is None:
            if len(meas) < 3:
                return None
            fix = self._lsq(meas, zsig)
            if fix is None:
                return None
            self._init_from(fix, t)
            self.qbase = self._fix_quality(fix, meas, t)
            self.n_last = fix["n"]
            return self._emit(t, "lsq", fix["n"], self.qbase)
        return self._emit(t, src, n_used, self.qbase)

    def _emit(self, t, src, n, qbase):
        D = self.D
        age = max(0.0, t - self.t_meas)
        pv = float(self.P[0, 0] + self.P[1, 1])
        coast = max(0.0, 1.0 - 0.85 * min(1.0, age / MAX_COAST_S))
        if src == "lsq" or self.qs is None:
            self.qs = qbase                       # fresh fix: no history to smooth with
        elif age <= 0.0:
            self.qs += 0.3 * (qbase - self.qs)    # per-tick residuals are noisy; q should not flicker
        q = self.qs * coast / (1.0 + pv / 0.35 ** 2)
        p3 = self._p3()
        v = np.zeros(3)
        v[:D] = self.x[D:]
        cov2 = (float(self.P[0, 0]), float(self.P[0, 1]), float(self.P[1, 1]))
        msg = make_pos(t, self.id, p3, v, cov2, n, q, src)
        if msg is None:
            self.drop()
        return msg


class _PairFilter:
    """Alpha-beta filter on one pairwise distance with outlier gating."""
    __slots__ = ("d", "v", "t", "los", "nrej", "n")

    def __init__(self):
        self.d = 0.0
        self.v = 0.0
        self.t = None
        self.los = 1.0
        self.nrej = 0
        self.n = 0

    def update(self, d, los, t):
        if self.t is None:
            self.d, self.v, self.t, self.los, self.n = d, 0.0, t, los, 1
            return
        dt = min(max(t - self.t, 0.0), 2.0)
        pred = self.d + self.v * dt
        nu = d - pred
        gate = 0.4 + 0.5 * dt + 0.3 * (1.0 - los)
        if abs(nu) > gate:
            self.nrej += 1
            if self.nrej < 3:
                return
            self.d, self.v = d, 0.0     # persistent jump: accept it
        else:
            alpha = 0.45 if self.n > 3 else 0.8
            beta = 0.06
            self.d = pred + alpha * nu
            if dt > 1e-3:
                self.v = max(-3.0, min(3.0, self.v + beta * nu / dt))
        self.nrej = 0
        self.n += 1
        self.t = t
        self.los = 0.7 * self.los + 0.3 * los

    def value(self, t):
        return max(0.0, self.d + self.v * min(max(t - self.t, 0.0), 0.5))


class _Mesh:
    """Anchor-free mesh solver (MDS + SMACOF) in the canonical frame (ids in natural order: N1 origin, N2 +x, N3 y>0).

    Every node that has ranges gets a pos (also anchor-role nodes when no anchor coordinates are known yet:
    for the usual A1..A4 layout that relative frame already equals the site frame)."""

    def __init__(self, dims: int, nodes: dict | None, rate_hz: float):
        self.dims = 3 if dims == 3 else 2
        self.nodes = nodes or {}
        self.rate_hz = rate_hz
        self.pairs: dict[tuple, _PairFilter] = {}
        self.prev_ids = None
        self.prev_X = None      # (n, k) canonical solution of the previous tick
        self.prev_t = None
        self.vel: dict = {}
        self.last_pos: dict = {}

    def reset(self, node_id=None):
        if node_id is None:
            self.pairs.clear()
            self.prev_ids = self.prev_X = None
            self.vel.clear()
            self.last_pos.clear()
            return
        for key in [k for k in self.pairs if node_id in k]:
            del self.pairs[key]
        self.prev_ids = self.prev_X = None
        self.vel.pop(node_id, None)
        self.last_pos.pop(node_id, None)

    def _height(self, nid):
        info = self.nodes.get(nid)
        if isinstance(info, dict):
            z = _num(info.get("z"))
            if z is not None:
                return z
        return None

    def update(self, ranges, t):
        batch: dict = {}
        for r in ranges:
            a, b, d = r.get("a"), r.get("b"), _num(r.get("d"))
            if a is None or b is None or d is None or d < 0 or d > 500 or a == b:
                continue
            a, b = str(a), str(b)
            key = (a, b) if natural_key(a) <= natural_key(b) else (b, a)
            batch.setdefault(key, []).append((d, _los(r)))
        for key, lst in batch.items():
            ds = sorted(x[0] for x in lst)
            d = ds[len(ds) // 2]
            los = sum(x[1] for x in lst) / len(lst)
            self.pairs.setdefault(key, _PairFilter()).update(d, los, t)
        return self._solve(t, set(batch.keys()))

    def _solve(self, t, fresh_keys):
        k = self.dims
        live = {key: f for key, f in self.pairs.items() if f.t is not None and t - f.t <= 3.0}
        ids = sorted({i for key in live for i in key}, key=natural_key)
        n = len(ids)
        if n < 2:
            return []
        idx = {nid: i for i, nid in enumerate(ids)}
        Dm = np.zeros((n, n))
        W = np.zeros((n, n))
        age_node = {nid: math.inf for nid in ids}
        deg = {nid: 0 for nid in ids}
        los_node = {nid: [] for nid in ids}
        for (a, b), f in live.items():
            age = t - f.t
            d = f.value(t)
            if k == 2:
                za, zb = self._height(a), self._height(b)
                if za is not None and zb is not None:
                    d = math.sqrt(max(d * d - (za - zb) ** 2, 0.0))
            w = (0.25 + 0.75 * f.los) * (1.0 if age <= 0.5 else math.exp(-(age - 0.5)))
            i, j = idx[a], idx[b]
            Dm[i, j] = Dm[j, i] = d
            W[i, j] = W[j, i] = w
            for nid in (a, b):
                age_node[nid] = min(age_node[nid], age)
                los_node[nid].append(f.los)
                if (a, b) in fresh_keys:
                    deg[nid] += 1
        warm = self.prev_ids == ids and self.prev_X is not None
        if warm:
            X = smacof(Dm, W, self.prev_X, iters=12, tol=1e-4)
            if stress_rms(Dm, W, X) > 0.25:
                Xc = smacof(Dm, W, classical_mds(complete_distances(Dm, W), k), iters=80, tol=1e-5)
                if stress_rms(Dm, W, Xc) < stress_rms(Dm, W, X):
                    X = Xc
        else:
            X = smacof(Dm, W, classical_mds(complete_distances(Dm, W), k), iters=80, tol=1e-5)
        if not np.all(np.isfinite(X)):
            self.prev_ids = self.prev_X = None
            return []
        P = np.zeros((n, 3))
        P[:, :k] = X
        ref = list(range(min(n, 4)))
        Q = canon_array(P, ref, k)
        # flip hysteresis: when the reflection-defining node is near the axis, stay on the previous side
        if warm and n >= 3:
            fl_axis = 2 if k == 3 else 1
            fl_node = 3 if k == 3 else 2
            if fl_node < n and abs(Q[fl_node, fl_axis]) < FLIP_MARGIN:
                Qm = Q.copy()
                Qm[:, fl_axis] *= -1.0
                prev = self.prev_X
                if np.sum((Qm[:, :k] - prev) ** 2) < np.sum((Q[:, :k] - prev) ** 2):
                    Q = Qm
        self.prev_ids = ids
        self.prev_X = Q[:, :k].copy()
        # per-node residual rms
        diff = Q[:, None, :k] - Q[None, :, :k]
        dist = np.sqrt((diff * diff).sum(axis=-1))
        err2 = W * (dist - Dm) ** 2
        wsum = W.sum(axis=1)
        out = []
        dt = (t - self.prev_t) if self.prev_t is not None else 0.0
        self.prev_t = t
        for nid, i in idx.items():
            if age_node[nid] > MAX_COAST_S:
                continue
            p = np.zeros(3)
            p[:k] = Q[i, :k]
            if k == 2:
                h = self._height(nid)
                p[2] = h if h is not None else 0.0
            v = self.vel.get(nid, np.zeros(3))
            lp = self.last_pos.get(nid)
            if lp is not None and dt > 1e-3:
                inst = (p - lp) / dt
                sp = float(np.linalg.norm(inst))
                if sp > 5.0:          # frame/jump artefact: do not let it into velocity
                    inst = v
                v = 0.7 * v + 0.3 * inst
            self.vel[nid] = v
            self.last_pos[nid] = p
            rms = math.sqrt(float(err2[i].sum()) / float(wsum[i])) if wsum[i] > 0 else 1.0
            nd = len(los_node[nid])
            var = (rms * rms + 0.04 ** 2) * 2.0 / max(1, nd)
            los_m = sum(los_node[nid]) / nd if nd else 0.0
            q = (min(1.0, (nd + 1) / max(2.0, float(n))) * (1.0 / (1.0 + (rms / 0.2) ** 2))
                 * (0.4 + 0.6 * los_m) * max(0.0, 1.0 - 0.85 * min(1.0, age_node[nid] / MAX_COAST_S)))
            msg = make_pos(t, nid, p, v, (var, 0.0, var), deg[nid], q, "mds")
            if msg is not None:
                out.append(msg)
        return out


class Tracker:
    """Ranges -> `pos` messages. See docs/CONTRACTS.md section 2."""

    def __init__(self, profile: str, anchors: dict | None, dims: int = 2,
                 nodes: dict | None = None, rate_hz: float = 10.0):
        self.profile = str(profile or "")
        self.dims = 3 if dims == 3 else 2
        self.nodes = dict(nodes or {})
        self.rate_hz = float(rate_hz) if rate_hz else 10.0
        self.tracks: dict[str, _TagTrack] = {}
        self.anchors: dict[str, np.ndarray] = {}
        self._zsig = None
        self.mesh = None
        self.set_anchors(anchors or {})

    @property
    def mode(self) -> str:
        return "mesh" if self.mesh is not None else "anchors"

    def set_anchors(self, anchors: dict) -> None:
        """Calibration changed. Tracks are kept (the EKF re-initialises itself if anchors moved a lot)."""
        if isinstance(anchors, dict) and isinstance(anchors.get("anchors"), dict):
            anchors = anchors["anchors"]       # a whole `anchors` message
        parsed = {}
        for aid, p in (anchors or {}).items():
            if not isinstance(p, dict):
                continue
            x, y, z = _num(p.get("x")), _num(p.get("y")), _num(p.get("z", 0.0))
            if x is None or y is None:
                continue
            parsed[str(aid)] = np.array([x, y, z if z is not None else 0.0])
        self.anchors = parsed
        if parsed:
            zs = [a[2] for a in parsed.values()]
            self._zsig = 0.5 if (max(zs) - min(zs)) < 0.3 else 3.0
        mesh_mode = self.profile == "mesh5" or not parsed
        if mesh_mode and self.mesh is None:
            self.mesh = _Mesh(self.dims, self.nodes, self.rate_hz)
        elif not mesh_mode:
            self.mesh = None

    def reset(self, node_id: str | None = None) -> None:
        if node_id is None:
            self.tracks.clear()
            if self.mesh is not None:
                self.mesh.reset()
        else:
            self.tracks.pop(str(node_id), None)
            if self.mesh is not None:
                self.mesh.reset(str(node_id))

    def _tag_z(self, tid: str) -> float:
        info = self.nodes.get(tid)
        if isinstance(info, dict):
            z = _num(info.get("z"))
            if z is not None:
                return z
        return 1.0

    def _is_anchor_role(self, nid: str) -> bool:
        info = self.nodes.get(nid)
        return isinstance(info, dict) and info.get("role") == "anchor"

    def update(self, ranges: list, t: float) -> list:
        t = float(t)
        rs = [r for r in (ranges or []) if isinstance(r, dict) and r.get("type", "range") == "range"]
        if self.mesh is not None:
            return self.mesh.update(rs, t)
        groups: dict = {}
        for r in rs:
            a, b, d = r.get("a"), r.get("b"), _num(r.get("d"))
            if a is None or b is None or d is None or d < 0 or d > 500:
                continue
            a, b = str(a), str(b)
            if a in self.anchors and b not in self.anchors and not self._is_anchor_role(b):
                tag, anc = b, a
            elif b in self.anchors and a not in self.anchors and not self._is_anchor_role(a):
                tag, anc = a, b
            else:
                continue        # anchor-anchor, tag-tag, or unknown anchor
            groups.setdefault(tag, {}).setdefault(anc, []).append((d, _los(r)))
        out = []
        for tag in sorted(set(groups) | set(self.tracks), key=natural_key):
            meas = []
            for anc, lst in groups.get(tag, {}).items():
                ds = sorted(x[0] for x in lst)
                meas.append((anc, self.anchors[anc], ds[len(ds) // 2], sum(x[1] for x in lst) / len(lst)))
            tr = self.tracks.get(tag)
            if tr is None:
                tr = self.tracks[tag] = _TagTrack(tag, self.dims, self._tag_z(tag))
            msg = tr.step(meas, t, self._zsig if self.dims == 3 else None)
            if msg is not None:
                out.append(msg)
            elif tr.x is None and not meas:
                del self.tracks[tag]
        return out
