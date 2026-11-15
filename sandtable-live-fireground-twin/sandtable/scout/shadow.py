"""Link-shadow tomography from the kit's ordinary `range` messages (Scout mode's no-CIR fallback).

STATUS: SIMULATION-ONLY. Exercised against synthetic links, builder R1's scout simulator and a kit fake-board
recording; never on real boards. Scout mode is a simulated mode this weekend (the owner's boards are DWM3001CDK,
for which the shared kit has no firmware or parser yet).

No firmware change at all. Five pucks in the kit's `mesh5` profile range every other puck: ten links. A human body
on the straight line between two pucks blocks the direct path, so that link's first-path power (`range.fp`) drops,
its total power (`rssi`) drops less, its line-of-sight confidence (`los`) falls and its range reads a little long
(NLOS bias). From which links are shadowed we estimate that someone is there and, coarsely, where: on the shadowed
lines, away from the clear ones.

    from sandtable.scout import shadow
    base = shadow.baseline(ranges_while_room_was_empty)          # optional
    res = shadow.locate(ranges_window, pucks_xy, baseline=base)   # pucks_xy: {"P1": [x, y], ...}, same ids as the ranges
    parts = shadow.to_scan_parts(res)                             # rung / victims / per_link / links / echo_map / limits

Two ways to say what "normal" is for a link:

* ``baseline`` given (learned from the first seconds while nobody stood between the pucks, or a median over a long
  stretch with ``baseline(ranges, mode="median")``): every link against its own history. Strong: it cancels each
  link's antenna orientation and multipath. This is the live demo with a person walking in after the pucks settle.
* ``baseline=None``: the links against each other. First-path power should fall as 20 log10(distance) in free space,
  so a robust fit across all links gives the expected level of each one, and a link far below it (or with a low
  ``los``) is shadowed. Weaker (real links differ by a few dB from antenna orientation alone) but it is the only
  option when the pucks land in a room where the person already lies: there is no empty room to learn.

Every optional field may be missing on real boards (uwb-kit DEVIATIONS.md): ``fp`` and ``los`` are absent on MaUWB
and vendor firmware, ``rssi`` on newer MaUWB firmware. The locator uses what is there, in this order of trust:
fp, los, rssi, and range excess (``d`` longer than its baseline; only with a baseline).

Honest limits (also returned in ``limits``): presence and a coarse position between the pucks; no breathing rate;
a person outside the convex hull of the pucks crosses no link and is not seen; a person standing exactly at a puck
or between links may be missed; with one shadowed link the position is "somewhere along that line".

Adapted from lane L08's stage 11 "shadow mode" (`../tagless-uwb-echo-radar/src/tagless/dsp/pipeline.py`,
`_on_range` / `_shadow_fix`, docs/DSP.md): first-path power per link against its baseline, links down by more than
3.5 dB vote along their line of sight with a Gaussian of the distance to the segment. Added here: the cross-link
path-loss fit when there is no baseline, the los / rssi / range-excess fallbacks, negative votes from links that
stay clear, an uncertainty that is deliberately large, and the Scan-object adapter. L08's caveat stands: this is
coarse by construction (L08 simulation: 16 cm median, 53 cm 90th percentile on a 1.8 m table; at room scale the
error grows with the puck spacing).
"""
from __future__ import annotations

import math

import numpy as np

