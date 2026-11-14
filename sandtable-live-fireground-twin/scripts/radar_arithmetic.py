"""Radar arithmetic for Sandtable's scout mode: is one UWB board enough, and what do more buy?

Every computed number in docs/RADAR_FEASIBILITY.md, docs/owner_questions.json and
docs/readme_radar_section.md comes from this file. It is plain arithmetic and geometry
(no simulator, no noise model beyond what is stated next to each number).

Run:   .venv/bin/python scripts/radar_arithmetic.py
Writes: results/radar_arithmetic.json, figures/radar_ellipses.png, figures/radar_wavelengths.png
Needs: numpy and pillow only (both in requirements.txt).

Where a constant comes from outside, its source key points into SOURCES (URLs and the
exact table or line). Constants marked ASSUMED are choices, not facts, and say so.
"""
from __future__ import annotations

import itertools
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "radar_arithmetic.json"
FIG_ELLIPSES = ROOT / "figures" / "radar_ellipses.png"
FIG_WAVES = ROOT / "figures" / "radar_wavelengths.png"

# ---------------------------------------------------------------------------------------
# Sources. Each entry is something that was actually opened while writing this.
# ---------------------------------------------------------------------------------------
SOURCES = {
    "dw3000_ds": {
        "what": "Decawave/Qorvo DW3000 Datasheet, Version 1.1 (2020)",
        "url": "https://atta.szlcsc.com/upload/public/pdf/source/20220621/2EAA42E8F3E2D83DDB8686FF8DCFAD44.pdf",
        "used": "Table 16 (channels 5 and 9: 6489.6 and 7987.2 MHz, bandwidth 499.2 MHz); Table 18 (SHR symbol "
                "993.59 ns at 16 MHz PRF, 1017.63 ns at 64 MHz PRF; data symbol 128.21 ns at 6.81 Mbps); "
                "Table 7 (8-symbol SFD for 64-symbol preambles); section 2 ('A TX/RX switch is used to connect the "
                "receiver or transmitter to the antenna port'); Table 21 (TX and RX are separate states).",
    },
    "dw3000_driver": {
        "what": "Qorvo DW3000 driver as shipped in the Arduino library UWB_DW1000_DW3000 1.0.1 "
                "(src/qorvo/dw3000_device_api.h)",
        "url": None,
        "used": "line 141 (DWT_PLEN_32, the shortest preamble the driver offers, 'Non-standard length 32'); "
                "line 171 (DWT_RESPONSE_EXPECTED: 'Will enable the receiver after TX has completed').",
    },
    "dwm3000_ds": {
        "what": "Qorvo DWM3000 module datasheet, revision B (May 2021)",
        "url": "https://farnell.com/datasheets/4509027.pdf",
        "used": "the module is 'based on the Qorvo DW3110 IC' (one antenna port, no PDoA); the antenna is "
                "vertically polarised and omnidirectional in one plane; operating temperature -40 to +85 C.",
    },
    "ledergerber2020": {
        "what": "Ledergerber and D'Andrea, A Multi-Static Radar Network with Ultra-Wideband Radio-Equipped "
                "Devices, Sensors 20(6):1599, 2020",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7147715",
        "used": "CIR sample spacing about 1 ns; real DWM1000 tracking of a walking person, RMSE 0.33 m with "
                "four devices and 0.42 m with three; static persons cannot be localised.",
    },
    "l08_real_eth": {
        "what": "Lane L08 (tagless-uwb-echo-radar) results/real_eth.json: its pipeline run on the ETH Zurich "
                "DWM1000 recording of the paper above (real radio data, not our hardware); excerpt in l08_excerpt",
        "url": None,
        "used": "median over 12 links, relative to the direct path: noise floor -33.0 dB; residual after the "
                "direct path in a quiet scene -21.9 dB; tracking verdict FAILED (person 2-4 m from radios).",
    },
    "l08_sim_model": {
        "what": "Lane L08 docs/SIM_MODEL.md (its simulator's echo amplitude model)",
        "url": None,
        "used": "echo amplitude relative to direct path a = sqrt(rcs / (4 pi)) / (R1 R2) times d; "
                "person rcs 0.6 m^2 (0.4-0.9 across seeds). An ASSUMPTION carried over, not a measurement.",
    },
    "dwm3001c_mikroe": {
        "what": "MikroElektronika, RTLS UWB Click (MIKROE-6863) data sheet, hosted by Farnell; a third-party board "
                "built on the Qorvo DWM3001C module",
        "url": "https://www.farnell.com/datasheets/4790155.pdf",
        "used": "the DWM3001C 'integrates a DW3110 UWB transceiver, nRF52833 Bluetooth Low Energy MCU, LIS2DH12 "
                "accelerometer, planar UWB antenna'.",
    },
    "qorvo_forum_radar": {
        "what": "Qorvo Tech Forum, 'Device-free human activity sensing project using the DWM3001CDK development "
                "board', reply by Qorvo staff, 2 June 2026",
        "url": "https://forum.qorvo.com/t/device-free-human-activity-sensing-project-using-the-dwm3001cdk-development-board/25697",
        "used": "'The DWM3001C was not meant for radar and it cannot simultaneously transmit and receive at the "
                "same time.' 'Our QM35 series has this ability.'",
    },
    "slmx4": {
        "what": "SensorLogic SLMX4 Health Module data sheet (2022), a module built on Novelda's X4 impulse radar",
        "url": "https://sensorlogicinc.github.io/modules/docs/SLMX4_Data_Sheet_2022.pdf",
        "used": "X4: impulse radar, CF = 7.29 GHz, BW = 1.4 GHz, integrated planar patch antenna pair, RF data "
                "sampled at 23.328 GS/s, respiration out to 5 m.",
    },
    "sample_scan": {
        "what": "fixtures/scout/sample_scan.json in this repository (a labelled sample, not a measurement)",
        "url": None,
        "used": "room polygon 9.5 x 8.65 m, puck layout P1-P5 and the victim position used for the geometry.",
    },
}

