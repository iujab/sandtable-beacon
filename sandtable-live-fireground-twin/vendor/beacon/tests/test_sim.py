"""Tests of the SIMULATED world (beacon/sim.py) against the real twin fixture."""
from __future__ import annotations

import copy
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from beacon import models
from beacon.sim import World, list_scenarios, load_scenario_file, scaled_noise
from beacon.twin import Twin, _pip, _poly_dist

ROOT = Path(__file__).resolve().parent.parent
TWIN = json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text())
COLD_KEYS = {"kind", "t", "puck", "window_s", "near", "mid", "far"}


@pytest.fixture(scope="module")
def twin():
    return Twin(TWIN)


def run(w: World, seconds: float, t0: float = 1000.0, hz: float = 10.0) -> list[dict]:
    out = []
    n = int(round(seconds * hz))
    for i in range(n + 1):
        out.extend(w.step(t0 + i / hz))
    return out


def quiet_noise(**over) -> dict:
    n = copy.deepcopy(models.DEFAULT_NOISE)
    for path, v in over.items():
        grp, key = path.split("__")
        n[grp][key] = v
    return n


# ---- determinism ---------------------------------------------------------------------------------------------------

def test_deterministic_for_a_seed(twin):
    a, b, c = World(twin, seed=7), World(twin, seed=7), World(twin, seed=8)
    for w in (a, b, c):
        w.load_scenario("suite-310", reseed=False)
        w.upsert_phone("extra", 5.0, 4.0, pointing=True)
    oa, ob, oc = run(a, 6.0), run(b, 6.0), run(c, 6.0)
    assert oa == ob
    assert oa != oc
    assert a.truth() == b.truth()


def test_scenario_reseeds_so_the_demo_is_identical(twin):
    a, b = World(twin, seed=1), World(twin, seed=2)
    a.load_scenario("suite-310")
    b.load_scenario("suite-310")
    assert a.pucks == b.pucks
    assert run(a, 3.0) == run(b, 3.0)


# ---- pucks ---------------------------------------------------------------------------------------------------------

def test_throw_lands_inside_the_room_near_the_door_and_is_prefix_stable(twin):
    w = World(twin, seed=3)
    room = twin.room("r310")
    door = twin.door("d310")["at"]
    for n in range(1, 6):
        pucks = w.throw_pucks("r310", n=n, seed=42, strategy="scatter")
        truth = w.truth()["pucks"]
        assert [p["id"] for p in pucks] == [f"P{k + 1}" for k in range(n)]
        for p in truth:
            assert _pip((p["x"], p["y"]), room["polygon"])
            assert _poly_dist((p["x"], p["y"]), room["polygon"])[0] >= 0.25
            assert math.dist((p["x"], p["y"]), door) <= 8.0
            # believed position = truth + small noise
            assert math.dist((p["x"], p["y"]), (p["believed"]["x"], p["believed"]["y"])) < 0.5
        if n > 1:
            assert [(p["x"], p["y"]) for p in truth[:-1]] == prev
        prev = [(p["x"], p["y"]) for p in truth]
    with pytest.raises(ValueError):
        w.throw_pucks("r999")


def test_two_pucks_default_to_the_door_wall_layout(twin):
    from beacon import layout
    w = World(twin, seed=3)
    for strategy in (None, "door-wall", "exterior-wall", "careless"):
        pucks = w.throw_pucks("r310", n=2, strategy=strategy)
        rec = layout.recommend(twin, "r310", n=2, strategy=strategy or "door-wall")
        assert [(p["x"], p["y"]) for p in pucks] == [(q["x"], q["y"]) for q in rec["pucks"]]     # believed = recommended
        for t, q in zip(w.truth()["pucks"], rec["pucks"]):
            assert 0.0 < math.dist((t["x"], t["y"]), (q["x"], q["y"])) < 0.15                    # placed by hand
            assert t["room"] == "r310"
    with pytest.raises(ValueError):
        w.throw_pucks("r310", n=3, strategy="door-wall")


