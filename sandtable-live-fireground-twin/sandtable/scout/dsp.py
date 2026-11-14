"""Scout radar DSP: thrown pucks -> their own geometry, static echoes, and an untagged breathing or moving person.

Pure numpy. Input is a recording (sim.simulate, or real pucks in the same shape); `rec["truth"]` is never read.

Adapted from lane L08's project `tagless-uwb-echo-radar` (src/tagless/dsp/pipeline.py, read-only, copied not
imported): stage 2 fractional first-path alignment (FFT phase ramp), the whole-tap re-seat, the leading-edge
"pulse lock" fine alignment, stage 3 self-referencing on the direct path's leading edge (which makes two
free-running radios phase-coherent), stage 7 back-projection of ellipses with a 0.5-tap kernel, stage 8 Gauss-Newton
on the ellipse equations with Cauchy weights and a covariance, the breathing-band FFT peak (0.1-0.6 Hz) and the
near-surface body model. New here, because pucks are thrown into an occupied room and there is no empty-room
background to subtract: a slow-time detector (per link and tap, remove the static mean, then test the energy in
the breathing band and in a broadband motion band against a per-link noise level plus a clutter term proportional
to the static echo at that tap), a static-echo extractor with a direct-path template taken as the median over
links, classical MDS self-location, and the 1- and 2-puck answers. L08's caveat applies: this DSP was written
alongside the simulator it is tested on, so its numbers are optimistic. See RADAR_NOTES.md.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g
from .geometry import K0, M_PER_TAP

BREATH_BAND_HZ = (0.1, 0.6)
MOTION_BAND_HZ = (0.8, 4.0)
NOISE_BAND_HZ = (4.0, 9.5)
REF_BAND_HZ = (0.75, 2.5)  # upper end of the band over which each tap's flicker is measured on V
THR_PAIR_MARGIN = 1.6     # single link (2 pucks): nothing else corroborates it, so flicker tails need a margin
THR_MOTION = 2.5          # mean motion-band power / expected background
CLUTTER_MARGIN = 1.5      # expected flicker = margin * c(f) * (flicker level measured on the V quadrature)
KERNEL_TAPS = 0.5         # back-projection kernel (L08)
ASSOC_TAPS = 1.2          # a link's echo belongs to the target if within this many taps of the prediction (L08)
SIGMA_PATH_M = 0.08       # floor of the per-link path error used for the covariance
GEOM_SIGMA_M = 0.10       # added isotropic error for the puck positions themselves
GRID_M = 0.1              # search grid
MAP_CELL_M = 0.25         # published echo map
GATE_START = 2            # taps after the direct-path peak before the gate opens (inside the direct pulse)


# ---------------------------------------------------------------------------------------------------- helpers
def _range_table(rec, ids):
    """Median two-way range per unordered pair present in `ids`."""
    acc: dict = {}
    want = set(ids)
    for m in rec.get("ranges", []):
        a, b = m.get("a"), m.get("b")
        if a in want and b in want and a != b and m.get("d") is not None:
            acc.setdefault(tuple(sorted((a, b))), []).append(float(m["d"]))
    return {k: float(np.median(v)) for k, v in acc.items()}


def _ids(rec, use, xy=None):
    ids = [p for p in rec.get("pucks", []) if (use is None or p in use) and (xy is None or p in xy)]
    return ids


def _shift(H, s, f):
    """Shift rows of H (frames x taps) EARLIER by s taps (fractional, circular): y[n] = x[n + s]."""
    return np.fft.ifft(np.fft.fft(H, axis=-1) * np.exp(2j * np.pi * f * np.asarray(s)[..., None]), axis=-1)


def _logpar(y, k):
    """Sub-sample peak offset of positive y around index k by a log-parabola, in [-0.5, 0.5]."""
    if k <= 0 or k >= len(y) - 1:
        return 0.0
    la, lb, lc = (math.log(max(float(y[k + o]), 1e-30)) for o in (-1, 0, 1))
    den = la - 2 * lb + lc
    return min(max(0.5 * (la - lc) / den, -0.5), 0.5) if den < -1e-12 else 0.0


# ---------------------------------------------------------------------------------- per-link preprocessing
def _prep_link(fr, fhz, T, n_taps):
    """L08 stages 2-3 on one ordered link: align every frame on its first path, re-seat whole-tap errors,
    lock the leading edge to the link's mean pulse, divide by the direct path. Returns the frames on a uniform
    cycle grid (gaps filled by interpolation) plus the link's mean profile."""
    H = np.asarray(fr["cir"], complex)
    if H.ndim != 2 or len(H) < 8:
        return None
    if H.shape[1] < n_taps:
        H = np.pad(H, ((0, 0), (0, n_taps - H.shape[1])))
    fp = np.asarray(fr["fp_index"], float)
    cyc = np.floor(np.asarray(fr["t"], float) * fhz + 1e-6).astype(int)
    f = np.fft.fftfreq(n_taps)
    Ha = _shift(H, fp - K0, f)
    lo = max(0, K0 - 6)
    pk = lo + np.argmax(np.abs(Ha[:, lo:K0 + 9]), axis=1)
    kref = int(np.argmax(np.bincount(pk, minlength=n_taps)))
    keep = np.abs(pk - kref) < 2                                   # early first-path reports: drop (L08)
    Ha, cyc = Ha[keep], cyc[keep]
    ok = (cyc >= 0) & (cyc < T)
    Ha, cyc = Ha[ok], cyc[ok]
    if len(Ha) < 8 or kref < 3 or kref > n_taps - 4:
        return None
    rows = np.arange(len(Ha))
    c1 = Ha[:, kref]
    c1 = np.where(np.abs(c1) < 1e-9, 1.0, c1)
    ref = (Ha / c1[:, None]).mean(axis=0)
    # whole-tap re-seat: a frame that matches the mean pulse better one tap over (L08)
    idx = kref + np.arange(-2, 3)
    B = np.conj(ref[idx])
    sc3 = np.stack([np.abs((Ha[:, np.clip(idx + s, 0, n_taps - 1)] * B).sum(axis=1)) for s in (-1, 0, 1)], axis=1)
    sh = np.argmax(sc3, axis=1) - 1
    sh = np.where(sc3[rows, sh + 1] > 1.3 * sc3[:, 1], sh, 0)
    for r in np.nonzero(sh)[0]:
        Ha[r] = np.roll(Ha[r], -int(sh[r]))
    Hn = Ha
    for _ in range(2):
        # pulse lock on the leading edge (no echo can be there): frame = a*ref + b*ref' -> timing error -Re(b/a)
        refd = np.fft.ifft(np.fft.fft(ref) * (2j * np.pi * f))
        kk = kref + np.arange(-2, 1)
        Bv, Dv, Yv = ref[kk][None, :], refd[kk][None, :], Ha[:, kk]
        BB, DD = (np.abs(Bv) ** 2).sum(1), (np.abs(Dv) ** 2).sum(1)
        BD = (np.conj(Bv) * Dv).sum(1)
        BY, DY = (np.conj(Bv) * Yv).sum(1), (np.conj(Dv) * Yv).sum(1)
        det = float(BB[0] * DD[0] - abs(BD[0]) ** 2)
        if det > 1e-12:
            av = (DD * BY - BD * DY) / det
            bv = (BB * DY - np.conj(BD) * BY) / det
            eps = np.where(np.abs(av) > 1e-9, -np.real(bv / np.where(np.abs(av) > 1e-9, av, 1.0)), 0.0)
            Ha = _shift(Ha, np.clip(eps, -0.5, 0.5), f)
        # self-reference: divide by the complex direct path on its leading edge up to the peak (L08 stage 3)
        b1, b0 = ref[kref - 1], ref[kref]
        cc = (Ha[:, kref - 1] * np.conj(b1) + 0.5 * Ha[:, kref] * np.conj(b0)) / (abs(b1) ** 2 + 0.5 * abs(b0) ** 2 + 1e-12)
        cc = np.where(np.abs(cc) < 1e-9, 1.0, cc)
        Hn = Ha / cc[:, None]
        ref = Hn.mean(axis=0)
    kpk = kref + _logpar(np.abs(ref), kref)
    # uniform cycle grid; lost and dropped frames are filled by linear interpolation in slow time
    _, first = np.unique(cyc, return_index=True)
    cyc, Hn = cyc[first], Hn[first]
    G = np.empty((T, n_taps), complex)
    valid = np.zeros(T, bool)
    valid[cyc] = True
    G[cyc] = Hn
    if not valid.all():
        tt = np.arange(T)
        miss = ~valid
        nxt = np.searchsorted(cyc, tt[miss])
        lo_i = np.clip(nxt - 1, 0, len(cyc) - 1)
        hi_i = np.clip(nxt, 0, len(cyc) - 1)
        c_lo, c_hi = cyc[lo_i], cyc[hi_i]
        w = np.where(c_hi != c_lo, (tt[miss] - c_lo) / np.maximum(c_hi - c_lo, 1), 0.0)
        G[miss] = (1 - w)[:, None] * Hn[lo_i] + w[:, None] * Hn[hi_i]
    pre = slice(2, max(3, kref - 3))
    noise_var = float(np.mean(np.abs(Hn[:, pre] - ref[None, pre]) ** 2))
    return {"G": G, "valid": valid, "ref": ref, "kref": kref, "kpk": float(kpk), "noise_var": max(noise_var, 1e-15),
            "n": int(len(Hn))}


