"""Scout scene: the TRUTH of one simulated throw (room from the twin, where the pucks landed, the victim, what
differs from the twin). Everything here is ground truth for the simulator and for evaluation only; the DSP, the
registration, the session and the UI never read it.

Deterministic for a given twin, room and seed. Pucks, victim, debris and furniture each draw from their own seeded
stream, so the pucks land in the same places with or without a victim or debris (that makes false-alarm tests fair).
Works for any room polygon of any twin (rectangular or not); constraints relax step by step in small rooms and the
relaxation is written into `notes`.

Person model numbers (radar cross-section 0.4-0.9 m^2, body radius 0.14 m, 3 % breathing-rate wander, 0.25 mm
heartbeat) are L08's (`tagless-uwb-echo-radar/src/tagless/sim/core.py`); a lying posture is new here.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g
from .sim import DEFAULT_NOISE

DURATION_S = 12.0
FRAME_HZ = 20.0
DEFAULT_HEIGHT_M = 2.7


def _twin(twin):
    if hasattr(twin, "room") and hasattr(twin, "data"):
        return twin
    from ..spatial import Twin
    return Twin(twin)


def _door_of(tw, room: dict) -> tuple[list[float], str | None]:
    """The room's door (prefer one onto a corridor), else the middle of its longest wall."""
    doors = [d for d in tw.data.get("doors", []) if room["id"] in d.get("rooms", [])]
    if doors:
        def key(d):
            other = [r for r in d["rooms"] if r != room["id"]]
            kind = tw._rooms.get(other[0], {}).get("kind") if other else None
            return (kind != "corridor", d["id"])
        d = sorted(doors, key=key)[0]
        return [float(d["at"][0]), float(d["at"][1])], d["id"]
    poly = room["polygon"]
    n = len(poly)
    i = max(range(n), key=lambda k: math.dist(poly[k], poly[(k + 1) % n]))
    a, b = poly[i], poly[(i + 1) % n]
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], None


def _inward(door, poly) -> np.ndarray:
    """Unit vector from the door into the room: normal of the nearest wall, pointing inside."""
    n = len(poly)
    best, nrm = 1e9, np.array([1.0, 0.0])
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        d = g.seg_dist(door, a, b)
        if d < best:
            e = np.array([b[0] - a[0], b[1] - a[1]], float)
            e /= max(np.linalg.norm(e), 1e-9)
            cand = np.array([-e[1], e[0]])
            probe = np.asarray(door, float) + 0.3 * cand
            if not g.pip(probe, poly):
                cand = -cand
            best, nrm = d, cand
    return nrm


def _collinear(pts: list) -> bool:
    if len(pts) < 3:
        return False
    P = np.asarray(pts, float)
    P = P - P.mean(axis=0)
    s = np.linalg.svd(P, compute_uv=False)
    return s[1] < 0.18 * s[0]


def _place_pucks(rng, n, door, inward, poly, avoid, notes):
    """Pucks thrown from the door: 1.5-5.5 m in, within +-60 degrees of the throw direction (toward the room's
    centre), >= 1.2 m apart, >= 0.5 m from walls, not collinear. Relaxed in steps when the room is too small."""
    stages = [(1.5, 5.5, 1.2, 0.5, 60), (1.0, 5.5, 1.0, 0.4, 90), (0.8, 6.5, 0.8, 0.3, 120), (0.5, 8.0, 0.6, 0.25, 180)]
    for si, (rmin, rmax, sep, wall, half) in enumerate(stages):
        for _attempt in range(40):
            pts = []
            tries = 0
            while len(pts) < n and tries < 600:
                tries += 1
                r = rng.uniform(rmin, rmax)
                a = math.radians(rng.uniform(-half, half))
                ca, sa = math.cos(a), math.sin(a)
                v = np.array([ca * inward[0] - sa * inward[1], sa * inward[0] + ca * inward[1]])
                p = np.asarray(door, float) + r * v
                if not g.pip(p, poly) or g.poly_dist(p, poly) < wall:
                    continue
                if any(math.dist(p, q) < sep for q in pts) or any(math.dist(p, q) < rad for q, rad in avoid):
                    continue
                pts.append([float(p[0]), float(p[1])])
            if len(pts) == n and not _collinear(pts):
                if si:
                    notes.append(f"small room: puck scatter relaxed to {rmin}-{rmax} m from the door, {sep} m apart, {wall} m from walls")
                return pts
    raise ValueError("room too small to scatter the pucks")


