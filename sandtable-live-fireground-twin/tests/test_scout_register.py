"""Scout registration and twin comparison (sandtable/scout/register.py, mismatch.py).

Offline and synthetic: a private copy of the image-source computation generates wall echoes for a
known puck layout (plus noise, missed echoes, spurious peaks and an extra reflector), the layout is
hidden behind a random rotation, mirror and offset, and the code must find it again. Every echo in
these tests is SIMULATED here; nothing is measured.
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import pytest

from sandtable.scout import mismatch, register

RES_M = 0.3
ROOM310 = [[9.5, 0.0], [9.5, 8.65], [0.0, 8.65], [0.0, 0.0]]       # the fixture's Suite 310 (tests only; code reads the twin)
DOOR310 = [9.5, 7.2]
L_ROOM = [[0.0, 0.0], [8.0, 0.0], [8.0, 4.0], [4.0, 4.0], [4.0, 8.0], [0.0, 8.0]]
L_DOOR = [8.0, 2.0]


# --- private image-source copy (independent of geometry.image_paths and of register._wall_paths) ---

def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _seg_cross(p, q, a, b, eps=1e-6):
    r, s = (q[0] - p[0], q[1] - p[1]), (b[0] - a[0], b[1] - a[1])
    den = _cross(r, s)
    if abs(den) < 1e-12:
        return False
    ap = (a[0] - p[0], a[1] - p[1])
    u, v = _cross(ap, s) / den, _cross(ap, r) / den
    return eps < u < 1 - eps and eps < v < 1 - eps


def image_paths_ref(tx, rx, polygon):
    """First-order specular wall reflections by the image-source method, with occlusion by other walls."""
    P = [tuple(map(float, p)) for p in polygon]
    n = len(P)
    area2 = sum(P[i][0] * P[(i + 1) % n][1] - P[(i + 1) % n][0] * P[i][1] for i in range(n))
    sgn = 1.0 if area2 > 0 else -1.0
    out = []
    for k in range(n):
        a, b = P[k], P[(k + 1) % n]
        L = math.dist(a, b)
        t = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        nv = (-sgn * t[1], sgn * t[0])                                  # inward normal
        hi = (tx[0] - a[0]) * nv[0] + (tx[1] - a[1]) * nv[1]
        hj = (rx[0] - a[0]) * nv[0] + (rx[1] - a[1]) * nv[1]
        if hi <= 0 or hj <= 0:
            continue
        img = (rx[0] - 2 * hj * nv[0], rx[1] - 2 * hj * nv[1])          # rx mirrored in the wall line
        f = hi / (hi + hj)
        pt = (tx[0] + f * (img[0] - tx[0]), tx[1] + f * (img[1] - tx[1]))
        s = (pt[0] - a[0]) * t[0] + (pt[1] - a[1]) * t[1]
        if s < 0 or s > L:
            continue
        if any(_seg_cross(tx, pt, P[m], P[(m + 1) % n]) or _seg_cross(pt, rx, P[m], P[(m + 1) % n]) for m in range(n) if m != k):
            continue
        out.append({"wall": k, "path_m": math.dist(tx, img), "point": [pt[0], pt[1]]})
    return out


def synth_echoes(pucks: dict, polygon, rng, noise=0.06, p_miss=0.12, spur_rate=0.6, reflectors=(), drop_walls=(),
                 max_path=18.0, both_dirs=False):
    """SIMULATED static echoes in the contract's Echo shape. Peaks closer than one resolution cell merge;
    peaks inside the direct path's main lobe (excess < 0.3 m) are lost."""
    out = []
    for a, b in combinations(sorted(pucks), 2):
        pa, pb = pucks[a], pucks[b]
        d = math.dist(pa, pb)
        raw = [(p["path_m"], 1.0) for p in image_paths_ref(pa, pb, polygon) if p["wall"] not in drop_walls]
        for (x, y), refl in reflectors:
            raw.append((math.dist(pa, (x, y)) + math.dist((x, y), pb), refl))
        peaks = []
        for L, g in raw:
            if rng.random() < p_miss or L > max_path:
                continue
            peaks.append((L + rng.normal(0, noise), g / max(L, 1.0) ** 2))
        for _ in range(rng.poisson(spur_rate)):
            peaks.append((d + rng.uniform(0.5, 12.0), 0.004))
        peaks.sort()
        merged = []
        for L, amp in peaks:
            if merged and L - merged[-1][0] < RES_M:
                if amp > merged[-1][1]:
                    merged[-1] = (L, amp)
                continue
            merged.append((L, amp))
        for L, amp in merged:
            if L - d < RES_M:
                continue
            e = {"tx": a, "rx": b, "path_m": round(L, 3), "excess_m": round(L - d, 3), "amp": amp,
                 "snr_db": round(10 * math.log10(amp / 0.001), 1)}
            out.append(e)
            if both_dirs:
                out.append(dict(e, tx=b, rx=a, path_m=round(L + rng.normal(0, 0.03), 3)))
    return out