def _prep(rec):
    cache = rec.get("_dsp_cache")
    if cache is not None:
        return cache
    meta = rec.get("meta", {})
    fhz = float(meta.get("frame_hz", 20.0))
    n_taps = int(meta.get("n_taps", 96))
    T = int(round(float(meta.get("duration_s", 0.0)) * fhz))
    if T <= 0:
        T = 1 + int(max((float(np.max(fr["t"])) for fr in rec["frames"].values() if len(fr["t"])), default=0.0) * fhz)
    links = {}
    for key, fr in rec.get("frames", {}).items():
        p = _prep_link(fr, fhz, T, n_taps)
        if p is not None:
            links[tuple(key)] = p
    cache = {"links": links, "fhz": fhz, "T": T, "n_taps": n_taps}
    rec["_dsp_cache"] = cache
    return cache


# ------------------------------------------------------------------------------------------- self-location
def self_locate(rec, use: list[str] | None = None) -> dict:
    """Classical MDS on the median pairwise two-way ranges, refined by a few SMACOF steps. The frame is the
    pucks' own: centroid at the origin, the first puck on +x, the second on the +y side (rotation, mirror and
    offset relative to the building are unknown here; registration resolves them)."""
    ids = _ids(rec, use)
    n = len(ids)
    if n == 0:
        return {"pucks_rel": {}, "rms_m": 0.0, "n": 0, "ranges": {}}
    rt = _range_table(rec, ids)
    if n == 1:
        return {"pucks_rel": {ids[0]: [0.0, 0.0]}, "rms_m": 0.0, "n": 1, "ranges": {}}
    D = np.full((n, n), np.nan)
    np.fill_diagonal(D, 0.0)
    W = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d = rt.get(tuple(sorted((ids[i], ids[j]))))
            if d is not None:
                D[i, j] = D[j, i] = max(d, 0.0)
                W[i, j] = W[j, i] = 1.0
    if np.isnan(D).any():                                   # missing pairs: shortest path through the mesh (upper bound)
        F = np.where(np.isnan(D), np.inf, D)
        for k in range(n):
            F = np.minimum(F, F[:, k:k + 1] + F[k:k + 1, :])
        if np.isinf(F).any():
            F[np.isinf(F)] = np.nanmax(np.where(np.isinf(F), np.nan, F)) if np.isfinite(F).any() else 1.0
        D = F
    J = np.eye(n) - 1.0 / n
    Bm = -0.5 * J @ (D ** 2) @ J
    vals, vecs = np.linalg.eigh(Bm)
    order = np.argsort(vals)[::-1][:2]
    X = vecs[:, order] * np.sqrt(np.maximum(vals[order], 0.0))[None, :]
    if X.shape[1] < 2:
        X = np.pad(X, ((0, 0), (0, 2 - X.shape[1])))
    # SMACOF refinement of the raw stress on measured pairs
    Wf = W if W.sum() else np.ones((n, n)) - np.eye(n)
    V = -Wf.copy()
    np.fill_diagonal(V, Wf.sum(axis=1))
    Vp = np.linalg.pinv(V)
    for _ in range(40):
        dist = np.linalg.norm(X[:, None, :] - X[None, :, :], axis=2)
        Bc = -Wf * np.where(dist > 1e-9, D / np.maximum(dist, 1e-9), 0.0)
        np.fill_diagonal(Bc, -Bc.sum(axis=1))
        X = Vp @ Bc @ X
    X = X - X.mean(axis=0)
    ang = math.atan2(X[0, 1], X[0, 0])
    R = np.array([[math.cos(-ang), -math.sin(-ang)], [math.sin(-ang), math.cos(-ang)]])
    X = X @ R.T
    if n >= 2 and X[1, 1] < 0:
        X[:, 1] = -X[:, 1]
    dist = np.linalg.norm(X[:, None, :] - X[None, :, :], axis=2)
    m = np.triu(W, 1) > 0
    rms = float(np.sqrt(np.mean((dist[m] - D[m]) ** 2))) if m.any() else 0.0
    return {"pucks_rel": {pid: [float(X[i, 0]), float(X[i, 1])] for i, pid in enumerate(ids)}, "rms_m": rms, "n": n,
            "ranges": {f"{a}-{b}": round(d, 4) for (a, b), d in sorted(rt.items())}}


