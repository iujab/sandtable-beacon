"""SIMULATED world for Sandtable Beacon: thrown pucks, victims' phones and other Bluetooth devices on the twin's floor.

Everything this module produces is SIMULATED. It stands in for the hardware (UWB/Bluetooth pucks, the occupant app on
real phones) so the session, the board and the evaluation can run on a machine with no radios. Nothing here is a
measurement, and nothing here ever scans, ranges or messages a real device.

    from beacon.sim import World
    w = World(twin, seed=310)              # twin: beacon.twin.Twin or the twin dict; noise defaults to models.DEFAULT_NOISE
    w.throw_pucks("r310", n=3)             # pucks land 1.5-5.5 m inside the room's door (like Sandtable's Scout pucks)
    w.upsert_phone("victim", 2.1, 1.8, report={"status": "trapped", "people": 2})
    obs = w.step(now)                      # observations of docs/CONTRACTS.md section 2, since the previous step
    w.truth()                              # ground truth, for evaluation only (never for a board)

Noise model (stated; every constant below that is not a sim-only constant comes from `beacon.models`)
----------------------------------------------------------------------------------------------------------
The radio constants live in ONE place, `models.DEFAULT_NOISE` (or the `noise` dict passed to `World`); the locator
reads the same dict, so one edit changes the world and the locator together. The forward models are the locator's
own: `models.uwb_expected`, `models.ble_expected_rssi`, `models.cold_band`. Walls are counted with
`Twin.walls_crossed` on the straight segment between the TRUE puck and the TRUE device position (door openings do
not count).

* Pucks. Thrown (any n, "scatter"): true position = where the puck landed, and `w.pucks` is what the pucks BELIEVE
  (what Scout's self-location would report): truth + N(0, puck.position_sigma_m) per axis, drawn once per throw.
  Two pucks placed by hand (n == 2, default "door-wall", `beacon.layout.recommend`): believed = the recommended
  spots, truth = those + N(0, 0.03 m) per axis.
* Links exist only between a puck and a device on the same floor; a device outside the floor outline has no link
  (slabs and the exterior wall are not modelled: treated as out of reach).
* UWB two-way ranging (opt-in phone with a UWB chip): no link beyond `uwb.max_range_m` or through more than
  `uwb.max_walls` walls, and none unless the pair's Bluetooth link is up (the UWB session is set up over Bluetooth).
  Per link (puck-phone pair), drawn ONCE per phone position and orientation (redrawn when the phone moves more than
  0.5 m or turns: pointing toggled or heading changed; or when the puck moves): whether the holder's body blocks the
  path (probability uwb.body_block_prob) and a static error N(0, s_static) (link bias uwb.link_bias_sigma_m: antenna
  delay and orientation, plus the wall and body spread; averaging never removes it). Each sample: with probability
  `uwb.dropout_prob` nothing; else d = mean + static + N(0, s_white), with mean and the two sigmas from
  models.uwb_expected(d_true, n_walls, noise, body=blocked): s_static = its sigma for n_samples -> infinity,
  s_white = sqrt(sigma(n=1)^2 - s_static^2) (= uwb.sigma_los_m), mean = truth + wall bias per wall (+ body bias
  when blocked). With probability
  `uwb.outlier_prob` a positive outlier of `uwb.outlier_m` * U(0.25, 1.0) is added (up to outlier_m).
  `los` is high on a wall-free link (N(0.88, 0.06)) and low through walls (N(0.32, 0.12) through one wall,
  N(0.18, 0.12) through two), 0.30 lower when a body blocks, clipped to 0..1: a noisy hint, not a truth.
  `rssi`/`fp` follow a plausible DW3000-like shape (rssi = -62 dBm - 20 log10 d - 5 dB per wall + N(0, 1.5);
  fp = rssi - 2 dB on a clear link, - 9 dB per wall otherwise, - 6 dB more when a body blocks, + N(0, 1)).
* Bluetooth SOS beacon (every opt-in phone): RSSI = models.ble_expected_rssi(d_true, n_walls, noise, body=blocked)[0]
  (log-distance path loss from `ble.tx_1m_dbm` + `ble.wall_loss_db` per wall + `ble.body_loss_db` when the same
  per-link body block as UWB applies) + a per-phone transmit offset
  N(0, ble.tx_spread_db) (drawn once per phone; the app advertises the NOMINAL `tx_dbm`, so the locator does not
  know the offset) + slow shadowing N(0, ble.shadow_sigma_db) (drawn once per puck-device pair and position:
  redrawn when the device moves more than 0.5 m from where it was last drawn or turns, or the puck moves) + fast fading
  N(0, ble.fast_fading_sigma_db) per sample. Nothing below `ble.sensitivity_dbm` is heard.
* Body effects: only through the forward models' `body=` argument (the per-link body block above); nothing extra.
  Cold devices (wearables, laptops, phones with no app) are never body-blocked in this model.
* Bearing (the phone's UWB direction + compass, computed on the phone): U1 phones (iPhone 14: Nearby Interaction
  direction, source "ni-direction+compass") only while the user points the phone (`pointing=True`); U2 phones
  (iPhone 15: no NI direction for accessories, camera-assisted angle, source "camera-assist+compass") only while
  pointing AND `camera_assist`, with sigma 1.3 x bearing.sigma_deg. Only on a wall-free UWB link, and only for the
  puck closest to where the phone points,
  if it is inside +-bearing.fov_deg/2 of the pointing direction. The pointing direction is the true direction to the
  nearest visible puck + N(0, 20 deg) aim error (drawn once per pointing session) unless `heading_deg` is given.
  Measured direction = true direction phone->puck + N(0, sigma), sent as `to_puck_compass_deg` (clockwise from
  true north) through the twin's georeference with `models.bldg_to_compass_deg`, the inverse of
  `models.compass_to_bldg_deg` (round trip tested, and checked against Twin.to_geo).
* Noise keys announced by the lead but possibly not yet in models.DEFAULT_NOISE (`uwb.link_bias_sigma_m`,
  `uwb.body_block_prob`): read from the dict; only when a key is missing the world falls back to 0.05 m / 0.0 and
  lists it in `w.noise_fallbacks` (TODO: drop the fallbacks once models carries the keys).
* Cold count (every puck, every 5 s, window 10 s): every ADVERTISING device on the floor (opt-in phones too: their
  SOS beacon counts) is heard if its window RSSI (mean + transmit offset + shadowing + N(0, fast/sqrt(3)): about 3
  adverts averaged) is above the sensitivity AND the scan caught it (probability 0.95). Its band is
  models.cold_band(rssi). Over-count: a heard device is counted twice in a window with probability
  1 - (1 - window_s / 900 s) * (1 - p_multi): address rotation (RPA timeout about 15 minutes on Android and iOS,
  docs/research/android_ble_models.md section 7) plus several simultaneous advertising sets (p_multi: phone 0.08,
  wearable 0.04, laptop 0.04; ASSUMPTION). Under-count: 40 % of phones with no app do not advertise at all (idle
  phones do not reliably advertise, research section 7; the 40 % is an ASSUMPTION), 20 % of laptops neither;
  wearables always advertise. Classes and transmit power at 1 m: phone = ble.tx_1m_dbm, wearable = ble.tx_1m_dbm -
  6 dB, laptop = ble.tx_1m_dbm, each + N(0, ble.tx_spread_db). Counts only: no address, no id, ever.
* Reports: on change and every 5 s, over the phone's encrypted Bluetooth connection, so only while some puck hears
  the phone (mean link RSSI above the sensitivity); a phone whose user pressed "I am out" (`safe_outside`) also
  reaches the session over the app's internet uplink, from anywhere. Barometer: 1002.0 hPa at ground level
  (weather, ASSUMPTION) - 0.12 hPa per metre above it (research section 8) at floor elevation + 1 m, + a per-phone
  offset N(0, 0.5) hPa clipped to +-1.4 hPa, + N(0, 0.03) hPa per report. Battery drains 1 % per 10 minutes.
* Rates (with +-10 % jitter per tick): uwb 5 Hz per puck-phone pair in range, sos 2 Hz per puck in Bluetooth range,
  bearing 2 Hz while pointing, report on change and every 5 s, cold every 5 s per puck. A step after a long gap
  only catches up the last 2 s (ticks older than that are skipped, as a radio buffer would drop them).
* Ephemeral ids: 8 hex digits, rotated every `EID_ROTATE_S` (600 s; first rotation at a random phase); the report
  right after a rotation (sent at once) and every report until the next rotation carry `prev_eid`.

Determinism: all randomness comes from `numpy.random.default_rng` streams derived from the world seed (pucks,
per-device attributes, shadowing and ids each have their own keyed stream; measurement noise has one stream consumed
in a fixed order). No global RNG, no wall clock: time is only the `now` passed to `step`. Same seed + same calls =
same observations.

Scenario schema (`fixtures/beacon/scenarios/*.json`, loaded with `w.load_scenario(name | path | dict)`)
------------------------------------------------------------------------------------------------------
    {"schema": "sandtable.beacon.scenario/1", "name": "suite-310", "simulated": true, "title": "...", "description": "...",
     "floor": "f3",                 # optional, default the twin's exercise floor
     "room": "r310",                # the fire room (where the pucks go); must be a room of the twin
     "seed": 310,                   # optional: the world is reseeded with it on load (same scene every time)
     "pucks": {"throw": {"room": "r310", "n": 2, "seed": 310, "strategy"?: "door-wall" | "exterior-wall" | "careless" | "scatter"}}
                                    # n == 2 defaults to the door-wall layout (beacon.layout), other n are thrown; or explicitly:
              | [{"id": "P1", "x": 7.9, "y": 6.7, "sigma_m"?: 0.08}],   # true positions; believed = truth + noise
     "phones": [{"key": "trapped-310", "x": 2.1, "y": 1.8, "floor"?: "f3",
                 "room"?: "r310" | null,     # declared room, CHECKED against the twin on load (ValueError if wrong)
                 "outside"?: true,           # declared outside the floor outline, checked on load
                 "opt_in": true,             # false: a phone with no app (cold count only)
                 "uwb": true, "chip"?: "U1" | "U2", "model"?: "iPhone 14", "pointing": false,
                 "camera_assist"?: false, "heading_deg"?: 210.0,
                 "advertises"?: true,        # no-app phones only: override the drawn "advertises at all"
                 "tx_offset_db"?: 0.0,       # override the drawn transmit offset
                 "report"?: {"status", "text", "people", "can_move", "battery_pct", "floor_reported",
                             "home_suite", "platform", "safe_outside"}}],
     "cold_devices": [{"key"?: "watch-310", "class": "wearable" | "phone" | "laptop", "x", "y", "room"?, "advertises"?}
                      | {"room": "r309", "n": 2, "class"?: "laptop"}]}      # n devices placed at random in the room
"""
from __future__ import annotations

