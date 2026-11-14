"""Locator geometry rules on the real twin fixture, with synthetic measurements from beacon.models only.

Nothing here hard-codes a room polygon or a door: the fire room's polygon and door come from the twin. No test depends
on the literal noise values in models.DEFAULT_NOISE.
"""
import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from beacon import locate, models
from beacon.twin import Twin, _pip

ROOT = Path(__file__).resolve().parents[1]
FIRE_ROOM = "r310"
N = models.DEFAULT_NOISE


@pytest.fixture(scope="module")
def twin():
    return Twin(json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text()))


@pytest.fixture(scope="module")
def ctx(twin):
    return locate.Context(twin)


@pytest.fixture(scope="module")
def ctx_free(twin):
    return locate.Context(twin, use_twin=False)


@pytest.fixture(scope="module")
def room(twin):
    """The fire room's frame from the twin: its south-west corner, size, and its door."""
    poly = twin.room(FIRE_ROOM)["polygon"]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    door = next(d for d in twin.data["doors"] if FIRE_ROOM in d["rooms"])
    return {"x0": min(xs), "y0": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys), "door": door["at"], "poly": poly}


def at(room, dx, dy):
    """A point given relative to the fire room's south-west corner."""
    return (room["x0"] + dx, room["y0"] + dy)


def story_pucks(room):
    """The storyboard: pucks thrown near the fire room's door (offsets from the door, into the room)."""
    dx, dy = room["door"]
    offs = [(-1.2, -0.3), (-3.1, 0.7), (-2.3, -2.6)]
    return [{"id": f"P{i + 1}", "x": dx + ox, "y": dy + oy} for i, (ox, oy) in enumerate(offs)]


def pucks_at(*pts):
    return [{"id": f"P{i + 1}", "x": float(x), "y": float(y)} for i, (x, y) in enumerate(pts)]


def uwb(twin, p, phone, rng=None, n=5):
    """A smoothed UWB range from the forward model; noisy when rng is given (one sample: link bias + white noise,
    body block, outlier, as the locator assumes)."""
    d = math.dist((p["x"], p["y"]), phone)
    w = twin.walls_crossed((p["x"], p["y"]), phone)
    u = N["uwb"]
    if rng is None:
        m, _ = models.uwb_expected(d, w, N)
        return {"puck": p["id"], "kind": "uwb", "d_m": m, "los": 0.9 if w == 0 else 0.3, "n": n}
    body = rng.random() < u["body_block_prob"]
    m, s = models.uwb_expected(d, w, N, body=body, n_samples=1)
    v = m + rng.normal(0.0, s)
    if rng.random() < u["outlier_prob"]:
        v += rng.exponential(u["outlier_m"])
    los = 0.9 if (w == 0 and not body) else 0.3
    return {"puck": p["id"], "kind": "uwb", "d_m": max(0.0, v), "los": los, "n": 1}


def ble(twin, p, phone, rng=None, n=10, tx_offset=0.0):
    d = math.dist((p["x"], p["y"]), phone)
    w = twin.walls_crossed((p["x"], p["y"]), phone)
    m, s = models.ble_expected_rssi(d, w, N, n_samples=n)
    if rng is not None:
        b = N["ble"]
        m = m + tx_offset + rng.normal(0.0, math.sqrt(b["shadow_sigma_db"] ** 2 + b["fast_fading_sigma_db"] ** 2 / n))
    return {"puck": p["id"], "kind": "ble", "rssi": m, "n": n}


def mirror(p, a, b):
    ax, ay = a
    bx, by = b
    ux, uy = bx - ax, by - ay
    L = math.hypot(ux, uy)
    ux, uy = ux / L, uy / L
    t = (p[0] - ax) * ux + (p[1] - ay) * uy
    fx, fy = ax + t * ux, ay + t * uy
    return (2 * fx - p[0], 2 * fy - p[1])


