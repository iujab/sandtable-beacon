"""Where to put two pucks so that two rings give one answer (lead-owned).

Two pucks leave two candidate points, mirror images across the line through the pucks. If both pucks sit close
to ONE wall of the room, that line runs along the wall, and the mirror image of anybody in the room falls on the
far side of that wall:

* "door-wall": both pucks just inside the wall the door is in (a crew at the door slides one puck along the wall
  and drops the other at the door). The mirror image falls beyond that wall: in the corridor, where the crew is
  standing, or in the suite next door. The twin removes it when the ranges do not fit a spot behind a wall;
  otherwise it is dismissed only on stated evidence (the occupant's suite, or command's search room), visibly.
* "exterior-wall": both pucks along an outside wall of the building. The mirror image falls outside the building,
  which the twin removes by itself. Harder to place from the door; natural when the outside wall is reachable.
* "careless" (for comparison in the evaluation): pucks in the middle of the room; the mirror image stays inside
  the room and nothing can dismiss it.

Geometry is worst for a phone close to the line through the pucks, so the pucks go on the wall the people are
farthest from. Pure geometry on the twin; no I/O.
"""
from __future__ import annotations

import math

INSET_M = 0.35          # how far inside the wall the pucks stand
SPACING_M = 4.0         # distance between the two pucks along the wall (clamped to the wall)
END_MARGIN_M = 0.6      # keep clear of the room's corners
MIN_BASELINE_M = 3.0    # a door wall with less usable length than this is too short: use the long wall nearest the door


def _edges(poly):
    n = len(poly)
    return [((float(poly[i][0]), float(poly[i][1])), (float(poly[(i + 1) % n][0]), float(poly[(i + 1) % n][1]))) for i in range(n)]


def _centroid(poly):
    return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))


def _point_seg_dist(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)), t


def _on_outline(edge, outline, tol=0.05):
    """True when the whole edge lies on the floor outline (an outside wall of the building)."""
    def on(p):
        return any(_point_seg_dist(p, a, b)[0] <= tol for a, b in _edges(outline))
    mid = ((edge[0][0] + edge[1][0]) / 2, (edge[0][1] + edge[1][1]) / 2)
    return on(edge[0]) and on(edge[1]) and on(mid)


def _along_wall(edge, centroid, t_values, inset):
    (ax, ay), (bx, by) = edge
    L = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / L, (by - ay) / L
    nx, ny = -uy, ux                                   # a normal; flip it to point into the room
    mx, my = (ax + bx) / 2, (ay + by) / 2
    if (centroid[0] - mx) * nx + (centroid[1] - my) * ny < 0:
        nx, ny = -nx, -ny
    return [[round(ax + ux * t + nx * inset, 2), round(ay + uy * t + ny * inset, 2)] for t in t_values], L


def _wall_name(edge, centroid):
    mx, my = (edge[0][0] + edge[1][0]) / 2, (edge[0][1] + edge[1][1]) / 2
    dx, dy = mx - centroid[0], my - centroid[1]
    if abs(dx) >= abs(dy):
        return "east wall" if dx > 0 else "west wall"
    return "north wall" if dy > 0 else "south wall"