# Physical constant.
C = 299_792_458.0  # m/s

# DW3000 (dw3000_ds Table 16, Table 18).
CH5_HZ = 6489.6e6
CH9_HZ = 7987.2e6
BANDWIDTH_HZ = 499.2e6
SHR_SYMBOL_S = {"prf16": 993.59e-9, "prf64": 1017.63e-9}
SFD_SYMBOLS_BPRF = 8                              # Table 7, IEEE 802.15.4z 8-symbol SFD
PREAMBLE_LENGTHS = [32, 64, 128, 1024]           # 32 = shortest in the driver; 64 = shortest standard
TAP_S = 1.0 / (2.0 * BANDWIDTH_HZ)               # 1.0016 ns: accumulator sample spacing (ledergerber2020, L08 CIR_NOTES)

# Numbers quoted from lane L08 (../tagless-uwb-echo-radar), copied here on 2026-11-14 because that project is
# being archived. Values exactly as in its results/*.json; labels say which are simulated and which are real data.
L08_EXCERPT = {
    "real_eth_json": {
        "simulated": False,
        "data": "ETH Zurich DWM1000 recording (doi:10.3929/ethz-b-000397625), four modules in a ~4 m square, "
                "12 ordered links, 31 taps per frame, untagged walking person, motion-capture truth; L08's pipeline",
        "direct_snr_db_median": 32.568,
        "noise_floor_db_rel_direct": -33.033,
        "post_peak_residual_db_quiet_scene": -21.874,
        "fp_jitter_taps": 0.087,
        "per_link_within_one_tap_of_truth": 0.327,
        "per_link_chance_level": 0.098,
        "median_abs_path_error_cm_of_matches": 14.1,
        "tracking_verdict": "FAILED: the tracker follows clutter, not the person",
        "tracking_inside_rectangle_median_cm": 187.0,
        "tracking_inside_rectangle_p90_cm": 342.8,
    },
    "simulated": {
        "breathing_pair_mae_bpm": 2.017,
        "breathing_pair_apnea_missed_of": [2, 4],
        "node_pos_sigma_m_vs_target_median_cm": {"0.00": 1.96, "0.03": 4.34, "0.06": 8.27, "0.10": 13.53},
        "noise_model": "direct-path SNR 36 dB at 1 m, first-path index jitter 0.03 tap + 0.4% one-tap misdetections, "
                       "3% gain jitter, random carrier phase per frame, 2% frame loss, 3 cm node-position error, "
                       "static multipath, body sway 1.2 mm, person rcs 0.4-0.9 m^2",
    },
    "cir_notes": "DW3000 accumulator read with dwt_readaccdata, 6 bytes per complex sample, first-path index in "
                 "1/64 tap, 1016 samples at 64 MHz PRF, about 1.0016 ns per sample, valid only until the receiver "
                 "is re-enabled (L08 hardware/CIR_NOTES.md, from the Qorvo driver and forum).",
}

# Real-data reference levels (l08_real_eth), dB relative to the direct path, DWM1000.
REF_NOISE_FLOOR_DB = -33.0
REF_QUIET_RESIDUAL_DB = -21.9

# ASSUMED values (stated, not measured).
PERSON_RCS_M2 = 0.6        # as in L08's simulator; a person lying on the floor seen at grazing angles is unknown
SIGMA_PATH_M = 0.10        # per-link echo path error used to turn GDOP into metres (an assumption)

# The demo room and the sample throw (sample_scan; illustration only).
ROOM_W, ROOM_H = 9.5, 8.65
DOOR = (9.5, 7.2)
PUCKS = {"P1": (7.9, 6.7), "P2": (6.2, 5.0), "P3": (7.5, 3.3), "P4": (4.7, 6.9), "P5": (5.3, 2.7)}
VICTIM = (2.4, 2.1)
# A contrast layout: the same five pucks placed deliberately around the room (ASSUMED, for comparison).
PUCKS_SPREAD = {"P1": (8.6, 7.8), "P2": (0.9, 7.8), "P3": (0.9, 0.9), "P4": (8.6, 0.9), "P5": (4.75, 4.3)}
POINTS = {"sample victim (2.4, 2.1)": VICTIM, "room centre (4.75, 4.33)": (4.75, 4.325),
          "north-west corner (1.0, 7.65)": (1.0, 7.65)}

