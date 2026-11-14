"""Noise and forward models (beacon/models.py). No test depends on the literal noise values."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from beacon import models
from beacon.twin import Twin

ROOT = Path(__file__).resolve().parents[1]
N = models.DEFAULT_NOISE


@pytest.fixture(scope="module")
def twin():
    return Twin(json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text()))


def test_noise_keys_and_sources():
    contract = {"uwb": ["sigma_los_m", "wall_bias_m", "wall_extra_sigma_m", "body_bias_m", "outlier_prob", "outlier_m",
                        "dropout_prob", "max_range_m", "max_walls", "link_bias_sigma_m"],
                "ble": ["tx_1m_dbm", "tx_spread_db", "n_los", "wall_loss_db", "shadow_sigma_db", "fast_fading_sigma_db",
                        "body_loss_db", "sensitivity_dbm"],
                "bearing": ["sigma_deg", "fov_deg", "available_only_when_pointing"],
                "puck": ["position_sigma_m"]}
    for sec, keys in contract.items():
        for k in keys:
            assert k in N[sec], (sec, k)
    for sec, d in N.items():
        for k in d:
            assert k in models.SOURCES[sec] and models.SOURCES[sec][k], f"no source for {sec}.{k}"
    assert models.BLE_TX_1M_DBM == N["ble"]["tx_1m_dbm"]
    json.dumps(N)                                    # the hello message carries it


def test_uwb_forward_model_walls_body_and_link_floor():
    u = N["uwb"]
    m0, s0 = models.uwb_expected(5.0, 0, N)
    m1, s1 = models.uwb_expected(5.0, 1, N)
    mb, sb = models.uwb_expected(5.0, 0, N, body=True)
    assert m0 == pytest.approx(5.0)
    assert m1 == pytest.approx(5.0 + u["wall_bias_m"]) and s1 > s0
    assert mb == pytest.approx(5.0 + u["body_bias_m"]) and sb >= s0
    # the static per-link bias is never averaged away
    for n in (1, 10, 1000, 10 ** 6):
        s = models.uwb_expected(5.0, 0, N, n_samples=n)[1]
        assert s >= u["link_bias_sigma_m"] - 1e-12
        assert s == pytest.approx(models.uwb_link_sigma(N, n))
    assert models.uwb_expected(5.0, 0, N, n_samples=10 ** 6)[1] == pytest.approx(u["link_bias_sigma_m"], rel=1e-3)
    assert models.uwb_expected(5.0, 0, N, n_samples=10)[1] < models.uwb_expected(5.0, 0, N, n_samples=1)[1]
    # unknown body: between none and blocked
    mu, _ = models.uwb_expected(5.0, 0, N, body=None)
    assert m0 <= mu <= mb
    # vectorised
    m, s = models.uwb_expected(np.array([1.0, 2.0, 3.0]), np.array([0, 1, 0]), N)
    assert m.shape == (3,) and s.shape == (3,) and s[1] > s[0]


def test_ble_forward_and_inverse():
    b = N["ble"]
    for d in (0.5, 1.0, 3.0, 8.0):
        rssi, sig = models.ble_expected_rssi(d, 0, N)
        assert sig > 0
        dd, lo, hi = models.ble_distance(rssi, N)
        assert dd == pytest.approx(d, rel=1e-9)
        assert lo < dd < hi
        assert hi / dd == pytest.approx(dd / lo, rel=1e-9)          # log-normal: symmetric in log, asymmetric in metres
    assert models.ble_expected_rssi(1.0, 0, N)[0] == pytest.approx(b["tx_1m_dbm"])
    assert models.ble_expected_rssi(3.0, 1, N)[0] == pytest.approx(models.ble_expected_rssi(3.0, 0, N)[0] - b["wall_loss_db"])
    assert models.ble_expected_rssi(3.0, 0, N, n_samples=20)[1] < models.ble_expected_rssi(3.0, 0, N, n_samples=1)[1]
    assert models.ble_expected_rssi(3.0, 0, N, tx_dbm=-50.0)[0] > models.ble_expected_rssi(3.0, 0, N)[0]
    assert models.ble_max_distance(N) > 1.0


def test_cold_band_thresholds():
    near, mid = models.COLD_BANDS_DBM["near"], models.COLD_BANDS_DBM["mid"]
    assert models.cold_band(near + 5) == "near"
    assert models.cold_band(near) == "near"
    assert models.cold_band(near - 0.1) == "mid"
    assert models.cold_band(mid) == "mid"
    assert models.cold_band(mid - 0.1) == "far"


def _compass(twin, frm, to):
    lat0, lon0 = twin.to_geo(*frm)
    lat1, lon1 = twin.to_geo(*to)
    north = (lat1 - lat0) * 111320.0
    east = (lon1 - lon0) * 111320.0 * math.cos(math.radians(twin.data["georef"]["origin"]["lat"]))
    return math.degrees(math.atan2(east, north)) % 360.0


def test_compass_to_bldg_round_trip_against_twin_geo(twin):
    b = twin.data["georef"]["bearing_deg"]
    rng = np.random.default_rng(3)
    for _ in range(50):
        phone = rng.uniform(0, 30, 2)
        puck = rng.uniform(0, 30, 2)
        if math.dist(phone, puck) < 0.5:
            continue
        az = _compass(twin, phone, puck)                  # what the phone reports: compass azimuth phone -> puck
        got = models.compass_to_bldg_deg(az, b)
        want = math.degrees(math.atan2(phone[1] - puck[1], phone[0] - puck[0])) % 360.0
        diff = abs((got - want + 180) % 360 - 180)
        assert diff < 0.05, (phone, puck, got, want)
        assert 0.0 <= got < 360.0
        assert models.bldg_to_compass_deg(got, b) == pytest.approx(az, abs=1e-6)


def test_compass_convention_simple_cases():
    # building +x points east (b = 90): a puck due east of the phone sits at +x, so the phone is at 180 from the puck
    assert models.compass_to_bldg_deg(90.0, 90.0) == pytest.approx(180.0)
    # puck due north of the phone: north is +y when b = 90, so the phone is at 270 (-y) from the puck
    assert models.compass_to_bldg_deg(0.0, 90.0) == pytest.approx(270.0)