def scatter_pucks(rng, polygon, door, n, near=(1.0, 4.5), wall_clear=0.4, sep=0.9):
    """Where thrown pucks come to rest: inside the room, a few metres from the door, apart from each other."""
    from sandtable.scout.register import _Room
    room = _Room(polygon)
    pts = []
    for _ in range(20000):
        p = np.array([rng.uniform(room.lo[0], room.hi[0]), rng.uniform(room.lo[1], room.hi[1])])
        dd = math.dist(p, door)
        if not (near[0] <= dd <= near[1]) or not room.inside(p[None], wall_clear)[0]:
            continue
        if all(math.dist(p, q) >= sep for q in pts):
            pts.append(p)
            if len(pts) == n:
                break
    assert len(pts) == n
    return {f"P{k + 1}": [float(p[0]), float(p[1])] for k, p in enumerate(pts)}


def hide_pose(pucks: dict, rng, mirror: bool, loc_noise=0.05, theta=None):
    """The relative frame self-location gives: random rotation, optional mirror, offset, plus MDS noise."""
    th = rng.uniform(0, 2 * math.pi) if theta is None else theta
    c, s = math.cos(th), math.sin(th)
    off = rng.uniform(-3, 3, 2)
    out = {}
    for k, (x, y) in pucks.items():
        y2 = -y if mirror else y
        out[k] = [c * x - s * y2 + off[0] + rng.normal(0, loc_noise), s * x + c * y2 + off[1] + rng.normal(0, loc_noise)]
    return out


def max_err(reg_pucks, truth):
    return max(math.dist(reg_pucks[k], truth[k]) for k in truth)


def apply_transform(T, p):
    m = -1.0 if T["mirror"] else 1.0
    th = math.radians(T["rotation_deg"])
    x, y = p[0], m * p[1]
    return [math.cos(th) * x - math.sin(th) * y + T["tx"], math.sin(th) * x + math.cos(th) * y + T["ty"]]


# --- registration ------------------------------------------------------------------------

@pytest.mark.parametrize("n,mirror,seed", [(5, False, 1), (5, True, 2), (4, False, 3), (4, True, 4), (3, False, 5), (3, True, 6)])
def test_register_recovers_pose_rectangle(n, mirror, seed):
    rng = np.random.default_rng(seed)
    truth = scatter_pucks(rng, ROOM310, DOOR310, n)
    echoes = synth_echoes(truth, ROOM310, rng, both_dirs=(seed % 2 == 0))
    rel = hide_pose(truth, rng, mirror)
    out = register.register(rel, echoes, {"polygon": ROOM310, "door": DOOR310, "label": "Suite 310"})
    assert out["ok"] and out["method"] == "twin-echo-match", out["text"]
    assert max_err(out["pucks"], truth) < 0.4, (out["text"], out["pucks"], truth)
    assert out["transform"]["mirror"] == mirror
    for k in rel:                                  # the reported transform reproduces the reported positions
        assert math.dist(apply_transform(out["transform"], rel[k]), out["pucks"][k]) < 0.01
    assert out["residual_m"] < 0.2
    if n >= 4:
        assert out["ambiguity"] < 0.85
    else:                                          # three pucks: right, but it may honestly not be sure
        assert out["ambiguity"] < 0.85 or "CANNOT TELL APART" in out["text"]
    assert out["runtime_s"] < 3.0


def test_door_prior_breaks_rectangle_symmetry():
    """Echoes alone fit the mirror image equally (exact symmetry, no noise, no misses): the door decides, and says so."""
    rng = np.random.default_rng(11)
    # its mirror image in the room's east-west axis (y -> 8.65 - y) is still inside the 6.5 m throw limit, ~2.5 m further out
    truth = {"P1": [8.3, 5.9], "P2": [7.2, 6.4], "P3": [6.8, 4.6], "P4": [8.4, 4.3], "P5": [5.9, 5.6]}
    echoes = synth_echoes(truth, ROOM310, rng, noise=0.0, p_miss=0.0, spur_rate=0.0)
    rel = hide_pose(truth, rng, mirror=False, loc_noise=0.0)
    out = register.register(rel, echoes, {"polygon": ROOM310, "door": DOOR310})
    assert out["ok"] and max_err(out["pucks"], truth) < 0.3, out["text"]
    assert out["echo_ambiguity"] > 0.9                      # the echoes cannot tell
    assert out["ambiguity"] < 0.85 and out["door_decided"]   # the door can, and the text says it was the door
    assert "door" in out["text"]
    assert out["alternative"] is not None