def recommend(twin, room_id: str, n: int = 2, strategy: str = "door-wall", spacing_m: float = SPACING_M, inset_m: float = INSET_M) -> dict:
    """Puck positions for `room_id`. strategy: "door-wall" | "exterior-wall" | "careless".

    Returns {"room", "strategy", "pucks": [{"id", "x", "y"}], "wall", "spacing_m", "mirror_falls", "certain": bool, "why"}.
    `certain` is True when the twin alone removes the mirror image (it falls outside the building).
    """
    room = twin.room(room_id)
    poly = [[float(p[0]), float(p[1])] for p in room["polygon"]]
    c = _centroid(poly)
    floor = twin.floor(room.get("floor"))
    outline = floor.get("outline") or poly
    edges = _edges(poly)
    door = next((d for d in twin.data.get("doors", []) if room_id in (d.get("rooms") or [])), None)

    if strategy == "careless":
        r = 0.18 * min(max(p[0] for p in poly) - min(p[0] for p in poly), max(p[1] for p in poly) - min(p[1] for p in poly))
        pts = [[round(c[0] - r, 2), round(c[1] - r, 2)], [round(c[0] + r, 2), round(c[1] + r, 2)]]
        return {"room": room_id, "strategy": strategy, "pucks": [{"id": f"P{i + 1}", "x": p[0], "y": p[1]} for i, p in enumerate(pts)],
                "wall": None, "spacing_m": round(math.dist(pts[0], pts[1]), 2), "mirror_falls": "inside the room", "certain": False,
                "why": "Comparison layout: both pucks mid-room. The mirror image stays inside the room and nothing can dismiss it."}

    if strategy == "exterior-wall":
        ext = [e for e in edges if _on_outline(e, outline)]
        if not ext:
            strategy = "door-wall"
        else:
            if door is not None:           # the outside wall farthest from the door: people at the windows are then not on the line
                edge = max(ext, key=lambda e: _point_seg_dist((float(door["at"][0]), float(door["at"][1])), e[0], e[1])[0])
            else:
                edge = max(ext, key=lambda e: math.dist(e[0], e[1]))
            L = math.dist(edge[0], edge[1])
            s = min(spacing_m, max(1.0, L - 2 * END_MARGIN_M))
            ts = [L / 2 - s / 2, L / 2 + s / 2]
            pts, _ = _along_wall(edge, c, ts, inset_m)
            return {"room": room_id, "strategy": "exterior-wall", "pucks": [{"id": f"P{i + 1}", "x": p[0], "y": p[1]} for i, p in enumerate(pts)],
                    "wall": _wall_name(edge, c) + " (outside wall)", "spacing_m": round(s, 2), "mirror_falls": "outside the building", "certain": True,
                    "why": f"Both pucks {inset_m:.2f} m inside the {_wall_name(edge, c)}, {s:.1f} m apart. The mirror image of anyone in the room is outside the building, so the twin removes it."}

    # door-wall (default)
    on_door_wall = True
    if door is None:
        edge, t_door = max(edges, key=lambda e: math.dist(e[0], e[1])), None
    else:
        dp = (float(door["at"][0]), float(door["at"][1]))

        def usable(e):
            return max(0.0, math.dist(e[0], e[1]) - 2 * END_MARGIN_M)

        door_edge = min(edges, key=lambda e: _point_seg_dist(dp, e[0], e[1])[0])
        edge = door_edge
        if usable(door_edge) < MIN_BASELINE_M:
            # the wall the door is in is too short for a useful distance between the pucks (small rooms, real scans):
            # take the wall near the door that gives the longest one
            edge = max(edges, key=lambda e: min(usable(e), spacing_m) - 0.25 * _point_seg_dist(dp, e[0], e[1])[0])
        on_door_wall = edge is door_edge
        t_door = _point_seg_dist(dp, edge[0], edge[1])[1] * math.dist(edge[0], edge[1])
    L = math.dist(edge[0], edge[1])
    s = min(spacing_m, max(1.0, L - 2 * END_MARGIN_M))
    if t_door is None:
        ts = [L / 2 - s / 2, L / 2 + s / 2]
    else:
        # first puck beside the door, second one slid along the wall toward its longer side
        direction = 1.0 if (L - t_door) >= t_door else -1.0
        t1 = min(max(t_door + direction * 0.5, END_MARGIN_M), L - END_MARGIN_M)
        t2 = min(max(t1 + direction * s, END_MARGIN_M), L - END_MARGIN_M)
        ts = [t1, t2]
        s = abs(t2 - t1)
    pts, _ = _along_wall(edge, c, ts, inset_m)
    beyond = "the corridor"
    if door is not None:
        other = [r for r in (door.get("rooms") or []) if r != room_id]
        if other:
            try:
                beyond = "the " + (twin.room(other[0]).get("label") or "corridor").lower()
            except Exception:
                pass
    if not on_door_wall:
        return {"room": room_id, "strategy": "door-wall", "pucks": [{"id": f"P{i + 1}", "x": p[0], "y": p[1]} for i, p in enumerate(pts)],
                "wall": _wall_name(edge, c) + " (the long wall nearest the door)", "spacing_m": round(s, 2), "mirror_falls": "beyond that wall, outside the room", "certain": False,
                "why": (f"The wall the door is in is too short to separate two pucks, so both go {inset_m:.2f} m inside the long wall nearest the door, {s:.1f} m apart. "
                        f"The mirror image of anyone in the room falls beyond that wall, outside the room; the twin removes it when the ranges do not fit a spot behind a wall, "
                        f"otherwise it is dismissed on the occupant's stated suite or command's search room, and shown as dismissed.")}
    return {"room": room_id, "strategy": "door-wall", "pucks": [{"id": f"P{i + 1}", "x": p[0], "y": p[1]} for i, p in enumerate(pts)],
            "wall": _wall_name(edge, c) + " (the wall the door is in)", "spacing_m": round(s, 2), "mirror_falls": "beyond the door wall: " + beyond + " or the next suite", "certain": False,
            "why": (f"Both pucks {inset_m:.2f} m inside the wall the door is in, {s:.1f} m apart: one beside the door, one slid along the wall. "
                    f"The mirror image of anyone in the room falls beyond that wall, outside the room (in {beyond} or the suite next door); the twin removes it when the ranges do not fit a spot behind a wall, otherwise it is dismissed on the occupant's stated suite or command's search room, and shown as dismissed.")}