# ---------------------------------------------------------------------------------------
# Smoke and sensor wavelengths (filled from sources; see SMOKE_SOURCES).
# ---------------------------------------------------------------------------------------
SMOKE_SOURCES = {
    "ul_smoke_2007": {
        "what": "Fabian and Gandhi, Smoke Characterization Project, UL / Fire Protection Research Foundation, 2007",
        "url": "https://code-authorities.ul.com/wp-content/uploads/2014/04/SmokeStudyTechnicalReport.pdf",
        "used": "particles measured from 10 nm to 10 um; mean diameters 0.040-0.293 um in small-scale flaming tests "
                "(Table 9), 0.041-0.156 um non-flaming (Table 10), 0.05-0.35 um in the fire test room (Table 21)."},
    "leeds_2019": {
        "what": "Mustafa, Andrews et al., Smoke Particle Size Distribution in Pine Wood Fires, ISFEH9, 2019",
        "url": "https://eprints.whiterose.ac.uk/157368/1/Mustafa%20et%20al%202019-ISFEH9%20Smoke%20Particle%20Size%20Distribution%20in%20Pine%20Wood%20Fires-k19-52.pdf",
        "used": "ultra-fine particles < 100 nm early, then 100-1000 nm with a peak at 200 nm as the fire progressed."},
    "nist_tn1499": {
        "what": "Amon, Bryner, Lock, Hamins, NIST Technical Note 1499, 2008 (thermal imagers for fire fighting)",
        "url": "https://nvlpubs.nist.gov/nistpubs/Legacy/TN/nbstechnicalnote1499.pdf",
        "used": "visible 0.4-0.7 um; fire service thermal imagers 8-14 um."},
    "lidar_review": {
        "what": "Li and Ibanez-Guzman, Lidar for Autonomous Driving, arXiv:2004.08467, 2020",
        "url": "https://arxiv.org/pdf/2004.08467",
        "used": "850-950 nm (905 nm mainstream) and 1550 nm lasers."},
    "psu_scattering": {
        "what": "Brune, METEO 300 section 6.12, Penn State",
        "url": "https://courses.ems.psu.edu/meteo300/node/785",
        "used": "x = 2 pi r / lambda; x < 0.002 negligible scattering; 0.002-0.2 Rayleigh; 0.2-2000 Mie; Rayleigh "
                "cross-section ~ r^6 / lambda^4."},
}
SMOKE_PARTICLES_M = (1e-8, 1e-6)          # 10 nm (instrument floor, ul_smoke_2007) to 1000 nm (leeds_2019)
SMOKE_MEANS_M = (0.04e-6, 0.35e-6)        # measured mean diameters (ul_smoke_2007 Tables 9, 10, 21)
SMOKE_LABEL = "10 nm - 1 um; measured means 0.04 - 0.35 um (darker)"
SENSOR_BANDS_M = {
    "visible:0.4 - 0.7 um": (0.4e-6, 0.7e-6),
    "lidar:905 nm": (905e-9, 905e-9),
    "lidar:1550 nm": (1550e-9, 1550e-9),
    "lwir:8 - 14 um": (8e-6, 14e-6),
    "uwb:ch 5, 46.2 mm": (C / CH5_HZ, C / CH5_HZ),
    "uwb:ch 9, 37.5 mm": (C / CH9_HZ, C / CH9_HZ),
}


def r(x, n=3):
    """Round for the JSON (keeps the file readable; full precision is in the code)."""
    if x is None:
        return None
    if isinstance(x, float) and (math.isinf(x) or math.isnan(x)):
        return None
    return round(float(x), n)


# ---------------------------------------------------------------------------------------
# 1. Why one board hears nothing: frame duration against room-scale round trips.
# ---------------------------------------------------------------------------------------
def frame_timing() -> dict:
    sym = SHR_SYMBOL_S["prf64"]
    preambles = {}
    for n in PREAMBLE_LENGTHS:
        t_pre = n * sym
        t_shr = (n + SFD_SYMBOLS_BPRF) * sym
        preambles[str(n)] = {
            "preamble_us": r(t_pre * 1e6, 2),
            "preamble_plus_sfd_us": r(t_shr * 1e6, 2),
            # A reflector would have to be this far away for its echo of the preamble to start
            # arriving only after the preamble+SFD has finished going out.
            "blind_range_m_for_shr": r(C * t_shr / 2.0, 0),
        }
    walls = {}
    t_min = PREAMBLE_LENGTHS[0] * sym
    for d in [1, 2, 3, 4, 5, 6]:
        rt = 2.0 * d / C
        walls[f"{d} m"] = {"round_trip_ns": r(rt * 1e9, 2),
                           "shortest_preamble_over_round_trip": r(t_min / rt, 0)}
    diag = math.hypot(ROOM_W, ROOM_H)
    return {
        "prf": "64 MHz (BPRF)",
        "shr_symbol_ns": r(sym * 1e9, 2),
        "preamble": preambles,
        "round_trip_to_wall": walls,
        "room_diagonal_m": r(diag, 2),
        "room_diagonal_round_trip_ns": r(2 * diag / C * 1e9, 1),
        "note": "Lower bound on the frame: the preamble (and SFD) alone. PHR and payload add more. "
                "The echo of every part of the frame arrives while that frame is still being sent, "
                "and the DW3000's antenna is switched to the transmitter for the whole frame.",
    }


# ---------------------------------------------------------------------------------------
# 2. Resolution, tap spacing, wavelength, breathing phase.
# ---------------------------------------------------------------------------------------
def resolution() -> dict:
    mono = C / (2.0 * BANDWIDTH_HZ)
    path_res = C / BANDWIDTH_HZ
    bistatic = {}
    for beta in [0, 60, 90, 120, 150, 170]:
        f = math.cos(math.radians(beta) / 2.0)
        bistatic[f"{beta} deg"] = r(mono / f, 3)
    x4_bw = 1.4e9   # slmx4
    return {
        "contrast_x4_1p4ghz_monostatic_range_resolution_m": r(C / (2.0 * x4_bw), 3),
        "bandwidth_mhz": r(BANDWIDTH_HZ / 1e6, 1),
        "monostatic_range_resolution_m": r(mono, 3),
        "bistatic_path_resolution_m": r(path_res, 3),
        "bistatic_range_resolution_m_by_bistatic_angle": bistatic,
        "pulse_width_1_over_B_ns": r(1e9 / BANDWIDTH_HZ, 3),
        "tap_ns": r(TAP_S * 1e9, 4),
        "tap_path_m": r(C * TAP_S, 3),
        "tap_monostatic_range_m": r(C * TAP_S / 2.0, 3),
        "note": "Resolution = how far apart two echoes must be to show as two. Path resolution c/B; "
                "projected onto the plane it is c/(2B cos(beta/2)), beta = bistatic angle at the target "
                "(derived from the gradient of R1 + R2, whose length is 2 cos(beta/2)). Accuracy of a single "
                "isolated echo can be a fraction of a tap; resolution cannot.",
    }