CUT_DB = 3.5            # first-path drop (dB) at which a link counts as shadowed when it has its own baseline (L08)
CUT_DB_FIT = 4.5        # the same against the cross-link fit (links differ from each other by a few dB anyway)
RSSI_FACTOR = 0.55      # total power drops about half as much as first-path power through a body
LOS_DROP = 0.35         # los falling by this much (or below LOS_LOW without baseline) is evidence
LOS_LOW = 0.45
D_EXCESS_M = 0.10       # range reading this much longer than its baseline is (weak) evidence of a body on the link
BODY_W_M = 0.30         # half-width of the vote along a line of sight (a torso is 0.3-0.5 m across)
CLEAR_WEIGHT = 0.6      # a link that stays clear votes against cells on its line
SIGMA_FLOOR_M = 0.5     # deliberately large: this rung is coarse
HULL_MARGIN_M = 0.25
CELL_M = 0.1
LIMITS = [
    "Link shadowing (rung 2): presence and a coarse position between the pucks, from which puck-to-puck links a body blocks",
    "No breathing rate and no echo: a person who lies still is seen only while they block a link",
    "A person outside the convex hull of the pucks crosses no link and is not seen",
    "With one shadowed link the position is only 'somewhere along that line'; the uncertainty circle says so",
    "Furniture or debris on a link looks like a person unless the link has its own baseline from before",
]


def _lid(a: str, b: str) -> str:
    return f"{a}-{b}" if a <= b else f"{b}-{a}"


def _num(m: dict, key: str):
    v = m.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _group(ranges) -> dict:
    """{link: {"t": [...], "fp": [...], "rssi": [...], "los": [...], "d": [...], "a", "b"}} (missing fields skipped)."""
    out: dict = {}
    for m in ranges:
        if not isinstance(m, dict) or m.get("type", "range") != "range":
            continue
        a, b = m.get("a"), m.get("b")
        if not a or not b or a == b:
            continue
        a, b = (str(a), str(b)) if str(a) <= str(b) else (str(b), str(a))
        g = out.setdefault(f"{a}-{b}", {"a": a, "b": b, "t": [], "fp": [], "rssi": [], "los": [], "d": []})
        t = _num(m, "t")
        g["t"].append(t if t is not None else float(len(g["t"])))
        for k in ("fp", "rssi", "los", "d"):
            v = _num(m, k)
            if v is not None:
                g[k].append((g["t"][-1], v))
    return out


def _med(pairs):
    return float(np.median([v for _, v in pairs])) if pairs else None


def _mad(pairs):
    if len(pairs) < 3:
        return None
    v = np.array([x for _, x in pairs])
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def baseline(ranges, seconds: float = 3.0, mode: str = "first", min_samples: int = 5) -> dict:
    """Per-link reference levels.

    mode="first": the first `seconds` of the stream (take it while nobody stands between the pucks).
    mode="median": the median over everything given (a rolling baseline: pass the last minute or so; a person who
    crossed a link for a few seconds barely moves it, a person who stood still on it for the whole stretch does).
    Returns {"mode", "t0", "t1", "links": {"P1-P2": {"fp", "fp_sd", "rssi", "los", "d", "n"}}}.
    """
    g = _group(ranges)
    ts = [t for v in g.values() for t in v["t"]]
    if not ts:
        return {"mode": mode, "t0": None, "t1": None, "links": {}}
    t0 = min(ts)
    t1 = t0 + seconds if mode == "first" else max(ts)
    links = {}
    for lid, v in g.items():
        sel = {k: [(t, x) for t, x in v[k] if t <= t1] for k in ("fp", "rssi", "los", "d")}
        n = sum(1 for t in v["t"] if t <= t1)
        if n < min_samples:
            continue
        links[lid] = {"fp": _med(sel["fp"]), "fp_sd": _mad(sel["fp"]), "rssi": _med(sel["rssi"]),
                      "los": _med(sel["los"]), "d": _med(sel["d"]), "n": n}
    return {"mode": mode, "t0": t0, "t1": t1, "links": links}


# ------------------------------------------------------------------------------------------------ geometry helpers

def _hull(pts: np.ndarray) -> np.ndarray:
    """Convex hull (monotone chain), counter-clockwise, without repeating the first point."""
    p = sorted(set(map(tuple, np.round(pts, 6))))
    if len(p) <= 2:
        return np.array(p, float)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for q in p:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(p):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], q) <= 0:
            hi.pop()
        hi.append(q)
    return np.array(lo[:-1] + hi[:-1], float)