# ------------------------------------------------------------------------------------------- static echoes
def _link_pairs(ids):
    return [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]


def static_echoes(rec, use: list[str] | None = None, max_per_link: int = 12, min_snr_db: float = 12.0) -> list[dict]:
    """Peaks of the time-averaged, self-referenced CIR beyond the direct path, one list per unordered pair
    (both directions averaged coherently: the channel is reciprocal). The direct-path pulse and its side lobes are
    removed with a template, the complex median of every link's mean profile around its direct path (echoes sit at
    different delays on different links, so the median keeps only the pulse); with fewer than 3 distinct pairs no
    template can be formed and only echoes beyond 5 taps (1.5 m of excess path) are reported.
    Echo = {"tx", "rx", "path_m", "excess_m", "amp" (relative to the direct path), "snr_db", "d_m" (pair range)}."""
    prep = _prep(rec)
    L = prep["links"]
    ids = _ids(rec, use)
    rt = _range_table(rec, ids)
    n_taps = prep["n_taps"]
    f = np.fft.fftfreq(n_taps)
    pairs = []
    for a, b in _link_pairs(ids):
        dirs = [L[k] for k in ((a, b), (b, a)) if k in L]
        if dirs:
            pairs.append((a, b, dirs))
    if not pairs:
        return []
    kpk0 = float(np.median([d["kpk"] for _, _, ds in pairs for d in ds]))
    k0 = int(round(kpk0))
    prof, nvar = [], []
    for a, b, ds in pairs:
        ps = []
        for d in ds:
            r = _shift(d["ref"], d["kpk"] - kpk0, f)
            ps.append(r / (r[k0] if abs(r[k0]) > 1e-9 else 1.0))
        prof.append(np.mean(ps, axis=0))
        nvar.append(np.mean([d["noise_var"] / d["n"] for d in ds]) / len(ds))
    prof = np.asarray(prof)
    template = None
    if len(pairs) >= 3:
        template = np.median(prof.real, axis=0) + 1j * np.median(prof.imag, axis=0)
    out = []
    for (a, b, ds), s, nv in zip(pairs, prof, nvar):
        if template is not None:
            sl = slice(k0 - 2, k0 + 2)
            tt = template[sl]
            alpha = (np.conj(tt) * s[sl]).sum() / max(float((np.abs(tt) ** 2).sum()), 1e-12)
            r = s - alpha * template
            start = k0 + GATE_START
        else:
            r = s.copy()
            start = k0 + 5
        mag = np.abs(r)
        floor = np.full(len(mag), math.sqrt(nv))          # thermal noise of the 12 s mean
        d_m = rt.get((a, b))
        if d_m is None:
            continue
        peaks = []
        for k in range(start, n_taps - 2):
            if mag[k] >= mag[k - 1] and mag[k] > mag[k + 1]:
                snr = 20 * math.log10(max(mag[k], 1e-15) / max(float(floor[k]), 1e-15))
                if snr >= min_snr_db:
                    peaks.append((float(mag[k]), k, snr))
        peaks.sort(reverse=True)
        for amp, k, snr in peaks[:max_per_link]:
            ks = k + _logpar(mag, k)
            ex = (ks - kpk0) * M_PER_TAP
            out.append({"tx": a, "rx": b, "path_m": round(d_m + ex, 3), "excess_m": round(ex, 3),
                        "amp": round(amp, 5), "snr_db": round(snr, 1), "d_m": round(d_m, 3)})
    out.sort(key=lambda e: (e["tx"], e["rx"], e["path_m"]))
    return out