def check_shape(e):
    """Invariants every estimate must keep (BEACON_FEED 2.2)."""
    assert e["geometry"] in ("fix", "candidates", "ring", "zone", "none")
    if e["geometry"] != "fix":
        for k in ("x", "y", "sigma_m", "cov", "geo", "node"):
            assert e[k] is None, (k, e["geometry"])
    else:
        assert e["x"] is not None and e["y"] is not None and len(e["cov"]) == 3
    assert 0.0 <= e["confidence"] <= 1.0
    if e["geometry"] != "none":
        assert e["hpd_area_m2"] is not None and e["hpd_area_m2"] > 0
    assert (e["ring"] is not None) == (e["geometry"] == "ring")
    assert (e["zone"] is not None) == (e["geometry"] == "zone")
    assert bool(e["candidates"]) == (e["geometry"] == "candidates")
    if e["candidates"]:
        assert sum(c["weight"] for c in e["candidates"]) == pytest.approx(1.0, abs=0.01)
    json.dumps(e)


# --------------------------------------------------------------------------------------------------------------

def test_grid_walls_match_twin(twin, ctx):
    rng = np.random.default_rng(1)
    agree = total = 0
    for _ in range(15):
        px, py = rng.uniform(0.5, 34.5), rng.uniform(0.5, 42.5)
        W = ctx.walls_from(px, py)
        for _ in range(40):
            j, i = rng.integers(0, ctx.ny), rng.integers(0, ctx.nx)
            want = twin.walls_crossed((px, py), (ctx.X[j, i], ctx.Y[j, i]))
            agree += int(W[j, i] == want)
            total += 1
    assert agree / total >= 0.97


def test_context_grid(ctx, ctx_free, twin):
    assert ctx.valid.any() and ctx.room_idx.max() >= 0
    k = [r["id"] for r in ctx.rooms].index(FIRE_ROOM)
    area = ctx.room_cells[k] * ctx.cell ** 2
    assert area == pytest.approx(twin.room(FIRE_ROOM)["area_m2"], rel=0.1)
    assert ctx_free.valid is None and not ctx_free.use_twin


def test_one_puck_uwb_is_a_ring(twin, ctx, room):
    P = story_pucks(room)[:1]
    phone = at(room, 2.4, 2.1)
    e = locate.estimate(ctx, P, [uwb(twin, P[0], phone)])
    check_shape(e)
    assert e["geometry"] == "ring" and e["method"] == "uwb-twr"
    r = e["ring"]
    assert r["puck"] == "P1" and r["kind"] == "uwb"
    assert r["r_m"] == pytest.approx(math.dist((P[0]["x"], P[0]["y"]), phone), abs=3 * r["sigma_m"])
    assert r["sigma_m"] < 0.5
    assert locate.inside(e, *phone)
    assert e["room"] is None


def test_ring_arcs_exclude_outside_of_building(twin, ctx, room):
    # a puck near the building's outer corner: much of its ring lies outside
    P = pucks_at(at(room, 1.5, 1.5))
    phone = at(room, 4.5, 3.0)
    e = locate.estimate(ctx, P, [uwb(twin, P[0], phone)])
    assert e["geometry"] == "ring"
    r = e["ring"]
    arcs = r["arcs"]
    assert arcs != [[0.0, 360.0]]
    outline = twin.floor()["outline"]
    outside_angles = 0
    for a in range(0, 360):
        t = math.radians(a + 0.5)
        q = (r["cx"] + r["r_m"] * math.cos(t), r["cy"] + r["r_m"] * math.sin(t))
        # well outside the outline (0.6 m margin for the grid and the ring's width)
        q_out = all(not _pip((q[0] + ox, q[1] + oy), outline) for ox in (-0.6, 0, 0.6) for oy in (-0.6, 0, 0.6))
        if q_out:
            outside_angles += 1
            assert not any(a0 <= a + 0.5 <= a1 for a0, a1 in arcs), (a, arcs)
    assert outside_angles > 60
    true_angle = math.degrees(math.atan2(phone[1] - r["cy"], phone[0] - r["cx"])) % 360
    assert any(a0 <= true_angle <= a1 for a0, a1 in arcs)