import copy
import json
import math
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from . import models
from .twin import Twin, _pip, _poly_dist

__all__ = ["World", "SIM_MODEL", "EID_ROTATE_S", "SCENARIO_DIR", "to_puck_compass_deg", "georef_bearing_deg",
           "list_scenarios", "load_scenario_file", "scaled_noise"]

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "beacon" / "scenarios"
EID_ROTATE_S = 600.0

# Sim-only constants (the radio constants are in models.DEFAULT_NOISE). Stated in the module docstring.
SIM_MODEL: dict[str, Any] = {
    "simulated": True,
    "schedule": {"uwb_hz": 5.0, "sos_hz": 2.0, "bearing_hz": 2.0, "report_every_s": 5.0, "cold_every_s": 5.0,
                 "cold_window_s": 10.0, "jitter_frac": 0.10, "max_catchup_s": 2.0},
    "eid_rotate_s": EID_ROTATE_S,
    "ble_shadow_redraw_m": 0.5,
    "links": "same floor only; a device outside the floor outline has no link",
    "uwb_link": {"needs_ble_link": True, "outlier_scale": [0.25, 1.0], "los_body_drop": 0.30, "fp_gap_body_db": 6.0,
                 "los_clear_mean": 0.88, "los_clear_sd": 0.06, "los_wall_mean": [0.32, 0.18], "los_wall_sd": 0.12,
                 "rssi_1m_dbm": -62.0, "rssi_wall_db": 5.0, "rssi_sd_db": 1.5,
                 "fp_gap_clear_db": 2.0, "fp_gap_wall_db": 9.0, "fp_sd_db": 1.0},
    "bearing": {"needs_wall_free_link": True, "aim_sd_deg": 20.0, "one_puck_at_a_time": True},
    "cold": {"window_s": 10.0, "samples_per_window": 3, "scan_hit_prob": 0.95, "addr_rotate_s": 900.0,
             "classes": {"phone": {"tx_offset_db": 0.0, "silent_frac": 0.40, "p_multi": 0.08},
                         "wearable": {"tx_offset_db": -6.0, "silent_frac": 0.0, "p_multi": 0.04},
                         "laptop": {"tx_offset_db": 0.0, "silent_frac": 0.20, "p_multi": 0.04}},
             "random_class_mix": {"phone": 0.5, "wearable": 0.35, "laptop": 0.15},
             "sources": "address rotation about 15 min: docs/research/android_ble_models.md section 7; "
                        "silent fractions and p_multi are assumptions"},
    "report": {"every_s": 5.0, "needs_ble_link": True, "safe_outside_uplink": True,
               "baro_ground_hpa": 1002.0, "baro_hpa_per_m": 0.12, "baro_phone_height_m": 1.0,
               "baro_offset_sd_hpa": 0.5, "baro_offset_max_hpa": 1.4, "baro_noise_hpa": 0.03,
               "battery_drain_pct_per_s": 1.0 / 600.0, "still_after_s": 5.0, "text_max": 120},
    "throw": {"stages": [[1.5, 5.5, 1.2, 0.5, 75], [1.0, 5.5, 1.0, 0.4, 90], [0.8, 6.5, 0.8, 0.3, 120],
                         [0.5, 8.0, 0.6, 0.25, 180]],
              "stage_fields": ["r_min_m", "r_max_m", "min_separation_m", "wall_clearance_m", "half_angle_deg"],
              "collinear_ratio": 0.18, "credit": "throw model copied from Sandtable's Scout (scout/scene.py)",
              "layout_truth_sd_m": 0.03,
              "two_puck_default": "n == 2: beacon.layout.recommend(strategy='door-wall'); truth = recommended spot + N(0, 0.03 m)"},
}

# Phones with UWB (the owner's hardware: iPhones ranging Qorvo DWM3001CDK pucks through Nearby Interaction).
CHIPS: dict[str, dict] = {
    "U1": {"model": "iPhone 14", "bearing": "pointing", "source": "ni-direction+compass", "sigma_factor": 1.0},
    "U2": {"model": "iPhone 15", "bearing": "pointing+camera_assist", "source": "camera-assist+compass", "sigma_factor": 1.3},
}
NO_UWB_MODEL = "iPhone SE (no UWB)"
SIM_MODEL["chips"] = copy.deepcopy(CHIPS)