# --------------------------------------------------------------------------------------------- dynamic part
def _mf(X, sig=0.8):
    """Matched filter along taps: unit-norm Gaussian of the pulse's main lobe (sigma 0.8 tap)."""
    w = np.exp(-0.5 * (np.arange(-2, 3) / sig) ** 2)
    w /= np.linalg.norm(w)
    out = np.zeros_like(X)
    K = X.shape[-1]
    for m, wm in zip(range(-2, 3), w):
        lo, hi = max(0, -m), min(K, K - m)
        out[..., lo:hi] += wm * X[..., lo + m:hi + m]
    return out


def _link_dynamic(dirs, T, fhz, nfft=2048):
    """Slow-time spectra of one unordered link: both directions averaged, static mean removed, matched filter."""
    G = np.mean([d["G"] for d in dirs], axis=0)
    kpk = float(np.mean([d["kpk"] for d in dirs]))
    s = _mf(G.mean(axis=0)[None, :])[0]
    X = _mf(G - G.mean(axis=0)[None, :])
    w = np.hanning(T)
    F = np.fft.fft(X * w[:, None], n=nfft, axis=0)
    P = np.abs(F) ** 2
    fr = np.fft.fftfreq(nfft, 1.0 / fhz)
    # Breathing is phase modulation: the dynamic part of a breathing echo is perpendicular to its own static
    # phasor (x(t) - mean ~ j a e^{j th} phi(t)). Flicker (1 + f(t)) and thermal noise are circular. So U, the
    # quadrature perpendicular to the static phasor, carries all of the breathing and half of the noise; V, the
    # quadrature along it, carries no breathing and measures that tap's flicker + noise.
    rot = np.exp(-1j * (np.angle(s) + np.pi / 2))
    Y = X * rot[None, :]
    Pu = np.abs(np.fft.rfft(Y.real * w[:, None], n=nfft, axis=0)) ** 2 * 2.0
    Pv = np.abs(np.fft.rfft(Y.imag * w[:, None], n=nfft, axis=0)) ** 2 * 2.0
    fq = np.fft.rfftfreq(nfft, 1.0 / fhz)
    return {"P": P, "f": fr, "s2": np.abs(s) ** 2, "kpk": kpk, "Pu": Pu, "Pv": Pv, "fq": fq}


def _band_pm(P, fr, band):
    """P(+f) + P(-f) for f in band -> [nb, K], plus the frequencies."""
    pos = np.nonzero((fr >= band[0]) & (fr <= band[1]))[0]
    neg = np.array([int(np.argmin(np.abs(fr + fr[i]))) for i in pos])
    return P[pos] + P[neg], fr[pos]




def _wh_quantile(dof: float, p: float) -> float:
    """Upper-tail quantile of a chi-square with `dof` degrees of freedom (Wilson-Hilferty), p = tail probability."""
    # normal quantile by a rational approximation (Acklam-style, enough for p in 1e-12..0.5)
    t = math.sqrt(-2.0 * math.log(max(p, 1e-300)))
    z = t - (2.515517 + 0.802853 * t + 0.010328 * t * t) / (1 + 1.432788 * t + 0.189269 * t * t + 0.001308 * t ** 3)
    k = max(dof, 1e-6)
    return k * (1 - 2 / (9 * k) + z * math.sqrt(2 / (9 * k))) ** 3


def _soft(z):
    """Soft cap of a per-link statistic so that one mis-normalised clutter tap cannot carry the sum alone."""
    return np.where(z <= 8.0, z, 8.0 + 4.0 * np.log(np.maximum(z, 8.0) / 8.0))


