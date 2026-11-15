"""Puck-to-puck baselines: the measured spacing of two pucks, placing a pair from it, and checking a placement against it.

docs/BEACON_FEED.md section A12. Pure standard library (no numpy), no I/O: `beacon.session` holds the state.

The product story: the beacon is two pucks. On landing they range each other, so their spacing is measured, not guessed;
the responder anchors one puck on the floor plan and taps the direction of the other; the other puck is placed at the
measured distance along that direction.

Hardware truth (hardware/BOARD_TO_BOARD.md): Qorvo's stock Nearby Interaction firmware (QANI) ranges iPhones but not
another board; our SS-TWR firmware (hardware/twr_firmware/) ranges board to board but not phones; a board runs one
firmware at a time. A baseline therefore comes either from the TWR firmware (`source: "twr"`, posted by
`python -m beacon.host --baseline-to ...`) or from a tape measure (`source: "manual"`).

Units: `d_m` and `sigma_m` are PHYSICAL metres, as measured. When the server applies an exercise scale (live mode,
BEACON_FEED A4), the spacing on the building plan is `d_m * scale`, exactly like every UWB range.
"""
from __future__ import annotations

import math
import re
from typing import Any

__all__ = ["PUCK_ID_RE", "SOURCES", "PLACED_BY", "PLACED_BY_API", "D_MIN_M", "D_MAX_M", "SAME_POINT_M",
           "MISMATCH_SIGMAS", "MISMATCH_FLOOR_M", "pair_key", "puck_id", "make_baseline", "anchor_point", "spacing_check"]

PUCK_ID_RE = re.compile(r"^P[1-5]$")
SOURCES = ("twr", "manual")
PLACED_BY = ("hand", "baseline", "layout", "sim")    # every value a Puck's `placed_by` can take
PLACED_BY_API = ("hand", "baseline")                 # what POST /api/pucks entries may state
D_MIN_M, D_MAX_M = 0.05, 150.0                       # a baseline must lie strictly between these
SAME_POINT_M = 0.01                                  # `toward` closer than this to the anchor gives no direction
MISMATCH_SIGMAS = 3.0                                # placed spacing vs measured: flagged beyond 3 sigma + 0.2 m
MISMATCH_FLOOR_M = 0.2


def puck_id(v: Any, name: str) -> str:
    if not isinstance(v, str) or not PUCK_ID_RE.match(v):
        raise ValueError(f"{name} must be a puck id P1..P5, got {v!r}")
    return v


def pair_key(a: str, b: str) -> tuple[str, str]:
    """The unordered pair as a sorted tuple: ("P1", "P2") for either order."""
    return (a, b) if a <= b else (b, a)


def _number(v: Any, name: str) -> float:
    if isinstance(v, bool):
        raise ValueError(f"{name} must be a number")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(f):
        raise ValueError(f"{name} must be finite")
    return f


def make_baseline(pair: Any, d_m: Any, *, sigma_m: Any = None, samples: Any = None, source: Any = "manual",
                  t: float) -> dict:
    """Validate one measured spacing and return the published object
    {"pair": ["P1", "P2"], "d_m", "sigma_m" | None, "samples" | None, "t", "source"}. ValueError when anything is off."""
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValueError("pair must be a list of two puck ids, e.g. [\"P1\", \"P2\"]")
    a, b = puck_id(pair[0], "pair[0]"), puck_id(pair[1], "pair[1]")
    if a == b:
        raise ValueError("pair must name two different pucks")
    d = _number(d_m, "d_m")
    if not D_MIN_M < d < D_MAX_M:
        raise ValueError(f"d_m must be between {D_MIN_M:g} and {D_MAX_M:g} m (exclusive), got {d:g}")
    sig = None
    if sigma_m is not None:
        sig = _number(sigma_m, "sigma_m")
        if not 0.0 <= sig < D_MAX_M:
            raise ValueError(f"sigma_m must be at least 0 and below {D_MAX_M:g} m, got {sig:g}")
    n = None
    if samples is not None:
        f = _number(samples, "samples")
        if f != int(f) or not 1 <= f <= 10_000_000:
            raise ValueError(f"samples must be a whole number of at least 1, got {samples!r}")
        n = int(f)
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}, got {source!r}")
    return {"pair": list(pair_key(a, b)), "d_m": round(d, 3), "sigma_m": None if sig is None else round(sig, 3),
            "samples": n, "t": round(float(t), 3), "source": source}


def anchor_point(ax: float, ay: float, tx: float, ty: float, d_plan_m: float) -> tuple[float, float]:
    """The other puck: `d_plan_m` from the anchor (ax, ay) along the direction to (tx, ty)."""
    dx, dy = tx - ax, ty - ay
    n = math.hypot(dx, dy)
    if n < SAME_POINT_M:
        raise ValueError(f"toward must differ from the anchor position (it is within {SAME_POINT_M * 100:g} cm of it): "
                         "tap the direction of the other puck")
    return ax + d_plan_m * dx / n, ay + d_plan_m * dy / n


def spacing_check(baseline: dict, pa: dict, pb: dict, scale: float = 1.0) -> dict | None:
    """Compare the spacing of two placed pucks with their measured baseline. Returns a warning dict when
    |placed spacing - measured| > 3 sigma + 0.2 m (compared in physical metres: plan metres / scale), else None.
    A baseline with no sigma is checked against 0.2 m alone."""
    if pa.get("floor") != pb.get("floor"):
        return None                                   # pucks on different floors: a plan spacing means nothing
    plan = math.hypot(pa["x"] - pb["x"], pa["y"] - pb["y"])
    placed = plan / scale if scale > 0 else plan
    sig = baseline.get("sigma_m") or 0.0
    tol = MISMATCH_SIGMAS * sig + MISMATCH_FLOOR_M
    diff = placed - baseline["d_m"]
    if abs(diff) <= tol:
        return None
    a, b = baseline["pair"]
    how = {pa["id"]: pa.get("placed_by"), pb["id"]: pb.get("placed_by")}
    scale_txt = f" (exercise scale 1 : {scale:g}: {plan:.2f} m on the plan)" if abs(scale - 1.0) > 1e-9 else ""
    by = "by hand" if "hand" in how.values() else "as placed"
    text = (f"Pucks {a} and {b} are {placed:.2f} m apart {by}{scale_txt} but measured {baseline['d_m']:.2f} m apart "
            f"({baseline['source']}; allowed difference {tol:.2f} m): one of them is misplaced on the plan. "
            f"Place the pair again from the measured spacing, or move them on the plan until the spacing matches.")
    return {"kind": "baseline-mismatch", "pair": [a, b], "placed_by": how, "placed_m": round(placed, 3),
            "plan_m": round(plan, 3), "measured_m": baseline["d_m"], "diff_m": round(diff, 3),
            "tolerance_m": round(tol, 3), "scale": scale, "text": text}