def _inside(poly, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    poly = np.asarray(poly, float)
    inside = np.zeros(x.shape, bool)
    n = len(poly)
    if n < 3:
        return inside
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cond = (y1 > y) != (y2 > y)
        xi = (x2 - x1) * (y - y1) / ((y2 - y1) if y2 != y1 else 1e-12) + x1
        inside ^= cond & (x < xi)
    return inside


def _segdist(ax, ay, bx, by, x, y):
    ex, ey = bx - ax, by - ay
    L2 = max(ex * ex + ey * ey, 1e-12)
    s = np.clip(((x - ax) * ex + (y - ay) * ey) / L2, 0.0, 1.0)
    return np.hypot(x - (ax + s * ex), y - (ay + s * ey))


def _edge_dist(poly, x, y):
    poly = np.asarray(poly, float)
    if len(poly) == 1:
        return np.hypot(x - poly[0, 0], y - poly[0, 1])
    d = np.full(x.shape, np.inf)
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        d = np.minimum(d, _segdist(a[0], a[1], b[0], b[1], x, y))
    return d


def in_hull(pucks_xy: dict, x: float, y: float, margin: float = 0.0) -> bool:
    """True when (x, y) lies inside the convex hull of the pucks (plus `margin` metres)."""
    pts = np.array([v[:2] for v in pucks_xy.values()], float)
    if len(pts) < 3:
        return False
    h = _hull(pts)
    xx, yy = np.array([x], float), np.array([y], float)
    return bool(_inside(h, xx, yy)[0] or (margin > 0 and _edge_dist(h, xx, yy)[0] <= margin))


# ------------------------------------------------------------------------------------------------ the locator

def _link_scores(g: dict, base: dict | None, method: str) -> dict:
    """Per link: current levels (medians over the window), drops against the reference, and a 0..1 shadow score."""
    out = {}
    if method == "fit":                                  # expected fp / rssi of every link from a robust cross-link fit
        ref = {}
        for key in ("fp", "rssi"):
            pts = [(lid, _med(v[key]), _med(v["d"])) for lid, v in g.items() if v[key] and v["d"]]
            pts = [(lid, val, d) for lid, val, d in pts if d and d > 0.05]
            if len(pts) >= 3:
                a = np.array([val + 20.0 * math.log10(max(d, 0.1)) for _, val, d in pts])
                A = float(np.median(a))                   # free-space slope fixed at 20 dB/decade; only the level is fitted
                for lid, val, d in pts:
                    ref.setdefault(lid, {})[key] = A - 20.0 * math.log10(max(d, 0.1))
    for lid, v in g.items():
        cur = {k: (None if _med(v[k]) is None else round(_med(v[k]), 3 if k in ("d", "los") else 2))
               for k in ("fp", "rssi", "los", "d")}
        r = (base or {}).get(lid) if method == "baseline" else (ref.get(lid) if method == "fit" else None)
        rec = {"a": v["a"], "b": v["b"], "n": len(v["t"]), "fp_dbm": cur["fp"], "rssi_dbm": cur["rssi"],
               "los": cur["los"], "d_m": cur["d"], "drop_db": None, "rssi_drop_db": None, "d_excess_m": None,
               "score": 0.0, "evidence": [], "has_ref": bool(r)}
        cut = CUT_DB if method == "baseline" else CUT_DB_FIT
        scores = []
        if r:
            if cur["fp"] is not None and r.get("fp") is not None:
                rec["drop_db"] = round(r["fp"] - cur["fp"], 2)
                s = min(1.0, max(0.0, (rec["drop_db"] - cut + 1.5) / 4.0))          # L08: 0 at cut-1.5 dB, 1 at cut+2.5 dB
                scores.append(s)
                if s > 0:
                    rec["evidence"].append("fp")
            if cur["rssi"] is not None and r.get("rssi") is not None:
                rec["rssi_drop_db"] = round(r["rssi"] - cur["rssi"], 2)
                c2 = cut * RSSI_FACTOR
                s = 0.8 * min(1.0, max(0.0, (rec["rssi_drop_db"] - c2 + 1.0) / 3.0))
                if cur["fp"] is None or rec["drop_db"] is None:                     # rssi only counts when fp is absent
                    scores.append(s)
                    if s > 0:
                        rec["evidence"].append("rssi")
            if method == "baseline" and cur["d"] is not None and r.get("d") is not None:
                rec["d_excess_m"] = round(cur["d"] - r["d"], 3)
                if cur["fp"] is None and cur["rssi"] is None and cur["los"] is None:   # last resort: NLOS range bias
                    s = 0.75 * min(1.0, max(0.0, (rec["d_excess_m"] - D_EXCESS_M + 0.1) / 0.15))   # 0.5 at +0.1 m
                    scores.append(s)
                    if s > 0:
                        rec["evidence"].append("range")
        if cur["los"] is not None:
            if method == "baseline" and r and r.get("los") is not None:
                s = min(1.0, max(0.0, (r["los"] - cur["los"] - LOS_DROP + 0.15) / 0.3))
            else:
                s = min(1.0, max(0.0, (LOS_LOW + 0.15 - cur["los"]) / 0.3))
            scores.append(0.9 * s)
            if s > 0:
                rec["evidence"].append("los")
        rec["score"] = round(float(max(scores)) if scores else 0.0, 3)
        out[lid] = rec
    return out


def _moving(g: dict, base: dict | None, method: str, bin_s: float = 1.0) -> bool | None:
    """Did the set of shadowed links change during the window? None when the window is too short to tell."""
    ts = [t for v in g.values() for t in v["t"]]
    if not ts or max(ts) - min(ts) < 3 * bin_s:
        return None
    t0 = min(ts)
    nb = int((max(ts) - t0) // bin_s) + 1
    sets = []
    for k in range(nb):
        lo, hi = t0 + k * bin_s, t0 + (k + 1) * bin_s
        sub = {lid: {**v, "t": [t for t in v["t"] if lo <= t < hi],
                     **{key: [(t, x) for t, x in v[key] if lo <= t < hi] for key in ("fp", "rssi", "los", "d")}}
               for lid, v in g.items()}
        sub = {lid: v for lid, v in sub.items() if len(v["t"]) >= 3}
        if len(sub) < 3:
            continue
        sc = _link_scores(sub, base, method)
        sets.append(frozenset(l for l, r in sc.items() if r["score"] >= 0.5))
    nonempty = [s for s in sets if s]
    return len(set(nonempty)) >= 2 if nonempty else False


def locate(ranges, pucks_xy: dict, baseline=None, *, window_s: float | None = None,
           bounds=None, cell_m: float = CELL_M) -> dict:
    """Presence and a coarse position from shadowed puck-to-puck links.

    ranges   : kit `range` messages ({"t", "a", "b", "d", "rssi"?, "fp"?, "los"?}); ids as in `pucks_xy`.
    pucks_xy : {"P1": [x, y], ...} in any frame (pass twin-frame positions to get a twin-frame estimate).
    baseline : the dict from `baseline()` (each link against its own reference), or None (links against each other).
    window_s : use only the last `window_s` seconds of `ranges` (the live view); None = everything given.
    bounds   : optional polygon (the room) to search inside, in addition to the pucks' convex hull.
    Returns {"present", "x", "y", "sigma_m", "cov", "links_down", "confidence", "method", "moving", "n_links",
             "n_links_alive", "per_link", "map", "why"}.
    """
    ranges = [m for m in (ranges or []) if isinstance(m, dict)]
    if window_s is not None and ranges:
        tmax = max((_num(m, "t") or 0.0) for m in ranges)
        ranges = [m for m in ranges if (_num(m, "t") or 0.0) >= tmax - window_s]
    base = baseline.get("links") if isinstance(baseline, dict) and "links" in baseline else baseline
    method = "baseline" if base else "fit"
    xy = {str(k): (float(v[0]), float(v[1])) for k, v in (pucks_xy or {}).items() if v is not None and len(v) >= 2}
    g = {lid: v for lid, v in _group(ranges).items() if v["a"] in xy and v["b"] in xy}
    res = {"present": False, "x": None, "y": None, "sigma_m": None, "cov": None, "links_down": [], "confidence": 0.0,
           "method": f"link-shadow/{'own-baseline' if method == 'baseline' else 'cross-link-fit'}",
           "moving": None, "n_links": len(g), "n_links_alive": 0, "per_link": [], "map": None, "why": ""}
    if len(xy) < 2 or not g:
        res["why"] = "need at least two placed pucks and ranges between them"
        return res
    if not any(v["fp"] or v["los"] or v["rssi"] for v in g.values()) and method == "fit":
        res["why"] = ("the boards report no first-path power, RSSI or line-of-sight confidence and there is no baseline: "
                      "link shadowing needs one of them (vendor firmware? learn a baseline to use the range excess)")
        res["method"] = "link-shadow/unavailable"
        return res
    sc = _link_scores(g, base, method)
    res["n_links_alive"] = sum(1 for r in sc.values() if r["n"] >= 3)
    res["per_link"] = sorted(sc.values(), key=lambda r: (r["a"], r["b"]))
    down = {lid: r for lid, r in sc.items() if r["score"] >= 0.5}
    res["links_down"] = [{"a": r["a"], "b": r["b"], "score": r["score"], "drop_db": r["drop_db"], "los": r["los"],
                          "evidence": r["evidence"]} for r in sorted(down.values(), key=lambda r: -r["score"])]
    res["moving"] = _moving(g, base, method)

    # grid over the convex hull of the pucks (a person outside it crosses no link), optionally clipped to the room
    pts = np.array(list(xy.values()), float)
    hull = _hull(pts)
    lo, hi = pts.min(0) - HULL_MARGIN_M, pts.max(0) + HULL_MARGIN_M
    nx, ny = max(2, int(math.ceil((hi[0] - lo[0]) / cell_m)) + 1), max(2, int(math.ceil((hi[1] - lo[1]) / cell_m)) + 1)
    X, Y = np.meshgrid(lo[0] + cell_m * np.arange(nx), lo[1] + cell_m * np.arange(ny))
    X, Y = X.ravel(), Y.ravel()
    mask = (_inside(hull, X, Y) | (_edge_dist(hull, X, Y) <= HULL_MARGIN_M)) if len(hull) >= 3 else \
        (_edge_dist(hull, X, Y) <= HULL_MARGIN_M)
    if bounds is not None and len(bounds) >= 3:
        mask &= _inside(bounds, X, Y)
    img = np.zeros(X.shape)
    for lid, r in sc.items():
        (ax, ay), (bx, by) = xy[r["a"]], xy[r["b"]]
        K = np.exp(-0.5 * (_segdist(ax, ay, bx, by, X, Y) / BODY_W_M) ** 2)
        if r["score"] >= 0.5:
            img += r["score"] * K
        elif r["score"] < 0.2 and r["n"] >= 3:
            img -= CLEAR_WEIGHT * (1.0 - r["score"] / 0.2) * K
    img[~mask] = -np.inf
    res["map"] = {"origin": [round(float(lo[0]), 3), round(float(lo[1]), 3)], "cell_m": cell_m, "nx": nx, "ny": ny,
                  "kind": "shadow",
                  "values": [round(float(v), 3) for v in
                             (np.clip(img, 0, None) / max(1e-9, float(np.max(np.where(mask, img, 0))))).tolist()]}
    if not down:
        res["why"] = (f"no link shadowed ({res['n_links_alive']} links alive): nobody between the pucks, "
                      "or a person outside their hull, or one standing still off every line")
        return res
    peak = float(np.max(img))
    if not math.isfinite(peak) or peak <= 0.15:
        res["why"] = "shadowed links do not agree on any place inside the pucks' hull (likely noise or a puck moved)"
        res["present"] = True
        res["confidence"] = 0.15
        return res
    R = mask & (img >= 0.75 * peak)
    w = img[R]
    x, y = float((X[R] * w).sum() / w.sum()), float((Y[R] * w).sum() / w.sum())
    dx, dy = X[R] - x, Y[R] - y
    cxx, cxy, cyy = (float((w * dx * dx).sum() / w.sum()) + BODY_W_M ** 2, float((w * dx * dy).sum() / w.sum()),
                     float((w * dy * dy).sum() / w.sum()) + BODY_W_M ** 2)
    lam = 0.5 * (cxx + cyy) + math.sqrt(0.25 * (cxx - cyy) ** 2 + cxy ** 2)
    sigma = math.sqrt(lam)
    if sigma < SIGMA_FLOOR_M:                                   # deliberately large uncertainty: scale the ellipse up
        f = (SIGMA_FLOOR_M / sigma) ** 2
        cxx, cxy, cyy, sigma = cxx * f, cxy * f, cyy * f, SIGMA_FLOOR_M
    ev = sum(r["score"] for r in down.values())
    near = sum(r["score"] for r in down.values()
               if float(_segdist(*xy[r["a"]], *xy[r["b"]], np.array([x]), np.array([y]))[0]) < 0.6)
    conf = (1.0 - math.exp(-0.6 * ev)) * (near / ev) * (1.0 if method == "baseline" else 0.85)
    res.update({"present": True, "x": round(x, 2), "y": round(y, 2), "sigma_m": round(sigma, 2),
                "cov": [round(cxx, 3), round(cxy, 3), round(cyy, 3)], "confidence": round(min(0.95, conf), 2),
                "why": f"{len(down)} of {res['n_links_alive']} links shadowed; the estimate is where they cross, "
                       f"away from the links that stayed clear"})
    return res


def to_scan_parts(result: dict, pucks_xy: dict | None = None) -> dict:
    """The parts of a `sandtable.scan/1` object that rung 2 fills: rung, victims, per_link, links, echo_map, limits.

    The session adds ids, text, room, node and geo to the victims as for the radar rung. Positions stay in the frame
    of the `pucks_xy` that `locate` was given."""
    victims = []
    if result.get("present") and result.get("x") is not None:
        victims.append({
            "x": result["x"], "y": result["y"], "sigma_m": result["sigma_m"], "cov": result["cov"],
            "confidence": result["confidence"], "n_links": len(result.get("links_down", [])),
            "state": "moving" if result.get("moving") else "none", "moving": bool(result.get("moving")),
            "breathing_bpm": None, "breathing_conf": 0.0, "detected_by": "link-shadow",
            "note": "someone blocks the links between these pucks; no breathing rate on this rung"})
    down = {_lid(d["a"], d["b"]) for d in result.get("links_down", [])}
    per_link, links = [], []
    for r in result.get("per_link", []):
        lid = _lid(r["a"], r["b"])
        per_link.append({"tx": r["a"], "rx": r["b"], "excess_m": None, "snr_db": None, "used": r["n"] >= 3,
                         "shadow_db": r["drop_db"], "los": r["los"], "score": r["score"], "shadowed": lid in down})
        d = r["d_m"]
        if pucks_xy and r["a"] in pucks_xy and r["b"] in pucks_xy:
            (ax, ay), (bx, by) = pucks_xy[r["a"]][:2], pucks_xy[r["b"]][:2]
            d = math.hypot(ax - bx, ay - by) if d is None else d
        links.append({"a": r["a"], "b": r["b"], "d_m": None if d is None else round(float(d), 2), "used": r["n"] >= 3,
                      "shadowed": lid in down})
    return {"rung": "link-shadow", "victims": victims, "per_link": per_link, "links": links,
            "echo_map": result.get("map"), "limits": list(LIMITS),
            "shadow": {k: result.get(k) for k in ("method", "present", "confidence", "moving", "n_links",
                                                  "n_links_alive", "why")}}