def _far_point(rng, poly, door, clear, avoid, frac_far=0.55, accept=None):
    """A point inside the room, `clear` from the walls, in the part far from the door (and passing `accept`)."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    dmax = max(math.dist(door, p) for p in poly)
    for far, cl in ((frac_far, clear), (0.4, clear * 0.8), (0.25, clear * 0.6), (0.0, 0.2)):
        for _ in range(2000):
            p = (rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys)))
            if not g.pip(p, poly) or g.poly_dist(p, poly) < cl:
                continue
            if math.dist(p, door) < far * dmax:
                continue
            if any(math.dist(p, q) < rad for q, rad in avoid):
                continue
            if accept is not None and not accept(p):
                continue
            return [float(p[0]), float(p[1])]
    raise ValueError("no free floor left in the room")


def build_scene(twin, room_id: str, n_pucks: int = 5, seed: int = 310, victim: bool = True, changes: bool = True,
                victim_state: str = "breathing", furniture: bool = True, victim_reach_m: float | None = 4.5) -> dict:
    """Ground truth for one simulated throw. `victim_state`: "breathing" (default), "still" (neither breathing nor
    moving: the honest limit), "moving" (crawling). Extra keys beyond the contract: door_id, inward, clutter, notes."""
    tw = _twin(twin)
    room = tw.room(room_id)
    poly = [[float(p[0]), float(p[1])] for p in room["polygon"]]
    door, door_id = _door_of(tw, room)
    inward = _inward(door, poly)
    # a crew throws toward the middle of the room: aim the scatter at the room's centre, unless that points out
    cen = np.mean(np.asarray(poly, float), axis=0)
    aim = cen - np.asarray(door, float)
    if np.linalg.norm(aim) > 0.5 and float(aim @ inward) > 0.2 * np.linalg.norm(aim):
        throw = aim / np.linalg.norm(aim)
    else:
        throw = inward
    notes: list[str] = []
    if n_pucks < 1:
        raise ValueError("n_pucks must be at least 1")

    # pucks first (their own stream, so they land in the same places with or without a victim or debris)
    rp = np.random.default_rng([int(seed), 1])
    pucks_xy = _place_pucks(rp, int(n_pucks), door, throw, poly, avoid=[], notes=notes)

    # the victim lies in the far part of the room. By default also within `victim_reach_m` of the nearest puck:
    # a 12 s scan under L08's noise model reaches about 4-5 m (RADAR_NOTES.md); victim_reach_m=None places the
    # victim anywhere in the far part, which is the honest test of that limit.
    rv = np.random.default_rng([int(seed), 2])
    reach = None if victim_reach_m is None else float(victim_reach_m)
    vxy = None
    for frac in (0.45, 0.3, 0.0):
        try:
            cand = _far_point(rv, poly, door, clear=0.6, avoid=[(q, 1.2) for q in pucks_xy], frac_far=frac,
                              accept=(lambda p: min(math.dist(p, q) for q in pucks_xy) <= reach) if reach else None)
        except ValueError:
            continue
        vxy = cand
        if frac != 0.45:
            notes.append(f"victim placed nearer the door than usual (room shape or reach {reach} m)")
        break
    if vxy is None:
        vxy = _far_point(rv, poly, door, clear=0.3, avoid=[(q, 0.8) for q in pucks_xy], frac_far=0.0)
        notes.append("victim placement relaxed: no free floor within reach of the pucks")
    heading = float(rv.uniform(0, 360))
    bpm = round(float(rv.uniform(11.0, 18.0)), 1)
    rcs = round(float(rv.uniform(0.4, 0.9)), 2)

    rd = np.random.default_rng([int(seed), 3])
    dxy = _far_point(rd, poly, door, clear=1.0, avoid=[(vxy, 1.6)] + [(q, 0.9) for q in pucks_xy], frac_far=0.15)

    rf = np.random.default_rng([int(seed), 4])
    clutter = []
    if furniture:
        nz = DEFAULT_NOISE
        for k in range(int(nz["furniture_n"])):
            for _ in range(500):
                i = int(rf.integers(len(poly)))
                a, b = poly[i], poly[(i + 1) % len(poly)]
                s = rf.uniform(0.1, 0.9)
                e = np.array([b[0] - a[0], b[1] - a[1]], float)
                L = float(np.linalg.norm(e))
                if L < 0.5:
                    continue
                nrm = np.array([-e[1], e[0]]) / L
                base = np.array([a[0] + s * e[0], a[1] + s * e[1]])
                off = rf.uniform(0.3, 0.6)
                p = base + off * nrm
                if not g.pip(p, poly):
                    p = base - off * nrm
                if not g.pip(p, poly) or math.dist(p, door) < 1.0 or any(math.dist(p, q) < 0.5 for q in pucks_xy) \
                        or math.dist(p, vxy) < 1.0:
                    continue
                lo, hi = nz["furniture_rcs_m2"]
                clutter.append({"kind": "furniture", "at": [float(p[0]), float(p[1])], "z": round(float(rf.uniform(0.3, 1.0)), 2),
                                "rcs_m2": round(float(10 ** rf.uniform(math.log10(lo), math.log10(hi))), 3),
                                "note": "desk or shelf along a wall: part of the real room, not drawn in the twin's polygons; weak"})
                break

    v = None
    if victim:
        v = {"x": vxy[0], "y": vxy[1], "z": g.TARGET_Z_M, "posture": "lying",
             "breathing_bpm": bpm if victim_state in ("breathing", "moving") else 0.0,
             "moving": victim_state == "moving", "state": victim_state,
             "heading_deg": round(heading, 1), "rcs_m2": rcs, "body_radius_m": 0.14,
             "breath_mm": DEFAULT_NOISE["breath_mm"] if victim_state != "still" else 0.0,
             "heart_mm": DEFAULT_NOISE["heart_mm"]}
    ch = []
    if changes:
        ch.append({"kind": "debris", "at": dxy, "radius_m": 0.6, "rcs_m2": DEFAULT_NOISE["debris_rcs_m2"], "z": 0.5,
                   "note": "metal-heavy debris pile (a toppled filing cabinet): a strong static reflector where the twin shows open floor"})
    return {"room": room_id, "room_label": room.get("label", room_id), "floor": room.get("floor"),
            "polygon": poly, "door": door, "door_id": door_id, "inward": [float(inward[0]), float(inward[1])],
            "height_m": float(room.get("height_m") or DEFAULT_HEIGHT_M),
            "pucks": [{"id": f"P{i + 1}", "x": p[0], "y": p[1], "z": g.PUCK_Z_M} for i, p in enumerate(pucks_xy)],
            "victim": v, "changes": ch, "clutter": clutter,
            "duration_s": DURATION_S, "frame_hz": FRAME_HZ, "seed": int(seed),
            "noise": dict(DEFAULT_NOISE), "notes": notes, "victim_reach_m": reach, "simulated": True}
