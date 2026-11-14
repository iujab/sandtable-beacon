"""Noise models and forward models shared by the simulator and the locator.

THE stated noise model is `DEFAULT_NOISE`. It is also sent in the server's `hello` and quoted next to
every simulated number. Every constant of the noise model lives in that dict: the simulator
(`beacon/sim.py`) and the locator (`beacon/locate.py`) read it from there, so one edit changes both.
`SOURCES` maps every key to where its value comes from (a cited paper or page, or "assumption").

Forward models (all accept numpy arrays as well as floats):

- `uwb_expected(d, n_walls, noise)`        mean and 1-sigma of a UWB two-way-ranging distance
- `ble_expected_rssi(d, n_walls, noise)`   mean and 1-sigma of a Bluetooth RSSI (log-distance path loss)
- `ble_distance(rssi, noise)`              inverse of the path-loss model, with its asymmetric 1-sigma band
- `cold_band(rssi)`                        near / mid / far signal-strength band of a cold advertiser
- `compass_to_bldg_deg(az, georef_b)`      phone's compass bearing to a puck -> bearing of the phone seen from the puck
"""
from __future__ import annotations

import copy
import math

import numpy as np

__all__ = ["DEFAULT_NOISE", "SOURCES", "COLD_BANDS_DBM", "BLE_TX_1M_DBM", "noise_or_default", "uwb_expected", "uwb_link_sigma",
           "ble_expected_rssi", "ble_distance", "ble_max_distance", "cold_band", "compass_to_bldg_deg", "bldg_to_compass_deg"]

DEFAULT_NOISE: dict = {
    "uwb": {"sigma_los_m": 0.10, "link_bias_sigma_m": 0.10, "wall_bias_m": 0.30, "wall_extra_sigma_m": 0.35,
            "body_bias_m": 0.30, "body_extra_sigma_m": 0.30, "body_block_prob": 0.25,
            "outlier_prob": 0.02, "outlier_m": 2.0, "dropout_prob": 0.05, "max_range_m": 15.0, "max_walls": 1},
    "ble": {"tx_1m_dbm": -62.0, "tx_spread_db": 4.5, "n_los": 2.5, "wall_loss_db": 5.0, "shadow_sigma_db": 6.0,
            "fast_fading_sigma_db": 4.0, "body_loss_db": 11.5, "sensitivity_dbm": -95.0},
    "bearing": {"sigma_deg": 25.0, "fov_deg": 120.0, "available_only_when_pointing": True},
    "puck": {"position_sigma_m": 0.08},
}

_AR5IV_PHONE_DW3000 = "https://ar5iv.labs.arxiv.org/html/2303.11220"
_UWB_NLOS = "https://nsl.cs.uh.edu/papers/uwbnlos-cpsbench18.pdf"
_BODY = "https://arxiv.org/pdf/2101.09075"
_NIST = "https://nvlpubs.nist.gov/nistpubs/ir/2022/NIST.IR.8437.pdf"

SOURCES: dict = {
    "uwb": {
        "sigma_los_m": _AR5IV_PHONE_DW3000 + " (white per-sample spread of phone-to-DW3000 ranging)",
        "link_bias_sigma_m": _AR5IV_PHONE_DW3000 + " (15 to 18 cm uncalibrated RMSE, a large part a constant per-pair offset; "
                             "the DW3000 datasheet's +/-15 cm uncalibrated accuracy and 5 to 25 cm orientation bias)",
        "wall_bias_m": "https://arxiv.org/html/2303.11220v1 (related work: DW3000 non-line-of-sight mean error 34 cm)",
        "wall_extra_sigma_m": "https://arxiv.org/html/2303.11220v1 (related work: DW3000 non-line-of-sight error SD 35 cm)",
        "body_bias_m": _BODY + " (body-worn UWB RMSE about 0.9 m)",
        "body_extra_sigma_m": _BODY,
        "body_block_prob": "assumption (share of links blocked by the holder's body)",
        "outlier_prob": _AR5IV_PHONE_DW3000 + " (outliers to +2.8 m)",
        "outlier_m": _AR5IV_PHONE_DW3000,
        "dropout_prob": "assumption",
        "max_range_m": "https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session "
                       "(accessory sessions work best within 9 m)",
        "max_walls": "assumption (consistent with " + _UWB_NLOS + ")",
    },
    "ble": {
        "tx_1m_dbm": "https://arxiv.org/pdf/2006.06822",
        "tx_spread_db": _BODY + " (per-model spread)",
        "n_los": "ITU-R P.1238 indoor path-loss exponent 2.2 to 3.0 (walls modelled separately)",
        "wall_loss_db": "https://blog.ibwave.com/?p=3095 (drywall partition 5.4 dB)",
        "shadow_sigma_db": _NIST + " (5 dB clean line of sight, 8 to 11 dB mixed)",
        "fast_fading_sigma_db": "assumption",
        "body_loss_db": _NIST,
        "sensitivity_dbm": "assumption (typical BLE receiver)",
    },
    "bearing": {
        "sigma_deg": "https://sinrgwebpage.web.illinois.edu/papers/walkcompass.pdf (indoor compass error 20 to 26 degrees)",
        "fov_deg": "assumption",
        "available_only_when_pointing": "assumption",
    },
    "puck": {"position_sigma_m": "assumption"},
}