def test_ring_without_twin_is_a_full_circle(twin, ctx_free, room):
    P = story_pucks(room)[:1]
    e = locate.estimate(ctx_free, P, [uwb(twin, P[0], at(room, 2.4, 2.1))])
    check_shape(e)
    assert e["geometry"] == "ring" and e["ring"]["arcs"] == [[0.0, 360.0]]
    assert e["room"] is None and e["pruned"] == []


def test_two_pucks_two_candidates_when_both_mirrors_are_in_the_room(twin, ctx, room):
    a, b = at(room, 0.84 * room["w"], room["h"] / 2), at(room, 0.53 * room["w"], room["h"] / 2)
    P = pucks_at(a, b)
    phone = at(room, 0.32 * room["w"], 0.17 * room["h"])
    mir = mirror(phone, a, b)
    assert twin.locate(*mir)["room"] == FIRE_ROOM
    e = locate.estimate(ctx, P, [uwb(twin, p, phone) for p in P])
    check_shape(e)
    assert e["geometry"] == "candidates" and len(e["candidates"]) == 2
    pts = [(c["x"], c["y"]) for c in e["candidates"]]
    assert min(math.dist(q, phone) for q in pts) < 0.5
    assert min(math.dist(q, mir) for q in pts) < 0.5
    assert all(c["room"] == FIRE_ROOM for c in e["candidates"]) and e["room"] == FIRE_ROOM
    assert all(abs(c["weight"] - 0.5) < 0.2 for c in e["candidates"])
    assert locate.inside(e, *phone)
    assert "pair_bearing_deg" in e["debug"] and "pair_bearing_alt_deg" in e["debug"]


def test_twin_prunes_the_mirror_outside_the_building(twin, ctx, ctx_free, room):
    # pucks in a line parallel to the outer wall: the mirror of the phone falls outside the building
    a, b = at(room, 1.0, 1.0), at(room, 1.0, 4.0)
    P = pucks_at(a, b)
    phone = at(room, 4.0, 3.0)
    rs = [uwb(twin, p, phone) for p in P]
    free = locate.estimate(ctx_free, P, rs)
    assert free["geometry"] == "candidates"                 # without the twin: honest mirror ambiguity
    e = locate.estimate(ctx, P, rs)
    check_shape(e)
    assert e["geometry"] == "fix"
    assert math.dist((e["x"], e["y"]), phone) < 0.5
    assert len(e["pruned"]) == 1
    pr = e["pruned"][0]
    assert "outside the building" in pr["reason"]
    assert math.dist((pr["x"], pr["y"]), mirror(phone, a, b)) < 0.6


def test_line_of_sight_prunes_the_mirror_behind_the_wall(twin, ctx, room):
    P = story_pucks(room)[:2]
    phone = at(room, 2.4, 2.1)
    mir = mirror(phone, (P[0]["x"], P[0]["y"]), (P[1]["x"], P[1]["y"]))
    mir_room = twin.locate(*mir)
    assert mir_room["room"] != FIRE_ROOM and mir_room["off_map_m"] == 0
    e = locate.estimate(ctx, P, [uwb(twin, p, phone) for p in P])
    check_shape(e)
    assert e["geometry"] == "fix"
    assert math.dist((e["x"], e["y"]), phone) < 0.6
    assert e["room"] == FIRE_ROOM
    assert len(e["pruned"]) == 1
    reason = e["pruned"][0]["reason"]
    assert mir_room["room_label"] in reason and "line-of-sight" in reason


