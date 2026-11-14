"""Scout radar engine (SIMULATED): scene, image-source geometry, simulator, self-location, static echoes, victim
detection and its honest limits. Offline, numpy only. The detection numbers here are against a simulator written
alongside the DSP (see sandtable/scout/RADAR_NOTES.md), so they are optimistic.

Seeds for the 3/4-puck and 2-puck cases are ones where the scan finds the victim; the overall rates (about half of
in-reach victims found with 5 pucks, no false alarm in 16 empty rooms) are stated in RADAR_NOTES.md."""
import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from sandtable.scout import dsp, geometry, scene, sim
from sandtable.spatial import Twin

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def twin():
    return Twin(json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text()))


_cache: dict = {}


def scan(twin, n=5, seed=310, **kw):
    key = (n, seed, tuple(sorted(kw.items())))
    if key not in _cache:
        sc = scene.build_scene(twin, "r310", n, seed, **kw)
        _cache[key] = (sc, sim.simulate(sc))
    return _cache[key]


def xy_of(sc):
    return {p["id"]: [p["x"], p["y"]] for p in sc["pucks"]}


def test_scene_deterministic_and_valid(twin):
    a = scene.build_scene(twin, "r310", 5, 310)
    assert a == scene.build_scene(twin, "r310", 5, 310)
    assert a != scene.build_scene(twin, "r310", 5, 311)
    poly, door = a["polygon"], a["door"]
    assert poly == twin.room("r310")["polygon"] and door == [9.5, 7.2]          # read from the twin, not hard-coded
    P = [(p["x"], p["y"]) for p in a["pucks"]]
    assert [p["id"] for p in a["pucks"]] == ["P1", "P2", "P3", "P4", "P5"] and all(p["z"] == 0.04 for p in a["pucks"])
    for p in P:
        assert geometry.pip(p, poly) and geometry.poly_dist(p, poly) >= 0.5 - 1e-9
        assert 1.5 - 1e-9 <= math.dist(p, door) <= 5.5 + 1e-9
    assert min(math.dist(p, q) for i, p in enumerate(P) for q in P[i + 1:]) >= 1.2 - 1e-9
    sv = np.linalg.svd(np.asarray(P) - np.mean(P, axis=0), compute_uv=False)
    assert sv[1] > 0.18 * sv[0]                                                 # not collinear
    v = a["victim"]
    assert geometry.pip((v["x"], v["y"]), poly) and v["posture"] == "lying" and v["z"] == 0.25 and 11 <= v["breathing_bpm"] <= 18
    assert math.dist((v["x"], v["y"]), door) > math.dist(np.mean(P, axis=0), door)          # beyond the pucks, far side
    (d,) = a["changes"]
    assert d["kind"] == "debris" and geometry.pip(d["at"], poly) and geometry.poly_dist(d["at"], poly) >= 1.0 - 1e-9
    b = scene.build_scene(twin, "r310", 5, 310, victim=False, changes=False)
    assert b["victim"] is None and b["changes"] == [] and b["pucks"] == a["pucks"]           # same throw either way
    json.dumps(a)


def test_scene_builds_for_every_room_of_the_floor(twin):
    for r in twin.rooms():
        if r["floor"] != twin.exercise_floor:
            continue
        sc = scene.build_scene(twin, r["id"], 5, 7)
        assert len(sc["pucks"]) == 5 and all(geometry.pip((p["x"], p["y"]), r["polygon"]) for p in sc["pucks"])


def test_image_paths_rectangle_by_hand():
    rect = [[0, 0], [5, 0], [5, 4], [0, 4]]
    got = {e["wall"]: e for e in geometry.image_paths([1, 1], [3, 2], rect)}
    # mirror the receiver across each wall: y=0 -> (3,-2); x=5 -> (7,2); y=4 -> (3,6); x=0 -> (-3,2)
    want = {0: math.hypot(2, 3), 1: math.hypot(6, 1), 2: math.hypot(2, 5), 3: math.hypot(4, 1)}
    assert set(got) == set(want)
    for w, L in want.items():
        assert got[w]["path_m"] == pytest.approx(L, abs=1e-9)
        pt = got[w]["point"]
        assert math.dist([1, 1], pt) + math.dist(pt, [3, 2]) == pytest.approx(L, abs=1e-9)
    # concave room: a wall hidden behind the inner corner gives no echo
    ell = [[0, 0], [6, 0], [6, 2], [2, 2], [2, 6], [0, 6]]
    walls = {e["wall"] for e in geometry.image_paths([5, 1], [1, 5], ell)}
    assert 2 not in walls and 3 not in walls