# Cold-scan signal-strength bands (BEACON_FEED 2.3, CONTRACTS section 2): near >= -60 dBm, mid -60..-75 dBm, far < -75 dBm.
# These are band DEFINITIONS (what "near" means in the feed), not noise constants.
COLD_BANDS_DBM: dict = {"near": -60.0, "mid": -75.0}

# Default calibrated signal strength of the opt-in app at 1 m. Alias of DEFAULT_NOISE["ble"]["tx_1m_dbm"]; code paths
# that receive a `noise` dict read it from there instead.
BLE_TX_1M_DBM: float = DEFAULT_NOISE["ble"]["tx_1m_dbm"]

_D_MIN_M = 0.1          # path-loss models are not defined at zero distance; closer than 10 cm counts as 10 cm


def noise_or_default(noise: dict | None) -> dict:
    """The full noise dict (DEFAULT_NOISE when None). Missing sections are filled from DEFAULT_NOISE."""
    if noise is None:
        return DEFAULT_NOISE
    if all(k in noise for k in DEFAULT_NOISE):
        return noise
    out = copy.deepcopy(DEFAULT_NOISE)
    for k, v in noise.items():
        if isinstance(v, dict) and k in out:
            out[k].update(v)
        else:
            out[k] = v
    return out


def _section(noise: dict | None, name: str) -> dict:
    """Accept the full noise dict or just its section."""
    noise = DEFAULT_NOISE if noise is None else noise
    return noise[name] if name in noise else noise


def uwb_link_sigma(noise: dict | None = None, n_samples: int = 1) -> float:
    """1-sigma of a UWB range in free line of sight after averaging n_samples:
    sqrt(link_bias_sigma_m^2 + sigma_los_m^2 / max(n, 1)). The static per-link bias never averages out."""
    u = _section(noise, "uwb")
    return math.sqrt(u.get("link_bias_sigma_m", 0.0) ** 2 + u["sigma_los_m"] ** 2 / max(1, int(n_samples)))


def uwb_expected(d_true_m, n_walls, noise: dict | None = None, body=False, n_samples: int = 1):
    """Forward model of a UWB two-way-ranging distance (one sample, or the average/median of n_samples).

    mean  = d_true + n_walls * wall_bias_m (+ body_bias_m when the holder's body blocks the link)
    sigma = sqrt(link_bias_sigma_m^2 + sigma_los_m^2 / max(n_samples, 1) + n_walls * wall_extra_sigma_m^2
                 (+ body_extra_sigma_m^2 when blocked))

    The link bias (antenna delay and orientation) is static per puck-phone link: the simulator draws it once per link
    and averaging does not remove it, so the sigma never shrinks below link_bias_sigma_m. Wall and body terms are also
    treated as static (not divided by n).
    body: False (no body in the path; the default), True (blocked) or None (unknown: the moment-matched mixture with
    probability body_block_prob; the locator itself uses the exact two-component mixture).
    Outliers (outlier_prob, positive, scale outlier_m) and dropouts are not part of the mean and sigma; the simulator
    draws them and the locator's likelihood carries them as a mixture component.
    Works on floats and numpy arrays. Returns (mean_m, sigma_m).
    """
    u = _section(noise, "uwb")
    d = np.asarray(d_true_m, dtype=float)
    w = np.asarray(n_walls, dtype=float)
    mean = d + w * u["wall_bias_m"]
    var = (u.get("link_bias_sigma_m", 0.0) ** 2 + u["sigma_los_m"] ** 2 / max(1, int(n_samples))
           + w * u["wall_extra_sigma_m"] ** 2)
    bb, bs = u["body_bias_m"], u.get("body_extra_sigma_m", 0.0)
    if body is None:
        p = u.get("body_block_prob", 0.5)
        mean = mean + p * bb
        var = var + p * bs ** 2 + p * (1.0 - p) * bb ** 2
    elif body:
        mean = mean + bb
        var = var + bs ** 2
    sigma = np.sqrt(var)
    if np.ndim(mean) == 0:
        return float(mean), float(sigma)
    return mean, np.broadcast_to(sigma, np.shape(mean))


