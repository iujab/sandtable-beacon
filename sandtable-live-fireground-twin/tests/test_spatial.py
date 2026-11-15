"""Spatial engine: locate, route, exits, registration, walls, anchor plan, hose stretch. Reads the bundle; no hard-coded ids."""
import json
import math

import pytest

from sandtable import config
from sandtable.spatial import Registration, Twin, default_registration, fit_similarity, hose_stretch, plan_anchor_drops


@pytest.fixture(scope="module")
def twin():
    return Twin(json.loads(config.TWIN_JSON.read_text()))


@pytest.fixture(scope="module")
def site():
    return json.loads(config.SITE_JSON.read_text())


def test_every_room_is_reachable_from_each_stair(twin):
    for ex in twin.exits():
        for r in twin.data["rooms"]:
            if r["kind"] == "elevator":
                continue
            assert twin.route(ex["id"], r["id"])["ok"], (ex["id"], r["id"])


def test_locate_suite_corridor_and_outside(twin):
    suite = twin.rooms("suite")[0]
    c = twin.room_centre(suite["id"])
    loc = twin.locate(*c)
    assert loc["room"] == suite["id"] and loc["suite"] == suite["suite"] and loc["off_map_m"] == 0
    corr = twin.rooms("corridor")[0]
    assert twin.locate(*twin.room_centre(corr["id"]))["room_kind"] == "corridor"
    out = twin.locate(-5.0, -5.0)
    assert out["off_map_m"] > 5 and out["room"] is not None