# ---- rates ---------------------------------------------------------------------------------------------------------

def test_rates(twin):
    w = World(twin, seed=11)
    w.set_pucks([{"id": "P1", "x": 7.0, "y": 6.0}, {"id": "P2", "x": 6.0, "y": 3.0}, {"id": "P3", "x": 4.5, "y": 6.5}])
    ang = math.degrees(math.atan2(6.0 - 3.0, 7.0 - 3.0))       # point the phone straight at P1
    w.upsert_phone("ph", 3.0, 3.0, pointing=True, heading_deg=ang, report={"status": "trapped"})
    secs = 20.0
    obs = run(w, secs)
    c = Counter((o["kind"], o.get("puck")) for o in obs)
    keep = 1.0 - w.noise["uwb"]["dropout_prob"]
    for p in ("P1", "P2", "P3"):
        assert 5.0 * secs * keep * 0.8 <= c[("uwb", p)] <= 5.0 * secs * 1.15, c
        assert 2.0 * secs * 0.8 <= c[("sos", p)] <= 2.0 * secs * 1.15, c
        assert 3 <= c[("cold", p)] <= 5, c
    bearings = [o for o in obs if o["kind"] == "bearing"]
    assert 2.0 * secs * 0.8 <= len(bearings) <= 2.0 * secs * 1.15
    reports = [o for o in obs if o["kind"] == "report"]
    assert secs / 5.0 <= len(reports) <= secs / 5.0 + 2
    assert all(o["t"] <= 1000.0 + secs + 1e-6 for o in obs)
    assert [o["t"] for o in obs] == sorted(o["t"] for o in obs)


def test_long_gap_only_catches_up_two_seconds(twin):
    w = World(twin, seed=5)
    w.set_pucks([{"id": "P1", "x": 7.0, "y": 6.0}])
    w.upsert_phone("ph", 3.0, 3.0)
    w.step(0.0)
    obs = w.step(600.0)
    assert all(o["t"] >= 598.0 for o in obs)
    assert sum(o["kind"] == "uwb" for o in obs) <= 12


# ---- range and walls -----------------------------------------------------------------------------------------------

def test_no_observation_beyond_range_or_walls(twin):
    w = World(twin, seed=2)
    # East corridor: a long wall-free line. Puck at the south end, phone farther than max_range_m along the corridor.
    w.set_pucks([{"id": "P1", "x": 24.4, "y": 7.0}], exact=True)
    far = 7.0 + w.noise["uwb"]["max_range_m"] + 2.0
    assert twin.walls_crossed((24.4, 7.0), (24.4, far)) == 0
    w.upsert_phone("far", 24.4, far)
    # more walls than max_walls: Suite 307 seen from the east corridor
    nw = twin.walls_crossed((24.4, 7.0), (4.0, 30.0))
    assert nw > w.noise["uwb"]["max_walls"]
    w.upsert_phone("walled", 4.0, 30.0)
    obs = run(w, 6.0)
    eids = {p["key"]: p["eid"] for p in w.truth()["phones"]}
    assert not [o for o in obs if o["kind"] == "uwb" and o["eid"] in (eids["far"], eids["walled"])]
    # nothing below the Bluetooth sensitivity, ever
    sens = w.noise["ble"]["sensitivity_dbm"]
    assert all(o["rssi"] >= sens for o in obs if o["kind"] == "sos")