# Noise keys the lead announced that may not be in models.DEFAULT_NOISE yet: fallback values used ONLY when the key is
# missing (TODO: remove once models.DEFAULT_NOISE carries them); the world records which fallbacks it used.
_NOISE_FALLBACKS = {("uwb", "link_bias_sigma_m"): 0.05, ("uwb", "body_block_prob"): 0.0}

_KIND_ORDER = {"report": 0, "bearing": 1, "uwb": 2, "sos": 3, "cold": 4}
_STREAM = {"puck": 1, "device": 2, "measure": 3, "shadow": 4, "cold_place": 5, "eid": 6, "puck_belief": 7, "aim": 8,
           "link": 9}


def _h(s: str) -> int:
    return zlib.crc32(s.encode("utf-8"))


def _r(v: float, nd: int = 2) -> float:
    return float(round(float(v), nd))


def georef_bearing_deg(twin: Twin) -> float:
    g = twin.data.get("georef") or {"bearing_deg": 90.0}
    return float(g.get("bearing_deg", 90.0))


def to_puck_compass_deg(theta_to_puck_bldg_deg: float, georef_bearing_deg_: float) -> float:
    """Direction phone -> puck in the building frame (deg CCW from +x) -> the phone's compass azimuth to the puck
    (deg clockwise from true north), through `models.bldg_to_compass_deg` (which takes the puck -> phone direction)."""
    return models.bldg_to_compass_deg((theta_to_puck_bldg_deg + 180.0) % 360.0, georef_bearing_deg_)


def scaled_noise(noise: dict, factor: float) -> dict:
    """The noise dict with every spread, bias, loss and outlier/dropout/body-block probability scaled by `factor`
    (probabilities capped at 0.5; model-mismatch arm of the evaluation). Ranges, sensitivity, path-loss exponent and
    the field of view are kept."""
    n = copy.deepcopy(noise)
    scale = {"uwb": ("sigma_los_m", "link_bias_sigma_m", "wall_bias_m", "wall_extra_sigma_m", "body_bias_m",
                     "body_extra_sigma_m", "body_block_prob", "outlier_prob", "outlier_m", "dropout_prob"),
             "ble": ("tx_spread_db", "wall_loss_db", "shadow_sigma_db", "fast_fading_sigma_db", "body_loss_db"),
             "bearing": ("sigma_deg",),
             "puck": ("position_sigma_m",)}
    for grp, keys in scale.items():
        for k in keys:
            if grp in n and isinstance(n[grp].get(k), (int, float)) and not isinstance(n[grp].get(k), bool):
                n[grp][k] = n[grp][k] * factor
    for k in ("outlier_prob", "dropout_prob", "body_block_prob"):
        if "uwb" in n and k in n["uwb"]:
            n["uwb"][k] = min(0.5, n["uwb"][k])
    return n


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.json"))


def load_scenario_file(ref) -> dict:
    """A scenario dict from a dict, a path, or a bare name of fixtures/beacon/scenarios/."""
    if isinstance(ref, dict):
        return ref
    p = Path(ref)
    if not p.suffix and not p.exists():
        p = SCENARIO_DIR / f"{ref}.json"
    if not p.exists():
        raise ValueError(f"unknown scenario {ref!r} (known: {', '.join(list_scenarios())})")
    return json.loads(p.read_text(encoding="utf-8"))


# --- small geometry (copied in spirit from Scout's scene.py, credit: Sandtable) --------------------------------------

def _door_of(tw: Twin, room: dict) -> list[float]:
    """The room's door (prefer one onto a corridor), else the middle of its longest wall."""
    doors = [d for d in tw.data.get("doors", []) if room["id"] in d.get("rooms", [])]
    if doors:
        def key(d):
            other = [r for r in d["rooms"] if r != room["id"]]
            kind = tw._rooms.get(other[0], {}).get("kind") if other else None
            return (kind != "corridor", d["id"])
        d = sorted(doors, key=key)[0]
        return [float(d["at"][0]), float(d["at"][1])]
    poly = room["polygon"]
    n = len(poly)
    i = max(range(n), key=lambda k: math.dist(poly[k], poly[(k + 1) % n]))
    a, b = poly[i], poly[(i + 1) % n]
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]


def _inward(door, poly) -> tuple[float, float]:
    """Unit vector from the door into the room: normal of the nearest wall, pointing inside."""
    best, nrm = 1e9, (1.0, 0.0)
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        d = _poly_dist(door, [a, b])[0]
        if d < best:
            ex, ey = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(ex, ey) or 1e-9
            cand = (-ey / ln, ex / ln)
            if not _pip((door[0] + 0.3 * cand[0], door[1] + 0.3 * cand[1]), poly):
                cand = (-cand[0], -cand[1])
            best, nrm = d, cand
    return nrm


def _collinear(pts, ratio: float) -> bool:
    if len(pts) < 3:
        return False
    P = np.asarray(pts, float)
    P = P - P.mean(axis=0)
    s = np.linalg.svd(P, compute_uv=False)
    return bool(s[1] < ratio * s[0])