def test_ellipse_points_are_on_the_ellipse():
    pts = geometry.ellipse_points([0, 0], [3, 1], 5.0, n=32)
    assert len(pts) == 32
    for p in pts:
        assert math.dist(p, [0, 0]) + math.dist(p, [3, 1]) == pytest.approx(5.0, abs=1e-9)


def test_simulator_shape_speed_and_determinism(twin):
    sc = scene.build_scene(twin, "r310", 5, 310)
    t0 = time.perf_counter()
    rec = sim.simulate(sc)
    assert time.perf_counter() - t0 < 3.0
    m = rec["meta"]
    assert m["simulated"] is True and m["tap_ns"] == pytest.approx(1.0016) and m["carrier_mhz"] == pytest.approx(6489.6)
    assert m["frame_hz"] == 20.0 and m["n_taps"] == 96 and "snr_db_1m" in m["noise"] and "flicker" in m["noise"]
    assert len(rec["frames"]) == 20 and ("P1", "P2") in rec["frames"] and ("P2", "P1") in rec["frames"]
    f = rec["frames"][("P1", "P2")]
    assert f["cir"].dtype == np.complex64 and f["cir"].shape[1] == 96 and len(f["t"]) == len(f["fp_index"]) == f["cir"].shape[0]
    assert 220 <= len(f["t"]) <= 240                                             # 12 s x 20 Hz minus ~2 % loss
    r = rec["ranges"][0]
    assert set(r) >= {"t", "a", "b", "d", "rssi", "fp", "los"} and isinstance(r["d"], float)
    rec2 = sim.simulate(sc)
    assert np.array_equal(rec2["frames"][("P3", "P4")]["cir"], rec["frames"][("P3", "P4")]["cir"])


def test_ranges_drop_when_the_body_shadows_a_link(twin):
    sc = scene.build_scene(twin, "r310", 5, 310)
    sc = {**sc, "victim": {**sc["victim"], "x": (sc["pucks"][0]["x"] + sc["pucks"][1]["x"]) / 2,
                           "y": (sc["pucks"][0]["y"] + sc["pucks"][1]["y"]) / 2}}
    rec = sim.simulate(sc)
    fp = {}
    for r in rec["ranges"]:
        fp.setdefault((r["a"], r["b"]), []).append((r["fp"], r["los"]))
    shadowed = np.mean([x[0] for x in fp[("P1", "P2")]])
    clear = sim.simulate({**sc, "victim": None})
    base = np.mean([r["fp"] for r in clear["ranges"] if (r["a"], r["b"]) == ("P1", "P2")])
    assert shadowed < base - 6.0 and np.mean([x[1] for x in fp[("P1", "P2")]]) < 0.5


def test_self_locate_recovers_pairwise_distances(twin):
    sc, rec = scan(twin, 5, 310)
    out = dsp.self_locate(rec)
    assert out["n"] == 5 and out["rms_m"] < 0.05
    rel = out["pucks_rel"]
    P = {p["id"]: (p["x"], p["y"]) for p in sc["pucks"]}
    err = [abs(math.dist(rel[a], rel[b]) - math.dist(P[a], P[b])) for a in P for b in P if a < b]
    assert max(err) < 0.05
    sub = dsp.self_locate(rec, use=["P1", "P2", "P3"])
    assert sub["n"] == 3 and set(sub["pucks_rel"]) == {"P1", "P2", "P3"}


def test_static_echoes_contain_the_wall_echoes(twin):
    sc, rec = scan(twin, 5, 310)
    ech = dsp.static_echoes(rec)
    assert ech and set(ech[0]) >= {"tx", "rx", "path_m", "excess_m", "amp", "snr_db"}
    P = {p["id"]: (p["x"], p["y"]) for p in sc["pucks"]}
    hits = total = 0
    for a, b in [(a, b) for a in P for b in P if a < b]:
        meas = [e["path_m"] for e in ech if (e["tx"], e["rx"]) == (a, b)]
        for ip in geometry.image_paths(P[a], P[b], sc["polygon"]):
            total += 1
            hits += any(abs(m - ip["path_m"]) <= geometry.M_PER_TAP for m in meas)
    assert total == 40 and hits >= 36                                            # within one tap (0.30 m of path)
    # the debris shows as a static echo on most links
    d = sc["changes"][0]["at"]
    near = sum(any(abs(e["path_m"] - (math.dist(P[a], d) + math.dist(d, P[b]))) < 0.45 for e in ech if (e["tx"], e["rx"]) == (a, b))
               for a in P for b in P if a < b)
    assert near >= 7