def test_symmetric_door_is_reported_ambiguous():
    """Door in the middle of a wall: the mirror pose is as close to the door. Never pretend: ambiguity ~1."""
    rng = np.random.default_rng(12)
    room = [[0.0, 0.0], [8.0, 0.0], [8.0, 6.0], [0.0, 6.0]]
    door = [8.0, 3.0]
    truth = {"P1": [6.8, 3.9], "P2": [5.4, 2.2], "P3": [6.6, 1.5], "P4": [4.6, 4.1]}
    echoes = synth_echoes(truth, room, rng, noise=0.0, p_miss=0.0, spur_rate=0.0)
    rel = hide_pose(truth, rng, mirror=False, loc_noise=0.0)
    out = register.register(rel, echoes, {"polygon": room, "door": door})
    assert out["ambiguity"] > 0.85
    assert "CANNOT TELL APART" in out["text"] and out["alternative"] is not None
    assert not out["confident"]


@pytest.mark.parametrize("seed", [21, 22])
def test_register_l_shaped_room(seed):
    rng = np.random.default_rng(seed)
    truth = scatter_pucks(rng, L_ROOM, L_DOOR, 5, near=(1.0, 5.0))
    echoes = synth_echoes(truth, L_ROOM, rng)
    rel = hide_pose(truth, rng, mirror=bool(seed % 2))
    out = register.register(rel, echoes, {"polygon": L_ROOM, "door": L_DOOR})
    assert out["ok"], out["text"]
    assert max_err(out["pucks"], truth) < 0.4, (out["text"], out["pucks"], truth)


def test_too_few_echoes_is_not_a_confident_pose():
    rng = np.random.default_rng(31)
    truth = scatter_pucks(rng, ROOM310, DOOR310, 4)
    echoes = synth_echoes(truth, ROOM310, rng, spur_rate=0.0)[:3]
    out = register.register(hide_pose(truth, rng, False), echoes, {"polygon": ROOM310, "door": DOOR310})
    assert out["ambiguity"] >= 0.85 and not out["confident"]
    assert out["method"] == "door-prior" and not out["ok"] and "GUESS" in out["text"]
    for k in truth:                                               # still a usable drawing: inside the room, near the door
        assert 0 <= out["pucks"][k][0] <= 9.5 and 0 <= out["pucks"][k][1] <= 8.65


def test_sparse_echoes_raise_ambiguity():
    """Few echoes on 3 pucks: whatever pose wins, the figure must not claim certainty when it is wrong."""
    worst_wrong_conf = 0.0
    for seed in range(40, 46):
        rng = np.random.default_rng(seed)
        truth = scatter_pucks(rng, ROOM310, DOOR310, 3)
        echoes = synth_echoes(truth, ROOM310, rng, p_miss=0.6, spur_rate=0.3)[:5]
        out = register.register(hide_pose(truth, rng, bool(seed % 2)), echoes, {"polygon": ROOM310, "door": DOOR310})
        if max_err(out["pucks"], truth) > 0.6 and out["confident"]:
            worst_wrong_conf = max(worst_wrong_conf, 1 - out["ambiguity"])
    assert worst_wrong_conf == 0.0


def test_no_room_falls_back_to_puck_frame():
    rng = np.random.default_rng(41)
    truth = scatter_pucks(rng, ROOM310, DOOR310, 5)
    rel = hide_pose(truth, rng, False)
    out = register.register(rel, synth_echoes(truth, ROOM310, rng), None)
    assert out["method"] == "box-fit" and not out["ok"]
    assert out["pucks"] == {k: [round(v[0], 3), round(v[1], 3)] for k, v in rel.items()}
    assert "own frame" in out["text"]


def test_constellation_that_cannot_fit_falls_back():
    rng = np.random.default_rng(42)
    truth = {"P1": [0, 0], "P2": [12, 0], "P3": [0, 11]}          # bigger than the room
    out = register.register(truth, synth_echoes(truth, [[-1, -1], [13, -1], [13, 12], [-1, 12]], rng),
                            {"polygon": ROOM310, "door": DOOR310})
    assert out["method"] == "door-prior" and not out["ok"] and out["ambiguity"] == 1.0


def test_vectorised_paths_match_reference():
    from sandtable.scout.register import _Room, _wall_paths
    rng = np.random.default_rng(5)
    for poly in (ROOM310, L_ROOM):
        room = _Room(poly)
        for _ in range(30):
            pts = []
            while len(pts) < 2:
                p = np.array([rng.uniform(0, 9.5), rng.uniform(0, 8.65)])
                if room.inside(p[None], 0.05)[0]:
                    pts.append(p)
            path, _ = _wall_paths(room, np.array(pts)[None], np.array([0]), np.array([1]))
            mine = sorted(float(v) for v in path[0, 0] if not np.isnan(v))
            ref = sorted(p["path_m"] for p in image_paths_ref(pts[0], pts[1], poly))
            assert np.allclose(mine, ref, atol=1e-6), (poly, pts, mine, ref)


