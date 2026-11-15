"""Regression tests from the code-quality pass over the locator and the simulator.

- two SIMULATOR bugs (each test failed before its fix): the battery never drained when the simulated clock started at
  t = 0, and a phone dragged slowly (small moves, as the phone page posts them every 250 ms) reported `still: true`;
- exactness of two locator speed-ups: `confidence` splits only the cells that can touch the published region, and
  the wall distances of the pruned-point reasons are computed for all walls at once. Both must give the same numbers as
  the straightforward computation, not merely close ones.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from beacon import locate
from beacon.sim import World
from beacon.twin import Twin

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def twin():
    return Twin(json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text()))


def _world(twin) -> World:
    w = World(twin, seed=5)
    w.set_pucks([{"id": "P1", "x": 7.0, "y": 6.0}])           # same room as the phone below (Suite 310)
    return w


# ---- simulator ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("t0", [0.0, 1000.0])
def test_battery_drain_does_not_depend_on_the_clock_origin(twin, t0):
    """1 % per 10 minutes from the first step, also when that step is at t = 0 (it was taken as 'no start time')."""
    w = _world(twin)
    w.upsert_phone("ph", 3.0, 3.0, report={"battery_pct": 50})
    reps = []
    for k in range(0, 1201):                                   # 20 simulated minutes at 1 Hz
        reps += [o["report"] for o in w.step(t0 + k) if o["kind"] == "report"]
    assert len(reps) > 100
    assert reps[0]["battery_pct"] == 50
    assert reps[-1]["battery_pct"] == 48


def _drag(w: World, key: str, path, t: float, dt: float = 0.25) -> tuple[list[dict], float]:
    """Move the phone through `path` one point every dt (the phone page posts the dragged position every 250 ms)."""
    reps = []
    for x, y in path:
        w.upsert_phone(key, x, y)
        t += dt
        reps += [o for o in w.step(t) if o["kind"] == "report"]
    return reps, t


def test_slowly_dragged_phone_is_not_reported_still(twin):
    w = _world(twin)
    w.upsert_phone("ph", 3.0, 2.0)
    t = 1000.0
    for _ in range(80):                                        # 8 s standing: still
        t += 0.1
        w.step(t)
    # 0.4 m/s: 0.1 m per post, less than the 0.2 m a single move needed to count before the fix
    start = t
    reps, t = _drag(w, "ph", [(3.0, 2.0 + 0.1 * i) for i in range(1, 51)], t)
    during = [o["report"] for o in reps if o["t"] >= start + 1.0]
    assert during, "the drag lasted 12.5 s: reports every 5 s"
    assert all(r["still"] is False for r in during), [(r["still"], r["still_s"]) for r in during]
    assert all(r["still_s"] < 1.0 for r in during)
    # standing again: still after still_after_s
    after, t = _drag(w, "ph", [(3.0, 7.0)] * 40, t)
    assert after and after[-1]["report"]["still"] is True


def test_jitter_in_place_is_still(twin):
    """Re-posting the same spot with centimetre jitter is not movement."""
    w = _world(twin)
    w.upsert_phone("ph", 3.0, 3.0)
    t = 1000.1
    w.step(t)
    reps, t = _drag(w, "ph", [(3.0 + 0.01 * (i % 3), 3.0 - 0.01 * (i % 2)) for i in range(60)], t)
    late = [o["report"] for o in reps if o["t"] >= 1000.1 + 6.0]
    assert late and all(r["still"] is True for r in late)


# ---- locator: exact speed-ups ---------------------------------------------------------------------------------------

def _in_arcs_ref(theta_deg, arcs):
    t = np.mod(theta_deg, 360.0)
    ok = np.zeros(np.shape(t), dtype=bool)
    for a0, a1 in arcs:
        ok |= (t >= a0) & (t <= a1)
    return ok


def _pred_ref(res, x, y):
    """The region tests as first written (no shortcuts)."""
    k = locate.geometry_rules["region_k_sigma"]
    g = res["geometry"]
    if g == "fix":
        c = res["cov"]
        Ci = np.linalg.inv(np.array([[c[0], c[1]], [c[1], c[2]]]) + np.eye(2) * 1e-12)
        dx, dy = x - res["x"], y - res["y"]
        return Ci[0, 0] * dx * dx + 2 * Ci[0, 1] * dx * dy + Ci[1, 1] * dy * dy <= k * k
    if g == "candidates":
        ok = np.zeros(np.shape(x), dtype=bool)
        for c in res["candidates"]:
            ok |= np.hypot(x - c["x"], y - c["y"]) <= k * c["sigma_m"]
        return ok
    r = res["ring"]
    d = np.hypot(x - r["cx"], y - r["cy"])
    th = np.degrees(np.arctan2(y - r["cy"], x - r["cx"]))
    return (np.abs(d - r["r_m"]) <= k * r["sigma_m"]) & _in_arcs_ref(th, r["arcs"])


def _supersample_ref(P, X, Y, cell, res):
    sel = P > P.max() * 1e-9
    w, x, y = P[sel], X[sel], Y[sel]
    off = ((np.arange(4) + 0.5) / 4.0 - 0.5) * cell
    ox, oy = np.meshgrid(off, off)
    frac = _pred_ref(res, x[:, None] + ox.ravel()[None, :], y[:, None] + oy.ravel()[None, :]).mean(axis=1)
    return float((w * frac).sum())


REGIONS = [
    {"geometry": "ring", "ring": {"cx": 9.1, "cy": 7.3, "r_m": 4.2, "sigma_m": 0.35, "arcs": [[10.0, 96.0], [200.0, 262.0]]}},
    {"geometry": "ring", "ring": {"cx": 9.1, "cy": 7.3, "r_m": 2.6, "sigma_m": 0.8, "arcs": [[0.0, 360.0]]}},
    {"geometry": "ring", "ring": {"cx": 0.0, "cy": 0.0, "r_m": 5.0, "sigma_m": 0.2, "arcs": [[0.0, 4.0], [356.0, 360.0]]}},
    {"geometry": "fix", "x": 6.0, "y": 8.0, "cov": [0.9, 0.5, 0.4]},
    {"geometry": "fix", "x": 12.3, "y": 3.3, "cov": [0.02, 0.0, 0.02]},
    {"geometry": "candidates", "candidates": [{"x": 3.0, "y": 4.0, "sigma_m": 0.6}, {"x": 15.0, "y": 11.0, "sigma_m": 1.1}]},
]


@pytest.mark.parametrize("res", REGIONS, ids=lambda r: r["geometry"])
def test_confidence_cell_filter_is_exact(res):
    rng = np.random.default_rng(3)
    cell = 0.25
    X, Y = np.meshgrid(0.125 + cell * np.arange(80), 0.125 + cell * np.arange(60))
    P = rng.random(X.shape) ** 4 + 1e-6                        # every cell carries mass: a wrongly skipped cell shows
    P /= P.sum()
    region = locate._region_for(res)
    pred, near = region
    assert near is not None
    hit = near(X.ravel(), Y.ravel(), locate._SUB_REACH * cell)
    assert 0 < hit.sum() < hit.size                           # the filter does skip cells
    assert locate._supersample_mass(P, X, Y, cell, region) == _supersample_ref(P, X, Y, cell, res)
    pts = rng.uniform(-1.0, 21.0, size=(2, 4000))
    assert np.array_equal(pred(pts[0], pts[1]), _pred_ref(res, pts[0], pts[1]))


def test_degenerate_fix_ellipse_skips_the_cell_filter():
    pred, near = locate._region_for({"geometry": "fix", "x": 1.0, "y": 1.0, "cov": [0.0001, 0.0001, 0.0001]})
    assert near is None


def test_wall_cache_never_serves_another_puck_position(twin):
    """The per-puck wall grid was cached under the position rounded to 1 cm: a puck 8 mm from one seen before got that
    puck's grid, so an estimate depended on what the context had computed earlier (and the evaluation's numbers on
    which trials a worker process happened to run first)."""
    a, b = (4.864, 8.0), (4.856, 8.0)                          # same 1 cm bucket, different walls behind door edges
    exact_b = locate.Context(twin).walls_from(*b)
    assert not np.array_equal(locate.Context(twin).walls_from(*a), exact_b)
    ctx = locate.Context(twin)
    ctx.walls_from(*a)
    assert np.array_equal(ctx.walls_from(*b), exact_b)
    assert ctx.walls_from(*b) is ctx.walls_from(*b)            # still cached for the same position


def test_wall_distances_match_the_per_segment_formula(twin):
    ctx = locate.Context(twin)
    rng = np.random.default_rng(11)
    pts = [tuple(rng.uniform([ctx.bbox[0] - 2, ctx.bbox[1] - 2], [ctx.bbox[2] + 2, ctx.bbox[3] + 2])) for _ in range(300)]
    pts += [((ax + bx) / 2, (ay + by) / 2) for ax, ay, bx, by in ctx.wall_segs]          # on the walls themselves
    for x, y in pts:
        ref = [float(locate._seg_dist_vec(np.array(x), np.array(y), *seg)) for seg in ctx.wall_segs]
        assert locate._wall_dists(ctx, x, y).tolist() == ref
        in_ref = (not any(np.hypot(x - dx, y - dy) <= hw for dx, dy, hw in ctx.doors)
                  and any(d < locate.geometry_rules["wall_half_thickness_m"] for d in ref))
        assert locate._in_wall(ctx, x, y) == in_ref