def test_no_link_to_another_floor_or_outside(twin):
    w = World(twin, seed=2)
    w.throw_pucks("r310", n=3)
    w.upsert_phone("upstairs", 3.0, 3.0, floor="f2")
    w.upsert_phone("street", -10.0, 3.0, report={"safe_outside": True, "status": "ok"})
    w.upsert_phone("street2", -10.0, 5.0, report={"status": "ok"})
    obs = run(w, 6.0)
    eids = {p["key"]: p["eid"] for p in w.truth()["phones"]}
    assert not [o for o in obs if o.get("eid") == eids["upstairs"]]
    assert not [o for o in obs if o["kind"] in ("uwb", "sos") and o.get("eid") in (eids["street"], eids["street2"])]
    # "I am out" reaches the session over the app's internet uplink; the other phone outside is never heard
    assert [o for o in obs if o["kind"] == "report" and o["eid"] == eids["street"]]
    assert not [o for o in obs if o.get("eid") == eids["street2"]]


def test_walls_have_the_right_sign(twin):
    """Same distance, with and without a wall: UWB reads longer through the wall, Bluetooth reads weaker."""
    p = (7.0, 4.0)
    los_pt, wall_pt = (3.0, 4.0), (7.0, 9.65)          # 4 m west inside 310; 5.65 m north through the 310/309 wall
    assert twin.walls_crossed(p, los_pt) == 0 and twin.walls_crossed(p, wall_pt) == 1
    d_los, d_wall = math.dist(p, los_pt), math.dist(p, wall_pt)
    noise = quiet_noise(uwb__body_block_prob=0.0)
    err = {"los": [], "wall": []}
    rssi = {"los": [], "wall": []}
    for seed in range(60):
        w = World(twin, seed=seed, noise=noise)
        w.set_pucks([{"id": "P1", "x": p[0], "y": p[1]}], exact=True)
        w.upsert_phone("los", *los_pt)
        w.upsert_phone("wall", *wall_pt)
        eids = {q["key"]: q["eid"] for q in w.truth()["phones"]}
        obs = run(w, 3.0)
        for k, d_true in (("los", d_los), ("wall", d_wall)):
            u = [o["d_m"] for o in obs if o["kind"] == "uwb" and o["eid"] == eids[k]]
            s = [o["rssi"] - (-10 * w.noise["ble"]["n_los"] * math.log10(d_true))
                 for o in obs if o["kind"] == "sos" and o["eid"] == eids[k]]
            if u:
                err[k].append(float(np.median(u)) - d_true)
            if s:
                rssi[k].append(float(np.median(s)))
    assert len(err["los"]) > 40 and len(err["wall"]) > 40
    assert np.mean(err["wall"]) > np.mean(err["los"]) + 0.03          # positive bias through the wall
    assert abs(np.mean(err["los"])) < 0.08                             # unbiased in line of sight
    assert np.mean(rssi["wall"]) < np.mean(rssi["los"]) - 2.0          # per-wall loss, distance removed
    # los hint (studies only, reports_los=True): high on a clear link, low through a wall
    w = World(twin, seed=1, noise=noise, reports_los=True)
    w.set_pucks([{"id": "P1", "x": p[0], "y": p[1]}], exact=True)
    w.upsert_phone("los", *los_pt)
    w.upsert_phone("wall", *wall_pt)
    eids = {q["key"]: q["eid"] for q in w.truth()["phones"]}
    obs = run(w, 5.0)
    los_vals = {k: np.mean([o["los"] for o in obs if o["kind"] == "uwb" and o["eid"] == eids[k]]) for k in eids}
    assert los_vals["los"] > 0.7 > 0.5 > los_vals["wall"]


def test_static_link_bias_does_not_average_out(twin):
    """The static part of the UWB error is drawn once per link: the median of many samples keeps it."""
    noise = quiet_noise(uwb__body_block_prob=0.0, uwb__link_bias_sigma_m=0.3)
    meds = []
    for seed in range(30):
        w = World(twin, seed=seed, noise=noise)
        w.set_pucks([{"id": "P1", "x": 7.0, "y": 4.0}], exact=True)
        w.upsert_phone("a", 3.0, 4.0)
        obs = run(w, 6.0)
        meds.append(np.median([o["d_m"] for o in obs if o["kind"] == "uwb"]) - 4.0)
    assert np.std(meds) > 0.15          # ~0.3 m spread between links, not ~0.1/sqrt(30)