def test_route_is_symmetric_and_blocking_an_edge_gives_another_way(twin):
    strictly_longer = 0
    for suite in twin.rooms("suite"):
        r1, r2 = twin.route("stairA", suite["id"]), twin.route(suite["id"], "stairA")
        assert r1["ok"] and abs(r1["length_m"] - r2["length_m"]) < 0.2
        assert r1["steps"][0]["text"].startswith("Start") and r1["steps"][-1]["text"].startswith("Arrive")
        corridor_edges = [(a, b) for a, b in zip(r1["nodes"], r1["nodes"][1:])
                          if all(twin.node(n)["kind"] == "sweep" and twin.room(twin.node(n)["room"])["kind"] == "corridor" for n in (a, b))]
        if not corridor_edges:
            continue
        a, b = corridor_edges[len(corridor_edges) // 2]
        alt = twin.route("stairA", suite["id"], blocked=[f"{a}-{b}"])
        assert alt["ok"], "the ring corridor must always offer a second way"      # two independent routes to every suite
        assert alt["length_m"] >= r1["length_m"] - 0.05
        assert (a, b) not in list(zip(alt["nodes"], alt["nodes"][1:])) and (b, a) not in list(zip(alt["nodes"], alt["nodes"][1:]))
        strictly_longer += alt["length_m"] > r1["length_m"] + 1
    assert strictly_longer >= 3


def test_blocking_every_corridor_cuts_the_suite_off(twin):
    suite = twin.rooms("suite")[0]
    r = twin.route("stairA", suite["id"], blocked=[c["id"] for c in twin.rooms("corridor")])
    assert not r["ok"] and r["reason"]


def test_exit_picks_the_nearer_stair_and_switches_when_it_is_lost(twin):
    suite = min(twin.rooms("suite"), key=lambda r: twin.route(r["id"], "stairB")["length_m"])
    first = twin.nearest_exit(suite["id"])
    assert first["dst_resolved"]["id"] == "stairB"
    second = twin.nearest_exit(suite["id"], blocked=["stairB"])
    assert second["dst_resolved"]["id"] == "stairA" and second["length_m"] > first["length_m"]
    assert not twin.nearest_exit(suite["id"], blocked=["stairA", "stairB"])["ok"]


def test_suite_number_and_unknown_references(twin):
    suite = twin.rooms("suite")[0]
    assert twin.resolve(suite["suite"])["id"] == suite["id"]
    with pytest.raises(ValueError):
        twin.resolve("no-such-place")
    with pytest.raises(ValueError):
        twin.route("stairA", suite["id"], blocked=["no-such-ref"])


def test_fit_similarity_recovers_scale_rotation_translation_and_mirror():
    truth = Registration(a=8.0 * math.cos(0.4), b=8.0 * math.sin(0.4), tx=3.0, ty=-2.0)
    pts = [(0, 0), (1, 0), (0.3, 1.2), (2.0, 0.7)]
    fit = fit_similarity(pts, [truth.apply(*p) for p in pts])
    assert abs(fit.scale - 8.0) < 1e-6 and abs(fit.rotation_deg - math.degrees(0.4)) < 1e-6 and fit.residual_m < 1e-6
    back = fit.invert(*fit.apply(0.7, 0.9))
    assert abs(back[0] - 0.7) < 1e-9 and abs(back[1] - 0.9) < 1e-9
    mirrored = Registration(a=5.0, b=0.0, tx=1.0, ty=1.0, mirror=True)
    fit_m = fit_similarity(pts, [mirrored.apply(*p) for p in pts], allow_mirror=True)
    assert fit_m.mirror and fit_m.residual_m < 1e-6


def test_default_registration_puts_the_whole_floor_inside_the_anchor_rectangle(twin):
    anchors = {"A1": {"x": 0, "y": 0}, "A2": {"x": 3.6, "y": 0}, "A3": {"x": 1.8, "y": 4.4}}
    reg = default_registration(anchors, twin)
    for p in twin.floor()["outline"]:
        x, y = reg.invert(*p)
        assert -1e-6 <= x <= 3.6 + 1e-6 and -1e-6 <= y <= 4.4 + 1e-6
    assert 5 < reg.scale < 20
    assert abs(default_registration(anchors, twin, scale=1.0).scale - 1.0) < 1e-9


def test_geo_round_trip_and_floor_lands_on_the_real_footprint(twin, site):
    x, y = twin.from_geo(*twin.to_geo(3.3, 7.7))
    assert abs(x - 3.3) < 0.05 and abs(y - 7.7) < 0.05
    outline = twin.floor()["outline"]
    cx, cy = sum(p[0] for p in outline) / 4, sum(p[1] for p in outline) / 4
    lat, lon = twin.to_geo(cx, cy)
    centre = site["frame"]["centre"]
    assert abs(lat - centre["lat"]) * 111320 < 1.0 and abs(lon - centre["lon"]) * 111320 < 1.0


def test_walls_block_and_doors_do_not(twin):
    suite = twin.rooms("suite")[0]
    door = next(d for d in twin.data["doors"] if d["rooms"][0] == suite["id"])
    inside, outside = twin.room_centre(suite["id"]), twin.node(door["node"])["at"]
    other = next(r for r in twin.rooms("suite") if r["id"] != suite["id"])
    assert twin.crosses_wall(inside, twin.room_centre(other["id"]))
    assert twin.walls_crossed(inside, twin.room_centre(other["id"])) >= 1
    assert not twin.crosses_wall(door["at"], [(door["at"][0] + inside[0]) / 2, (door["at"][1] + inside[1]) / 2])


def test_anchor_plan_and_hose_stretch(twin, site):
    plan = plan_anchor_drops(twin, k=4)
    assert len(plan["anchors"]) == 4 and 0 <= plan["line_of_sight_pct"] <= plan["coverage_pct"] <= 100
    assert plan_anchor_drops(twin, k=6)["coverage_pct"] >= plan["coverage_pct"]
    suite = twin.rooms("suite")[0]
    h = hose_stretch(twin, site, suite["id"], "stairA")
    assert h["total_m"] == h["vertical_m"] + h["inside_m"] and h["lengths_50ft"] == math.ceil(h["total_m"] / 15.24)
    full = hose_stretch(twin, site, suite["id"], "stairA", standpipe=False)
    assert full["total_m"] > h["total_m"] and full["outside_m"] == h["outside_m"]


def test_describe_is_deterministic_and_free_of_coordinates(twin):
    a, b = json.dumps(twin.describe(), sort_keys=True), json.dumps(twin.describe(), sort_keys=True)
    assert a == b and '"at"' not in a and '"polygon"' not in a


def test_anchor_plan_is_computed_once_and_handed_out_as_copies(twin):
    a = plan_anchor_drops(twin)
    a["anchors"].clear()
    a["coverage_pct"] = -1
    b = plan_anchor_drops(twin)
    assert b["anchors"] and b["coverage_pct"] >= 0 and b == plan_anchor_drops(Twin(twin.data))     # same as a fresh twin's plan


def test_wall_tests_agree_with_a_brute_force_scan_over_every_wall_and_door(twin):
    """The per-floor indexes and the bounding-box rejection must not change an answer: compare with the plain definition."""
    import random
    from sandtable.spatial import _seg_intersection

    def brute(p0, p1, margin=0.15):
        floor, hits = twin.exercise_floor, []
        for w in twin.data["walls"]:
            hit = _seg_intersection(p0, p1, w["a"], w["b"]) if w["floor"] == floor else None
            if hit is None or any(math.dist(hit, d["at"]) <= d.get("width_m", 0.9) / 2 + margin for d in twin.data["doors"] if d["floor"] == floor):
                continue
            if not any(math.dist(hit, h) < 0.08 for h in hits):
                hits.append(hit)
        return len(hits)
    rnd = random.Random(3)
    xs = [p[0] for p in twin.floor()["outline"]]
    ys = [p[1] for p in twin.floor()["outline"]]
    for _ in range(400):
        p0 = (rnd.uniform(min(xs), max(xs)), rnd.uniform(min(ys), max(ys)))
        p1 = (p0[0] + rnd.uniform(-8, 8), p0[1] + rnd.uniform(-8, 8))
        n = brute(p0, p1)
        assert twin.walls_crossed(p0, p1) == n and twin.crosses_wall(p0, p1) == (n > 0)