def ble_expected_rssi(d_true_m, n_walls, noise: dict | None = None, tx_dbm: float | None = None, n_samples: int = 1, body=False):
    """Forward model of the Bluetooth RSSI of the SOS advertisement at a puck (log-distance path loss).

    mean  = tx_1m - 10 * n_los * log10(max(d, 0.1 m)) - n_walls * wall_loss_db (- body_loss_db when a body blocks it)
    sigma = sqrt(shadow_sigma_db^2 + fast_fading_sigma_db^2 / n_samples + tx_spread_db^2)

    tx_dbm: the app's calibrated strength at 1 m (None: noise["ble"]["tx_1m_dbm"]); tx_spread_db stays in sigma either way
    (per-phone transmit and antenna differences). n_samples: averaged samples (fast fading averages out, shadowing does
    not). body: False / True / None (unknown: moment-matched mixture with probability uwb.body_block_prob when the
    full noise dict is given, else 0.5).
    Works on floats and numpy arrays. Returns (mean_dbm, sigma_db) for ONE averaged reading.
    """
    b = _section(noise, "ble")
    tx = b["tx_1m_dbm"] if tx_dbm is None else float(tx_dbm)
    d = np.maximum(np.asarray(d_true_m, dtype=float), _D_MIN_M)
    w = np.asarray(n_walls, dtype=float)
    mean = tx - 10.0 * b["n_los"] * np.log10(d) - w * b["wall_loss_db"]
    var = b["shadow_sigma_db"] ** 2 + b["fast_fading_sigma_db"] ** 2 / max(1, int(n_samples)) + b["tx_spread_db"] ** 2
    if body is None:
        p = (noise or DEFAULT_NOISE).get("uwb", {}).get("body_block_prob", 0.5) if isinstance(noise or DEFAULT_NOISE, dict) else 0.5
        mean = mean - p * b["body_loss_db"]
        var = var + p * (1.0 - p) * b["body_loss_db"] ** 2
    elif body:
        mean = mean - b["body_loss_db"]
    sigma = math.sqrt(var)
    if np.ndim(mean) == 0:
        return float(mean), float(sigma)
    return mean, np.full(np.shape(mean), sigma)


def ble_distance(rssi_dbm: float, noise: dict | None = None, tx_dbm: float | None = None, n_samples: int = 1) -> tuple[float, float, float]:
    """Invert the line-of-sight path-loss model: (d_m, d_lo_m, d_hi_m).

    d = 10 ** ((tx - rssi) / (10 n_los)); the 1-sigma band is log-normal, so it is asymmetric:
    d_lo = d * 10 ** (-sigma / (10 n_los)), d_hi = d * 10 ** (+sigma / (10 n_los)) with sigma from `ble_expected_rssi`.
    Walls are not inverted (unknown here): a reading through a wall reads farther than it is.
    """
    b = _section(noise, "ble")
    tx = b["tx_1m_dbm"] if tx_dbm is None else float(tx_dbm)
    _, sigma = ble_expected_rssi(1.0, 0, {"ble": b}, tx_dbm=tx, n_samples=n_samples)
    k = 10.0 * b["n_los"]
    d = 10.0 ** ((tx - float(rssi_dbm)) / k)
    return d, d * 10.0 ** (-sigma / k), d * 10.0 ** (sigma / k)


def ble_max_distance(noise: dict | None = None, tx_dbm: float | None = None) -> float:
    """Line-of-sight distance at which the mean RSSI reaches the receiver sensitivity."""
    b = _section(noise, "ble")
    tx = b["tx_1m_dbm"] if tx_dbm is None else float(tx_dbm)
    return 10.0 ** ((tx - b["sensitivity_dbm"]) / (10.0 * b["n_los"]))


def cold_band(rssi_dbm: float) -> str:
    """'near' (>= -60 dBm), 'mid' (-60 to -75 dBm) or 'far' (< -75 dBm): COLD_BANDS_DBM."""
    r = float(rssi_dbm)
    if r >= COLD_BANDS_DBM["near"]:
        return "near"
    if r >= COLD_BANDS_DBM["mid"]:
        return "mid"
    return "far"


def compass_to_bldg_deg(to_puck_compass_deg: float, georef_bearing_deg: float) -> float:
    """Phone's compass azimuth to the puck -> BEACON_FEED `from_puck_deg`.

    Georef convention (Twin.to_geo): the twin's georef.bearing_deg b is the compass bearing (clockwise from north) of
    the building +x axis; east = x sin b - y cos b, north = x cos b + y sin b. A direction with compass azimuth c has
    (east, north) = (sin c, cos c), which in the building frame points at the angle (b - c) counter-clockwise from +x.
    The observation gives the direction FROM the phone TO the puck; the feed wants the bearing of the phone seen FROM the
    puck, the opposite direction: (b - c + 180) mod 360.
    """
    return float((float(georef_bearing_deg) - float(to_puck_compass_deg) + 180.0) % 360.0)


def bldg_to_compass_deg(from_puck_deg: float, georef_bearing_deg: float) -> float:
    """Inverse of `compass_to_bldg_deg` (for the simulator): from_puck_deg -> compass azimuth from the phone to the puck."""
    return float((float(georef_bearing_deg) - float(from_puck_deg) + 180.0) % 360.0)