def test_three_non_collinear_pucks_fix(twin, ctx, room):
    P = story_pucks(room)
    phone = at(room, 2.4, 2.1)
    e = locate.estimate(ctx, P, [uwb(twin, p, phone) for p in P])
    check_shape(e)
    assert e["geometry"] == "fix" and e["method"] == "uwb-twr"
    assert math.dist((e["x"], e["y"]), phone) < 0.3
    assert e["sigma_m"] <= locate.geometry_rules["fix_sigma_max_m"]["uwb"]
    assert e["room"] == FIRE_ROOM and e["room_prob"] > 0.9 and e["room_label"] == twin.room(FIRE_ROOM)["label"]
    assert e["node"] is not None and set(e["geo"]) == {"lat", "lon"}
    assert e["pruned"] == []
    assert locate.inside(e, *phone)


def test_collinear_pucks_do_not_give_a_fix(twin, ctx, room):
    y = room["h"] / 2
    P = pucks_at(at(room, 0.84 * room["w"], y), at(room, 0.63 * room["w"], y), at(room, 0.42 * room["w"], y))
    for phone in (at(room, 0.32 * room["w"], 0.17 * room["h"]), at(room, 0.2 * room["w"], 0.3 * room["h"])):
        mir = mirror(phone, (P[0]["x"], P[0]["y"]), (P[2]["x"], P[2]["y"]))
        assert twin.locate(*mir)["room"] == FIRE_ROOM
        e = locate.estimate(ctx, P, [uwb(twin, p, phone) for p in P])
        check_shape(e)
        assert e["geometry"] != "fix"
        assert e["geometry"] == "candidates"


def test_bearing_collapses_one_ring_to_a_fix(twin, ctx, room):
    P = story_pucks(room)[:1]
    phone = at(room, 4.5, 3.0)
    deg = math.degrees(math.atan2(phone[1] - P[0]["y"], phone[0] - P[0]["x"])) % 360
    e0 = locate.estimate(ctx, P, [uwb(twin, P[0], phone)])
    assert e0["geometry"] == "ring"
    e = locate.estimate(ctx, P, [uwb(twin, P[0], phone)], bearing={"puck": "P1", "from_puck_deg": deg, "sigma_deg": 10.0})
    check_shape(e)
    assert e["geometry"] == "fix" and e["method"] == "uwb-twr+bearing"
    assert math.dist((e["x"], e["y"]), phone) < 1.0
    assert e["hpd_area_m2"] < e0["hpd_area_m2"]


def test_wide_bearing_does_not_fake_a_tight_point(twin, ctx, room):
    P = story_pucks(room)[:1]
    phone = at(room, 2.4, 2.1)
    r = math.dist((P[0]["x"], P[0]["y"]), phone)
    deg = math.degrees(math.atan2(phone[1] - P[0]["y"], phone[0] - P[0]["x"])) % 360
    sg = N["bearing"]["sigma_deg"]
    e = locate.estimate(ctx, P, [uwb(twin, P[0], phone)], bearing={"puck": "P1", "from_puck_deg": deg, "sigma_deg": sg})
    check_shape(e)
    if e["geometry"] == "fix":
        assert e["sigma_m"] >= 0.3 * r * math.radians(sg)
    else:
        assert e["geometry"] == "ring"
    assert locate.inside(e, *phone)


def test_bluetooth_only_never_gives_a_tight_fix(twin, ctx, room):
    rng = np.random.default_rng(7)
    story = story_pucks(room)
    for k in range(25):
        phone = at(room, rng.uniform(0.5, room["w"] - 0.5), rng.uniform(0.5, room["h"] - 0.5))
        off = rng.normal(0, N["ble"]["tx_spread_db"])
        one = locate.estimate(ctx, story[:1], [ble(twin, story[0], phone, rng, tx_offset=off)])
        check_shape(one)
        assert one["method"] == "ble-rssi"
        assert one["geometry"] in ("ring", "zone")
        if one["geometry"] == "ring":
            assert one["ring"]["sigma_m"] >= 1.0 and one["ring"]["kind"] == "ble"
        three = locate.estimate(ctx, story, [ble(twin, p, phone, rng, tx_offset=off) for p in story])
        check_shape(three)
        if three["geometry"] == "fix":
            assert three["sigma_m"] >= 1.0
        for c in three["candidates"]:
            assert c["sigma_m"] >= 1.0