def wavelength_and_breathing() -> dict:
    lam5 = C / CH5_HZ
    lam9 = C / CH9_HZ
    rows = []
    for mm in [1, 2, 3, 4, 5]:
        d = mm / 1000.0
        row = {"chest_mm": mm}
        for beta in [0, 90]:
            dpath = 2.0 * d * math.cos(math.radians(beta) / 2.0)
            ph = 2.0 * math.pi * dpath / lam5
            row[f"beta{beta}_path_change_mm"] = r(dpath * 1000, 2)
            row[f"beta{beta}_phase_rad"] = r(ph, 3)
            row[f"beta{beta}_phase_deg"] = r(math.degrees(ph), 1)
        rows.append(row)
    return {
        "ch5_wavelength_mm": r(lam5 * 1000, 2),
        "ch9_wavelength_mm": r(lam9 * 1000, 2),
        "ch5_phase_deg_per_mm_path": r(360.0 / (lam5 * 1000), 2),
        "chest_motion_ch5": rows,
        "note": "Phase change of the echo = 2 pi x (path change) / lambda; path change = 2 x chest motion x "
                "cos(beta/2) for motion along the bistatic bisector. beta = 0 is the monostatic best case.",
    }


# ---------------------------------------------------------------------------------------
# 3. Pairs, self-location redundancy.
# ---------------------------------------------------------------------------------------
def pairs_table() -> list:
    out = []
    for n in range(1, 6):
        pairs = n * (n - 1) // 2
        dof = max(2 * n - 3, 0) if n >= 2 else 0
        out.append({
            "pucks": n,
            "pairs_ellipses": pairs,
            "ordered_links": n * (n - 1),
            "self_location_unknowns_2d": dof,
            "self_location_redundancy": pairs - dof if n >= 2 else 0,
            "what_it_gives": {
                1: "nothing: the board cannot receive while it transmits",
                2: "one ellipse: presence, echo path length and breathing; no position",
                3: "three ellipses: a 2-D position, no redundancy in the self-location",
                4: "six ellipses: a 2-D position with redundancy; one bad link can be outvoted",
                5: "ten ellipses: a 2-D position, better geometry, outlier rejection, a coarse echo map",
            }[n],
        })
    return out


# ---------------------------------------------------------------------------------------
# 4. Geometry: GDOP of the ellipse intersection, consistent regions, self-location error.
# ---------------------------------------------------------------------------------------
def path_len(p, a, b):
    return math.dist(p, a) + math.dist(p, b)


def grad_row(p, a, b):
    pa = np.array(p) - np.array(a)
    pb = np.array(p) - np.array(b)
    na, nb = np.linalg.norm(pa), np.linalg.norm(pb)
    if na < 1e-9 or nb < 1e-9:
        return np.zeros(2)
    return pa / na + pb / nb


def gdop(p, pucks: list) -> float:
    rows = [grad_row(p, a, b) for a, b in itertools.combinations(pucks, 2)]
    if len(rows) < 2:
        return math.inf
    J = np.array(rows)
    JTJ = J.T @ J
    if np.linalg.cond(JTJ) > 1e10:
        return math.inf
    return float(math.sqrt(np.trace(np.linalg.inv(JTJ))))


def room_grid(step=0.25, margin=0.25):
    xs = np.arange(margin, ROOM_W - margin + 1e-9, step)
    ys = np.arange(margin, ROOM_H - margin + 1e-9, step)
    return [(float(x), float(y)) for x in xs for y in ys]


def gdop_stats(layout: dict, ids: list, grid) -> dict:
    pucks = [layout[i] for i in ids]
    vals = []
    for p in grid:
        if min(math.dist(p, q) for q in pucks) < 0.3:
            continue
        vals.append(gdop(p, pucks))
    v = np.array(vals)
    finite = v[np.isfinite(v)]
    return {
        "pucks": ids,
        "median": r(float(np.median(finite)), 2),
        "p90": r(float(np.percentile(finite, 90)), 2),
        "share_over_5": r(float(np.mean(v > 5.0)), 3),
        "at_points": {k: r(gdop(p, pucks), 2) for k, p in POINTS.items()},
    }


def gdop_tables(grid) -> dict:
    out = {}
    for name, layout in [("sample_throw", PUCKS), ("spread_contrast", PUCKS_SPREAD)]:
        ids_all = list(layout)
        per_n = {}
        for n in [3, 4, 5]:
            first = gdop_stats(layout, ids_all[:n], grid)
            subs = [gdop_stats(layout, list(s), grid) for s in itertools.combinations(ids_all, n)]
            meds = [s["median"] for s in subs]
            per_n[str(n)] = {
                "first_n_in_throw_order": first,
                "all_subsets_room_median_gdop": {"best": min(meds), "worst": max(meds),
                                                 "n_subsets": len(subs)},
                "sigma_xy_m_at_room_median_for_sigma_path_0p10": r(first["median"] * SIGMA_PATH_M, 2),
            }
        out[name] = per_n
    out["note"] = ("GDOP = sqrt(trace((J^T J)^-1)), J rows = gradient of R1 + R2 for every pair; position "
                   "error ~ GDOP x per-link path error. Grid 0.25 m inside the room, cells within 0.3 m of a puck "
                   "skipped. Infinite GDOP (on a baseline, or one link only) counts as over 5. "
                   f"sigma_path {SIGMA_PATH_M} m is an ASSUMPTION used only to show the scale.")
    return out