# ---- bearing -------------------------------------------------------------------------------------------------------

def test_bearing_round_trip_and_georeference(twin):
    noise = quiet_noise(bearing__sigma_deg=0.01)
    w = World(twin, seed=4, noise=noise)
    puck = (7.5, 6.0)
    phone = (2.5, 2.0)
    w.set_pucks([{"id": "P1", "x": puck[0], "y": puck[1]}], exact=True)
    to_puck = math.degrees(math.atan2(puck[1] - phone[1], puck[0] - phone[0]))
    w.upsert_phone("ph", *phone, pointing=True, heading_deg=to_puck)
    obs = [o for o in run(w, 3.0) if o["kind"] == "bearing"]
    assert obs and all(o["source"] == "ni-direction+compass" for o in obs)
    b = TWIN["georef"]["bearing_deg"]
    from_puck_true = math.degrees(math.atan2(phone[1] - puck[1], phone[0] - puck[0])) % 360.0
    for o in obs:
        back = models.compass_to_bldg_deg(o["to_puck_compass_deg"], b)
        assert abs((back - from_puck_true + 180) % 360 - 180) < 0.5
    # independent check through the twin's georeference: azimuth phone -> puck from lat/lon
    la1, lo1 = twin.to_geo(*phone)
    la2, lo2 = twin.to_geo(*puck)
    east = (lo2 - lo1) * 111320.0 * math.cos(math.radians(la1))
    north = (la2 - la1) * 111320.0
    az = math.degrees(math.atan2(east, north)) % 360.0
    assert abs((obs[0]["to_puck_compass_deg"] - az + 180) % 360 - 180) < 0.5


def test_bearing_only_while_pointing_and_chip_rules(twin):
    w = World(twin, seed=9)
    w.set_pucks([{"id": "P1", "x": 7.0, "y": 6.0}], exact=True)
    ang = math.degrees(math.atan2(4.0, 4.0))
    w.upsert_phone("pocket", 3.0, 2.0, pointing=False)
    w.upsert_phone("u1", 3.0, 2.2, pointing=True, heading_deg=ang, chip="U1")
    w.upsert_phone("u2", 3.2, 2.0, pointing=True, heading_deg=ang, chip="U2")
    w.upsert_phone("u2cam", 3.2, 2.2, pointing=True, heading_deg=ang, chip="U2", camera_assist=True)
    w.upsert_phone("away", 2.8, 2.0, pointing=True, heading_deg=ang + 180.0)       # puck behind the phone
    w.upsert_phone("ble", 2.8, 2.2, uwb=False, pointing=True)
    eids = {q["key"]: q["eid"] for q in w.truth()["phones"]}
    obs = [o for o in run(w, 4.0) if o["kind"] == "bearing"]
    by = Counter(o["eid"] for o in obs)
    assert by[eids["u1"]] > 0 and by[eids["u2cam"]] > 0
    assert by[eids["pocket"]] == by[eids["u2"]] == by[eids["away"]] == by[eids["ble"]] == 0
    base = w.noise["bearing"]["sigma_deg"]
    for o in obs:
        if o["eid"] == eids["u2cam"]:
            assert o["source"] == "camera-assist+compass" and abs(o["sigma_deg"] - 1.3 * base) < 0.01
        else:
            assert o["source"] == "ni-direction+compass" and abs(o["sigma_deg"] - base) < 0.01
    rep = [o for o in run(w, 1.0, t0=1010.0) if o["kind"] == "report" and o["eid"] == eids["u2cam"]]
    assert rep and rep[0]["report"]["chip"] == "U2" and rep[0]["report"]["model"]