@pytest.mark.parametrize("baseline", [0.1, 0.3, 0.5])
def test_two_boards_on_one_device_short_baseline_never_a_tight_fix(twin, ctx, ctx_free, room, baseline):
    rng = np.random.default_rng(int(baseline * 100))
    cx, cy = at(room, 0.63 * room["w"], 0.58 * room["h"])
    for k in range(6):
        ang = math.radians(rng.uniform(190, 260))
        phone = (cx + 5.0 * math.cos(ang), cy + 5.0 * math.sin(ang))
        assert twin.locate(*phone)["room"] == FIRE_ROOM
        P = pucks_at((cx - baseline / 2, cy), (cx + baseline / 2, cy))
        rs = [uwb(twin, p, phone, rng) for p in P]
        for c in (ctx, ctx_free):
            e = locate.estimate(c, P, rs)
            check_shape(e)
            assert "pair_bearing_deg" in e["debug"] and "pair_bearing_alt_deg" in e["debug"]
            if e["geometry"] == "fix":                       # only when the twin removed something, and never tight
                assert e["pruned"] and c.use_twin
                assert e["sigma_m"] >= 0.5
            for cand in e["candidates"]:
                assert cand["sigma_m"] >= 0.3
            assert e["hpd_area_m2"] > 1.0


def test_pair_bearing_from_the_midpoint(twin, ctx, room):
    P = story_pucks(room)[:2]
    phone = at(room, 2.4, 2.1)
    e = locate.estimate(ctx, P, [uwb(twin, p, phone) for p in P])
    mx, my = (P[0]["x"] + P[1]["x"]) / 2, (P[0]["y"] + P[1]["y"]) / 2
    want = math.degrees(math.atan2(phone[1] - my, phone[0] - mx)) % 360
    got = e["debug"]["pair_bearing_deg"]
    assert abs((got - want + 180) % 360 - 180) < 5
    base = math.degrees(math.atan2(P[1]["y"] - P[0]["y"], P[1]["x"] - P[0]["x"]))
    assert e["debug"]["pair_bearing_alt_deg"] == pytest.approx((2 * base - got) % 360, abs=0.2)
    e3 = locate.estimate(ctx, story_pucks(room), [uwb(twin, p, phone) for p in story_pucks(room)])
    assert "pair_bearing_deg" not in e3["debug"]


def test_line_of_sight_hint_reduces_the_ring(twin, ctx, room):
    P = story_pucks(room)[:1]
    phone = at(room, 2.4, 2.1)
    r = uwb(twin, P[0], phone)
    with_los = locate.estimate(ctx, P, [dict(r, los=0.95)])
    without = locate.estimate(ctx, P, [dict(r, los=None)])
    assert with_los["geometry"] == without["geometry"] == "ring"
    assert with_los["hpd_area_m2"] < without["hpd_area_m2"]
    assert with_los["debug"]["room_mass"][FIRE_ROOM] > without["debug"]["room_mass"][FIRE_ROOM]


def test_room_prior_and_zone(twin, ctx, room):
    P = story_pucks(room)[:1]
    phone = at(room, 2.4, 2.1)
    r = ble(twin, P[0], phone)
    e0 = locate.estimate(ctx, P, [r])
    e1 = locate.estimate(ctx, P, [r], prior={"room": FIRE_ROOM, "weight": 0.9})
    check_shape(e0)
    check_shape(e1)
    m0 = e0["debug"]["room_mass"].get(FIRE_ROOM, 0)
    m1 = e1["debug"]["room_mass"].get(FIRE_ROOM, 0)
    assert m1 > m0
    for e in (e0, e1):
        if e["geometry"] == "zone":
            z = e["zone"]
            assert z["band"] in ("near", "mid", "far") and z["r_min_m"] < z["r_max_m"] and len(z["polygon"]) >= 3
            assert e["confidence"] == pytest.approx(z["prob"])