def consistent_mask(target, pucks: list, tol: float, step=0.05):
    xs = np.arange(step / 2, ROOM_W, step)
    ys = np.arange(step / 2, ROOM_H, step)
    X, Y = np.meshgrid(xs, ys)
    ok = np.ones_like(X, dtype=bool)
    for a, b in itertools.combinations(pucks, 2):
        p_true = path_len(target, a, b)
        p = np.hypot(X - a[0], Y - a[1]) + np.hypot(X - b[0], Y - b[1])
        ok &= np.abs(p - p_true) <= tol
    return xs, ys, ok


def count_components(mask: np.ndarray) -> int:
    seen = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    comps = 0
    for i in range(h):
        for j in range(w):
            if mask[i, j] and not seen[i, j]:
                comps += 1
                stack = [(i, j)]
                seen[i, j] = True
                while stack:
                    a, b = stack.pop()
                    for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                        c, d = a + da, b + db
                        if 0 <= c < h and 0 <= d < w and mask[c, d] and not seen[c, d]:
                            seen[c, d] = True
                            stack.append((c, d))
    return comps


def consistent_regions() -> dict:
    out = {}
    ids = list(PUCKS)
    for tol in [0.30, 0.10]:
        rows = {}
        for n in [2, 3, 4, 5]:
            pucks = [PUCKS[i] for i in ids[:n]]
            xs, ys, m = consistent_mask(VICTIM, pucks, tol)
            cell = (xs[1] - xs[0]) * (ys[1] - ys[0])
            X, Y = np.meshgrid(xs, ys)
            far = float(np.hypot(X - VICTIM[0], Y - VICTIM[1])[m].max())
            rows[str(n)] = {"area_m2": r(float(m.sum() * cell), 2), "separate_regions": count_components(m),
                            "farthest_consistent_point_m": r(far, 2)}
        out[f"tol_{tol:.2f}_m_path"] = rows
    out["note"] = ("Area of the room whose bistatic path on EVERY link matches the sample victim's within the "
                   "tolerance (no noise, exact puck positions, first N pucks in throw order). 0.30 m of path = "
                   "one CIR tap; 0.10 m = a sub-tap estimate. 'separate_regions' (8-connected, 5 cm cells) > 1 means "
                   "pieces that do not touch at this grid; 'farthest_consistent_point_m' is how far from the true "
                   "target a point can sit and still satisfy every pair.")
    return out


def solve_gn(p0, pucks, paths, iters=30):
    p = np.array(p0, dtype=float)
    links = list(itertools.combinations(range(len(pucks)), 2))
    for _ in range(iters):
        J, res = [], []
        for k, (i, j) in enumerate(links):
            a, b = pucks[i], pucks[j]
            J.append(grad_row(p, a, b))
            res.append(paths[k] - path_len(p, a, b))
        J, res = np.array(J), np.array(res)
        step, *_ = np.linalg.lstsq(J, res, rcond=None)
        p = p + step
        if np.linalg.norm(step) < 1e-6:
            break
    return p


def self_location_effect(trials=400, seed=310) -> dict:
    rng = np.random.default_rng(seed)
    ids = list(PUCKS)
    out = {}
    for name, target in [("sample_victim", VICTIM), ("room_centre", POINTS["room centre (4.75, 4.33)"])]:
        rows = {}
        for n in [3, 4, 5]:
            true = [np.array(PUCKS[i]) for i in ids[:n]]
            links = list(itertools.combinations(range(n), 2))
            excess = [path_len(target, true[i], true[j]) - math.dist(true[i], true[j]) for i, j in links]
            row = {}
            for sp in [0.03, 0.05, 0.10, 0.20]:
                errs = []
                for _ in range(trials):
                    est = [t + rng.normal(0, sp, 2) for t in true]
                    paths = [excess[k] + float(np.linalg.norm(est[i] - est[j])) for k, (i, j) in enumerate(links)]
                    p = solve_gn(target, est, paths)
                    errs.append(float(np.linalg.norm(p - np.array(target))))
                errs = np.array(errs)
                row[f"{int(sp * 100)} cm"] = {"median_m": r(float(np.median(errs)), 3),
                                              "p90_m": r(float(np.percentile(errs, 90)), 3)}
            rows[str(n)] = row
        out[name] = rows
    out["note"] = ("Monte Carlo, seed 310, 400 trials: each puck position off by N(0, s) per axis, echo excess path "
                   "exact, the solver uses the wrong puck positions (and the baseline between them). Target error "
                   "relative to the pucks' own frame; registration into the twin adds its own error on top. "
                   "Pucks: first N of the sample throw.")
    return out


# ---------------------------------------------------------------------------------------
# 5. Link budget in relative terms: echo against the direct path.
# ---------------------------------------------------------------------------------------
def echo_to_direct_db(p, a, b, rcs=PERSON_RCS_M2):
    d = math.dist(a, b)
    r1, r2 = math.dist(p, a), math.dist(p, b)
    amp = math.sqrt(rcs / (4.0 * math.pi)) * d / (r1 * r2)
    return 20.0 * math.log10(amp)