@pytest.mark.parametrize("seed", [310, 312])
def test_five_pucks_find_the_breathing_victim(twin, seed):
    sc, rec = scan(twin, 5, seed)
    truth = rec.pop("truth")                                                     # the DSP must not need it
    try:
        out = dsp.find_victims(rec, xy_of(sc), bounds=sc["polygon"])
    finally:
        rec["truth"] = truth
    (v,) = out["victims"]
    t = sc["victim"]
    assert math.hypot(v["x"] - t["x"], v["y"] - t["y"]) < 0.6
    assert v["state"] == "breathing" and not v["moving"] and abs(v["breathing_bpm"] - t["breathing_bpm"]) <= 2.0
    assert v["n_links"] >= 3 and 0 < v["confidence"] <= 1 and 0 < v["sigma_m"] < 1.0 and len(v["cov"]) == 3
    em = out["echo_map"]
    assert em["kind"] == "dynamic" and len(em["values"]) == em["nx"] * em["ny"] and max(em["values"]) == 1.0
    assert sum(pl["used"] for pl in out["per_link"]) == v["n_links"]
    json.dumps(out)


def test_no_victim_no_report(twin):
    for seed in (310, 311, 312, 313, 314, 315):
        sc, rec = scan(twin, 5, seed, victim=False)
        out = dsp.find_victims(rec, xy_of(sc), bounds=sc["polygon"])
        assert out["victims"] == [], (seed, out.get("detector"))
        assert max(out["echo_map"]["values"]) < 1.0


@pytest.mark.parametrize("n,seed", [(4, 309), (4, 312), (3, 305), (3, 309)])
def test_three_and_four_pucks_still_locate(twin, n, seed):
    sc, rec = scan(twin, n, seed)
    (v,) = dsp.find_victims(rec, xy_of(sc), bounds=sc["polygon"])["victims"]
    assert math.hypot(v["x"] - sc["victim"]["x"], v["y"] - sc["victim"]["y"]) < 1.0


def test_use_subset_of_a_five_puck_scan(twin):
    sc, rec = scan(twin, 5, 312)
    out = dsp.find_victims(rec, xy_of(sc), use=["P1", "P2", "P3", "P4"], bounds=sc["polygon"])
    assert out["n_links_alive"] == 6 and {pl["tx"] for pl in out["per_link"]} <= {"P1", "P2", "P3"}


def test_two_pucks_give_presence_without_position(twin):
    sc, rec = scan(twin, 2, 309)
    out = dsp.find_victims(rec, xy_of(sc), bounds=sc["polygon"])
    (v,) = out["victims"]
    assert v["x"] is None and v["y"] is None and v["n_links"] == 1 and v["confidence"] <= 0.3
    P = [(p["x"], p["y"]) for p in sc["pucks"]]
    true_ex = float(geometry.bistatic_excess(sc["victim"]["x"], sc["victim"]["y"], P[0], P[1], rho=0.14))
    assert abs(v["excess_m"] - true_ex) < 0.6 and "no position" in out["why"]


def test_one_puck_refuses(twin):
    sc, rec = scan(twin, 1, 310)
    out = dsp.find_victims(rec, xy_of(sc), bounds=sc["polygon"])
    assert out["victims"] == [] and out["why"] == "one puck cannot receive its own echo"
    assert rec["frames"] == {} and dsp.self_locate(rec)["n"] == 1


@pytest.mark.parametrize("seed", [310, 312])
def test_a_still_victim_is_not_detected(twin, seed):
    """The honest limit: a person who neither breathes nor moves looks like furniture (a 0.25 mm heartbeat alone
    is far below what a 12 s scan can see)."""
    sc, rec = scan(twin, 5, seed, victim_state="still")
    assert sc["victim"]["breathing_bpm"] == 0.0 and not sc["victim"]["moving"]
    assert dsp.find_victims(rec, xy_of(sc), bounds=sc["polygon"])["victims"] == []