def test_bad_references_raise(twin, ctx, room):
    P = story_pucks(room)
    with pytest.raises(ValueError):
        locate.estimate(ctx, P, [{"puck": "P9", "kind": "uwb", "d_m": 3.0}])
    with pytest.raises(ValueError):
        locate.estimate(ctx, P, [{"puck": "P1", "kind": "laser", "d_m": 3.0}])
    with pytest.raises(ValueError):
        locate.estimate(ctx, P, [{"puck": "P1", "kind": "uwb", "d_m": 3.0}], prior={"room": "nowhere"})
    with pytest.raises(ValueError):
        locate.estimate(ctx, P, [{"puck": "P1", "kind": "uwb", "d_m": 3.0}], bearing={"puck": "P7", "from_puck_deg": 10})
    e = locate.estimate(ctx, P, [])
    assert e["geometry"] == "none" and e["method"] == "ble-presence"


def test_heat_grid(twin, ctx, room):
    P = story_pucks(room)
    e = locate.estimate(ctx, P, [uwb(twin, p, at(room, 2.4, 2.1)) for p in P], want_heat=True)
    h = e["heat"]
    assert set(h) == {"origin", "cell_m", "nx", "ny", "values"}
    assert len(h["values"]) == h["nx"] * h["ny"]
    assert max(h["values"]) == pytest.approx(1.0) and min(h["values"]) >= 0.0


@pytest.mark.parametrize("n_pucks", [1, 3])
def test_confidence_coverage(twin, ctx, room, n_pucks):
    """Over 200 noisy trials the truth lies inside the published geometry about as often as `confidence` says."""
    rng = np.random.default_rng(100 + n_pucks)
    P = story_pucks(room)[:n_pucks]
    hits, confs, geoms = [], [], {}
    for _ in range(200):
        phone = at(room, rng.uniform(0.4, room["w"] - 0.4), rng.uniform(0.4, room["h"] - 0.4))
        if min(math.dist(phone, (p["x"], p["y"])) for p in P) < 1.0:
            phone = at(room, rng.uniform(0.4, 4.0), rng.uniform(0.4, 4.0))
        e = locate.estimate(ctx, P, [uwb(twin, p, phone, rng) for p in P])
        check_shape(e)
        geoms[e["geometry"]] = geoms.get(e["geometry"], 0) + 1
        hits.append(locate.inside(e, *phone))
        confs.append(e["confidence"])
    rate, mean_conf = float(np.mean(hits)), float(np.mean(confs))
    assert abs(rate - mean_conf) < 0.12, (rate, mean_conf, geoms)
    assert rate > 0.6
    if n_pucks == 1:
        assert set(geoms) == {"ring"}
    else:
        assert geoms.get("fix", 0) > 150


def test_one_estimate_under_30_ms(twin, ctx, room):
    P = story_pucks(room)
    phone = at(room, 2.4, 2.1)
    cases = [(P[:1], [uwb(twin, P[0], phone)], None),
             (P, [uwb(twin, p, phone) for p in P], None),
             (P[:2], [uwb(twin, p, phone) for p in P[:2]], None),
             (P[:1], [ble(twin, P[0], phone)], None)]
    for pk, rs, br in cases:
        locate.estimate(ctx, pk, rs, br)                    # warm the per-puck wall cache
        ts = []
        for _ in range(7):
            t0 = time.perf_counter()
            locate.estimate(ctx, pk, rs, br)
            ts.append(time.perf_counter() - t0)
        assert sorted(ts)[3] < 0.030, (rs, sorted(ts))