def link_budget() -> dict:
    sym = {}
    for rr in [1, 2, 3, 4, 5, 6]:
        sym[f"{rr} m"] = r(echo_to_direct_db((0.0, float(rr)), (-1.0, 0.0), (1.0, 0.0)), 1)
    ids = list(PUCKS)
    per_link = []
    for i, j in itertools.combinations(ids, 2):
        a, b = PUCKS[i], PUCKS[j]
        per_link.append({"pair": f"{i}-{j}", "baseline_m": r(math.dist(a, b), 2),
                         "R1_m": r(math.dist(VICTIM, a), 2), "R2_m": r(math.dist(VICTIM, b), 2),
                         "echo_to_direct_db": r(echo_to_direct_db(VICTIM, a, b), 1)})
    vals = [x["echo_to_direct_db"] for x in per_link]
    # Integration gain if the echo phase held still and the noise were white (an upper bound).
    frame_hz = 20.0
    integ = {f"{s} s": r(10 * math.log10(frame_hz * s), 1) for s in [0.5, 1, 2]}
    return {
        "person_rcs_m2_assumed": PERSON_RCS_M2,
        "formula": "echo/direct amplitude = sqrt(rcs / (4 pi)) x d / (R1 x R2), isotropic antennas",
        "symmetric_case_baseline_2m": {"note": "target on the perpendicular bisector of a 2 m baseline, "
                                               "distance from the baseline centre", "echo_to_direct_db": sym},
        "sample_victim_per_link": per_link,
        "sample_victim_best_db": max(vals),
        "sample_victim_median_db": r(float(np.median(vals)), 1),
        "sample_victim_worst_db": min(vals),
        "real_reference_dwm1000": {"noise_floor_db": REF_NOISE_FLOOR_DB,
                                   "quiet_scene_residual_after_direct_path_db": REF_QUIET_RESIDUAL_DB,
                                   "source": "l08_real_eth"},
        "coherent_integration_gain_db_at_20hz_upper_bound": integ,
        "note": "Relative levels only: antenna gains, polarisation, wall loss and the body's real cross-section "
                "are left out. The real reference levels come from different hardware (DWM1000) in a 4 m room.",
    }


# ---------------------------------------------------------------------------------------
# 6. Smoke: particle size against sensor wavelengths (sourced values only).
# ---------------------------------------------------------------------------------------
def regime(x: float) -> str:
    # Thresholds from psu_scattering.
    if x < 0.002:
        return "negligible scattering"
    if x < 0.2:
        return "Rayleigh"
    if x < 2000:
        return "Mie"
    return "geometric"


def smoke_table() -> dict:
    rows = {}
    for label, (lo, hi) in SENSOR_BANDS_M.items():
        lam = math.sqrt(lo * hi)
        row = {"band_m": [lo, hi], "wavelength_used_m": lam}
        for d in [0.1e-6, 1e-6]:
            x = math.pi * d / lam
            row[f"x_for_{d * 1e6:g}um"] = float(f"{x:.3g}")
            row[f"regime_for_{d * 1e6:g}um"] = regime(x)
        rows[label.replace(":", ": ")] = row
    return {"smoke_particle_diameter_m": list(SMOKE_PARTICLES_M), "measured_mean_diameter_m": list(SMOKE_MEANS_M),
            "sensors": rows,
            "note": "x = pi d / lambda; regimes from psu_scattering. Scattering is not the only loss: soot "
                    "absorbs light and infrared, and flames, water and walls are separate effects.",
            "sources": SMOKE_SOURCES}


# ---------------------------------------------------------------------------------------
# Figures (pillow only).
# ---------------------------------------------------------------------------------------
SURFACE = (252, 252, 251)
INK = (11, 11, 11)
INK2 = (82, 81, 78)
MUTED = (150, 149, 145)
BLUE = (42, 120, 214)
ORANGE = (235, 104, 52)
AQUA = (27, 175, 122)
YELLOW = (237, 161, 0)
GRID = (226, 225, 221)
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size, bold=False):
    for name in (["DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"]):
        p = FONT_DIR / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def ellipse_pts(a, b, path, n=360):
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dd = math.dist(a, b) / 2
    A = path / 2
    if A <= dd:
        return []
    B = math.sqrt(A * A - dd * dd)
    th = math.atan2(b[1] - a[1], b[0] - a[0])
    pts = []
    for k in range(n + 1):
        t = 2 * math.pi * k / n
        x, y = A * math.cos(t), B * math.sin(t)
        pts.append((cx + x * math.cos(th) - y * math.sin(th), cy + x * math.sin(th) + y * math.cos(th)))
    return pts


