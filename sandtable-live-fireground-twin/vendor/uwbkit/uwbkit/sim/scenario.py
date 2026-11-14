"""Scenario loading, validation and normalisation for the uwbkit simulator.

Schema: INTERFACE.md "Simulator scenario file" (+ the additive keys documented in uwbkit/sim/__init__.py).
Unknown keys are kept and ignored. Every problem raises ValueError naming the exact location, e.g.
``scenario 'my.json': nodes.T1.path[2]: needs "at": [x, y] ...``.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
from pathlib import Path as _FsPath

from .paths import PathError, build_keyframes

SCENARIO_DIR = _FsPath(__file__).resolve().parent / "scenarios"

NOISE_DEFAULTS = {
    "sigma_m": 0.08,             # Gaussian range noise (LOS), metres
    "nlos_extra_sigma_m": 0.15,  # extra Gaussian sigma per crossed wall (default for walls without their own)
    "nlos_bias_m": 0.30,         # default positive NLOS bias per crossed wall (walls may override)
    "nlos_bias_gamma_k": 4.0,    # NLOS bias ~ nlos_bias_m * Gamma(k, 1/k): mean nlos_bias_m, sd = mean/sqrt(k)
    "attenuation_db": 8.0,       # default per-wall attenuation of rssi (walls may override)
    "outlier_prob": 0.02,        # probability of a multipath outlier per measurement
    "outlier_m": 1.5,            # outlier size scale, metres
    "outlier_neg_frac": 0.10,    # fraction of outliers that are short instead of long
    "outlier_neg_scale": 0.25,   # short outliers are this fraction of the long ones
    "dropout_prob": 0.03,        # base probability that a pair is missing in a cycle (LOS, near)
    "dropout_per_wall": 1.0,     # each crossed wall adds this multiple of dropout_prob
    "far_m": 10.0,               # beyond this distance dropouts grow linearly
    "antenna_offset_m": 0.035,   # raw - d (the kit's constant antenna-delay correction)
    "rssi_1m_dbm": -68.0,        # log-distance path loss: RSSI at 1 m
    "path_loss_exp": 2.0,        # log-distance path-loss exponent
    "rssi_sigma_db": 1.5,        # RSSI shadowing noise
    "fp_gap_los_db": 2.0,        # rssi - fp in clear LOS (minimum)
    "fp_gap_sigma_db": 2.0,      # half-normal spread of rssi - fp
    "fp_nlos_frac": 0.8,         # first path loses this extra fraction of wall attenuation
    "fp_outlier_db": 4.0,        # a long (missed-first-path) outlier widens rssi - fp by this
    "los_mid_db": 8.0,           # los = 1/(1+exp((gap - los_mid_db)/los_scale_db))
    "los_scale_db": 1.2,
}
_PROB_KEYS = {"outlier_prob", "dropout_prob", "outlier_neg_frac"}
_SIGNED_KEYS = {"rssi_1m_dbm"}
_POSITIVE_KEYS = {"far_m", "los_scale_db", "path_loss_exp", "nlos_bias_gamma_k"}
_ROLES = ("tag", "mesh", "anchor")


class ScenarioError(ValueError):
    pass


def list_scenarios() -> list[str]:
    """Built-in scenario names (stems of uwbkit/sim/scenarios/*.json), sorted."""
    if not SCENARIO_DIR.is_dir():
        return []
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.json"))


# ----------------------------------------------------------------------------------------- helpers

def _num(v, where, *, ge=None, gt=None, le=None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ScenarioError(f"{where}: expected a number, got {v!r}")
    v = float(v)
    if ge is not None and v < ge:
        raise ScenarioError(f"{where}: must be >= {ge:g}, got {v:g}")
    if gt is not None and v <= gt:
        raise ScenarioError(f"{where}: must be > {gt:g}, got {v:g}")
    if le is not None and v > le:
        raise ScenarioError(f"{where}: must be <= {le:g}, got {v:g}")
    return v


def _point(v, where, sizes=(2, 3)) -> list:
    want = " or ".join({2: "[x, y]", 3: "[x, y, z]"}[s] for s in sizes)
    if not isinstance(v, (list, tuple)) or len(v) not in sizes:
        raise ScenarioError(f"{where}: expected a point {want}, got {v!r}")
    return [_num(c, f"{where}[{i}]") for i, c in enumerate(v)]


def _bool(v, where) -> bool:
    if not isinstance(v, bool):
        raise ScenarioError(f"{where}: expected true or false, got {v!r}")
    return v


def _anchor(v, where, default_z) -> dict:
    if isinstance(v, (list, tuple)):
        p = _point(v, where)
        return {"x": p[0], "y": p[1], "z": p[2] if len(p) > 2 else default_z}
    if not isinstance(v, dict):
        raise ScenarioError(f'{where}: expected {{"x": .., "y": .., "z": ..}}, got {v!r}')
    for k in ("x", "y"):
        if k not in v:
            raise ScenarioError(f'{where}: missing "{k}" (an anchor needs "x", "y" and optionally "z")')
    out = dict(v)
    out["x"] = _num(v["x"], f"{where}.x")
    out["y"] = _num(v["y"], f"{where}.y")
    out["z"] = _num(v.get("z", default_z), f"{where}.z")
    return out


# ----------------------------------------------------------------------------------------- reading

def _read_spec(spec):
    if isinstance(spec, dict):
        return copy.deepcopy(spec), "custom", "inline scenario"
    if isinstance(spec, (str, os.PathLike)):
        s = os.fspath(spec)
        if isinstance(spec, str) and s.lstrip().startswith("{"):
            try:
                return json.loads(s), "custom", "inline scenario"
            except json.JSONDecodeError as e:
                raise ScenarioError(f"inline scenario JSON is invalid at line {e.lineno} column {e.colno}: {e.msg}") from None
        builtins = list_scenarios()
        if isinstance(spec, str) and s in builtins:
            path = SCENARIO_DIR / f"{s}.json"
        elif os.path.isfile(s):
            path = _FsPath(s)
        elif s.endswith(".json") or "/" in s or "\\" in s:
            raise ScenarioError(f"scenario file not found: {s}")
        else:
            raise ScenarioError(f"unknown scenario {s!r}: not a built-in ({', '.join(builtins)}) and no such file")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ScenarioError(f"cannot read scenario file {path}: {e}") from None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ScenarioError(
                f"scenario file {path}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from None
        return data, path.stem, f"scenario {path.name!r}"
    raise ScenarioError(f"scenario must be a built-in name, a path to a .json file or a dict, got {type(spec).__name__}")


def load_scenario(spec) -> dict:
    """Load + validate + fill defaults. ``spec``: built-in name | path to .json | inline dict (or JSON text).

    Returns a normalised dict (idempotent: ``load_scenario(load_scenario(x)) == load_scenario(x)``).
    Raises ValueError with a readable message naming the offending key on bad input.
    """
    raw, default_name, src = _read_spec(spec)
    try:
        return _normalise(raw, default_name)
    except ScenarioError as e:
        raise ScenarioError(f"{src}: {e}") from None


# ----------------------------------------------------------------------------------------- normalise

def _normalise(raw, default_name) -> dict:
    if not isinstance(raw, dict):
        raise ScenarioError(f"a scenario must be a JSON object, got {type(raw).__name__}")
    sc = dict(raw)  # unknown keys are kept
    sc["name"] = str(raw.get("name") or default_name)
    sc["description"] = str(raw.get("description", ""))
    dims = raw.get("dims", 2)
    if isinstance(dims, bool) or dims not in (2, 3):
        raise ScenarioError(f'dims: must be 2 or 3, got {dims!r}')
    sc["dims"] = int(dims)
    sc["rate_hz"] = _num(raw.get("rate_hz", 10), "rate_hz", gt=0, le=1000)
    loop = sc["loop"] = _bool(raw.get("loop", True), "loop")
    seed = raw.get("seed", 0)
    if seed is None:
        seed = 0
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ScenarioError(f"seed: expected a non-negative integer, got {seed!r}")
    sc["seed"] = seed

    # noise
    noise_in = raw.get("noise", {}) or {}
    if not isinstance(noise_in, dict):
        raise ScenarioError(f"noise: expected an object like {{\"sigma_m\": 0.08}}, got {noise_in!r}")
    noise = dict(noise_in)
    for k, dv in NOISE_DEFAULTS.items():
        v = noise_in.get(k, dv)
        where = f"noise.{k}"
        if k in _PROB_KEYS:
            noise[k] = _num(v, where, ge=0, le=1)
        elif k in _SIGNED_KEYS:
            noise[k] = _num(v, where)
        elif k in _POSITIVE_KEYS:
            noise[k] = _num(v, where, gt=0)
        else:
            noise[k] = _num(v, where, ge=0)
    sc["noise"] = noise

    # room
    if raw.get("room") is None:
        sc["room"] = {}
    else:
        sc["room"] = _room(raw["room"], noise)

    # anchors + nodes
    anchors_in = raw.get("anchors", {}) or {}
    if not isinstance(anchors_in, dict):
        raise ScenarioError('anchors: expected an object like {"A1": {"x": 0, "y": 0, "z": 2.0}, ...}')
    anchors = {str(aid): _anchor(v, f"anchors.{aid}", 2.0) for aid, v in anchors_in.items()}

    if "nodes" not in raw:
        raise ScenarioError('missing "nodes": add e.g. "nodes": {"T1": {"role": "tag", "path": '
                            '[{"at": [1, 1]}, {"at": [4, 1], "speed": 1.0}]}}')
    nodes_in = raw["nodes"]
    if not isinstance(nodes_in, dict) or not nodes_in:
        raise ScenarioError('nodes: expected a non-empty object mapping node ids to specs, e.g. {"T1": {"at": [1, 1]}}')
    default_role = "tag" if anchors else "mesh"
    nodes = {}
    for nid, spec in nodes_in.items():
        nid = str(nid)
        where = f"nodes.{nid}"
        if not isinstance(spec, dict):
            raise ScenarioError(f"{where}: expected an object, got {spec!r}")
        role = spec.get("role", default_role)
        if role not in _ROLES:
            raise ScenarioError(f'{where}.role: must be one of {", ".join(_ROLES)}, got {role!r}')
        if role == "anchor":  # tolerate anchors declared among nodes
            if nid in anchors:
                raise ScenarioError(f'{where}: id is also listed in "anchors"')
            if "at" not in spec and "x" not in spec:
                raise ScenarioError(f'{where}: an anchor node needs "at": [x, y, z]')
            anchors[nid] = _anchor(spec.get("at", spec), f"{where}.at", 2.0)
            continue
        if nid in anchors:
            raise ScenarioError(f'{where}: id is also used by an anchor')
        nodes[nid] = _node(nid, spec, role, loop)
    if not nodes:
        raise ScenarioError('nodes: needs at least one tag or mesh node (anchors alone produce no ranges)')
    sc["anchors"] = anchors
    sc["nodes"] = nodes

    # profile
    n_tags = len(nodes)
    prof = raw.get("profile")
    if prof is None or prof == "":
        prof = f"{len(anchors)}A{n_tags}T" if anchors else f"mesh{n_tags}"
    prof = str(prof)
    m = re.fullmatch(r"(\d+)A(\d+)T", prof)
    mm = re.fullmatch(r"mesh(\d+)", prof)
    if m and (int(m.group(1)) != len(anchors) or int(m.group(2)) != n_tags):
        raise ScenarioError(
            f'profile "{prof}" says {m.group(1)} anchors and {m.group(2)} tag(s), but the scenario has '
            f'{len(anchors)} anchor(s) ({", ".join(anchors) or "none"}) and {n_tags} tag(s) ({", ".join(nodes)}); '
            f'fix "profile" or omit it to infer it')
    if mm and (anchors or int(mm.group(1)) != n_tags):
        raise ScenarioError(
            f'profile "{prof}" means {mm.group(1)} mesh nodes and no anchors, but the scenario has '
            f'{len(anchors)} anchor(s) and {n_tags} node(s) ({", ".join(nodes)})')
    sc["profile"] = prof

    # timing: path periods (also validates waypoint timing) and events
    longest = 0.0
    for nid, node in nodes.items():
        try:
            times, _ = build_keyframes(node, loop)
        except PathError as e:
            raise ScenarioError(f"nodes.{nid}.{e}") from None
        longest = max(longest, times[-1])
    last_event = max([ev["t"] for n in nodes.values() for ev in n["events"]] + [0.0])
    if raw.get("duration_s") is None:
        dur = max(longest, last_event)
        sc["duration_s"] = round(dur, 6) if dur > 0 else 60.0
    else:
        sc["duration_s"] = _num(raw["duration_s"], "duration_s", gt=0)
        if loop:
            for nid, n in nodes.items():
                for i, ev in enumerate(n["events"]):
                    if ev["t"] > sc["duration_s"]:
                        raise ScenarioError(
                            f'nodes.{nid}.events[{i}]: t={ev["t"]:g} is after duration_s={sc["duration_s"]:g}; '
                            f'with "loop": true it would never fire')
    return sc


def _room(room, noise) -> dict:
    if not isinstance(room, dict):
        raise ScenarioError('room: expected an object with "outline", "walls", "labels"')
    out = dict(room)
    if "outline" in room:
        ol = room["outline"]
        if not isinstance(ol, list) or len(ol) < 3:
            raise ScenarioError("room.outline: expected a list of at least 3 points [[x, y], ...]")
        out["outline"] = [_point(p, f"room.outline[{i}]", (2,)) for i, p in enumerate(ol)]
    walls = room.get("walls", []) or []
    if not isinstance(walls, list):
        raise ScenarioError('room.walls: expected a list of {"a": [x, y], "b": [x, y]}')
    out["walls"] = []
    for i, w in enumerate(walls):
        where = f"room.walls[{i}]"
        if not isinstance(w, dict):
            raise ScenarioError(f'{where}: expected {{"a": [x, y], "b": [x, y]}}, got {w!r}')
        for k in ("a", "b"):
            if k not in w:
                raise ScenarioError(f'{where}: missing "{k}" (a wall is the segment from "a": [x, y] to "b": [x, y])')
        nw = dict(w)
        nw["a"] = _point(w["a"], f"{where}.a", (2,))
        nw["b"] = _point(w["b"], f"{where}.b", (2,))
        nw["nlos_bias_m"] = _num(w.get("nlos_bias_m", noise["nlos_bias_m"]), f"{where}.nlos_bias_m", ge=0)
        nw["attenuation_db"] = _num(w.get("attenuation_db", noise["attenuation_db"]), f"{where}.attenuation_db", ge=0)
        nw["nlos_extra_sigma_m"] = _num(w.get("nlos_extra_sigma_m", noise["nlos_extra_sigma_m"]),
                                        f"{where}.nlos_extra_sigma_m", ge=0)
        out["walls"].append(nw)
    labels = room.get("labels", []) or []
    if not isinstance(labels, list):
        raise ScenarioError('room.labels: expected a list of {"name": "door", "at": [x, y]}')
    out["labels"] = labels
    return out


def _node(nid, spec, role, loop) -> dict:
    where = f"nodes.{nid}"
    n = dict(spec)
    n["role"] = role
    n["z"] = _num(spec.get("z", 1.0), f"{where}.z")
    if "label" in spec:
        n["label"] = str(spec["label"])
    mode = str(spec.get("path_mode", "linear")).strip().lower()
    n["path_mode"] = "spline" if mode == "spline" else "linear"  # "linear|spline" doc shorthand -> linear
    n["jitter_m"] = _num(spec.get("jitter_m", 0.0), f"{where}.jitter_m", ge=0)
    n["bias_m"] = _num(spec.get("bias_m", 0.0), f"{where}.bias_m")
    if "speed" in spec:
        n["speed"] = _num(spec["speed"], f"{where}.speed", gt=0)
    if "close_loop" in spec:
        n["close_loop"] = _bool(spec["close_loop"], f"{where}.close_loop")
    if "at" in spec:
        n["at"] = _point(spec["at"], f"{where}.at")
    if "path" in spec:
        path = spec["path"]
        if not isinstance(path, list) or not path:
            raise ScenarioError(f'{where}.path: expected a non-empty list of waypoints like {{"at": [x, y], "t": 0}}')
        wps = []
        for i, wp in enumerate(path):
            w = f"{where}.path[{i}]"
            if not isinstance(wp, dict):
                raise ScenarioError(f'{w}: expected a waypoint object like {{"at": [x, y], "speed": 1.0}}, got {wp!r}')
            o = dict(wp)
            if "at" in wp:
                o["at"] = _point(wp["at"], f"{w}.at")
            elif i == 0 and "at" not in spec:
                raise ScenarioError(f'{w}: the first waypoint needs "at": [x, y]')
            elif i > 0 and "hold_s" not in wp:
                raise ScenarioError(f'{w}: needs "at": [x, y] (only a pure {{"hold_s": N}} waypoint may omit it)')
            if "t" in wp:
                o["t"] = _num(wp["t"], f"{w}.t", ge=0)
            if "speed" in wp:
                o["speed"] = _num(wp["speed"], f"{w}.speed", gt=0)
            if "hold_s" in wp:
                o["hold_s"] = _num(wp["hold_s"], f"{w}.hold_s", ge=0)
            wps.append(o)
        n["path"] = wps
    elif "at" not in spec:
        raise ScenarioError(f'{where}: needs either "path" (a list of waypoints) or "at" (a fixed point [x, y])')
    events = spec.get("events", []) or []
    if not isinstance(events, list):
        raise ScenarioError(f'{where}.events: expected a list like [{{"t": 20, "name": "button", "data": {{"n": 1}}}}]')
    evs = []
    for i, ev in enumerate(events):
        w = f"{where}.events[{i}]"
        if not isinstance(ev, dict):
            raise ScenarioError(f"{w}: expected an object, got {ev!r}")
        if "t" not in ev:
            raise ScenarioError(f'{w}: missing "t" (sim time in seconds when the event fires)')
        if not ev.get("name"):
            raise ScenarioError(f'{w}: missing "name" (e.g. "button")')
        data = ev.get("data", {})
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ScenarioError(f"{w}.data: expected an object, got {data!r}")
        e = dict(ev)
        e["t"] = _num(ev["t"], f"{w}.t", ge=0)
        e["name"] = str(ev["name"])
        e["data"] = data
        evs.append(e)
    n["events"] = evs
    return n