def _sum_threshold(n_links: int, n_tests: float, p_fa: float = 1e-2) -> float:
    """Detection threshold on a sum of n_links per-link statistics, each exponential with mean 1 under noise only
    (one real quadrature), for a false-alarm probability p_fa over n_tests independent (position, frequency)
    cells. The per-link values are soft-capped (`_soft`), which makes the true tail lighter than this bound.
    Checked empirically: 16 empty-room seeds with 5 pucks, maximum 29.6 against 32.2 (tests/test_scout_radar.py)."""
    return _wh_quantile(2.0 * n_links, p_fa / max(n_tests, 1.0)) / 2.0


def find_victims(rec, pucks_xy: dict, use: list[str] | None = None, bounds: list | None = None) -> dict:
    """Untagged person from the slow-time behaviour of the echoes (breathing or moving), located by ellipses.

    Per unordered pair and CIR tap: remove the static mean (there is no empty-room background when pucks land in
    an occupied room), then compare the energy in the breathing band (0.1-0.6 Hz) and in a motion band (0.8-4 Hz)
    with what thermal noise plus the slow flicker of the static echo at that tap would give. Detection is made on a
    grid inside `bounds`: for every cell and breathing frequency the per-link statistics at that cell's predicted
    tap (its ellipse) are summed, and the sum is tested against a gamma-tail threshold (incoherent integration over
    links before deciding, so a weak echo seen by many links is not lost). The winning cell is refined by
    Gauss-Newton on the ellipse equations (L08 stage 8). A person who neither breathes nor moves is not detected:
    that is the method's limit, not a bug. One target is reported; others show in the map only.
    Positions are in the frame of `pucks_xy`."""
    ids = _ids(rec, use, pucks_xy)
    if len(ids) == 0:
        return {"victims": [], "why": "no puck positions to work with", "echo_map": None, "per_link": []}
    if len(ids) == 1:
        return {"victims": [], "why": "one puck cannot receive its own echo", "echo_map": None, "per_link": []}
    prep = _prep(rec)
    L, T, fhz = prep["links"], prep["T"], prep["fhz"]
    xy = {p: np.asarray(pucks_xy[p][:2], float) for p in ids}

    # ---- per link: slow-time spectra and background model ----
    links = []
    for a, b in _link_pairs(ids):
        dirs = [L[k] for k in ((a, b), (b, a)) if k in L]
        if dirs:
            dy = _link_dynamic(dirs, T, fhz)
            dy.update({"a": a, "b": b})
            links.append(dy)
    if not links:
        return {"victims": [], "why": "no usable CIR frames", "echo_map": None, "per_link": []}
    for dy in links:
        K = dy["P"].shape[1]
        dy["g0"] = int(math.ceil(dy["kpk"])) + GATE_START
        dy["gate"] = np.arange(dy["g0"], K - 3)
        dy["Pm"], _ = _band_pm(dy["P"], dy["f"], MOTION_BAND_HZ)
        Pn, _ = _band_pm(dy["P"], dy["f"], NOISE_BAND_HZ)
        # thermal noise per (+f, -f) bin of the complex series: white in slow time, so the median of the 4-9.5 Hz
        # band over the gate divided by the median of a Gamma(2) variable (1.678 / 2)
        dy["N"] = max(float(np.median(Pn[:, dy["gate"]])) / 1.678 * 2.0, 1e-30)
        fq = dy["fq"]
        ib = (fq >= BREATH_BAND_HZ[0]) & (fq <= BREATH_BAND_HZ[1])
        iw = (fq >= BREATH_BAND_HZ[0]) & (fq <= REF_BAND_HZ[1])
        inn = (fq >= NOISE_BAND_HZ[0]) & (fq <= NOISE_BAND_HZ[1])
        # per-quadrature noise: exponential, median = ln 2 x mean
        dy["Nq"] = max(float(np.median(np.concatenate([dy["Pu"][inn][:, dy["gate"]], dy["Pv"][inn][:, dy["gate"]]]))) / math.log(2), 1e-30)
        dy["fb"] = fq[ib]
        dy["Pub"], dy["Pvw"], dy["ib_in_w"] = dy["Pu"][ib], dy["Pv"][iw], ib[iw]
    # flicker shape c(f) = median over clutter-dominated taps of (P - N) / |static|^2, pooled over links
    # (L08's flicker is one physical process for every static path, so one curve serves all links); for the
    # breathing test it is measured on V, the quadrature that carries no breathing (flicker is circular: U = V)
    qv, qm = [], []
    for dy in links:
        gt = dy["gate"]
        s2 = dy["s2"][gt]
        strong = gt[(s2 >= np.quantile(s2, 0.75)) & (s2 > 1e-30)]
        if len(strong):
            qv.append((dy["Pvw"][:, strong] - dy["Nq"]) / dy["s2"][strong][None, :])
            qm.append((dy["Pm"][:, strong] - dy["N"]) / dy["s2"][strong][None, :])
    nb = len(links[0]["fb"])
    nw = links[0]["Pvw"].shape[0]
    cv = np.maximum(np.median(np.concatenate(qv, axis=1), axis=1), 0.0) if qv else np.zeros(nw)
    cm = np.maximum(np.median(np.concatenate(qm, axis=1), axis=1), 0.0) if qm else np.zeros(links[0]["Pm"].shape[0])
    n_ind = max(4.0, (REF_BAND_HZ[1] - BREATH_BAND_HZ[0]) * T / fhz / 1.5)
    for dy in links:
        s3 = dy["s2"].copy()
        s3[1:-1] = np.maximum(np.maximum(dy["s2"][:-2], dy["s2"][1:-1]), dy["s2"][2:])
        # Breathing test on U against noise + this tap's own flicker, measured on V over 0.1-2.5 Hz (minus one
        # standard error of that estimate) and shaped by c(f). The static echo is not used for this: a still
        # person's own echo does not flicker and would otherwise be charged as clutter.
        if cv.sum() > 0:
            num = (dy["Pvw"] - dy["Nq"]).sum(axis=0) - dy["Nq"] * nw / math.sqrt(n_ind)
            alpha = np.maximum(num, 0.0) / cv.sum()
        else:
            alpha = np.zeros_like(s3)
        alpha3 = alpha.copy()
        alpha3[1:-1] = np.maximum(np.maximum(alpha[:-2], alpha[1:-1]), alpha[2:])
        zb = dy["Pub"] / (dy["Nq"] + CLUTTER_MARGIN * np.outer(cv[dy["ib_in_w"]], alpha3))
        zm = (dy["Pm"] / (dy["N"] + 2.0 * np.outer(cm, s3))).mean(axis=0)
        outside = np.ones(zb.shape[1], bool)
        outside[dy["gate"]] = False
        zb[:, outside] = 1.0                          # no information inside the direct pulse or past the window
        zm[outside] = 1.0
        dy["zb"], dy["zm"] = zb, zm
        dy["zc"] = _soft(zb)
        dy["zbk"] = zb.max(axis=0)

    # ---- grids inside bounds ----
    if bounds:
        bx = [p[0] for p in bounds]
        by = [p[1] for p in bounds]
        x0, x1, y0, y1 = min(bx), max(bx), min(by), max(by)
    else:
        P = np.array(list(xy.values()))
        x0, x1 = float(P[:, 0].min()) - 6.0, float(P[:, 0].max()) + 6.0
        y0, y1 = float(P[:, 1].min()) - 6.0, float(P[:, 1].max()) + 6.0

    def grid(cell):
        nx = max(1, int(math.ceil((x1 - x0) / cell - 1e-9)))
        ny = max(1, int(math.ceil((y1 - y0) / cell - 1e-9)))
        gx = x0 + (np.arange(nx) + 0.5) * cell
        gy = y0 + (np.arange(ny) + 0.5) * cell
        X, Y = (v.ravel() for v in np.meshgrid(gx, gy))
        inside = g.pip_grid(X, Y, bounds) if bounds else np.ones(len(X), bool)
        return X, Y, nx, ny, inside

    def sample(dy, X, Y, key):
        """Per-link statistic at the predicted (fractional) tap of every cell: [nf, C] or [C]."""
        kf = dy["kpk"] + g.bistatic_excess(X, Y, xy[dy["a"]], xy[dy["b"]]) / M_PER_TAP
        K = dy["zb"].shape[1]
        kf = np.clip(kf, 0.0, K - 1.001)
        k0 = np.floor(kf).astype(int)
        w = kf - k0
        z = dy[key]
        return (1 - w) * z[..., k0] + w * z[..., k0 + 1]

    def maps(X, Y):
        Sb = np.zeros((nb, len(X)))
        Sm = np.zeros(len(X))
        for dy in links:
            Sb += sample(dy, X, Y, "zc")
            Sm += sample(dy, X, Y, "zm")
        return Sb, Sm

    Lu = len(links)
    Xf, Yf, _, _, inf_ = grid(GRID_M)
    Xf, Yf = Xf[inf_], Yf[inf_]
    Xm, Ym, nxm, nym, inm = grid(MAP_CELL_M)
    if len(Xf) == 0:
        return {"victims": [], "why": "search area is empty", "echo_map": None, "per_link": []}
    Sb, Sm = maps(Xf, Yf)
    Sbm, Smm = maps(Xm, Ym)
    area = max(len(Xf) * GRID_M * GRID_M, 1.0)
    n_tests = max(area / (0.3 * 0.3), 50.0) * max(1.0, (BREATH_BAND_HZ[1] - BREATH_BAND_HZ[0]) * T / fhz)
    thr_b = _sum_threshold(Lu, n_tests) if Lu > 1 else THR_PAIR_MARGIN * _sum_threshold(1, len(links[0]["gate"]) / 2.0 * 6.0, p_fa=1e-4)
    # motion statistic: mean of ~2 * 3.2 Hz * 12 s bins per tap -> nearly Gaussian, mean 1, sd ~0.2 per link
    thr_m = Lu + max(1.5 * Lu, 6.0 * 0.25 * math.sqrt(Lu))

    per_link = []
    for dy in links:
        zmax = float(dy["zbk"][dy["gate"]].max()) if len(dy["gate"]) else 0.0
        per_link.append({"tx": dy["a"], "rx": dy["b"], "excess_m": None, "snr_db": round(10 * math.log10(max(zmax, 1e-9)), 1),
                         "used": False, "kind": None})

    def echo_map(vals, scale):
        v = np.where(inm, np.clip(vals / max(scale, 1e-12), 0.0, 1.0), 0.0)
        return {"origin": [round(x0, 3), round(y0, 3)], "cell_m": MAP_CELL_M, "nx": nxm, "ny": nym, "kind": "dynamic",
                "values": [round(float(q), 3) for q in v]}

    def peak_freq(spec, fb):
        i = int(np.argmax(spec))
        d = 0.0
        if 0 < i < len(spec) - 1:
            a_, b_, c_ = spec[i - 1], spec[i], spec[i + 1]
            den = a_ - 2 * b_ + c_
            d = 0.5 * (a_ - c_) / den if den < 0 else 0.0
        return float(fb[i] + d * (fb[1] - fb[0])), i

    def link_measure(dy, x, y, fi, kind):
        """The link's echo near the cell's predicted tap: sub-tap excess and statistic."""
        kp = dy["kpk"] + float(g.bistatic_excess(x, y, xy[dy["a"]], xy[dy["b"]])) / M_PER_TAP
        lo, hi = max(dy["g0"], int(math.floor(kp - ASSOC_TAPS))), min(dy["zb"].shape[1] - 2, int(math.ceil(kp + ASSOC_TAPS)))
        if hi < lo:
            return None
        if kind == "breathing":
            fis = [i for i in (fi - 1, fi, fi + 1) if 0 <= i < nb]
            z = dy["zb"][fis].max(axis=0)
        else:
            z = dy["zm"]
        k = lo + int(np.argmax(z[lo:hi + 1]))
        ks = k + _logpar(z, k)
        return {"k": k, "ex": (ks - dy["kpk"]) * M_PER_TAP, "z": float(z[k]), "kind": kind}

    bmax = Sb.max(axis=0)
    victims, why = [], None
    kind = None
    if Lu == 1:
        # ---- two pucks: presence and range only (an ellipse, no position) ----
        dy = links[0]
        zt = dy["zb"][:, dy["gate"]] if len(dy["gate"]) else np.zeros((nb, 1))
        best = float(zt.max())
        zm_best = float(dy["zm"][dy["gate"]].max()) if len(dy["gate"]) else 0.0
        if best >= thr_b or zm_best >= 1.0 + 1.5:
            moving = zm_best >= 2.5 and best < thr_b * 2
            if moving:
                k = int(dy["gate"][int(np.argmax(dy["zm"][dy["gate"]]))])
                z = dy["zm"]
            else:
                fi, kk = np.unravel_index(int(np.argmax(zt)), zt.shape)
                k = int(dy["gate"][kk])
                z = dy["zb"][fi]
            ex = (k + _logpar(z, k) - dy["kpk"]) * M_PER_TAP
            bpm = None
            bconf = 0.0
            if not moving:
                f0, _ = peak_freq(dy["zb"][:, k], dy["fb"])
                bpm = 60.0 * f0
                bconf = round(min(1.0, (best - 1.0) / (2.0 * max(thr_b - 1.0, 1e-9))), 2)
            per_link[0].update({"used": True, "excess_m": round(ex, 3), "kind": "moving" if moving else "breathing"})
            d = float(np.linalg.norm(xy[dy["a"]] - xy[dy["b"]]))
            conf = 0.3 * (1.0 - 0.5 ** max((best - 1.0) / max(thr_b - 1.0, 1e-9), 0.0))
            victims.append({"x": None, "y": None, "excess_m": round(ex, 3), "sigma_m": None, "cov": None,
                            "confidence": round(conf, 2), "n_links": 1, "state": "moving" if moving else "breathing",
                            "moving": bool(moving), "breathing_bpm": round(bpm, 1) if bpm else None, "breathing_conf": bconf,
                            "ellipse": {"foci": [dy["a"], dy["b"]], "path_m": round(d + ex + 2 * g.BODY_RADIUS_M, 3)},
                            "text": f"someone {'moving' if moving else 'breathing'} about {ex:.1f} m of extra path beyond the "
                                    f"line between {dy['a']} and {dy['b']}; two pucks give presence and range, no position"})
            why = ("two pucks: presence and echo range only, no position; low confidence: one link has nothing to "
                   "corroborate it (simulated false-alarm rate about 1 in 5 with no one in the room)")
        else:
            why = "two pucks: no breathing or moving echo on the only link"
        mvals = np.maximum(Sbm.max(axis=0) - 1.0, 0.0)
        out = {"victims": victims, "why": why, "per_link": per_link, "n_links_alive": 1,
               "echo_map": echo_map(mvals, float(mvals.max()) if victims else max(float(mvals.max()), 2.0 * (thr_b - 1.0)))}
        return out

    # ---- three or more pucks: detect on the grid, then refine ----
    ci = int(np.argmax(bmax))
    sbest = float(bmax[ci])
    mi = int(np.argmax(Sm))
    if sbest >= thr_b:
        kind = "breathing"
        cx, cy = float(Xf[ci]), float(Yf[ci])
        f0, fi = peak_freq(Sb[:, ci], links[0]["fb"])
        margin = (sbest - Lu) / max(thr_b - Lu, 1e-9)
    elif float(Sm[mi]) >= thr_m:
        kind = "moving"
        cx, cy = float(Xf[mi]), float(Yf[mi])
        f0, fi = None, 0
        margin = (float(Sm[mi]) - Lu) / max(thr_m - Lu, 1e-9)
    if kind is None:
        why = f"no breathing or moving echo stands out (best {sbest:.1f} against a threshold of {thr_b:.1f} over {Lu} links)"
    else:
        meas = []
        for dy in links:
            m = link_measure(dy, cx, cy, fi, kind)
            if m is not None and m["z"] >= (4.0 if kind == "breathing" else 1.8):
                meas.append((dy, m))
        res = _refine(meas, xy, cx, cy, bounds)
        if res is not None:
            x, y, cov, used = res
        else:                                          # fewer than 3 consistent ellipses: keep the grid peak, wide error
            x, y, used = cx, cy, meas
            cov = np.eye(2) * 0.6 ** 2
        for dy, m in used:
            for pl in per_link:
                if pl["tx"] == dy["a"] and pl["rx"] == dy["b"]:
                    pl.update({"used": True, "excess_m": round(m["ex"], 3), "snr_db": round(10 * math.log10(max(m["z"], 1e-9)), 1),
                               "kind": m["kind"]})
        zm_used = [float(dy["zm"][m["k"]]) for dy, m in used] or [1.0]
        moving = kind == "moving" or float(np.median(zm_used)) >= THR_MOTION
        bpm, bconf = None, 0.0
        if kind == "breathing" and not moving:
            spec = np.zeros(nb)
            peaks = []
            for dy, m in used:
                sp = dy["zb"][:, m["k"]]
                spec += sp
                peaks.append(float(dy["fb"][int(np.argmax(sp))]))
            f1, _ = peak_freq(spec, links[0]["fb"])
            bpm = 60.0 * f1
            agree = float(np.mean([abs(p - f1) <= 0.06 for p in peaks])) if peaks else 0.0
            ex_ = np.maximum(spec - len(used), 0.0)
            conc = float(ex_[np.abs(links[0]["fb"] - f1) <= 0.06].sum() / max(ex_.sum(), 1e-12))
            bconf = round(min(1.0, 0.5 * agree + 0.5 * conc), 2)
        conf = (1.0 - 0.5 ** max(margin, 0.0)) * min(1.0, len(used) / 3.0)
        ev = np.linalg.eigvalsh(cov)
        victims.append({"x": round(float(x), 3), "y": round(float(y), 3), "sigma_m": round(float(math.sqrt(max(ev.max(), 0.0))), 3),
                        "cov": [round(float(cov[0, 0]), 4), round(float(cov[0, 1]), 4), round(float(cov[1, 1]), 4)],
                        "confidence": round(float(min(1.0, conf)), 2), "n_links": len(used),
                        "state": "moving" if moving else "breathing", "moving": bool(moving),
                        "breathing_bpm": round(bpm, 1) if bpm else None, "breathing_conf": bconf,
                        "refined": res is not None})
    mvals = np.maximum(Sbm.max(axis=0) - Lu, 0.0) if kind != "moving" else np.maximum(Smm - Lu, 0.0)
    scale = float(mvals.max()) if victims else max(float(mvals.max()), 2.0 * (thr_b - Lu))
    out = {"victims": victims, "echo_map": echo_map(mvals, scale), "per_link": per_link, "n_links_alive": Lu,
           "detector": {"statistic": round(sbest, 2), "threshold": round(thr_b, 2), "links": Lu,
                        "motion_statistic": round(float(Sm[mi]), 2), "motion_threshold": round(thr_m, 2)}}
    if why:
        out["why"] = why
    return out