def draw_ellipses_figure(path: Path, frame: dict, regions: dict):
    S = 2                                   # supersample
    W, H = 1500 * S, 1530 * S
    img = Image.new("RGB", (W, H), SURFACE)
    dr = ImageDraw.Draw(img)
    f_title = font(30 * S, True)
    f_h = font(21 * S, True)
    f_b = font(16 * S)
    f_s = font(14 * S)
    dr.text((40 * S, 26 * S), "What 1, 2, 3 and 5 pucks can say about one target", font=f_title, fill=INK)
    dr.text((40 * S, 68 * S), "Room 9.5 x 8.65 m drawn to scale. Each line: the points whose path transmitter -> point "
            "-> receiver equals the target's.", font=f_b, fill=INK2)
    dr.text((40 * S, 92 * S), "Shaded: where every pair agrees within 0.30 m of path (one CIR tap). Geometry only, "
            "no noise. Sample throw layout and sample victim, not a measurement.", font=f_b, fill=INK2)

    panels = [(1, "1 puck: no measurement"), (2, "2 pucks: 1 ellipse, no position"),
              (3, "3 pucks: 3 ellipses"), (5, "5 pucks: 10 ellipses")]
    scale = 68 * S                          # px per metre
    pw, ph = int(ROOM_W * scale), int(ROOM_H * scale)
    origins = [(60 * S, 170 * S), (60 * S + pw + 90 * S, 170 * S),
               (60 * S, 170 * S + ph + 115 * S), (60 * S + pw + 90 * S, 170 * S + ph + 115 * S)]
    ids = list(PUCKS)
    tol = 0.30
    for (n, title), (ox, oy) in zip(panels, origins):
        def P(x, y):
            return (ox + x * scale, oy + (ROOM_H - y) * scale)
        dr.text((ox, oy - 40 * S), title, font=f_h, fill=INK)
        # light 1 m grid
        for gx in range(1, int(ROOM_W) + 1):
            dr.line([P(gx, 0), P(gx, ROOM_H)], fill=GRID, width=1 * S)
        for gy in range(1, int(ROOM_H) + 1):
            dr.line([P(0, gy), P(ROOM_W, gy)], fill=GRID, width=1 * S)
        pucks = [PUCKS[i] for i in ids[:n]]
        if n >= 2:
            xs, ys, m = consistent_mask(VICTIM, pucks, tol, step=0.05)
            half = 0.025
            for iy, y in enumerate(ys):
                for ix, x in enumerate(xs):
                    if m[iy, ix]:
                        x0, y0 = P(x - half, y + half)
                        x1, y1 = P(x + half, y - half)
                        dr.rectangle([x0, y0, x1, y1], fill=(250, 200, 178))
            for a, b in itertools.combinations(pucks, 2):
                pts = ellipse_pts(a, b, path_len(VICTIM, a, b))
                seg = []
                for (x, y) in pts:
                    inside = 0 <= x <= ROOM_W and 0 <= y <= ROOM_H
                    if inside:
                        seg.append(P(x, y))
                    else:
                        if len(seg) > 1:
                            dr.line(seg, fill=BLUE, width=2 * S)
                        seg = []
                if len(seg) > 1:
                    dr.line(seg, fill=BLUE, width=2 * S)
        # room outline and door
        dr.rectangle([P(0, ROOM_H), P(ROOM_W, 0)], outline=INK2, width=3 * S)
        dx, dy = P(*DOOR)
        dr.line([(dx, dy - 0.45 * scale), (dx, dy + 0.45 * scale)], fill=SURFACE, width=7 * S)
        dr.text((dx - 52 * S, dy - 0.45 * scale - 22 * S), "door", font=f_s, fill=INK2)
        # target
        tx, ty = P(*VICTIM)
        k = 9 * S
        dr.line([(tx - k, ty - k), (tx + k, ty + k)], fill=INK, width=3 * S)
        dr.line([(tx - k, ty + k), (tx + k, ty - k)], fill=INK, width=3 * S)
        dr.text((tx + 12 * S, ty + 6 * S), "target", font=f_s, fill=INK)
        # pucks
        for i, (x, y) in zip(ids[:n], pucks):
            px, py = P(x, y)
            rr = 8 * S
            dr.ellipse([px - rr, py - rr, px + rr, py + rr], fill=INK, outline=SURFACE, width=2 * S)
            dr.text((px + 11 * S, py - 22 * S), i, font=f_s, fill=INK)
        # caption under each panel
        cap_y = oy + ph + 10 * S
        if n == 1:
            t_pre = frame["preamble"]["32"]["preamble_us"]
            t_std = frame["preamble"]["64"]["preamble_plus_sfd_us"]
            rt = frame["round_trip_to_wall"]["3 m"]["round_trip_ns"]
            px, py = P(*PUCKS["P1"])
            dr.text((P(0.5, 4.4)[0], P(0.5, 4.4)[1]),
                    f"P1 sends a frame lasting at least {t_pre:.1f} us\n(the shortest preamble; {t_std:.1f} us "
                    f"for the standard one).\nA wall 3 m away answers after {rt:.0f} ns,\nwhile its antenna is "
                    "switched to the transmitter.\nNo echo is ever recorded.", font=f_b, fill=INK2, spacing=6 * S)
            dr.text((ox, cap_y), "A ranging chip cannot listen while it transmits.", font=f_s, fill=INK2)
        else:
            reg = regions["tol_0.30_m_path"][str(n)]
            pairs_txt = "1 pair" if n == 2 else f"{n * (n - 1) // 2} pairs"
            dr.text((ox, cap_y), f"{pairs_txt}. Shaded area {reg['area_m2']:.2f} m^2; it reaches "
                    f"{reg['farthest_consistent_point_m']:.2f} m from the target.", font=f_s, fill=INK2)
    out = img.resize((W // S, H // S), Image.LANCZOS)
    out.save(path, optimize=True)


def draw_wavelengths_figure(path: Path):
    if SMOKE_PARTICLES_M is None:
        return False
    S = 2
    W, H = 1500 * S, 760 * S
    img = Image.new("RGB", (W, H), SURFACE)
    dr = ImageDraw.Draw(img)
    f_title = font(30 * S, True)
    f_b = font(16 * S)
    f_s = font(14 * S)
    f_l = font(17 * S, True)
    dr.text((40 * S, 26 * S), "Smoke particles are far smaller than a radio wavelength", font=f_title, fill=INK)
    dr.text((40 * S, 68 * S), "Logarithmic scale. Particle diameters and sensor wavelengths from the sources in "
            "docs/RADAR_FEASIBILITY.md. Light and LiDAR fall in the Mie regime", font=f_b, fill=INK2)
    dr.text((40 * S, 92 * S), "for these particles (strong scattering); a centimetre radio wave sees them as "
            "far below the size at which scattering stops mattering.", font=f_b, fill=INK2)
    x0, x1 = 300 * S, W - 60 * S
    lo, hi = -8.0, -1.0    # log10 metres: 10 nm .. 10 cm

    def X(m):
        return x0 + (math.log10(m) - lo) / (hi - lo) * (x1 - x0)

    lanes = [("Fire smoke particles", "smoke"), ("Visible camera", "visible"), ("LiDAR", "lidar"),
             ("Thermal imager (LWIR)", "lwir"), ("UWB radar (DW3000)", "uwb")]
    top = 160 * S
    lane_h = 92 * S
    colours = {"smoke": MUTED, "visible": BLUE, "lidar": ORANGE, "lwir": AQUA, "uwb": INK}
    # decade grid
    labels = {-8: "10 nm", -7: "100 nm", -6: "1 um", -5: "10 um", -4: "100 um", -3: "1 mm", -2: "1 cm", -1: "10 cm"}
    axis_y = top + lane_h * len(lanes) + 10 * S
    for e in range(int(lo), int(hi) + 1):
        xx = X(10.0 ** e)
        dr.line([(xx, top - 20 * S), (xx, axis_y)], fill=GRID, width=1 * S)
        tw = dr.textlength(labels[e], font=f_s)
        dr.text((xx - tw / 2, axis_y + 8 * S), labels[e], font=f_s, fill=INK2)
    for k, (name, key) in enumerate(lanes):
        y = top + k * lane_h + lane_h // 2
        dr.text((40 * S, y - 11 * S), name, font=f_l, fill=INK)
        col = colours[key]
        if key == "smoke":
            a, b = SMOKE_PARTICLES_M
            dr.rounded_rectangle([X(a), y - 14 * S, X(b), y + 14 * S], radius=4 * S, fill=(205, 204, 199))
            ma, mb = SMOKE_MEANS_M
            dr.rounded_rectangle([X(ma), y - 14 * S, X(mb), y + 14 * S], radius=4 * S, fill=MUTED)
            dr.text((X(b) + 10 * S, y - 9 * S), SMOKE_LABEL, font=f_s, fill=INK2)
        else:
            for label, (a, b) in SENSOR_BANDS_M.items():
                if not label.startswith(key + ":"):
                    continue
                xp = math.pi * 0.1e-6 / math.sqrt(a * b)
                txt = label.split(":", 1)[1] + f"   x = {xp:.2g}"
                if b / a < 1.02:
                    xx = X(a)
                    dr.rounded_rectangle([xx - 3 * S, y - 11 * S, xx + 3 * S, y + 11 * S], radius=2 * S, fill=col)
                    ty = y - 9 * S + (19 * S if "1550" in txt or "ch 5" in txt else -19 * S)
                    tw = dr.textlength(txt, font=f_s)
                    if xx + 8 * S + tw > W - 20 * S:          # would overflow: put it on the left
                        dr.text((X(C / CH9_HZ) - 16 * S - tw, ty), txt, font=f_s, fill=INK2)
                    else:
                        dr.text((xx + 8 * S, ty), txt, font=f_s, fill=INK2)
                else:
                    dr.rounded_rectangle([X(a), y - 14 * S, X(b), y + 14 * S], radius=4 * S, fill=col)
                    dr.text((X(b) + 10 * S, y - 9 * S), txt, font=f_s, fill=INK2)
    # smoke band shadow across lanes, to compare sizes
    a, b = SMOKE_PARTICLES_M
    for yy in range(top + lane_h, axis_y, 6 * S):
        dr.line([(X(a), yy), (X(a), yy + 3 * S)], fill=MUTED, width=1 * S)
        dr.line([(X(b), yy), (X(b), yy + 3 * S)], fill=MUTED, width=1 * S)
    dr.text((40 * S, H - 74 * S), "x = pi d / wavelength for a 0.1 um particle. Below 0.002 scattering is negligible, "
            "0.002 - 0.2 is Rayleigh, 0.2 - 2000 is Mie (Penn State METEO 300). Dashed: the smoke range carried down.",
            font=f_s, fill=INK2)
    dr.text((40 * S, H - 50 * S), "Scattering is not the only loss: soot also absorbs light and infrared, and flames, "
            "water spray and walls are separate effects (docs/RADAR_FEASIBILITY.md).", font=f_s, fill=INK2)
    out = img.resize((W // S, H // S), Image.LANCZOS)
    out.save(path, optimize=True)
    return True




def main():
    grid = room_grid()
    frame = frame_timing()
    res = resolution()
    wav = wavelength_and_breathing()
    regions = consistent_regions()
    result = {
        "schema": "sandtable.radar_arithmetic/1",
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "simulated": False,
        "kind": "arithmetic and geometry from datasheet constants; no radio data",
        "frame_timing": frame,
        "resolution": res,
        "wavelength": wav,
        "pairs": pairs_table(),
        "gdop": gdop_tables(grid),
        "consistent_regions": regions,
        "self_location_error": self_location_effect(),
        "link_budget": link_budget(),
        "smoke": smoke_table(),
        "layout": {"room_m": [ROOM_W, ROOM_H], "door": list(DOOR), "pucks_sample_throw": PUCKS,
                   "pucks_spread_contrast": PUCKS_SPREAD, "victim": list(VICTIM), "points": POINTS},
        "l08_excerpt": L08_EXCERPT,
        "boards": "Qorvo DWM3001CDK (DWM3001C module: nRF52833 + DW3110), confirmed by the coordinator 2026-11-14",
        "assumptions": {"person_rcs_m2": PERSON_RCS_M2, "sigma_path_m": SIGMA_PATH_M,
                        "layout": "sample throw from fixtures/scout/sample_scan.json; spread layout chosen by hand"},
        "sources": SOURCES,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=1) + "\n")
    tmp.replace(OUT_JSON)
    FIG_ELLIPSES.parent.mkdir(parents=True, exist_ok=True)
    draw_ellipses_figure(FIG_ELLIPSES, frame, regions)
    made = draw_wavelengths_figure(FIG_WAVES)
    print(f"wrote {OUT_JSON.relative_to(ROOT)}, {FIG_ELLIPSES.relative_to(ROOT)}"
          + (f", {FIG_WAVES.relative_to(ROOT)}" if made else " (wavelength figure skipped: smoke sources not set)"))


if __name__ == "__main__":
    main()
