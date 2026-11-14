"""``uwbkit eval``: solver accuracy against simulator ground truth, per built-in scenario.

Everything here is SIMULATED: the ranges come from ``uwbkit.sim`` with the scenario's stated noise
model, and the error is the distance between the kit's ``pos`` output and the simulator's truth.

For every scenario and seed it runs the simulator faster than real time, feeds the ranges to the
same ``Tracker`` the hub uses, and records per-tick position error after a 2 s warm-up. It also runs:

* a naive baseline (unweighted linearised least squares on each tick's ranges, no filtering, no
  outlier handling), to show what the robust solver buys;
* an NLOS ablation for scenarios with walls: the same scenario with its walls removed.

    uwbkit eval                         # -> results/solver_accuracy.json + results/solver_accuracy.png
    uwbkit eval --seeds 8 --duration 180 --out results

Dependencies: numpy; matplotlib only for the figure (``--no-figure`` skips it).
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from .. import __version__
from ..sim import Simulator, list_scenarios, load_scenario
from ..solver import Tracker, canonical_frame

WARMUP_S = 2.0


def naive_fix(ranges: list, anchors: dict, tag_z: float, dims: int):
    """Unweighted linearised least squares for one tick (the textbook method), or None."""
    pts, ds = [], []
    for r in ranges:
        a = anchors.get(r["b"]) or anchors.get(r["a"])
        if a is None:
            continue
        d = r["d"]
        if dims == 2:
            dz = a["z"] - tag_z
            d = math.sqrt(max(1e-6, d * d - dz * dz))
            pts.append((a["x"], a["y"]))
        else:
            pts.append((a["x"], a["y"], a["z"]))
        ds.append(d)
    n = len(pts)
    if n < dims + 1:
        return None
    p = np.asarray(pts, float)
    d = np.asarray(ds, float)
    A = 2.0 * (p[1:] - p[0])
    b = (d[0] ** 2 - d[1:] ** 2) + np.sum(p[1:] ** 2, axis=1) - np.sum(p[0] ** 2)
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    return sol if np.all(np.isfinite(sol)) else None


def stats(errs: list) -> dict:
    if not errs:
        return {"n": 0, "median_m": None, "p95_m": None, "mean_m": None, "rmse_m": None, "max_m": None}
    e = np.asarray(errs, float)
    return {"n": int(e.size), "median_m": round(float(np.median(e)), 4), "p95_m": round(float(np.percentile(e, 95)), 4),
            "mean_m": round(float(e.mean()), 4), "rmse_m": round(float(np.sqrt(np.mean(e ** 2))), 4),
            "max_m": round(float(e.max()), 4)}


def run_once(scenario: dict, seed: int, duration: float) -> dict:
    """One simulated run. Returns error samples for the kit solver and the naive baseline."""
    sim = Simulator(scenario, seed=seed)
    dims = int(sim.dims)
    mesh = not sim.anchors
    tracker = Tracker(sim.profile, None if mesh else sim.anchors, dims=dims, nodes=sim.nodes, rate_hz=sim.rate_hz)
    targets = [n for n, v in sim.nodes.items() if v.get("role") != "anchor"]
    ids = sorted(sim.nodes)
    dt = 1.0 / float(sim.rate_hz)
    t0 = 1_800_000_000.0
    out = {"kit": [], "kit_los": [], "kit_nlos": [], "naive": [], "kit_3d": [], "ticks": 0, "fixes": 0,
           "solve_s": 0.0, "ranges": 0, "nlos_ranges": 0}
    steps = int(round(duration / dt))
    for k in range(steps):
        now = t0 + k * dt
        ranges = [m for m in sim.step(now) if m["type"] == "range"]
        out["ranges"] += len(ranges)
        out["nlos_ranges"] += sum(1 for r in ranges if r.get("los", 1.0) < 0.5)
        t_a = time.perf_counter()
        pos = tracker.update(ranges, now)
        out["solve_s"] += time.perf_counter() - t_a
        if k * dt < WARMUP_S:
            continue
        truth = sim.truth()["nodes"]
        if mesh:
            truth = canonical_frame(truth, ids, dims)
        by_id = {p["id"]: p for p in pos}
        for nid in targets:
            out["ticks"] += 1
            tr = truth[nid]
            p = by_id.get(nid)
            if p is not None:
                out["fixes"] += 1
                e2 = math.hypot(p["x"] - tr["x"], p["y"] - tr["y"])
                err = math.dist((p["x"], p["y"], p["z"]), (tr["x"], tr["y"], tr["z"])) if dims == 3 else e2
                out["kit"].append(err)
                if dims == 3:
                    out["kit_3d"].append(err)
                mine = [r for r in ranges if nid in (r["a"], r["b"])]
                nlos = any(r.get("los", 1.0) < 0.5 for r in mine)
                out["kit_nlos" if nlos else "kit_los"].append(err)
            if not mesh:
                mine = [r for r in ranges if nid in (r["a"], r["b"]) and (r["a"] in sim.anchors or r["b"] in sim.anchors)]
                sol = naive_fix(mine, sim.anchors, float(sim.nodes[nid].get("z", 1.0)), dims)
                if sol is not None:
                    if dims == 3:
                        out["naive"].append(math.dist(tuple(sol), (tr["x"], tr["y"], tr["z"])))
                    else:
                        out["naive"].append(math.hypot(sol[0] - tr["x"], sol[1] - tr["y"]))
    return out


def evaluate(scenario, seeds: int, duration: float) -> dict:
    sc = load_scenario(scenario)
    agg = {"kit": [], "kit_los": [], "kit_nlos": [], "naive": [], "ticks": 0, "fixes": 0, "solve_s": 0.0,
           "ranges": 0, "nlos_ranges": 0}
    base_seed = int(sc.get("seed", 1) or 1)
    for i in range(seeds):
        r = run_once(copy.deepcopy(sc), base_seed + 1000 * i, duration)
        for k in agg:
            agg[k] += r[k]
    sim = Simulator(copy.deepcopy(sc))
    return {
        "profile": sim.profile, "dims": int(sim.dims), "rate_hz": float(sim.rate_hz),
        "walls": len((sc.get("room") or {}).get("walls") or []),
        "noise": getattr(sim, "noise_model", None) or sc.get("noise"),
        "kit": stats(agg["kit"]),
        "kit_los_ticks": stats(agg["kit_los"]), "kit_nlos_ticks": stats(agg["kit_nlos"]),
        "naive_lsq": stats(agg["naive"]) if agg["naive"] else None,
        "fix_availability": round(agg["fixes"] / max(1, agg["ticks"]), 4),
        "nlos_range_fraction": round(agg["nlos_ranges"] / max(1, agg["ranges"]), 4),
        "solve_ms_per_tick": round(1000.0 * agg["solve_s"] / max(1, seeds * int(round(duration * sim.rate_hz))), 4),
        "error_metric": "3-D distance" if int(sim.dims) == 3 else
                        ("2-D distance in the canonical mesh frame, all nodes" if not sim.anchors else "2-D horizontal distance"),
    }


def without_walls(name: str) -> dict:
    sc = copy.deepcopy(load_scenario(name))
    sc.setdefault("room", {})["walls"] = []
    sc["name"] = f"{name} (walls removed)"
    return sc


def make_figure(results: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    INK, INK2, MUTED, GRID, AXIS, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
    BLUE, ORANGE, BLUE_LIGHT = "#2a78d6", "#eb6834", "#86b6ef"
    sc = results["scenarios"]
    names = sorted(sc, key=lambda n: sc[n]["kit"]["p95_m"] or 0)
    abl = [n for n in names if n in results["nlos_ablation"]]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": AXIS,
                         "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": INK})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.5, 6.4), dpi=110, gridspec_kw={"width_ratios": [1.45, 1.0]})
    fig.patch.set_facecolor(SURF)

    def lane(ax, y, st, color, label=None):
        """median dot -> p95 tick on one row."""
        if not st or st["median_m"] is None:
            return
        ax.plot([st["median_m"], st["p95_m"]], [y, y], color=color, lw=2, solid_capstyle="round", zorder=2)
        ax.plot([st["p95_m"]], [y], marker="|", ms=11, mew=2, color=color, zorder=3)
        ax.plot([st["median_m"]], [y], marker="o", ms=8.5, mfc=color, mec=SURF, mew=1.6, color=color, zorder=4, label=label)

    def style(ax, title, xmax):
        ax.set_facecolor(SURF)
        ax.set_xlim(0, xmax)
        ax.grid(axis="x", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.set_xlabel("position error vs simulator ground truth (m)")
        ax.set_title(title, loc="left", color=INK, fontsize=11.5, fontweight="bold", pad=30)

    # panel 1: every scenario, kit solver vs naive baseline
    xmax1 = 0.0
    for i, n in enumerate(names):
        r = sc[n]
        lane(ax1, i + 0.16, r["kit"], BLUE, "kit solver (robust LSQ + EKF; MDS for mesh)" if i == 0 else None)
        lane(ax1, i - 0.16, r["naive_lsq"], ORANGE, None)
        xmax1 = max(xmax1, r["kit"]["p95_m"] or 0, (r["naive_lsq"] or {}).get("p95_m") or 0)
    first_naive = next((i for i, n in enumerate(names) if sc[n]["naive_lsq"]), None)
    if first_naive is not None:
        ax1.plot([], [], marker="o", ms=8.5, mfc=ORANGE, mec=SURF, color=ORANGE, lw=2, label="naive per-tick least squares (no filter)")
    xmax1 = min(xmax1 * 1.32, 3.2)
    for i, n in enumerate(names):
        r = sc[n]
        ax1.text(xmax1 * 0.995, i + 0.16, f"{r['kit']['median_m'] * 100:.0f} / {r['kit']['p95_m'] * 100:.0f} cm",
                 ha="right", va="center", color=INK, fontsize=9)
        if r["naive_lsq"]:
            clip = "  (off scale)" if r["naive_lsq"]["p95_m"] > xmax1 else ""
            ax1.text(xmax1 * 0.995, i - 0.16, f"{r['naive_lsq']['median_m'] * 100:.0f} / {r['naive_lsq']['p95_m'] * 100:.0f} cm{clip}",
                     ha="right", va="center", color=INK2, fontsize=9)
    ax1.set_yticks(range(len(names)))
    ax1.set_yticklabels([f"{n}\n{sc[n]['profile']}" + (f", {sc[n]['walls']} walls" if sc[n]["walls"] else "") for n in names], fontsize=9.5)
    ax1.set_ylim(-0.6, len(names) - 0.4)
    style(ax1, "All built-in scenarios: dot = median, tick = 95th percentile", xmax1)
    ax1.legend(loc="lower left", bbox_to_anchor=(0.0, 1.005), ncol=2, frameon=False, fontsize=9, labelcolor=INK2,
               handletextpad=0.4, columnspacing=1.6, borderaxespad=0)

    # panel 2: NLOS ablation (one hue, two shades: with walls / walls removed)
    xmax2 = 0.0
    for i, n in enumerate(abl):
        on, off = sc[n]["kit"], results["nlos_ablation"][n]["kit"]
        lane(ax2, i + 0.16, on, BLUE, "as designed (NLOS through walls)" if i == 0 else None)
        lane(ax2, i - 0.16, off, BLUE_LIGHT, "walls removed (all line of sight)" if i == 0 else None)
        xmax2 = max(xmax2, on["p95_m"], off["p95_m"])
    xmax2 *= 1.42
    for i, n in enumerate(abl):
        on, off = sc[n]["kit"], results["nlos_ablation"][n]["kit"]
        ax2.text(xmax2 * 0.995, i + 0.16, f"{on['median_m'] * 100:.0f} / {on['p95_m'] * 100:.0f} cm", ha="right", va="center", color=INK, fontsize=9)
        ax2.text(xmax2 * 0.995, i - 0.16, f"{off['median_m'] * 100:.0f} / {off['p95_m'] * 100:.0f} cm", ha="right", va="center", color=INK2, fontsize=9)
    ax2.set_yticks(range(len(abl)))
    ax2.set_yticklabels([f"{n}\n{sc[n]['nlos_range_fraction'] * 100:.0f} % of ranges NLOS" for n in abl], fontsize=9.5)
    ax2.set_ylim(-0.6, max(1, len(abl)) - 0.4)
    style(ax2, "Effect of NLOS: kit solver with and without walls", xmax2)
    ax2.legend(loc="lower left", bbox_to_anchor=(0.0, 1.005), ncol=2, frameon=False, fontsize=9, labelcolor=INK2,
               handletextpad=0.4, columnspacing=1.6, borderaxespad=0)

    meta = results["meta"]
    fig.suptitle("uwb-kit solver accuracy  -  SIMULATED ranges, simulator ground truth", x=0.012, y=0.985, ha="left",
                 color=INK, fontsize=14, fontweight="bold")
    fig.text(0.012, 0.012, f"{meta['seeds']} seeds x {meta['duration_s']:g} s per scenario at the scenario rate, first {WARMUP_S:g} s discarded. "
             f"Noise model per scenario is in results/solver_accuracy.json. Numbers right of each row: median / p95.  uwbkit {meta['kit_version']}",
             color=MUTED, fontsize=8.5, ha="left")
    fig.tight_layout(rect=(0, 0.035, 1, 0.93), w_pad=3.0)
    fig.savefig(path, facecolor=SURF)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uwbkit eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--duration", type=float, default=120.0, help="simulated seconds per run")
    ap.add_argument("--out", default="results")
    ap.add_argument("--scenario", action="append", help="only these (built-in name or file); default: all built-ins")
    ap.add_argument("--no-figure", action="store_true")
    ap.add_argument("--figure-only", action="store_true", help="redraw the figure from an existing solver_accuracy.json")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.figure_only:
        make_figure(json.loads((out / "solver_accuracy.json").read_text(encoding="utf-8")), out / "solver_accuracy.png")
        print(f"wrote {out / 'solver_accuracy.png'}")
        return 0
    names = a.scenario or list_scenarios()
    t_start = time.time()
    results = {"meta": {"simulated": True, "kit_version": __version__, "seeds": a.seeds, "duration_s": a.duration,
                        "warmup_s": WARMUP_S, "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "command": "uwbkit eval " + " ".join(argv if argv is not None else sys.argv[2:]),
                        "note": "All numbers are SIMULATED: simulator ranges with the stated noise model versus simulator "
                                "ground truth. They say nothing yet about the real boards."},
               "scenarios": {}, "nlos_ablation": {}}
    print(f"{'scenario':16s} {'profile':7s} {'median':>8s} {'p95':>8s} {'naive med':>10s} {'naive p95':>10s} {'fix %':>6s} {'ms/tick':>8s}")
    for n in names:
        r = evaluate(n, a.seeds, a.duration)
        key = r_name = load_scenario(n).get("name", str(n))
        results["scenarios"][key] = r
        nv = r["naive_lsq"] or {}
        print(f"{r_name:16s} {r['profile']:7s} {r['kit']['median_m']:8.3f} {r['kit']['p95_m']:8.3f} "
              f"{nv.get('median_m') if nv else float('nan'):10.3f} {nv.get('p95_m') if nv else float('nan'):10.3f} "
              f"{100 * r['fix_availability']:6.1f} {r['solve_ms_per_tick']:8.3f}")
        if r["walls"]:
            results["nlos_ablation"][key] = evaluate(without_walls(n), a.seeds, a.duration)
            ab = results["nlos_ablation"][key]["kit"]
            print(f"{'  walls removed':16s} {'':7s} {ab['median_m']:8.3f} {ab['p95_m']:8.3f}")
    results["meta"]["runtime_s"] = round(time.time() - t_start, 1)
    tmp = out / "solver_accuracy.json.tmp"
    tmp.write_text(json.dumps(results, indent=1), encoding="utf-8")
    tmp.replace(out / "solver_accuracy.json")
    print(f"\nwrote {out / 'solver_accuracy.json'}  (SIMULATED; {results['meta']['runtime_s']} s)")
    if not a.no_figure:
        try:
            make_figure(results, out / "solver_accuracy.png")
            print(f"wrote {out / 'solver_accuracy.png'}")
        except ImportError:
            print("matplotlib not installed: skipped the figure (pip install matplotlib)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