def test_no_bearing_through_a_wall(twin):
    w = World(twin, seed=9)
    w.set_pucks([{"id": "P1", "x": 7.0, "y": 6.0}], exact=True)
    ang = math.degrees(math.atan2(6.0 - 12.0, 7.0 - 4.0))
    w.upsert_phone("ph", 4.0, 12.0, pointing=True, heading_deg=ang)          # Suite 309, one wall away
    assert not [o for o in run(w, 4.0) if o["kind"] == "bearing"]


# ---- cold counts ---------------------------------------------------------------------------------------------------

def test_cold_counts_never_carry_ids(twin):
    w = World(twin, seed=12)
    w.load_scenario("crowded")
    w.add_cold_devices("r310", 4)
    obs = run(w, 12.0)
    cold = [o for o in obs if o["kind"] == "cold"]
    assert cold
    for o in cold:
        assert set(o) == COLD_KEYS
        assert all(isinstance(o[b], int) and o[b] >= 0 for b in ("near", "mid", "far"))
        assert "eid" not in o and "key" not in o and "address" not in o
    assert max(o["near"] + o["mid"] + o["far"] for o in cold) > 0


def test_cold_counts_follow_the_devices(twin):
    w = World(twin, seed=13)
    w.load_scenario("empty-room")
    obs = run(w, 12.0)
    assert not [o for o in obs if o["kind"] in ("uwb", "sos", "report", "bearing")]
    cold = [o for o in obs if o["kind"] == "cold"]
    assert cold and all(o["near"] + o["mid"] + o["far"] == 0 for o in cold)
    w2 = World(twin, seed=13)
    w2.load_scenario("empty-room")
    w2.add_cold_devices("r310", 8, kind="wearable")
    tot = [o["near"] + o["mid"] + o["far"] for o in run(w2, 12.0) if o["kind"] == "cold"]
    assert np.mean(tot) >= 5


# ---- ephemeral ids -------------------------------------------------------------------------------------------------

def test_eid_rotation_links_with_prev_eid(twin):
    w = World(twin, seed=21, eid_rotate_s=3.0)
    w.set_pucks([{"id": "P1", "x": 7.0, "y": 6.0}])
    w.upsert_phone("ph", 3.0, 3.0)
    obs = run(w, 10.0)
    seen = []
    for o in obs:
        if "eid" in o and o["eid"] not in seen:
            seen.append(o["eid"])
    assert len(seen) >= 3
    assert all(len(e) == 8 and int(e, 16) >= 0 for e in seen)
    reports = [o for o in obs if o["kind"] == "report"]
    for prev, new in zip(seen, seen[1:]):
        first_new = next(o for o in obs if o.get("eid") == new)
        assert first_new["kind"] == "report" and first_new["prev_eid"] == prev
    assert all(o["prev_eid"] in (None, *seen) for o in reports)


# ---- scenarios -----------------------------------------------------------------------------------------------------

def test_scenario_files_load_against_the_real_twin(twin):
    names = list_scenarios()
    assert {"suite-310", "empty-room", "crowded"} <= set(names)
    for name in names:
        sc = load_scenario_file(name)
        assert sc["simulated"] is True and sc["schema"] == "sandtable.beacon.scenario/1"
        w = World(twin)
        summary = w.load_scenario(name)
        assert summary["room"] == sc["room"]
        truth = w.truth()
        rooms = {r["id"] for r in TWIN["rooms"]}
        for p in truth["pucks"]:
            assert p["room"] == sc["room"]
        declared = {ph["key"]: ph.get("room") for ph in sc["phones"] if "room" in ph}
        for ph in truth["phones"]:
            if ph["key"] in declared:
                assert ph["room"] == declared[ph["key"]]
            assert ph["room"] is None or ph["room"] in rooms
        obs = run(w, 6.0)
        assert all("t" in o and o["kind"] in ("uwb", "sos", "bearing", "report", "cold") for o in obs)


