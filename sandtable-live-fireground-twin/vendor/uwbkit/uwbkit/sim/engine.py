"""The uwbkit Simulator (see the uwbkit.sim module docstring for the noise model)."""
from __future__ import annotations

import math

import numpy as np

from .paths import Path, build_keyframes
from .scenario import load_scenario


class Simulator:
    """Deterministic two-way-ranging simulator driven by a scenario (see ``uwbkit.sim`` for the noise model).

    Time comes only from ``step(now)``; the system clock is never read.
    """

    def __init__(self, scenario, seed: int | None = None):
        sc = load_scenario(scenario)
        if seed is not None:
            sc["seed"] = int(seed)
        self.scenario = sc
        self.name: str = sc["name"]
        self.profile: str = sc["profile"]
        self.dims: int = sc["dims"]
        self.rate_hz: float = float(sc["rate_hz"])
        self.room: dict = sc["room"]
        self.seed: int = int(sc["seed"])
        self.loop: bool = sc["loop"]
        self.duration_s: float = float(sc["duration_s"])
        self.anchors: dict = {aid: {"x": a["x"], "y": a["y"], "z": a["z"]} for aid, a in sorted(sc["anchors"].items())}
        movers = sorted(sc["nodes"])
        self.nodes: dict = {aid: {"role": "anchor"} for aid in self.anchors}
        for nid in movers:
            spec = sc["nodes"][nid]
            info = {"role": spec["role"]}
            if "label" in spec:
                info["label"] = spec["label"]
            info["z"] = spec["z"]
            self.nodes[nid] = info
        self._ids = list(self.nodes)
        self._index = {nid: i for i, nid in enumerate(self._ids)}

        # paths (anchors are static paths)
        self._paths = []
        self._jit = []
        for i, nid in enumerate(self._ids):
            if nid in self.anchors:
                a = self.anchors[nid]
                self._paths.append(Path([0.0], [(a["x"], a["y"], a["z"])], "linear", self.loop))
                self._jit.append(None)
                continue
            spec = sc["nodes"][nid]
            times, pts = build_keyframes(spec, self.loop)
            self._paths.append(Path(times, pts, spec["path_mode"], self.loop))
            jm = spec["jitter_m"]
            if jm > 0:  # smooth sway: per axis two unit sinusoids at 0.2-0.7 Hz with seeded phases (RMS = jitter_m)
                jr = np.random.default_rng([self.seed, 1, i])
                w = (2 * math.pi * jr.uniform(0.2, 0.7, 4)).tolist()
                ph = jr.uniform(0, 2 * math.pi, 4).tolist()
                self._jit.append((jm, w, ph))
            else:
                self._jit.append(None)

        # measurement pairs: every mover to every anchor (a = mover, b = anchor), then mover pairs a < b
        anchor_idx = [self._index[a] for a in self.anchors]
        mover_idx = [self._index[m] for m in movers]
        pairs = [(m, a) for m in mover_idx for a in anchor_idx]
        if len(mover_idx) >= 2:
            pairs += [(p, q) for k, p in enumerate(mover_idx) for q in mover_idx[k + 1:]]
        self._pairs = self._pairset(pairs)
        survey_ids = anchor_idx if len(anchor_idx) >= 2 else mover_idx
        self._survey_pairs = self._pairset(
            [(p, q) if self._ids[p] < self._ids[q] else (q, p)
             for k, p in enumerate(survey_ids) for q in survey_ids[k + 1:]])

        # noise + walls
        nz = sc["noise"]
        self._nz = {k: float(v) for k, v in nz.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        walls = self.room.get("walls", []) if self.room else []
        self._W = len(walls)
        if self._W:
            self._wa = np.array([w["a"] for w in walls], dtype=float)
            self._wb = np.array([w["b"] for w in walls], dtype=float)
            self._wbias = np.array([w["nlos_bias_m"] for w in walls], dtype=float)
            self._watt = np.array([w["attenuation_db"] for w in walls], dtype=float)
            self._wvar = np.array([w["nlos_extra_sigma_m"] ** 2 for w in walls], dtype=float)
        self._nbias = np.array([0.0 if nid in self.anchors else float(sc["nodes"][nid]["bias_m"]) for nid in self._ids])
        self.noise_model: dict = {k: self._nz[k] for k in sorted(self._nz)}
        self.noise_model["walls"] = [
            {"a": w["a"], "b": w["b"], "nlos_bias_m": w["nlos_bias_m"], "attenuation_db": w["attenuation_db"],
             "nlos_extra_sigma_m": w["nlos_extra_sigma_m"]} for w in walls]

        # events (absolute scenario times; repeat every duration_s when loop)
        self._events = sorted(
            ((float(ev["t"]), nid, ev["name"], ev.get("data") or {})
             for nid in movers for ev in sc["nodes"][nid]["events"]), key=lambda e: (e[0], e[1]))

        self.speed: float = 1.0
        self._survey_count = 0
        self._reset()

    # ------------------------------------------------------------------------------------------ internals

    def _pairset(self, pairs):
        ia = np.array([p for p, _ in pairs], dtype=int)
        ib = np.array([q for _, q in pairs], dtype=int)
        names = [(self._ids[p], self._ids[q]) for p, q in pairs]
        return ia, ib, names

    def _reset(self):
        self._rng = np.random.default_rng(self.seed)
        self._prev_now = None
        self._last_now = 0.0
        self.t_sim: float = 0.0
        self.paused: bool = False
        self._override = {}
        self._offset = [0.0] * len(self._ids)

    def _node_index(self, node_id) -> int:
        try:
            return self._index[str(node_id)]
        except KeyError:
            raise ValueError(f"unknown node {node_id!r}; nodes are {', '.join(self._ids)}") from None

    def _state(self, t: float):
        out = []
        for i in range(len(self._ids)):
            ov = self._override.get(i)
            if ov is not None:
                out.append((ov[0], ov[1], ov[2], 0.0, 0.0, 0.0))
                continue
            x, y, z, vx, vy, vz = self._paths[i].eval(t + self._offset[i])
            j = self._jit[i]
            if j is not None:
                jm, w, ph = j
                x += jm * (math.sin(w[0] * t + ph[0]) + math.sin(w[1] * t + ph[1]))
                y += jm * (math.sin(w[2] * t + ph[2]) + math.sin(w[3] * t + ph[3]))
                vx += jm * (w[0] * math.cos(w[0] * t + ph[0]) + w[1] * math.cos(w[1] * t + ph[1]))
                vy += jm * (w[2] * math.cos(w[2] * t + ph[2]) + w[3] * math.cos(w[3] * t + ph[3]))
            out.append((x, y, z, vx, vy, vz))
        return out

    def _crossings(self, P, Q):
        """(n_pairs, n_walls) bool: does the 2-D segment P-Q properly cross wall a-b."""
        a = self._wa[None, :, :]
        e = (self._wb - self._wa)[None, :, :]
        p = P[:, None, :]
        r = (Q - P)[:, None, :]
        pa = p - a
        qa = pa + r
        d1 = e[..., 0] * pa[..., 1] - e[..., 1] * pa[..., 0]
        d2 = e[..., 0] * qa[..., 1] - e[..., 1] * qa[..., 0]
        ap = -pa
        bp = ap + e
        d3 = r[..., 0] * ap[..., 1] - r[..., 1] * ap[..., 0]
        d4 = r[..., 0] * bp[..., 1] - r[..., 1] * bp[..., 0]
        return (d1 * d2 < 0) & (d3 * d4 < 0)

    def _measure(self, st, pairset, rng, now):
        ia, ib, names = pairset
        n = len(names)
        if n == 0:
            return []
        nz = self._nz
        pos = np.array([s[:3] for s in st], dtype=float)
        A, B = pos[ia], pos[ib]
        diff = A - B
        true = np.sqrt((diff * diff).sum(axis=1))
        N = rng.standard_normal((n, 3))
        U = rng.random((n, 4))
        if self._W:
            k = nz["nlos_bias_gamma_k"]
            G = rng.gamma(k, 1.0 / k, (n, self._W))
            cross = self._crossings(A[:, :2], B[:, :2]).astype(float)
            n_walls = cross.sum(axis=1)
            bias = (cross * self._wbias[None, :] * G).sum(axis=1)
            extra_var = cross @ self._wvar
            att = cross @ self._watt
        else:
            n_walls = bias = extra_var = att = np.zeros(n)
        sig = np.sqrt(nz["sigma_m"] ** 2 + extra_var)
        d = true + bias + sig * N[:, 0] + self._nbias[ia] + self._nbias[ib]
        is_out = U[:, 1] < nz["outlier_prob"]
        mag = nz["outlier_m"] * (0.2 + 0.8 * U[:, 2])
        long_out = is_out & (U[:, 3] >= nz["outlier_neg_frac"])
        short_out = is_out & ~long_out
        d = d + np.where(long_out, mag, 0.0) - np.where(short_out, nz["outlier_neg_scale"] * mag, 0.0)
        d = np.maximum(d, 0.0)
        raw = d + nz["antenna_offset_m"]
        rssi = (nz["rssi_1m_dbm"] - 10.0 * nz["path_loss_exp"] * np.log10(np.maximum(true, 0.1))
                - att + nz["rssi_sigma_db"] * N[:, 1])
        gap = (nz["fp_gap_los_db"] + nz["fp_gap_sigma_db"] * np.abs(N[:, 2]) + nz["fp_nlos_frac"] * att
               + nz["fp_outlier_db"] * long_out)
        fp = rssi - gap
        los = 1.0 / (1.0 + np.exp((gap - nz["los_mid_db"]) / nz["los_scale_db"]))
        far = nz["far_m"]
        p_drop = (nz["dropout_prob"] * (1.0 + nz["dropout_per_wall"] * n_walls)
                  * (1.0 + np.maximum(0.0, true - far) / far))
        keep = U[:, 0] >= np.minimum(p_drop, 0.95)
        dl = np.round(d, 3).tolist()
        rl = np.round(raw, 3).tolist()
        sl = np.round(rssi, 1).tolist()
        fl = np.round(fp, 1).tolist()
        ll = np.round(los, 2).tolist()
        kl = keep.tolist()
        out = []
        for k in range(n):
            if kl[k]:
                a, b = names[k]
                out.append({"type": "range", "t": now, "a": a, "b": b, "d": dl[k], "raw": rl[k],
                            "rssi": sl[k], "fp": fl[k], "los": ll[k]})
        return out

    def _fire_events(self, t0: float, t1: float, now: float, include_start: bool):
        fired = []
        D = self.duration_s
        for te, nid, name, data in self._events:
            if self.loop:
                for k in range(int(math.floor(t0 / D)), int(math.floor(t1 / D)) + 1):
                    ta = k * D + te
                    if (ta > t0 or (include_start and ta == t0)) and ta <= t1:
                        fired.append((ta, nid, name, data))
            elif (te > t0 or (include_start and te == t0)) and te <= t1:
                fired.append((te, nid, name, data))
        fired.sort(key=lambda e: (e[0], e[1]))
        return [{"type": "event", "t": now, "id": nid, "name": name, "data": dict(data)}
                for _, nid, name, data in fired]

    # ------------------------------------------------------------------------------------------ public API

    def step(self, now: float) -> list[dict]:
        """One measurement cycle at caller-supplied wall time ``now`` (Unix s). Returns range + event dicts."""
        now = float(now)
        if self._prev_now is None:  # first call: scenario starts at sim time 0
            self._prev_now = now
            events = self._fire_events(self.t_sim, self.t_sim, now, include_start=True)
        else:
            dt = 0.0 if self.paused else (now - self._prev_now) * self.speed
            dt = min(max(dt, 0.0), 1.0)
            self._prev_now = now
            t0 = self.t_sim
            self.t_sim = t0 + dt
            events = self._fire_events(t0, self.t_sim, now, include_start=False) if dt > 0 else []
        self._last_now = now
        return self._measure(self._state(self.t_sim), self._pairs, self._rng, now) + events

    def truth(self) -> dict:
        """Ground truth at the current sim time: every node, absolute scenario coordinates, velocity in m/s
        of sim time (exact path derivative; 0 while paused or teleported)."""
        nodes = {}
        for nid, (x, y, z, vx, vy, vz) in zip(self._ids, self._state(self.t_sim)):
            if self.paused:
                vx = vy = vz = 0.0
            nodes[nid] = {"x": round(x, 4), "y": round(y, 4), "z": round(z, 4),
                          "vx": round(vx, 4), "vy": round(vy, 4), "vz": round(vz, 4)}
        return {"t_sim": round(self.t_sim, 6), "nodes": nodes}

    def teleport(self, node_id: str, x: float, y: float, z: float | None = None) -> None:
        """Move a node to (x, y[, z]) and suspend its path until ``release`` or ``control("restart")``."""
        i = self._node_index(node_id)
        if z is None:
            z = self._state(self.t_sim)[i][2]
        self._override[i] = (float(x), float(y), float(z))

    def release(self, node_id: str) -> None:
        """Resume a teleported node's path from the path point nearest to where it was put."""
        i = self._node_index(node_id)
        ov = self._override.pop(i, None)
        if ov is None:
            return
        path = self._paths[i]
        if path.T > 0:
            self._offset[i] = path.nearest_time(*ov) - self.t_sim

    def control(self, cmd: str, value: float | None = None) -> None:
        """``pause`` | ``resume`` | ``restart`` (t_sim -> 0, releases teleports, reseeds, keeps speed, unpauses)
        | ``speed`` (value > 0, sim seconds per wall second)."""
        c = str(cmd).strip().lower()
        if c == "pause":
            self.paused = True
        elif c == "resume":
            self.paused = False
        elif c == "restart":
            self._reset()
        elif c == "speed":
            try:
                v = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"control('speed') needs a numeric value > 0, got {value!r}") from None
            if not math.isfinite(v) or v <= 0:
                raise ValueError(f"control('speed') needs a value > 0, got {value!r}")
            self.speed = v
        else:
            raise ValueError(f"unknown control command {cmd!r}; use pause, resume, restart or speed")

    def survey(self, n: int = 20) -> list[dict]:
        """``n`` cycles of noisy anchor-to-anchor ranges (a < b) at the current positions; all node pairs for
        anchor-free (mesh) scenarios. Uses its own RNG stream so it never perturbs ``step`` output."""
        rng = np.random.default_rng([self.seed, 2, self._survey_count])
        self._survey_count += 1
        st = self._state(self.t_sim)
        out = []
        for _ in range(max(0, int(n))):
            out += self._measure(st, self._survey_pairs, rng, self._last_now)
        return out