def test_geometry_image_paths_agrees_when_available():
    """R1's geometry.image_paths and this file's private copy must agree on a convex room."""
    geometry = pytest.importorskip("sandtable.scout.geometry")
    rng = np.random.default_rng(6)
    for _ in range(20):
        a, b = [float(rng.uniform(0.3, 9.2)), float(rng.uniform(0.3, 8.3))], [float(rng.uniform(0.3, 9.2)), float(rng.uniform(0.3, 8.3))]
        theirs = sorted(p["path_m"] for p in geometry.image_paths(a, b, ROOM310))
        mine = sorted(p["path_m"] for p in image_paths_ref(a, b, ROOM310))
        assert np.allclose(theirs, mine, atol=1e-6)


# --- box fit (no twin) -------------------------------------------------------------------

def test_fit_box_clean_rectangle_in_rotated_frame():
    rng = np.random.default_rng(51)
    truth = scatter_pucks(rng, ROOM310, DOOR310, 5)
    echoes = synth_echoes(truth, ROOM310, rng, noise=0.03, p_miss=0.0, spur_rate=0.0)
    th = math.radians(37.0)
    rot = lambda p: [math.cos(th) * p[0] - math.sin(th) * p[1], math.sin(th) * p[0] + math.cos(th) * p[1]]
    rel = {k: rot(v) for k, v in truth.items()}
    out = register.fit_box(rel, echoes)
    poly = out["polygon"]
    from sandtable.scout.register import _Room
    box = _Room(poly)
    assert box.inside(np.array(list(rel.values())), 0.0).all()
    true_corners = [rot(p) for p in ROOM310]
    for c in true_corners:
        assert min(math.dist(c, q) for q in poly) < 1.2, (poly, true_corners)
    assert out["confidence"] > 0.5 and "oarse" in out["text"]


def test_fit_box_without_echoes_is_labelled():
    out = register.fit_box({"P1": [0, 0], "P2": [2, 0], "P3": [1, 1.5]}, [])
    assert out["confidence"] == 0.0 and "Not a map" in out["text"]


# --- mismatches ----------------------------------------------------------------------------

def _registered(seed, n=5, **kw):
    rng = np.random.default_rng(seed)
    truth = scatter_pucks(rng, ROOM310, DOOR310, n)
    echoes = synth_echoes(truth, ROOM310, rng, **kw)
    return truth, echoes


@pytest.mark.parametrize("seed", [61, 62, 63])
def test_debris_reflector_is_flagged(seed):
    debris = (3.6, 6.1)
    truth, echoes = _registered(seed, reflectors=[(debris, 0.8)])
    out = mismatch.find_mismatches(truth, echoes, ROOM310)
    refl = [m for m in out if m["kind"] == "unexpected_reflector"]
    assert refl, out
    best = min(refl, key=lambda m: math.dist(m["where"]["at"], debris))
    assert math.dist(best["where"]["at"], debris) < 0.8, refl
    assert best["pairs"] >= 3 and 0 < best["confidence"] <= 1
    assert "not in the twin" in best["text"]
    assert len(refl) == 1, refl


def test_clean_room_has_no_false_alarms():
    alarms = []
    for seed in range(70, 82):
        truth, echoes = _registered(seed, n=5 if seed % 3 else 4)
        alarms += mismatch.find_mismatches(truth, echoes, ROOM310, door=DOOR310)
    assert alarms == [], alarms


def test_missing_wall_is_flagged():
    truth, echoes = _registered(91, drop_walls=(1,), p_miss=0.1)                # wall 1 = the north wall, y = 8.65
    out = mismatch.find_mismatches(truth, echoes, ROOM310, door=DOOR310)
    walls = [m for m in out if m["kind"] == "missing_wall"]
    assert len(walls) == 1, out
    w = walls[0]["where"]
    assert w["kind"] == "segment" and abs(w["a"][1] - 8.65) < 1e-6 and abs(w["b"][1] - 8.65) < 1e-6
    assert "north" in walls[0]["text"]


def test_mismatch_texts_use_compass_and_tolerance():
    truth, echoes = _registered(61, reflectors=[((3.6, 6.1), 0.8)])
    out = mismatch.find_mismatches(truth, echoes, ROOM310)
    m = out[0]
    assert any(w in m["text"] for w in ("north", "south", "east", "west"))
    assert m["tol_m"] == 0.45 and "0.3 m" in m["text"]