def test_suite_310_demo_contents(twin):
    """The owner's live demo: two door-wall pucks, an iPhone 14 (U1) and an iPhone 15 (U2) in the fire room, one out."""
    from beacon import layout
    w = World(twin)
    w.load_scenario("suite-310")
    assert [(p["x"], p["y"]) for p in w.pucks] == [(q["x"], q["y"]) for q in layout.recommend(twin, "r310", 2, "door-wall")["pucks"]]
    phones = {p["key"]: p for p in w.truth()["phones"]}
    inside = [p for p in phones.values() if p["opt_in"] and p["room"] == "r310"]
    assert len(inside) == 2 and all(p["uwb"] and not p["pointing"] for p in inside)
    assert sorted(p["chip"] for p in inside) == ["U1", "U2"]
    trapped = next(p for p in inside if p["chip"] == "U1")
    injured = next(p for p in inside if p["chip"] == "U2")
    assert trapped["model"] == "iPhone 14" and injured["model"] == "iPhone 15"
    assert trapped["report"]["status"] == "trapped" and trapped["report"]["people"] == 2 and trapped["report"]["can_move"] is False
    assert injured["report"]["status"] == "injured" and injured["report"]["people"] == 1
    assert trapped["report"]["home_suite"] == injured["report"]["home_suite"] == "310"
    out = [p for p in phones.values() if p["opt_in"] and p["report"]["safe_outside"]]
    assert len(out) == 1 and out[0]["report"]["home_suite"] == "310"
    assert sum(not p["opt_in"] for p in phones.values()) >= 3
    assert len(w.truth()["cold"]["devices"]) >= 5
    obs = run(w, 6.0)
    assert not [o for o in obs if o["kind"] == "bearing"]                 # neither iPhone reports a direction by default
    assert {o["eid"] for o in obs if o["kind"] == "uwb"} == {trapped["eid"], injured["eid"]}


def test_scenario_with_a_wrong_room_is_rejected(twin):
    sc = load_scenario_file("suite-310")
    bad = copy.deepcopy(sc)
    bad["phones"][0]["room"] = "r309"
    with pytest.raises(ValueError):
        World(twin).load_scenario(bad)
    bad = copy.deepcopy(sc)
    bad["room"] = "r999"
    with pytest.raises(ValueError):
        World(twin).load_scenario(bad)


# ---- bookkeeping ---------------------------------------------------------------------------------------------------

def test_upsert_move_remove_and_clear(twin):
    w = World(twin, seed=1)
    w.throw_pucks("r310", n=2)
    rec = w.upsert_phone("a", 3.0, 3.0, report={"status": "trapped", "people": 2})
    assert rec["room"] == "r310" and rec["eid"] and rec["simulated"] is True
    run(w, 1.0)
    rec2 = w.upsert_phone("a", 3.2, 3.1, report={"text": "still here"})
    assert rec2["eid"] == rec["eid"] and rec2["report"]["people"] == 2 and rec2["report"]["text"] == "still here"
    w.upsert_phone("b", 4.0, 4.0, opt_in=False)
    assert w.truth()["phones"][1]["eid"] is None
    w.remove_phone("b")
    with pytest.raises(ValueError):
        w.remove_phone("b")
    w.clear()
    assert w.pucks == [] and w.truth()["phones"] == []


def test_noise_defaults_and_mismatch_scaling(twin):
    w = World(twin)
    assert w.noise == models.DEFAULT_NOISE and w.noise is not models.DEFAULT_NOISE
    s = scaled_noise(models.DEFAULT_NOISE, 1.5)
    assert s["uwb"]["sigma_los_m"] == pytest.approx(1.5 * models.DEFAULT_NOISE["uwb"]["sigma_los_m"])
    assert s["uwb"]["max_range_m"] == models.DEFAULT_NOISE["uwb"]["max_range_m"]
    assert w.stated_model()["simulated"] is True


# ---- live-like observations and board sharing ------------------------------------------------------------------------