def _random_point_in(rng, poly, clearance: float) -> list[float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    for cl in (clearance, clearance / 2, 0.05):
        for _ in range(2000):
            p = (float(rng.uniform(min(xs), max(xs))), float(rng.uniform(min(ys), max(ys))))
            if _pip(p, poly) and _poly_dist(p, poly)[0] >= cl:
                return [p[0], p[1]]
    raise ValueError("no free floor in the room")


# --- the world ---------------------------------------------------------------------------------------------------------

class _Device:
    __slots__ = ("key", "cls", "x", "y", "floor", "opt_in", "uwb", "pointing", "heading_deg", "aim", "tx_offset_db",
                 "advertises", "report", "eid", "prev_eid", "prev2_eid", "eid_since", "eid_index", "version", "shadow_epoch",
                 "shadow_anchor", "last_moved", "baro_offset", "battery0", "battery_t0", "report_dirty", "outside",
                 "room", "explicit_heading", "chip", "camera_assist", "model")

    def public(self) -> dict:
        return {"key": self.key, "class": self.cls, "eid": self.eid, "prev_eid": self.prev_eid, "x": _r(self.x, 3),
                "y": _r(self.y, 3), "floor": self.floor, "room": self.room, "outside": self.outside,
                "opt_in": self.opt_in, "uwb": self.uwb, "chip": self.chip, "model": self.model,
                "pointing": self.pointing, "camera_assist": self.camera_assist, "advertises": self.advertises,
                "report": dict(self.report) if self.report is not None else None, "simulated": True}


class World:
    """The simulated world. See the module docstring for the stated noise model and the scenario schema."""

    def __init__(self, twin, seed: int = 310, noise: dict | None = None, eid_rotate_s: float | None = None):
        self.twin = twin if isinstance(twin, Twin) else Twin(twin)
        self.seed = int(seed)
        self.noise = copy.deepcopy(noise if noise is not None else models.DEFAULT_NOISE)
        self.sim_model = copy.deepcopy(SIM_MODEL)
        self.eid_rotate_s = float(eid_rotate_s if eid_rotate_s is not None else EID_ROTATE_S)
        self.floor = self.twin.exercise_floor
        self.georef_bearing_deg = georef_bearing_deg(self.twin)
        self.room_id: str | None = None
        self.layout: dict | None = None
        self.noise_fallbacks: dict[str, float] = {}
        for (grp, k), v in _NOISE_FALLBACKS.items():
            if k not in self.noise.get(grp, {}):
                self.noise_fallbacks[f"{grp}.{k}"] = v
        self._init_state()

    def _nz(self, grp: str, key: str) -> float:
        v = self.noise.get(grp, {}).get(key)
        return float(v) if v is not None else float(_NOISE_FALLBACKS[(grp, key)])

    # ---- state
    def _init_state(self):
        self._mrng = np.random.default_rng([self.seed, _STREAM["measure"]])
        self._pucks_true: list[dict] = []
        self._pucks_believed: list[dict] = []
        self._puck_version = 0
        self._devices: dict[str, _Device] = {}
        self._due: dict[tuple, float] = {}
        self._now: float | None = None
        self._links: dict[tuple, tuple[float, int]] = {}
        self._shadow: dict[tuple, float] = {}
        self._linkstate: dict[tuple, tuple[float, bool]] = {}
        self._cold_counter = 0
        self._throws = 0

    def clear(self):
        """Remove pucks, phones and cold devices; the clock restarts at the next step."""
        self.room_id = None
        self._init_state()

    @property
    def now(self) -> float | None:
        return self._now

    # ---- pucks
    @property
    def pucks(self) -> list[dict]:
        """What the pucks believe about their own positions (truth + position noise)."""
        return [dict(p) for p in self._pucks_believed]

    def _install_pucks(self, true_pts: list[dict], believe_seed: int, exact: bool = False,
                       believed: list[dict] | None = None) -> list[dict]:
        sig_default = float(self.noise.get("puck", {}).get("position_sigma_m", 0.08))
        self._pucks_true, self._pucks_believed = [], []
        for k, p in enumerate(true_pts):
            sig = float(p.get("sigma_m", sig_default))
            pid = str(p.get("id") or f"P{k + 1}")
            floor = p.get("floor") or self.floor
            x, y = float(p["x"]), float(p["y"])
            loc = self.twin.locate(x, y, floor)
            self._pucks_true.append({"id": pid, "x": x, "y": y, "floor": floor, "room": loc["room"]})
            if believed is not None:
                bx, by = float(believed[k]["x"]), float(believed[k]["y"])
            elif exact:
                bx, by = x, y
            else:
                rng = np.random.default_rng([self.seed, _STREAM["puck_belief"], believe_seed, k])
                bx, by = x + float(rng.normal(0, sig)), y + float(rng.normal(0, sig))
            self._pucks_believed.append({"id": pid, "x": _r(bx, 3), "y": _r(by, 3), "sigma_m": _r(sig, 3),
                                         "floor": floor, "room": loc["room"], "radios": ["ble", "uwb"],
                                         "simulated": True})
        self._puck_version += 1
        self._links = {k: v for k, v in self._links.items() if k[0] != "puck"}
        for key in [k for k in self._due if k[0] in ("uwb", "sos", "cold")]:
            if key[1] not in {p["id"] for p in self._pucks_true}:
                del self._due[key]
        return self.pucks

    def throw_pucks(self, room_id: str, n: int = 3, seed: int | None = None, strategy: str | None = None) -> list[dict]:
        """Put n pucks into the room. Replaces the pucks.

        strategy (default "door-wall" when n == 2, else "scatter"):
        * "door-wall" | "exterior-wall" | "careless" (n == 2 only): the positions of `beacon.layout.recommend` (the
          owner's two-puck layouts). The pucks BELIEVE they are exactly there; the truth is within a few centimetres
          (N(0, 0.03 m) per axis: placed by hand).
        * "scatter": thrown from the door (Scout's model: 1.5-5.5 m in, within +-75 degrees of straight in, >= 1.2 m
          apart, >= 0.5 m from walls, not in a line; relaxed in steps for small rooms). Prefix-stable: with the same
          seed, throwing n+1 pucks keeps the first n where they landed."""
        room = self.twin.room(room_id)
        n = int(n)
        if not 1 <= n <= 5:
            raise ValueError("n must be between 1 and 5 pucks")
        seed = self.seed if seed is None else int(seed)
        strategy = strategy or ("door-wall" if n == 2 else "scatter")
        if strategy != "scatter":
            if n != 2:
                raise ValueError(f"strategy {strategy!r} places exactly 2 pucks")
            from . import layout                     # lead-owned: where two pucks give one answer
            rec = layout.recommend(self.twin, room_id, n=2, strategy=strategy)
            rng = np.random.default_rng([seed, _STREAM["puck"], 99])
            sd = self.sim_model["throw"]["layout_truth_sd_m"]
            true = [{"id": p["id"], "x": float(p["x"]) + float(rng.normal(0, sd)), "y": float(p["y"]) + float(rng.normal(0, sd)),
                     "floor": room["floor"]} for p in rec["pucks"]]
            self.room_id = room_id
            self._throws += 1
            self.layout = rec
            return self._install_pucks(true, believe_seed=seed, believed=rec["pucks"])
        poly = room["polygon"]
        door = _door_of(self.twin, room)
        inward = _inward(door, poly)
        ratio = self.sim_model["throw"]["collinear_ratio"]
        pts: list[list[float]] = []
        for k in range(n):
            rng = np.random.default_rng([seed, _STREAM["puck"], k])
            got = None
            for rmin, rmax, sep, wall, half in self.sim_model["throw"]["stages"]:
                for _ in range(800):
                    r = rng.uniform(rmin, rmax)
                    a = math.radians(rng.uniform(-half, half))
                    ca, sa = math.cos(a), math.sin(a)
                    v = (ca * inward[0] - sa * inward[1], sa * inward[0] + ca * inward[1])
                    p = (door[0] + r * v[0], door[1] + r * v[1])
                    if not _pip(p, poly) or _poly_dist(p, poly)[0] < wall:
                        continue
                    if any(math.dist(p, q) < sep for q in pts):
                        continue
                    if len(pts) >= 2 and _collinear(pts + [list(p)], ratio):
                        continue
                    got = [float(p[0]), float(p[1])]
                    break
                if got:
                    break
            if got is None:
                raise ValueError(f"room {room_id!r} too small to scatter {n} pucks")
            pts.append(got)
        self.room_id = room_id
        self._throws += 1
        self.layout = None
        return self._install_pucks([{"id": f"P{k + 1}", "x": p[0], "y": p[1], "floor": room["floor"]}
                                    for k, p in enumerate(pts)], believe_seed=seed)

    def set_pucks(self, pucks: list[dict], exact: bool = False) -> list[dict]:
        """Place pucks explicitly: [{"id", "x", "y", "sigma_m"?, "floor"?}] are the TRUE positions; `w.pucks` adds the
        position noise unless exact=True (then the pucks know exactly where they are)."""
        for p in pucks:
            if "x" not in p or "y" not in p:
                raise ValueError(f"puck without x/y: {p!r}")
        out = self._install_pucks(list(pucks), believe_seed=1000 + self._puck_version, exact=exact)
        if self._pucks_true and self.room_id is None:
            self.room_id = self._pucks_true[0]["room"]
        return out

    # ---- devices
    def _new_device(self, key: str, cls: str, x: float, y: float, floor: str | None) -> _Device:
        d = _Device()
        rng = np.random.default_rng([self.seed, _STREAM["device"], _h(key)])
        ble = self.noise["ble"]
        cm = self.sim_model["cold"]["classes"][cls]
        d.key, d.cls = key, cls
        d.floor = floor or self.floor
        d.x, d.y = float(x), float(y)
        d.opt_in, d.uwb, d.pointing, d.camera_assist = False, False, False, False
        d.chip, d.model = None, None
        d.heading_deg, d.aim, d.explicit_heading = None, None, False
        d.tx_offset_db = float(cm["tx_offset_db"] + rng.normal(0.0, ble["tx_spread_db"]))
        d.advertises = bool(rng.random() >= cm["silent_frac"])
        rep = self.sim_model["report"]
        d.baro_offset = float(np.clip(rng.normal(0.0, rep["baro_offset_sd_hpa"]), -rep["baro_offset_max_hpa"],
                                      rep["baro_offset_max_hpa"]))
        d.battery0 = float(rng.uniform(20, 95))
        d.battery_t0 = None
        d.report = None
        d.eid = d.prev_eid = d.prev2_eid = None
        d.eid_since = None
        d.eid_index = 0
        d.version = 0
        d.shadow_epoch = 0
        d.shadow_anchor = (d.x, d.y)
        d.last_moved = None
        d.report_dirty = False
        self._place(d)
        return d

    def _place(self, d: _Device):
        outline = self.twin.floor(d.floor)["outline"]
        d.outside = not _pip((d.x, d.y), outline)
        d.room = None if d.outside else self.twin.locate(d.x, d.y, d.floor)["room"]

    def _move(self, d: _Device, x: float, y: float, floor: str | None):
        floor = floor or d.floor
        moved = math.dist((d.x, d.y), (x, y))
        if moved < 1e-9 and floor == d.floor:
            return
        d.x, d.y, d.floor = float(x), float(y), floor
        d.version += 1
        if math.dist(d.shadow_anchor, (d.x, d.y)) > self.sim_model["ble_shadow_redraw_m"]:
            d.shadow_epoch += 1
            d.shadow_anchor = (d.x, d.y)
        if moved > 0.2:
            d.last_moved = self._now
        if not d.explicit_heading:
            d.aim = None
        self._place(d)

    def upsert_phone(self, key: str, x: float, y: float, floor: str | None = None, opt_in: bool = True,
                     uwb: bool = True, pointing: bool = False, report: dict | None = None,
                     heading_deg: float | None = None, tx_offset_db: float | None = None,
                     advertises: bool | None = None, chip: str | None = "U1", camera_assist: bool = False,
                     model: str | None = None) -> dict:
        """Create or move a simulated phone. opt_in False: no app (cold count only). uwb False: Bluetooth only.
        chip: "U1" (bearing while pointing, Nearby Interaction direction) or "U2" (bearing only while pointing AND
        camera_assist, camera-assisted angle, sigma x1.3); ignored when uwb is False.
        pointing True: the user holds the phone up toward a puck (bearing observations). `report` is merged into
        the phone's current report. Returns the phone's truth record."""
        if not isinstance(key, str) or not key:
            raise ValueError("phone key must be a non-empty string")
        if floor is not None and floor not in {f["id"] for f in self.twin.data["floors"]}:
            raise ValueError(f"unknown floor {floor!r}")
        d = self._devices.get(key)
        if d is None:
            d = self._new_device(key, "phone", x, y, floor)
            self._devices[key] = d
            d.last_moved = self._now
            d.report_dirty = True
        else:
            if d.cls != "phone":
                raise ValueError(f"{key!r} is a {d.cls}, not a phone")
            self._move(d, x, y, floor)
        was_opt_in, was_pointing, was_heading = d.opt_in, d.pointing, d.heading_deg
        d.opt_in, d.uwb = bool(opt_in), bool(uwb) and bool(opt_in)
        if chip is not None and chip not in CHIPS:
            raise ValueError(f"unknown chip {chip!r} (one of {sorted(CHIPS)})")
        d.chip = (chip or "U1") if d.uwb else None
        d.model = model or (CHIPS[d.chip]["model"] if d.chip else (NO_UWB_MODEL if d.opt_in else None))
        d.pointing = bool(pointing) and d.uwb
        d.camera_assist = bool(camera_assist) and d.uwb
        if heading_deg is not None:
            d.heading_deg, d.explicit_heading = float(heading_deg) % 360.0, True
        if d.pointing and not was_pointing and not d.explicit_heading:
            d.aim = None
        if d.pointing != was_pointing or d.heading_deg != was_heading:
            d.shadow_epoch += 1            # the phone turned: static link bias, body block and shadowing are redrawn
        if tx_offset_db is not None:
            d.tx_offset_db = float(tx_offset_db)
        if d.opt_in:
            d.advertises = True
            if d.report is None:
                d.report = {"status": "unknown", "text": "", "people": 1, "can_move": None, "floor_reported": None,
                            "home_suite": None, "platform": "sim", "safe_outside": False}
            if d.report.get("model") != d.model or d.report.get("chip") != d.chip:
                d.report = {**d.report, "model": d.model, "chip": d.chip}
                d.report_dirty = True
            if report:
                new = {**d.report, **{k: v for k, v in report.items() if k not in ("age_s",)}}
                if new != d.report:
                    d.report = new
                    d.report_dirty = True
            if not was_opt_in:
                d.report_dirty = True
            if d.eid is None:
                self._new_eid(d)
        else:
            if advertises is not None:
                d.advertises = bool(advertises)
            elif was_opt_in:
                d.advertises = bool(np.random.default_rng([self.seed, _STREAM["device"], _h(key), 1]).random()
                                    >= self.sim_model["cold"]["classes"]["phone"]["silent_frac"])
            d.report = None
            d.eid = d.prev_eid = None
        if d.pointing != was_pointing:
            d.report_dirty = True
        return d.public()

    def remove_phone(self, key: str):
        d = self._devices.get(key)
        if d is None or d.cls != "phone":
            raise ValueError(f"unknown phone {key!r}")
        del self._devices[key]
        for k in [k for k in self._due if k[2] == key]:
            del self._due[k]

    def add_cold_devices(self, room_id: str, n: int, kind: str | None = None, positions: list | None = None,
                         keys: list | None = None, advertises: list | None = None) -> list[dict]:
        """Add n devices with no app (phones, wearables, laptops) at random places in the room (or at `positions`).
        kind None: drawn from the class mix. Returns their truth records."""
        room = self.twin.room(room_id)
        classes = self.sim_model["cold"]["classes"]
        if kind is not None and kind not in classes:
            raise ValueError(f"unknown device class {kind!r} (one of {sorted(classes)})")
        out = []
        for i in range(int(n)):
            self._cold_counter += 1
            rng = np.random.default_rng([self.seed, _STREAM["cold_place"], _h(room_id), self._cold_counter])
            cls = kind
            if cls is None:
                mix = self.sim_model["cold"]["random_class_mix"]
                names = sorted(mix)
                cls = names[int(rng.choice(len(names), p=[mix[c] for c in names]))]
            if positions is not None:
                x, y = float(positions[i][0]), float(positions[i][1])
            else:
                x, y = _random_point_in(rng, room["polygon"], 0.3)
            key = keys[i] if keys else f"cold-{room_id}-{self._cold_counter}"
            if key in self._devices:
                raise ValueError(f"device key {key!r} already exists")
            d = self._new_device(key, cls, x, y, room["floor"])
            if advertises is not None and advertises[i] is not None:
                d.advertises = bool(advertises[i])
            self._devices[key] = d
            out.append(d.public())
        return out

    # ---- scenarios
    def load_scenario(self, scenario, reseed: bool = True) -> dict:
        """Clear the world and load a scenario (dict, path or name of fixtures/beacon/scenarios/). Every declared room
        and every 'outside' flag is checked against the twin (ValueError when wrong)."""
        sc = load_scenario_file(scenario)
        if sc.get("schema") not in (None, "sandtable.beacon.scenario/1"):
            raise ValueError(f"unknown scenario schema {sc.get('schema')!r}")
        floor = sc.get("floor") or self.twin.exercise_floor
        if floor not in {f["id"] for f in self.twin.data["floors"]}:
            raise ValueError(f"scenario floor {floor!r} is not a floor of the twin")
        if sc.get("room") is not None:
            self.twin.room(sc["room"])
        if reseed and sc.get("seed") is not None:
            self.seed = int(sc["seed"])
        self.clear()
        self.floor = floor
        pk = sc.get("pucks")
        if isinstance(pk, dict) and pk.get("throw"):
            th = pk["throw"]
            self.throw_pucks(th.get("room") or sc["room"], n=int(th.get("n", 3)), seed=th.get("seed"),
                             strategy=th.get("strategy"))
        elif isinstance(pk, list) and pk:
            self.set_pucks(pk)
        if sc.get("room"):
            self.room_id = sc["room"]

        def check(item, x, y, fl):
            outline = self.twin.floor(fl)["outline"]
            outside = not _pip((x, y), outline)
            if item.get("outside") is not None and bool(item["outside"]) != outside:
                raise ValueError(f"scenario item {item.get('key')!r} at ({x}, {y}) is "
                                 f"{'outside' if outside else 'inside'} the floor outline, declared otherwise")
            if "room" in item and not item.get("n"):
                got = None if outside else self.twin.locate(x, y, fl)["room"]
                if item["room"] != got:
                    raise ValueError(f"scenario item {item.get('key')!r} at ({x}, {y}) is in room {got!r} of the "
                                     f"twin, declared {item['room']!r}")

        for ph in sc.get("phones", []):
            x, y = float(ph["x"]), float(ph["y"])
            fl = ph.get("floor") or floor
            check(ph, x, y, fl)
            self.upsert_phone(ph["key"], x, y, floor=fl, opt_in=ph.get("opt_in", True), uwb=ph.get("uwb", True),
                              pointing=ph.get("pointing", False), report=ph.get("report"),
                              heading_deg=ph.get("heading_deg"), tx_offset_db=ph.get("tx_offset_db"),
                              advertises=ph.get("advertises"), chip=ph.get("chip", "U1"),
                              camera_assist=ph.get("camera_assist", False), model=ph.get("model"))
        for cd in sc.get("cold_devices", []):
            if cd.get("n"):
                self.add_cold_devices(cd["room"], int(cd["n"]), kind=cd.get("class"))
                continue
            x, y = float(cd["x"]), float(cd["y"])
            fl = cd.get("floor") or floor
            check(cd, x, y, fl)
            if not _pip((x, y), self.twin.floor(fl)["outline"]):
                raise ValueError(f"cold device {cd.get('key')!r} is outside the floor outline")
            room_id = self.twin.locate(x, y, fl)["room"]
            self.add_cold_devices(room_id, 1, kind=cd.get("class", "phone"), positions=[[x, y]],
                                  keys=[cd["key"]] if cd.get("key") else None, advertises=[cd.get("advertises")])
        return {"name": sc.get("name"), "simulated": True, "room": self.room_id, "pucks": self.pucks,
                "phones": sum(1 for d in self._devices.values() if d.cls == "phone"),
                "opt_in": sum(1 for d in self._devices.values() if d.opt_in),
                "cold_devices": sum(1 for d in self._devices.values() if not d.opt_in)}

    # ---- physics helpers
    def _link(self, puck: dict, d: _Device) -> tuple[float, int] | None:
        """(true distance, walls crossed) between a true puck and a device, or None when no link is possible."""
        if d.outside or d.floor != puck["floor"]:
            return None
        k = ("puck", puck["id"], self._puck_version, d.key, d.version)
        v = self._links.get(k)
        if v is None:
            dist = math.hypot(d.x - puck["x"], d.y - puck["y"])
            nw = self.twin.walls_crossed((puck["x"], puck["y"]), (d.x, d.y), d.floor)
            v = (dist, int(nw))
            self._links[k] = v
        return v

    def _shadow_db(self, puck: dict, d: _Device) -> float:
        k = (puck["id"], self._puck_version, d.key, d.shadow_epoch)
        v = self._shadow.get(k)
        if v is None:
            rng = np.random.default_rng([self.seed, _STREAM["shadow"], _h(puck["id"]), self._puck_version, _h(d.key),
                                         d.shadow_epoch])
            v = float(rng.normal(0.0, self.noise["ble"]["shadow_sigma_db"]))
            self._shadow[k] = v
        return v

    def _link_state(self, puck: dict, d: _Device) -> tuple[float, bool]:
        """(z, body) for a puck-phone link: z ~ N(0, 1) scales the STATIC part of the UWB error and `body` says whether
        the holder's body blocks the path. Drawn once per link, phone position and orientation (redrawn when the phone
        moves more than 0.5 m or turns, or the puck moves)."""
        k = (puck["id"], self._puck_version, d.key, d.shadow_epoch)
        v = self._linkstate.get(k)
        if v is None:
            rng = np.random.default_rng([self.seed, _STREAM["link"], _h(puck["id"]), self._puck_version, _h(d.key),
                                         d.shadow_epoch])
            z = float(rng.normal())
            body = bool(rng.random() < self._nz("uwb", "body_block_prob")) if d.cls == "phone" else False
            v = (z, body)
            self._linkstate[k] = v
        return v

    def _uwb_terms(self, dist: float, nw: int, body: bool) -> tuple[float, float, float]:
        """(mean, static sigma, white sigma) of one UWB sample, from models.uwb_expected: the static part (link bias,
        wall and body spread; drawn once per link) is the sigma with infinitely many samples averaged, the white part
        is what remains of the one-sample sigma."""
        mean, s1 = models.uwb_expected(dist, nw, self.noise, body=body, n_samples=1)
        _, s_inf = models.uwb_expected(dist, nw, self.noise, body=body, n_samples=10 ** 9)
        if "link_bias_sigma_m" not in self.noise.get("uwb", {}):          # TODO: fallback only, see _NOISE_FALLBACKS
            s_inf = math.sqrt(s_inf ** 2 + self._nz("uwb", "link_bias_sigma_m") ** 2)
        return float(mean), float(s_inf), math.sqrt(max(float(s1) ** 2 - float(s_inf) ** 2, 0.0))

    def _ble_mean(self, puck: dict, d: _Device, link: tuple[float, int]) -> float:
        """Mean RSSI of the pair without fast fading: forward model (with the body loss when a body blocks the link)
        + transmit offset + slow shadowing."""
        body = self._link_state(puck, d)[1]
        mean, _sd = models.ble_expected_rssi(max(link[0], 0.1), link[1], self.noise, body=body)
        return float(mean) + d.tx_offset_db + self._shadow_db(puck, d)

    def _ble_up(self, puck: dict, d: _Device) -> bool:
        link = self._link(puck, d)
        return link is not None and self._ble_mean(puck, d, link) >= self.noise["ble"]["sensitivity_dbm"]

    def _new_eid(self, d: _Device):
        rng = np.random.default_rng([self.seed, _STREAM["eid"], _h(d.key), d.eid_index])
        d.prev2_eid = d.prev_eid
        d.prev_eid = d.eid
        d.eid = f"{int(rng.integers(0, 2 ** 32)):08x}"
        d.eid_index += 1

    # ---- the clock
    def _period(self, hz: float) -> float:
        return 1.0 / hz

    def _ticks(self, key: tuple, period: float, now: float, t_prev: float | None) -> list[float]:
        """Tick times of one stream in (t_prev, now], jittered; first tick at a random phase."""
        sch = self.sim_model["schedule"]
        due = self._due.get(key)
        if due is None:
            due = now if key[0] == "report" else now + float(self._mrng.uniform(0.0, period))
        out = []
        floor_t = now - sch["max_catchup_s"]
        while due <= now + 1e-9:
            if due >= floor_t:
                out.append(due)
            due += period * (1.0 + float(self._mrng.uniform(-sch["jitter_frac"], sch["jitter_frac"])))
        self._due[key] = due
        return out

    # ---- observations
    def step(self, now: float) -> list[dict]:
        """Everything the pucks observed since the previous step (docs/CONTRACTS.md section 2 shapes), time-ordered."""
        now = float(now)
        t_prev = self._now
        if t_prev is not None and now < t_prev:
            raise ValueError("time went backwards")
        self._now = now
        sch = self.sim_model["schedule"]
        obs: list[dict] = []
        pucks = sorted(self._pucks_true, key=lambda p: p["id"])
        devices = [self._devices[k] for k in sorted(self._devices)]
        for d in devices:
            if d.last_moved is None:
                d.last_moved = now
            if d.battery_t0 is None:
                d.battery_t0 = now

        for d in devices:
            if not d.opt_in:
                continue
            # eid rotation
            if d.eid_since is None:
                d.eid_since = now - float(np.random.default_rng([self.seed, _STREAM["eid"], _h(d.key), 10 ** 6])
                                          .uniform(0.0, self.eid_rotate_s))
            while now - d.eid_since >= self.eid_rotate_s:
                self._new_eid(d)
                d.eid_since += self.eid_rotate_s
                d.report_dirty = True
            obs.extend(self._phone_obs(d, pucks, now, t_prev))

        for p in pucks:
            for t in self._ticks(("cold", p["id"], None), sch["cold_every_s"], now, t_prev):
                obs.append(self._cold_obs(p, devices, t))

        obs.sort(key=lambda o: (o["t"], _KIND_ORDER[o["kind"]], o.get("puck") or "", o.get("eid") or ""))
        return obs

    @staticmethod
    def _eid_at(d: _Device, t: float) -> tuple[str, str | None]:
        """(eid, prev_eid) the phone was broadcasting at time t (a rotation inside the current step)."""
        if d.eid_since is None or t >= d.eid_since - 1e-9 or d.prev_eid is None:
            return d.eid, d.prev_eid
        return d.prev_eid, d.prev2_eid

    def _phone_obs(self, d: _Device, pucks: list[dict], now: float, t_prev: float | None) -> list[dict]:
        sch = self.sim_model["schedule"]
        uwbn, blen = self.noise["uwb"], self.noise["ble"]
        out: list[dict] = []
        heard_by = []
        for p in pucks:
            link = self._link(p, d)
            if link is None:
                continue
            dist, nw = link
            ble_mean = self._ble_mean(p, d, link)
            if ble_mean >= blen["sensitivity_dbm"]:
                heard_by.append(p["id"])
            # Bluetooth SOS beacon
            for t in self._ticks(("sos", p["id"], d.key), self._period(sch["sos_hz"]), now, t_prev):
                rssi = ble_mean + float(self._mrng.normal(0.0, blen["fast_fading_sigma_db"]))
                if rssi < blen["sensitivity_dbm"]:
                    continue
                out.append({"kind": "sos", "t": _r(t, 3), "puck": p["id"], "eid": self._eid_at(d, t)[0], "rssi": _r(rssi, 1),
                            "tx_dbm": float(blen["tx_1m_dbm"]), "report": self._compact_report(d)})
            # UWB two-way ranging
            if d.uwb:
                ok = dist <= uwbn["max_range_m"] and nw <= uwbn["max_walls"]
                if self.sim_model["uwb_link"]["needs_ble_link"]:
                    ok = ok and ble_mean >= blen["sensitivity_dbm"]
                for t in self._ticks(("uwb", p["id"], d.key), self._period(sch["uwb_hz"]), now, t_prev):
                    if not ok:
                        continue
                    o = self._uwb_sample(p, d, dist, nw, t)
                    if o is not None:
                        out.append(o)
        # bearing (computed on the phone)
        if self._bearing_on(d):
            for t in self._ticks(("bearing", None, d.key), self._period(sch["bearing_hz"]), now, t_prev):
                o = self._bearing_sample(d, pucks, t)
                if o is not None:
                    out.append(o)
        # report over the encrypted connection (or the internet uplink for "I am out")
        rep = self.sim_model["report"]
        reachable = bool(heard_by) or (bool(d.report.get("safe_outside")) and rep["safe_outside_uplink"])
        key = ("report", None, d.key)
        if d.report_dirty:
            rotated_now = t_prev is not None and d.eid_since is not None and t_prev < d.eid_since <= now
            self._due[key] = d.eid_since if rotated_now else now
        for t in self._ticks(key, rep["every_s"], now, t_prev):
            if not reachable:
                continue
            eid, prev = self._eid_at(d, t)
            out.append({"kind": "report", "t": _r(t, 3), "eid": eid, "prev_eid": prev,
                        "report": self._full_report(d, t)})
            d.report_dirty = False
        return out

    def _uwb_sample(self, p: dict, d: _Device, dist: float, nw: int, t: float) -> dict | None:
        uwbn = self.noise["uwb"]
        ul = self.sim_model["uwb_link"]
        m = self._mrng
        if m.random() < uwbn["dropout_prob"]:
            return None
        z, body = self._link_state(p, d)
        mean, s_static, s_white = self._uwb_terms(dist, nw, body)
        r = mean + z * s_static + s_white * float(m.normal())
        if m.random() < uwbn["outlier_prob"]:
            r += uwbn["outlier_m"] * float(m.uniform(*ul["outlier_scale"]))
        r = max(r, 0.05)
        if nw == 0:
            los = m.normal(ul["los_clear_mean"], ul["los_clear_sd"])
        else:
            los = m.normal(ul["los_wall_mean"][min(nw, len(ul["los_wall_mean"])) - 1], ul["los_wall_sd"])
        if body:
            los -= ul["los_body_drop"]
        rssi = ul["rssi_1m_dbm"] - 20.0 * math.log10(max(dist, 0.3)) - ul["rssi_wall_db"] * nw + m.normal(0, ul["rssi_sd_db"])
        gap = (ul["fp_gap_clear_db"] if nw == 0 else ul["fp_gap_wall_db"] * nw) + (ul["fp_gap_body_db"] if body else 0.0)
        fp = rssi - gap + m.normal(0, ul["fp_sd_db"])
        return {"kind": "uwb", "t": _r(t, 3), "puck": p["id"], "eid": self._eid_at(d, t)[0], "d_m": _r(r, 3), "rssi": _r(rssi, 1),
                "fp": _r(fp, 1), "los": _r(float(np.clip(los, 0.0, 1.0)), 2)}

    def _visible_for_bearing(self, d: _Device, pucks: list[dict]) -> list[tuple[dict, float, float]]:
        """Pucks with a wall-free UWB link to the phone: (puck, true direction phone->puck in deg, distance)."""
        uwbn, blen = self.noise["uwb"], self.noise["ble"]
        out = []
        for p in pucks:
            link = self._link(p, d)
            if link is None:
                continue
            dist, nw = link
            if dist > uwbn["max_range_m"] or nw > uwbn["max_walls"]:
                continue
            if self.sim_model["bearing"]["needs_wall_free_link"] and nw > 0:
                continue
            if self._ble_mean(p, d, link) < blen["sensitivity_dbm"]:
                continue
            ang = math.degrees(math.atan2(p["y"] - d.y, p["x"] - d.x)) % 360.0
            out.append((p, ang, dist))
        return out

    @staticmethod
    def _bearing_on(d: _Device) -> bool:
        """U1: while pointing. U2: while pointing AND camera assistance is on."""
        if not (d.uwb and d.pointing and d.chip in CHIPS):
            return False
        return d.camera_assist if CHIPS[d.chip]["bearing"] == "pointing+camera_assist" else True

    def _bearing_sample(self, d: _Device, pucks: list[dict], t: float) -> dict | None:
        bn = self.noise["bearing"]
        chip = CHIPS[d.chip]
        sigma = float(bn["sigma_deg"]) * chip["sigma_factor"]
        vis = self._visible_for_bearing(d, pucks)
        if not vis:
            return None
        if d.explicit_heading:
            heading = d.heading_deg
        else:
            if d.aim is None:
                nearest = min(vis, key=lambda v: v[2])
                rng = np.random.default_rng([self.seed, _STREAM["aim"], _h(d.key), d.version, int(t * 10)])
                d.aim = (nearest[1] + float(rng.normal(0.0, self.sim_model["bearing"]["aim_sd_deg"]))) % 360.0
            heading = d.aim

        def off(a):
            return abs((a - heading + 180.0) % 360.0 - 180.0)

        best = min(vis, key=lambda v: off(v[1]))
        if off(best[1]) > bn["fov_deg"] / 2.0:
            return None
        meas = (best[1] + float(self._mrng.normal(0.0, sigma))) % 360.0
        return {"kind": "bearing", "t": _r(t, 3), "eid": self._eid_at(d, t)[0], "puck": best[0]["id"],
                "to_puck_compass_deg": _r(to_puck_compass_deg(meas, self.georef_bearing_deg), 1),
                "sigma_deg": _r(sigma, 2), "source": chip["source"]}

    def _battery(self, d: _Device, t: float) -> int:
        start = d.report.get("battery_pct") if d.report and d.report.get("battery_pct") is not None else d.battery0
        drain = self.sim_model["report"]["battery_drain_pct_per_s"] * max(0.0, t - (d.battery_t0 or t))
        return int(max(1, round(float(start) - drain)))

    def _compact_report(self, d: _Device) -> dict:
        r = d.report or {}
        out = {"status": r.get("status", "unknown"), "people": r.get("people", 1), "can_move": r.get("can_move"),
               "battery_pct": self._battery(d, self._now or 0.0)}
        if r.get("safe_outside"):
            out["safe_outside"] = True
        return out

    def _full_report(self, d: _Device, t: float) -> dict:
        rep = self.sim_model["report"]
        r = dict(d.report or {})
        floor = self.twin.floor(d.floor)
        h = float(floor.get("elevation_m", 0.0)) + rep["baro_phone_height_m"]
        rng = np.random.default_rng([self.seed, _STREAM["device"], _h(d.key), 2, int(t * 1000)])
        baro = rep["baro_ground_hpa"] - rep["baro_hpa_per_m"] * h + d.baro_offset + float(rng.normal(0, rep["baro_noise_hpa"]))
        still_s = max(0.0, t - (d.last_moved if d.last_moved is not None else t))
        text = str(r.get("text") or "")[: rep["text_max"]]
        return {"status": r.get("status", "unknown"), "text": text, "people": r.get("people", 1),
                "can_move": r.get("can_move"), "battery_pct": self._battery(d, t),
                "floor_reported": r.get("floor_reported"), "baro_hpa": _r(baro, 2),
                "still": still_s >= rep["still_after_s"], "still_s": _r(still_s, 1),
                "platform": r.get("platform", "sim"), "model": d.model, "chip": d.chip, "network": None,
                "app": "0.1-sim", "home_suite": r.get("home_suite"),
                "safe_outside": bool(r.get("safe_outside", False)), "pointing": d.pointing, "simulated": True}

    def _cold_obs(self, p: dict, devices: list[_Device], t: float) -> dict:
        cm = self.sim_model["cold"]
        blen = self.noise["ble"]
        m = self._mrng
        counts = {"near": 0, "mid": 0, "far": 0}
        fast = blen["fast_fading_sigma_db"] / math.sqrt(cm["samples_per_window"])
        rot = cm["window_s"] / cm["addr_rotate_s"]
        for d in devices:
            if not d.advertises:
                continue
            link = self._link(p, d)
            if link is None:
                continue
            rssi = self._ble_mean(p, d, link) + float(m.normal(0.0, fast))
            hit = m.random() < cm["scan_hit_prob"]
            dup = m.random() < 1.0 - (1.0 - rot) * (1.0 - cm["classes"][d.cls]["p_multi"])
            if rssi < blen["sensitivity_dbm"] or not hit:
                continue
            band = models.cold_band(rssi)
            counts[band] += 2 if dup else 1
        return {"kind": "cold", "t": _r(t, 3), "puck": p["id"], "window_s": float(cm["window_s"]), **counts}

    # ---- truth (evaluation only)
    def truth(self) -> dict:
        """Ground truth. For evaluation scripts and GET /api/sim/truth only; never for a board."""
        devs = [self._devices[k] for k in sorted(self._devices)]
        by_room: dict[str, dict] = {}
        for d in devs:
            if d.room is None:
                continue
            b = by_room.setdefault(d.room, {"devices": 0, "advertising": 0, "opt_in_phones": 0, "no_app_phones": 0,
                                            "wearables": 0, "laptops": 0, "people_reported": 0})
            b["devices"] += 1
            b["advertising"] += int(d.advertises)
            if d.cls == "phone":
                b["opt_in_phones" if d.opt_in else "no_app_phones"] += 1
            else:
                b["wearables" if d.cls == "wearable" else "laptops"] += 1
            if d.opt_in and d.report and not d.report.get("safe_outside"):
                b["people_reported"] += int(d.report.get("people") or 0)
        return {"simulated": True, "t": self._now, "seed": self.seed, "floor": self.floor, "room": self.room_id,
                "pucks": [{**p, "believed": {"x": b["x"], "y": b["y"]}}
                          for p, b in zip(self._pucks_true, self._pucks_believed)],
                "phones": [d.public() for d in devs if d.cls == "phone"],
                "cold": {"devices": [d.public() for d in devs if not d.opt_in], "by_room": by_room,
                         "model": copy.deepcopy(self.sim_model["cold"])}}

    def stated_model(self) -> dict:
        """The noise model the world runs with (radio constants + sim-only constants), for hello messages and results."""
        return {"simulated": True, "noise": copy.deepcopy(self.noise), "sim": copy.deepcopy(self.sim_model),
                "noise_fallbacks_used": dict(self.noise_fallbacks)}
