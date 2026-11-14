"""Stated evidence may dismiss candidates, visibly (lead-owned).

The locator publishes what the physics supports: with two pucks that is usually two candidate points, mirror images
across the line between the pucks. The twin removes a candidate by itself only when it is physically impossible
(outside the building, inside a wall). This module handles the softer case: a candidate that is possible in the
twin but contradicts something that was SAID:

* the occupant's own statement in the app ("I am in Suite 310"), or
* a decision by command ("we are searching Suite 310; the crew is standing in the corridor and it is empty").

A candidate dismissed this way is never silently dropped: it moves to `pruned` with the reason, and the phone carries
an `assumptions` list that the board shows next to the position. If the statement leaves two or more candidates, or
contradicts every candidate, the geometry stays what it was: two candidates stay two candidates.

Pure function over the locator's result dict (docs/BEACON_FEED.md Phone fields); no I/O.
"""
from __future__ import annotations

import copy


def apply_statement(est: dict, room_id, room_label: str | None, basis: str, kind: str = "occupant-statement") -> dict:
    """Return a copy of `est` in which candidates outside `room_id` are dismissed on the strength of `basis`.

    est:   a locator / tracker result (`geometry`, `candidates`, `pruned`, ...).
    room_id: one room id, or a list of room ids when a suite is several rooms of the twin.
    basis: human-readable, e.g. "the occupant says they are in Suite 310".
    kind:  "occupant-statement" | "search-room".
    """
    if not room_id or est.get("geometry") != "candidates":
        return est
    rooms = {room_id} if isinstance(room_id, str) else set(room_id)       # a suite may be several rooms of the twin
    cands = est.get("candidates") or []
    inside = [c for c in cands if c.get("room") in rooms]
    outside = [c for c in cands if c.get("room") not in rooms]
    if not outside:
        return est                                  # nothing to dismiss: both candidates are in that room
    out = copy.deepcopy(est)
    if not inside:
        out["conflict"] = f"No candidate is in {room_label or 'that room'}, although {basis}: showing every candidate"
        return out
    label = room_label or (room_id if isinstance(room_id, str) else "that suite")
    out.setdefault("pruned", [])
    for c in outside:
        where = c.get("room_label") or "outside any room"
        out["pruned"].append({"x": c.get("x"), "y": c.get("y"),
                              "reason": f"in {where}, not in {label}: {basis}", "by": kind,
                              "weight": c.get("weight")})
    geometry_share = round(sum(float(c.get("weight") or 0.0) for c in inside), 2)
    assumption = {"kind": kind, "room": room_id if isinstance(room_id, str) else sorted(rooms)[0], "rooms": sorted(rooms),
                  "text": f"Mirror candidate dismissed because {basis}. On the measurements alone it was "
                          f"{int(round((1.0 - geometry_share) * 100))}% likely."}
    out.setdefault("assumptions", []).append(assumption)
    if len(inside) == 1:
        c = inside[0]
        s = float(c.get("sigma_m") or 0.0)
        out.update({
            "geometry": "fix", "x": c.get("x"), "y": c.get("y"), "sigma_m": c.get("sigma_m"),
            "cov": [round(s * s, 4), 0.0, round(s * s, 4)], "geo": c.get("geo"),
            "room": c.get("room"), "room_label": c.get("room_label"), "room_prob": geometry_share,
            "candidates": [],
        })
    else:
        total = sum(float(c.get("weight") or 0.0) for c in inside) or 1.0
        out["candidates"] = [dict(c, weight=round(float(c.get("weight") or 0.0) / total, 3)) for c in inside]
    return out