def test_uwb_is_live_like_by_default_and_los_is_an_opt_in(twin):
    """An iPhone's Nearby Interaction session gives a distance only: no los, rssi or fp unless a study asks for them."""
    obs = {}
    for flag in (None, True):
        w = World(twin, seed=17, reports_los=flag)
        w.set_pucks([{"id": "P1", "x": 7.0, "y": 6.0}, {"id": "P2", "x": 7.0, "y": 2.0}], exact=True)
        w.upsert_phone("ph", 3.0, 3.0)
        obs[flag] = [o for o in run(w, 4.0) if o["kind"] == "uwb"]
    assert obs[None] and all(set(o) == {"kind", "t", "puck", "eid", "d_m"} for o in obs[None])
    assert all({"los", "rssi", "fp"} <= set(o) for o in obs[True])
    assert [o["d_m"] for o in obs[None]] == [o["d_m"] for o in obs[True]]          # paired: same draws either way
    assert World(twin).sim_model["uwb_link"]["reports_los"] is False


def test_board_sharing_turns_serves_one_phone_at_a_time(twin):
    w = World(twin, seed=23, board_sharing="turns")
    assert w.sim_model["board_sharing"] == "turns" and "one phone at a time" in w.sim_model["board_sharing_modes"]["turns"]
    w.set_pucks([{"id": "P1", "x": 9.15, "y": 6.7}, {"id": "P2", "x": 9.15, "y": 2.7}], exact=True)
    w.upsert_phone("a", 6.5, 5.0)
    w.upsert_phone("b", 6.5, 3.5)
    eids = {q["key"]: q["eid"] for q in w.truth()["phones"]}
    obs = [o for o in run(w, 30.0) if o["kind"] == "uwb"]
    both = [p for p in w._pucks_true if all(w._uwb_link_ok(p, w._devices[k]) for k in ("a", "b"))]
    assert both                                                                   # the scene has a puck both phones can use
    for pid in [p["id"] for p in both]:
        seq = [(o["t"], o["eid"]) for o in obs if o["puck"] == pid]
        assert {e for _, e in seq} == set(eids.values())                         # both phones get turns
        bursts = []
        for t, e in seq:
            if bursts and bursts[-1][0] == e and t - bursts[-1][2] < 0.9:
                bursts[-1][2] = t
            else:
                bursts.append([e, t, t])
        for (e1, a1, b1), (e2, a2, b2) in zip(bursts, bursts[1:]):
            assert e1 != e2                                                       # alternates between the phones
            assert a2 - b1 >= 0.9                                                 # connection set-up gap
        assert all(b - a <= 4.0 for _, a, b in bursts)                           # 4 s turns
    # one phone in range: served continuously (no gaps beyond the 5 Hz cadence and dropouts)
    w1 = World(twin, seed=23, board_sharing="turns")
    w1.set_pucks([{"id": "P1", "x": 9.15, "y": 6.7}], exact=True)
    w1.upsert_phone("a", 2.1, 1.8)
    ts = [o["t"] for o in run(w1, 20.0) if o["kind"] == "uwb"]
    assert len(ts) > 0.8 * 5 * 18 and max(b - a for a, b in zip(ts, ts[1:])) < 1.2
    with pytest.raises(ValueError):
        World(twin, board_sharing="sometimes")


def test_pairs_is_the_default_and_ranges_every_phone_continuously(twin):
    w = World(twin, seed=23)
    assert w.sim_model["board_sharing"] == "pairs"
    w.set_pucks([{"id": "P1", "x": 9.15, "y": 6.7}], exact=True)
    w.upsert_phone("a", 2.1, 1.8)
    w.upsert_phone("b", 3.4, 6.4)
    obs = [o for o in run(w, 10.0) if o["kind"] == "uwb"]
    per = Counter(o["eid"] for o in obs)
    assert len(per) == 2 and min(per.values()) > 0.8 * 5 * 10
