"""Scout radar simulator: what thrown DW3000 pucks would deliver for one scan (SIMULATED, never measured).

For every ordered pair of pucks it produces complex channel-impulse-response (CIR) frames at `frame_hz` for
`duration_s`, plus the pairwise two-way ranges in the shared kit's `range` shape.

Adapted from lane L08's project `tagless-uwb-echo-radar` (src/tagless/sim/core.py, read-only, copied not
imported): the pulse shape, the frame model h[k] = g e^{j phi} (S/d p(t_k) + sum_i a_i e^{j th_i} p(t_k - tau_i))
* 10000 + n[k], the receiver impairments (random carrier phase per frame between free-running radios, U(0,1) tap
sampling offset, first-path index error and misdetections, early first-path reports, gain jitter, frame loss,
36 dB direct-path SNR at 1 m), the slow complex "flicker" of every static path (fitted in L08 to a real DW1000
recording), the bistatic radar equation, L08's person model (cylinder, breathing shape, rate wander, heartbeat,
sway, log-normal RCS fluctuation), ITU-R P.526 knife-edge shadowing of the direct path and the v1 `range`
formulas (rssi, fp, los). New here: walls from the twin's room polygon by the image-source method (first order,
floor-level), a ceiling bounce, a debris reflector, furniture, a lying person on the floor, a longer CIR window
for room-scale delays. L08's caveat applies in full: the DSP was written alongside this simulator, so results
against it are optimistic. The model is written up in sandtable/scout/RADAR_NOTES.md.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g
from .geometry import C, K0, LAMBDA_M, M_PER_TAP, TS_NS

N_TAPS = 96                      # L08 read 64 taps for a 1.5 m zone; a room needs ~26 m of excess path (88 taps after K0)
COUNTS_AT_1M = 10000.0          # direct-path peak (ADC counts) for a 1 m link (L08)

DEFAULT_NOISE = {
    # receiver (L08 src/tagless/sim/core.py DEFAULT_IMPAIR, code values)
    "snr_db_1m": 36.0,            # direct-path peak / complex noise sigma at d = 1 m
    "fp_sigma_taps": 0.09,        # error of the reported first-path index (tap RMS)
    "fp_lead_taps": 1.0,          # the first-path index marks the leading edge; the pulse peak follows ~1 tap later
    "fp_early_prob": 0.04,        # first path reported 2-4 taps early
    "fp_misdetect_prob": 0.004,   # first path off by one whole tap
    "gain_sigma": 0.03,           # per-frame amplitude jitter
    "dropout_prob": 0.02,         # frame lost
    "carrier_phase": "U(0, 2 pi) per frame: free-running radios share no oscillator",
    "sampling_offset": "U(0, 1) tap per frame: the receiver's sampling grid is not locked to the pulse",
    "adc": "rounded to integer counts; 10000 counts = direct-path peak at 1 m",
    # ranges (L08 v1 range model)
    "range_hz": 10.0, "range_sigma_m": 0.03, "range_drop_prob": 0.03,
    # static multipath flicker (L08 DEFAULT_CLUTTER; fitted there to a real DW1000 recording in an occupied space)
    "flicker": 0.4, "flicker_tau_s": 0.5,
    # room (new here)
    "wall_reflection": [0.3, 0.5],          # |Gamma| per wall (seeded), phase pi; L08 used 0.25-0.5 for specular paths
    "ceiling": True, "ceiling_reflection": 0.3,   # ceiling bounce, times cos(elevation) at each puck (vertical antenna)
    "path_loss": "amplitude 1/path for direct and specular paths (Friis); sqrt(rcs/4pi)/(R1 R2) for scatterers",
    "debris_rcs_m2": 2.5,                   # toppled metal cabinet / debris pile, diffuse
    "furniture_n": 5, "furniture_rcs_m2": [0.02, 0.15],
    # person (L08 person model; lying posture new)
    "person_rcs_m2": "0.4-0.9 per seed", "rcs_fluct_db": 1.2, "rcs_tau_s": 2.0, "body_radius_m": 0.14,
    "breath_mm": 3.0,                       # torso surface displacement seen from floor level (L08 used 4 mm for a standing chest)
    "rate_wander": 0.03, "heart_mm": 0.25, "heart_hz": 1.15, "sway_mm": 0.5, "sway_tau_s": 2.0,
    "moving_phase_jitter_rad": 0.35, "crawl_speed_mps": 0.15,
    "shadow": "ITU-R P.526 knife edge from the body's clearance to each line of sight, in Fresnel radii, capped at 25 dB",
    "not_modelled": "antenna patterns (except the ceiling term), second-order bounces, floor bounce, wall transmission, "
                    "smoke/heat effects on the radio (negligible at 6.5 GHz), the crew at the door, chip artefacts, clock drift",
}


def pulse(t_ns):
    """Band-limited DW3000-like pulse (L08 sim/core.py, unchanged): unit peak near t = 0, ~2 ns FWHM, with
    small asymmetric pre/post lobes so that a perfect template cannot be assumed."""
    gg = np.exp(-0.5 * (t_ns / 0.80) ** 2)
    gg = gg - 0.06 * np.exp(-0.5 * ((t_ns + 2.0) / 0.60) ** 2)
    gg = gg - 0.12 * np.exp(-0.5 * ((t_ns - 2.2) / 0.75) ** 2)
    gg = gg + 0.04 * np.exp(-0.5 * ((t_ns - 4.0) / 0.90) ** 2)
    return gg


def knife_edge_db(v):
    """ITU-R P.526 single knife-edge loss J(v) in dB (0 for v <= -0.78), capped at 25 dB (L08)."""
    v = np.asarray(v, float)
    out = np.zeros_like(v)
    m = v > -0.78
    out[m] = 6.9 + 20 * np.log10(np.sqrt((v[m] - 0.1) ** 2 + 1) + v[m] - 0.1)
    return np.clip(out, 0.0, 25.0)


def _breath_shape(ph):
    # asymmetric tidal breathing (L08): quicker inhale, slower exhale; range about [-1, 1]
    return (np.sin(ph) + 0.28 * np.sin(2 * ph - 0.9)) / 1.14


def _ou(rng, shape, T, sigma, tau, dt, complex_=False):
    """Ornstein-Uhlenbeck series [T, *shape] with stationary RMS `sigma` (complex: RMS of the magnitude)."""
    a = math.exp(-dt / tau)
    b = math.sqrt(1 - a * a)
    out = np.zeros((T,) + tuple(shape), complex if complex_ else float)
    if complex_:
        x = sigma / math.sqrt(2) * (rng.standard_normal(shape) + 1j * rng.standard_normal(shape))
    else:
        x = sigma * rng.standard_normal(shape)
    for k in range(T):
        out[k] = x
        if complex_:
            w = (rng.standard_normal(shape) + 1j * rng.standard_normal(shape)) / math.sqrt(2)
        else:
            w = rng.standard_normal(shape)
        x = a * x + b * sigma * w
    return out


def _seg_seg_dist(p, q, a, b) -> float:
    """Distance between segments pq and ab in 2-D."""
    if g._proper_cross(p, q, a, b):
        return 0.0
    return min(g.seg_dist(p, a, b), g.seg_dist(q, a, b), g.seg_dist(a, p, q), g.seg_dist(b, p, q))


def _body_shadow_db(pa, pb, chest, heading_deg, radius=0.15):
    """Knife-edge loss of the line of sight pa-pb past a lying body (a capsule from feet to head)."""
    hd = math.radians(heading_deg)
    u = (math.cos(hd), math.sin(hd))
    head = (chest[0] + 0.45 * u[0], chest[1] + 0.45 * u[1])
    feet = (chest[0] - 1.15 * u[0], chest[1] - 1.15 * u[1])
    clear = _seg_seg_dist(pa, pb, head, feet)
    d = math.dist(pa, pb)
    ab = (pb[0] - pa[0], pb[1] - pa[1])
    s = ((chest[0] - pa[0]) * ab[0] + (chest[1] - pa[1]) * ab[1]) / max(d * d, 1e-9)
    s = min(max(s, 0.02), 0.98)
    rf = math.sqrt(LAMBDA_M * s * d * (1 - s) * d / max(d, 1e-6))
    v = (radius - clear) / max(rf, 1e-6) * math.sqrt(2)
    return float(knife_edge_db(np.array([v]))[0])


def simulate(scene: dict) -> dict:
    """Simulate one scan of `scene` (scene.build_scene). Deterministic for the scene's seed. Returns the recording
    {"meta", "pucks", "ranges", "frames", "truth"}; frames are keyed by (tx, rx) and hold numpy arrays."""
    nz = {**DEFAULT_NOISE, **(scene.get("noise") or {})}
    seed = int(scene.get("seed", 0))
    fhz = float(scene.get("frame_hz", 20.0))
    dt = 1.0 / fhz
    T = int(round(float(scene.get("duration_s", 12.0)) * fhz))
    n_taps = int(scene.get("n_taps", N_TAPS))
    pucks = scene["pucks"]
    ids = [p["id"] for p in pucks]
    N = len(ids)
    P = np.array([[p["x"], p["y"]] for p in pucks], float)
    zp = float(pucks[0].get("z", g.PUCK_Z_M)) if pucks else g.PUCK_Z_M
    poly = scene["polygon"]
    H = float(scene.get("height_m", 2.7))
    rs = np.random.default_rng([seed, 11])          # static world
    rd = np.random.default_rng([seed, 12])          # dynamics (person, flicker)
    rr = np.random.default_rng([seed, 13])          # receiver impairments
    rg = np.random.default_rng([seed, 14])          # ranges

    meta = {"simulated": True, "tap_ns": TS_NS, "bandwidth_mhz": g.BANDWIDTH_MHZ, "carrier_mhz": g.CARRIER_MHZ,
            "frame_hz": fhz, "n_taps": n_taps, "k0": K0, "m_per_tap": M_PER_TAP, "lambda_m": LAMBDA_M,
            "counts_at_1m": COUNTS_AT_1M, "duration_s": T * dt, "puck_z_m": zp, "noise": nz,
            "source": "sandtable.scout.sim (adapted from L08 tagless-uwb-echo-radar sim/core.py)"}
    rec = {"meta": meta, "pucks": list(ids), "ranges": [], "frames": {}, "truth": scene}
    if N < 2:
        return rec

    links = [(i, j) for i in range(N) for j in range(i + 1, N)]
    Lu = len(links)
    D = np.array([math.dist(P[i], P[j]) for i, j in links])

    # ---- static paths per unordered link: (excess_m, complex amplitude relative to a 1 m direct path) ----------
    gam_wall = rs.uniform(nz["wall_reflection"][0], nz["wall_reflection"][1], len(poly))
    static = [[] for _ in range(Lu)]
    for u, (i, j) in enumerate(links):
        d = D[u]
        for ip in g.image_paths(P[i], P[j], poly):
            L = ip["path_m"]
            static[u].append((L - d, -gam_wall[ip["wall"]] / L * np.exp(-2j * np.pi * (L - d) / LAMBDA_M)))
        if nz.get("ceiling", True):
            cp = g.ceiling_path(P[i], P[j], H, zp)
            L = cp["path_m"]
            el = math.radians(cp["elevation_deg"])
            static[u].append((L - d, -float(nz["ceiling_reflection"]) * math.cos(el) ** 2 / L
                              * np.exp(-2j * np.pi * (L - d) / LAMBDA_M)))
        scat = [(c["at"], float(c.get("z", 0.5)), float(c["rcs_m2"])) for c in scene.get("changes", [])]
        scat += [(c["at"], float(c.get("z", 0.5)), float(c["rcs_m2"])) for c in scene.get("clutter", [])]
        for at, z, rcs in scat:
            r1 = math.sqrt(math.dist(at, P[i]) ** 2 + (z - zp) ** 2)
            r2 = math.sqrt(math.dist(at, P[j]) ** 2 + (z - zp) ** 2)
            th0 = rs.uniform(0, 2 * np.pi)
            static[u].append((r1 + r2 - d, math.sqrt(rcs / (4 * math.pi)) / (r1 * r2)
                              * np.exp(1j * (th0 - 2 * np.pi * (r1 + r2 - d) / LAMBDA_M))))
    Smax = max((len(s) for s in static), default=0)
    st_ex = np.zeros((Lu, Smax))
    st_amp = np.zeros((Lu, Smax), complex)
    for u in range(Lu):
        for k, (ex, am) in enumerate(static[u]):
            st_ex[u, k], st_amp[u, k] = ex, am
    # slow complex fluctuation of every static path (L08 flicker), reciprocal: one process per unordered link
    fl = float(nz["flicker"])
    flick = _ou(rd, (Lu, Smax), T, fl, float(nz["flicker_tau_s"]), dt, complex_=True) if fl > 0 and Smax else None

    # ---- the person ------------------------------------------------------------------------------------------
    v = scene.get("victim")
    shadow = np.zeros(Lu)
    vic_ex = vic_amp = None
    if v:
        rcs = float(v.get("rcs_m2", 0.6))
        rho = float(v.get("body_radius_m", 0.14))
        zt = float(v.get("z", g.TARGET_Z_M))
        bpm = float(v.get("breathing_bpm") or 0.0)
        amp_mm = float(v.get("breath_mm", nz["breath_mm"])) if bpm > 0 else 0.0
        heart_mm = float(v.get("heart_mm", nz["heart_mm"]))
        moving = bool(v.get("moving"))
        heading = float(v.get("heading_deg", 0.0))
        hu = np.array([math.cos(math.radians(heading)), math.sin(math.radians(heading))])
        # chest displacement (mm) and sway (m) per frame, L08's breathing integrator
        chest = np.zeros(T)
        sway = np.zeros((T, 2))
        pos = np.zeros((T, 2))
        ph = rd.uniform(0, 2 * np.pi)
        hph = rd.uniform(0, 2 * np.pi)
        wander, c_mm = 0.0, 0.0
        sw = np.zeros(2)
        sig_sw = (6.0 if moving else float(nz["sway_mm"])) * 1e-3 if (bpm > 0 or moving) else 0.0
        tau_sw = 0.8 if moving else float(nz["sway_tau_s"])
        p_now = np.array([v["x"], v["y"]], float)
        vel = hu * float(nz["crawl_speed_mps"])
        for k in range(T):
            wander += -wander / 8.0 * dt + float(nz["rate_wander"]) * math.sqrt(2 * dt / 8.0) * rd.standard_normal()
            if bpm > 0:
                ph += 2 * math.pi * bpm * (1 + wander) / 60.0 * dt
            goal = amp_mm * float(_breath_shape(ph))
            c_mm += (goal - c_mm) * min(1.0, dt / 0.12)
            hph += 2 * math.pi * float(nz["heart_hz"]) * dt
            chest[k] = c_mm + heart_mm * math.sin(hph)
            sw += -sw / tau_sw * dt + sig_sw * math.sqrt(2 * dt / tau_sw) * rd.standard_normal(2)
            sway[k] = sw
            if moving:
                if rd.random() < 0.02:
                    a = rd.uniform(-1.2, 1.2)
                    ca, sa = math.cos(a), math.sin(a)
                    vel = np.array([ca * vel[0] - sa * vel[1], sa * vel[0] + ca * vel[1]])
                nxt = p_now + vel * dt
                if not g.pip(nxt, poly) or g.poly_dist(nxt, poly) < 0.4:
                    vel = -vel
                    nxt = p_now + vel * dt
                p_now = nxt
            pos[k] = p_now
        c = pos + sway                                                        # [T, 2]
        rcs_db = _ou(rd, (Lu,), T, float(nz["rcs_fluct_db"]) * (2.5 if moving else 1.0), float(nz["rcs_tau_s"]), dt)
        vic_ex = np.zeros((T, Lu))
        vic_amp = np.zeros((T, Lu), complex)
        th0 = rs.uniform(0, 2 * np.pi, Lu)
        dz = zt - zp
        for u, (i, j) in enumerate(links):
            ua = P[i][None, :] - c
            ub = P[j][None, :] - c
            ha = np.maximum(np.linalg.norm(ua, axis=1), 1e-6)
            hb = np.maximum(np.linalg.norm(ub, axis=1), 1e-6)
            r1 = np.sqrt(ha ** 2 + dz ** 2)
            r2 = np.sqrt(hb ** 2 + dz ** 2)
            bis = ua / ha[:, None] + ub / hb[:, None]
            bl = np.linalg.norm(bis, axis=1)
            cos_half = bl / 2.0
            # how much of the breathing torso the link sees: broadside to the body axis fully, end-on 40 %
            vis = 0.4 + 0.6 * np.abs(bis @ np.array([-hu[1], hu[0]])) / np.maximum(bl, 1e-6)
            path = r1 + r2 - 2.0 * rho * cos_half - 2.0 * cos_half * vis * chest * 1e-3
            a = math.sqrt(rcs / (4 * math.pi)) / (r1 * r2) * 10 ** (rcs_db[:, u] / 20.0)
            phs = th0[u] - 2 * np.pi * (path - D[u]) / LAMBDA_M
            if moving:
                phs = phs + float(nz["moving_phase_jitter_rad"]) * rd.standard_normal(T)
                a = a * (1 + 0.15 * rd.standard_normal(T))
            vic_ex[:, u] = path - D[u]
            vic_amp[:, u] = a * np.exp(1j * phs)
            shadow[u] = _body_shadow_db(P[i], P[j], (float(c[T // 2, 0]), float(c[T // 2, 1])), heading)

    # ---- CIR frames per ordered pair -----------------------------------------------------------------------
    sig_n = COUNTS_AT_1M / 10 ** (float(nz["snr_db_1m"]) / 20.0)
    kk = np.arange(n_taps) - K0
    cycles = np.arange(T)
    for u, (i, j) in enumerate(links):
        S = 10 ** (-shadow[u] / 20.0)
        taus = [np.zeros((T, 1))]
        amps = [np.full((T, 1), S / D[u], complex)]
        if Smax:
            taus.append(np.broadcast_to(st_ex[u][None, :] / C * 1e9, (T, Smax)))
            amp_s = st_amp[u][None, :] * (1.0 + flick[:, u, :]) if flick is not None else np.broadcast_to(st_amp[u][None, :], (T, Smax))
            amps.append(amp_s)
        if vic_ex is not None:
            taus.append(vic_ex[:, u:u + 1] / C * 1e9)
            amps.append(vic_amp[:, u:u + 1])
        TAU = np.concatenate(taus, axis=1)             # [T, P] ns
        AMP = np.concatenate(amps, axis=1)             # [T, P]
        for tx, rx in ((i, j), (j, i)):
            uu = rr.random(T)
            early = np.where(rr.random(T) < nz["fp_early_prob"], rr.integers(2, 5, T), 0)
            pos_pk = uu + float(nz["fp_lead_taps"]) + early
            arg = (kk[None, None, :] - pos_pk[:, None, None]) * TS_NS - TAU[:, :, None]
            h = (AMP[:, :, None] * pulse(arg)).sum(axis=1)
            gain = 1.0 + float(nz["gain_sigma"]) * rr.standard_normal(T)
            phi = rr.uniform(0, 2 * np.pi, T)
            h = h * (gain * np.exp(1j * phi))[:, None] * COUNTS_AT_1M
            h = h + sig_n / math.sqrt(2) * (rr.standard_normal(h.shape) + 1j * rr.standard_normal(h.shape))
            h = np.clip(np.rint(h.real), -131072, 131071) + 1j * np.clip(np.rint(h.imag), -131072, 131071)   # 18-bit accumulator
            fp = K0 + uu + float(nz["fp_sigma_taps"]) * rr.standard_normal(T)
            mis = rr.random(T) < nz["fp_misdetect_prob"]
            fp = fp + mis * rr.choice([-1.0, 1.0], T)
            keep = rr.random(T) >= nz["dropout_prob"]
            t = (cycles + tx / N) * dt
            rec["frames"][(ids[tx], ids[rx])] = {"t": t[keep], "cir": h[keep].astype(np.complex64), "fp_index": fp[keep]}

    # ---- two-way ranges, kit `range` shape (L08 v1 formulas) -------------------------------------------------
    rstep = max(1, int(round(fhz / float(nz["range_hz"]))))
    for k in range(0, T, rstep):
        t = k * dt
        for u, (i, j) in enumerate(links):
            if rg.random() < nz["range_drop_prob"]:
                continue
            sd = shadow[u]
            dd = D[u] + float(nz["range_sigma_m"]) * rg.standard_normal()
            fpw = -70.0 - 20 * math.log10(D[u]) - sd + 0.5 * rg.standard_normal()
            rssi = fpw + 2.0 + 0.55 * sd + 0.4 * rg.standard_normal()
            los = float(np.clip(1.0 - (rssi - fpw - 6.0) / 4.0, 0.0, 1.0))
            rec["ranges"].append({"type": "range", "t": round(float(t), 3), "a": ids[i], "b": ids[j], "d": round(float(dd), 4),
                                  "rssi": round(float(rssi), 2), "fp": round(float(fpw), 2), "los": round(los, 2)})
    return rec