def _refine(meas, xy, x, y, bounds, iters=8):
    """L08 stage 8: Gauss-Newton on the ellipse equations of the links whose echo lies within ASSOC_TAPS of the
    prediction, Cauchy weights, covariance (J^T W J)^-1 sigma^2 plus the puck-geometry floor. None if < 3 links."""
    assoc = ASSOC_TAPS * M_PER_TAP
    Nm = None
    used = []
    for _ in range(iters):
        rows, ws, es, used = [], [], [], []
        for dy, m in meas:
            a, b = xy[dy["a"]], xy[dy["b"]]
            k3 = g.bistatic_excess(np.array([x, x + 0.01, x]), np.array([y, y, y + 0.01]), a, b)
            e = m["ex"] - k3[0]
            if abs(e) > assoc:
                continue
            rows.append([(k3[1] - k3[0]) / 0.01, (k3[2] - k3[0]) / 0.01])
            w = min(1.0, max(0.35, (10 * math.log10(max(m["z"], 1e-9)) - 4.0) / 10.0)) / (1.0 + (e / (0.6 * M_PER_TAP)) ** 2)
            ws.append(w)
            es.append(e)
            used.append((dy, m))
        if len(used) < 3:
            return None
        J = np.asarray(rows)
        e = np.asarray(es)
        Wd = np.asarray(ws)
        JTW = J.T * Wd
        Nm = JTW @ J
        if np.linalg.cond(Nm) > 1e6:
            return None
        step = np.linalg.solve(Nm, JTW @ e)
        n = float(np.hypot(*step))
        if n > 0.3:
            step *= 0.3 / n
        x, y = x + float(step[0]), y + float(step[1])
        if n < 2e-3:
            break
    if bounds and not g.pip((x, y), bounds) and g.poly_dist((x, y), bounds) > 0.3:
        return None
    rms = math.sqrt(float((Wd * e ** 2).sum() / max(Wd.sum(), 1e-9)))
    sig = max(SIGMA_PATH_M, rms)
    cov = np.linalg.inv(Nm) * sig ** 2 + np.eye(2) * GEOM_SIGMA_M ** 2
    return x, y, cov, used
