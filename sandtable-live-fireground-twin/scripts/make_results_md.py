#!/usr/bin/env python3
"""Writes the "Results" section of README.md (and the Scout table inside its radar section) from the files in results/
and vendor/beacon/results/, so that every number in the README is traceable to a file produced by code.

    python scripts/make_results_md.py        (re-run after any evaluation, recording, screenshot walk or test run)

It never fails on a missing or older file: that row says "not yet measured" and names the file it wanted.
Files read (all optional):
  vendor/beacon/results/eval_beacon.json, eval_layouts.json   lane L16's Beacon locator evaluation (scripts/eval_beacon.py there)
  results/scout_ablation.json                                 scripts/eval_scout.py
  results/radar_arithmetic.json                               scripts/radar_arithmetic.py
  results/sim_eval.json                                       scripts/eval_sim.py (tagged crews on the uwb-kit simulator)
  results/replay_check_*.json                                 scripts/check_replay.py (one file per exercise run)
  results/ui_walk.json                                        scripts/shots.cjs
  results/tests.json                                          bash run_tests.sh
  fixtures/llm_cache/*.json                                   recorded model responses (counted, never edited)
"""
import glob
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / "results"
VB = ROOT / "vendor" / "beacon" / "results"
README = ROOT / "README.md"
NYM = "not yet measured"


def load(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def pct(x, digits=0):
    if x is None:
        return "n/a"
    v = round(100 * float(x), digits)
    return f"{v:.{digits}f}%" if digits else f"{int(v)}%"


def num(x, unit="", nd=2):
    if x is None:
        return "n/a"
    return f"{round(float(x), nd):g}{unit}"


def g(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def missing(path: Path, what: str) -> list[str]:
    return [f"*{what}: {NYM} (`{rel(path)}` is missing or unreadable).*", ""]


# ---------------------------------------------------------------- Beacon (lane L16)
def beacon_section() -> list[str]:
    out = ["### Beacon: locating phones (lane L16's evaluation, SIMULATED)", ""]
    pb = VB / "eval_beacon.json"
    eb = load(pb)
    if not eb:
        out += missing(pb, "Beacon locator accuracy by method, pucks and twin")
    else:
        n = g(eb, "noise_model", "noise", "uwb") or {}
        trials = g(eb, "trial_counts", "main_trials")
        out += [f"From `{rel(pb)}`, made by lane L16's `scripts/eval_beacon.py` on its own simulator (not on phones). "
                f"{trials} simulated phones per row ({g(eb, 'definitions', 'phone positions') or 'positions as stated in the file'}); "
                f"each estimate after {num(eb.get('obs_s'), ' s', 1)} of observations. Stated UWB noise: {num(n.get('sigma_los_m'), ' m')} per range in line of sight, "
                f"a per-link bias of {num(n.get('link_bias_sigma_m'), ' m')}, +{num(n.get('wall_bias_m'), ' m')} through a wall, a body blocking the link "
                f"{pct(n.get('body_block_prob'))} of the time, {pct(n.get('outlier_prob'))} outliers, {pct(n.get('dropout_prob'))} dropouts. "
                "The simulator and the locator come from the same team; real iPhone ranges against these boards have not been measured.", "",
                "| Method | Pucks | Twin used | What was published: fix / candidates / ring / zone / nothing | Fix error, median (p90) | Truth inside what was published | Wrong fixes (of all trials) | Room right |",
                "|---|---|---|---|---|---|---|---|"]
        arms = [a for a in eb.get("arms") or [] if a.get("exp") == "main"]
        want = [("uwb-twr", 1, True), ("uwb-twr", 2, False), ("uwb-twr", 2, True), ("uwb-twr", 3, False), ("uwb-twr", 3, True),
                ("uwb-twr", 5, True), ("ble-rssi", 1, True), ("ble-rssi", 3, True)]
        label = {"uwb-twr": "UWB range (app)", "ble-rssi": "Bluetooth signal strength (app)", "uwb-twr+bearing": "UWB + phone bearing"}
        for m, k, tw in want:
            a = next((x for x in arms if x.get("method") == m and x.get("pucks") == k and bool(x.get("twin")) == tw), None)
            if not a:
                out.append(f"| {label.get(m, m)} | {k} | {'yes' if tw else 'no'} | {NYM} | | | | |")
                continue
            s = a.get("geometry_share") or {}
            share = " / ".join(pct(s.get(x)) for x in ("fix", "candidates", "ring", "zone", "none"))
            fe = "no fixes" if not a.get("fix_n") else f"{num(a.get('fix_err_median_m'), ' m')} ({num(a.get('fix_err_p90_m'), ' m')})"
            room = pct(a.get("room_accuracy")) if a.get("room_accuracy") is not None else "no room published"
            out.append(f"| {label.get(m, m)} | {k} | {'yes' if tw else 'no'} | {share} | {fe} | {pct(a.get('truth_inside_3sigma'))} | {pct(a.get('wrong_fix_rate_of_trials'))} | {room} |")
        tp = next((x for x in g(eb, "effects", "twin_pruning", default=[]) or [] if x.get("method") == "uwb-twr" and x.get("pucks") == 2), None)
        out.append("")
        if tp:
            out.append(f"**What the twin does with two pucks** (the demo's set-up): with UWB ranges from two pucks, using the twin's walls and outline "
                       f"raised the share of phones published as one point from {pct(tp.get('fix_share_off'))} to {pct(tp.get('fix_share_on'))} and cut the share left as two "
                       f"candidates from {pct(tp.get('candidates_share_off'))} to {pct(tp.get('candidates_share_on'))}; wrong fixes: {pct(tp.get('wrong_fix_rate_off'))} without the twin, "
                       f"{pct(tp.get('wrong_fix_rate_on'))} with it. A third puck without the twin published a point in "
                       f"{pct(next((x.get('fix_share_off') for x in g(eb, 'effects', 'twin_pruning', default=[]) if x.get('method') == 'uwb-twr' and x.get('pucks') == 3), None))} of trials.")
        cold = eb.get("cold") or {}
        if cold:
            out.append(f"**Phones without the app** (cold Bluetooth count, {cold.get('n_windows')} simulated 10 s windows): the published device band contained the true number of "
                       f"advertising devices in the room {pct(cold.get('band_covers_true_advertising'))} of the time, and the count read zero while devices were present "
                       f"{pct(cold.get('count_zero_while_devices_present'))} of the time. This is why the board shows a count band and never people.")
        out.append("")
    pl = VB / "eval_layouts.json"
    el = load(pl)
    if not el:
        out += missing(pl, "Two boards on one thrown device against separate pucks")
    else:
        rec = el.get("recommendation") or {}
        out += [f"**Two boards on one device, or separate pucks?** From `{rel(pl)}` (same simulator; phone 2 to 8 m away, {g(el, 'trials_per_arm', 'no_bearing')} trials per row; "
                "rows with a phone bearing are in the file and left out here, because an iPhone 14 or 15 reports no direction to these boards):", "",
                "| Layout | Published as one point | Bearing error, median | Both mirror sides still open | Twin cut it to one point |", "|---|---|---|---|---|"]
        for name, r in rec.items():
            if name.endswith("+ bearing"):
                continue                      # the owner's iPhones give no bearing to an accessory; those rows stay in the file
            out.append(f"| {name} | {pct(r.get('fix_share'))} | {num(r.get('bearing_err_median_deg'), ' deg', 1)} | {pct(r.get('mirror_ambiguity_rate'))} | {pct(r.get('twin_single_rate'))} |")
        out.append("")
    return out


# ---------------------------------------------------------------- Scout (radar, simulated)
def scout_table() -> list[str]:
    p = R / "scout_ablation.json"
    d = load(p)
    if not d or "per_pucks" not in d:
        return missing(p, "Scout radar by number of pucks")
    nm = d.get("noise_model") or {}
    out = [f"SIMULATED, from `{rel(p)}` (`{d.get('command', 'scripts/eval_scout.py')}`): {d.get('n_seeds')} seeded throws per row into {g(d, 'room', 'label')}, "
           f"{num(d.get('scan_s'), ' s', 1)} scans at {num(d.get('frame_hz'), ' Hz', 1)}, the full pipeline (self-location, registration into the twin, detection). "
           f"Noise model: direct-path SNR {num(nm.get('snr_db_1m'), ' dB', 1)} at 1 m, random carrier phase per frame, multipath flicker, person radar cross-section {nm.get('person_rcs_m2', 'n/a')} (m²). "
           "The simulator was written alongside the detector, so real boards will do worse.", "",
           "| Pucks | Links | Breathing person within 4.5 m of a puck: reported / within 0.6 m | Median error when reported | Breathing rate error, median | Person anywhere in the room: reported | Empty room: false alarms | Still, not breathing: reported |",
           "|---|---|---|---|---|---|---|---|"]
    for k in sorted(d["per_pucks"], key=lambda s: int(s)):
        r = d["per_pucks"][k]
        reach, nat, emp, still = (r.get(c) or {} for c in ("reach", "natural", "empty", "still"))
        rp = g(reach, "reported", "rate")
        if int(k) == 2:
            loc, err = "presence and range only", "no position"
        else:
            loc, err = pct(g(reach, "within_0.6m", "rate")), num(g(reach, "error_m", "median"), " m")
        if int(k) == 1:
            loc = err = "-"
        out.append(f"| {k} | {r.get('links')} | {pct(rp)} / {loc} | {err} | {num(g(reach, 'bpm_error', 'median'), ' /min', 1)} | {pct(g(nat, 'reported', 'rate'))} | "
                   f"{pct(g(emp, 'reported', 'rate'))} | {pct(g(still, 'reported', 'rate'))} |")
    why1 = (g(d, "per_pucks", "1", "why_examples") or [None])[0]
    out += ["", f"One puck reports nothing by construction ({why1 or 'it cannot receive its own echo'}). Rates are over all scans; Wilson 95% intervals are in the file.", ""]
    return out


def eval_scout_block() -> list[str]:
    """The table block that scripts/eval_scout.py writes into docs/readme_radar_section.md (from results/scout_*.json),
    copied unchanged into the README's radar section. Empty when that block does not exist yet."""
    try:
        t = (ROOT / "docs" / "readme_radar_section.md").read_text()
    except Exception:
        return []
    m = re.search(r"<!-- eval_scout:begin[^>]*-->\n(.*?)<!-- eval_scout:end -->", t, flags=re.S)
    if not m or "|" not in m.group(1) or not (R / "scout_ablation.json").exists():
        return []
    return m.group(1).rstrip("\n").split("\n") + [""]


def radar_numbers() -> list[str]:
    p = R / "radar_arithmetic.json"
    d = load(p)
    if not d:
        return missing(p, "Radar arithmetic")
    rt3 = g(d, "frame_timing", "round_trip_to_wall", "3 m") or {}
    reg = g(d, "consistent_regions", "tol_0.30_m_path") or {}
    lb = d.get("link_budget") or {}
    ref = lb.get("real_reference_dwm1000") or {}
    rows = [("Shortest DW3000 preamble alone", f"{num(g(d, 'frame_timing', 'preamble', '32', 'preamble_us'), ' µs')}", "datasheet arithmetic"),
            ("Round trip to a wall 3 m away", f"{num(rt3.get('round_trip_ns'), ' ns')} (the preamble lasts {num(rt3.get('shortest_preamble_over_round_trip'), ' times', 0)} as long)", "arithmetic"),
            ("Monostatic range resolution at 499.2 MHz", num(g(d, "resolution", "monostatic_range_resolution_m"), " m"), "arithmetic"),
            ("Bistatic resolution at 90 and 120 degrees", f"{num(g(d, 'resolution', 'bistatic_range_resolution_m_by_bistatic_angle', '90 deg'), ' m')} and {num(g(d, 'resolution', 'bistatic_range_resolution_m_by_bistatic_angle', '120 deg'), ' m')}", "arithmetic"),
            ("Wavelength, channel 5", num(g(d, "wavelength", "ch5_wavelength_mm"), " mm", 1), "arithmetic"),
            ("Room area consistent with every pair (sample throw, one-tap tolerance): 2 / 3 / 5 pucks",
             " / ".join(num(g(reg, k, "area_m2"), " m²") for k in ("2", "3", "5")), "geometry, no noise"),
            ("Sample victim's echo below the direct pulse: best / median / worst pair",
             f"{num(lb.get('sample_victim_best_db'), ' dB', 1)} / {num(lb.get('sample_victim_median_db'), ' dB', 1)} / {num(lb.get('sample_victim_worst_db'), ' dB', 1)}", "link budget, assumed cross-section"),
            ("Empty room's own multipath flicker in the one real recording", num(ref.get("quiet_scene_residual_after_direct_path_db"), " dB", 1), "real DWM1000 data (ETH Zurich), analysed by lane L08"),
            ("Lane L08's tracker on that real recording", str(g(d, "l08_excerpt", "real_eth_json", "tracking_verdict") or "n/a"), "real data, not our hardware")]
    out = [f"**Radar arithmetic quoted above** (`{rel(p)}`, from `scripts/radar_arithmetic.py`; {d.get('kind', '')}):", "",
           "| Quantity | Value | Kind |", "|---|---|---|"]
    out += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return out + [""]


# ---------------------------------------------------------------- Crews (tagged firefighters, simulated)
def crews_section() -> list[str]:
    p = R / "sim_eval.json"
    ev = load(p)
    out = ["### Crews: tagged firefighters on the uwb-kit simulator (SIMULATED)", ""]
    if not ev or not ev.get("rigs"):
        return out + missing(p, "Crew positioning")
    out += [f"From `{rel(p)}` (`scripts/eval_sim.py`). Positions come from the shared uwb-kit simulator through the kit's solver, then this board's registration and map matching, as in the mayday exercise. No board was attached.", "",
            "| Rig (scenario file) | Scale | Simulator noise | Error in the building, median / p90 | Room-level location right | Corridors only | Maydays detected | Latency, mean | False maydays in 40 s of walking |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in ev["rigs"]:
        a, n, m = r.get("accuracy") or {}, r.get("noise_model") or {}, r.get("mayday") or {}
        noise = f"range sigma {n.get('sigma_m')} m, {pct(n.get('outlier_prob'))} outliers, {pct(n.get('dropout_prob'))} dropouts"
        out.append(f"| `{Path(r.get('scenario', '?')).name}` | 1:{num(r.get('registration_scale'), '', 1)} | {noise} | {g(a, 'error_building_m', 'median')} m / {g(a, 'error_building_m', 'p90')} m | "
                   f"{a.get('room_level_accuracy_pct')}% of {a.get('samples')} | {g(a, 'room_level_accuracy_pct_by_room_kind', 'corridor')}% | {m.get('detected')} of {m.get('trials')} | "
                   f"{g(m, 'latency_s', 'mean')} s (threshold {num(m.get('threshold_s'), ' s', 1)}) | {m.get('false_maydays_while_walking_40s')} |")
    return out + [""]


# ---------------------------------------------------------------- exercises, agent, UI, tests
def runs_section() -> list[str]:
    out = ["### The exercises, end to end", ""]
    files = sorted(glob.glob(str(R / "replay_check_*.json")))
    if not files:
        out += missing(R / "replay_check_<mode>_<rig>.json", "Exercise replays")
    else:
        out += ["From `results/replay_check_*.json` (`scripts/check_replay.py`, run against a live board). Each row is one complete exercise on the simulator; "
                "it lists where every command-agent turn came from: `replay` (a recorded real model response), `live` (a model called during the run) or `fallback` (the labelled deterministic policy).", "",
                "| Script | Mode | Rig | Wall time | Agent turns: replay / live / fallback | Turns that fell back |", "|---|---|---|---|---|---|"]
        for f in files:
            c = load(Path(f)) or {}
            script = c.get("script") or "mayday"
            mode = {"auto": "hands-free", "assist": "guided"}.get(c.get("mode"), c.get("mode"))
            src = [s for v in (c.get("runs") or {}).values() for s in (v if isinstance(v, list) else [v])]
            cnt = {k: sum(1 for s in src if str(s).startswith(k)) for k in ("replay", "live", "fallback")}
            cnt["fallback"] += sum(1 for s in src if str(s).startswith("reflex"))
            fb = c.get("fallback_runs") or []
            out.append(f"| {script} | {mode} | {c.get('profile')} | {c.get('wall_s')} s | {cnt['replay']} / {cnt['live']} / {cnt['fallback']} | "
                       f"{', '.join(map(str, fb)) if fb else 'none'} |")
        scripts_seen = {(load(Path(f)) or {}).get("script") or "mayday" for f in files}
        for s in ("beacon", "scout", "mayday"):
            if s not in scripts_seen:
                out.append(f"| {s} | | | {NYM} | | |")
        out.append("")
    cache = sorted((ROOT / "fixtures" / "llm_cache").glob("*.json"))
    if cache:
        recs = [load(p) or {} for p in cache]
        models = sorted({r.get("model") or "?" for r in recs})
        when = sorted(r.get("recorded_at") for r in recs if r.get("recorded_at"))
        out.append(f"Recorded agent responses: {len(cache)} files in `fixtures/llm_cache/`, model {', '.join(models)}, recorded through the Claude CLI between "
                   f"{when[0][:16] if when else '?'} and {when[-1][:16] if when else '?'} (local time). The demo replays them; nothing in that folder is hand-written.")
    else:
        out.append(f"Recorded agent responses: {NYM} (`fixtures/llm_cache/` is empty).")
    u = load(R / "ui_walk.json")
    if u:
        runs = u.get("runs") or {u.get("exercise", "?"): u}
        parts = [f"{k} {v.get('storyboard_wall_s')} s, {len(v.get('figures') or [])} screenshots, {len(v.get('console_issues') or [])} console messages"
                 for k, v in runs.items()]
        out.append(f"Headless-browser walk of the board (`results/ui_walk.json`, `scripts/shots.cjs`): {'; '.join(parts)}.")
    else:
        out.append(f"Headless-browser walk: {NYM}.")
    t = load(R / "tests.json")
    if t:
        out.append(f"Tests (`{t.get('command', 'bash run_tests.sh')}`, offline, replay-mode model): {t.get('passed')} passed, {t.get('failed')} failed in {t.get('duration_s')} s, last run {str(t.get('last_run', ''))[:16]}.")
    else:
        out.append(f"Tests: {NYM}.")
    return out + [""]


def main() -> None:
    lines = ["## Results (generated by `scripts/make_results_md.py` from the files named; everything is simulated or arithmetic unless a row says otherwise)", "",
             "No UWB board and no phone has been in the loop of any number below.", ""]
    lines += beacon_section()
    radar_block = eval_scout_block()
    if radar_block:
        lines += ["### Scout: the pucks as a radar (SIMULATED)", "",
                  "Detection by number of pucks: the table in the radar section below, generated by `scripts/eval_scout.py` from `results/scout_ablation.json` "
                  "and `results/scout_placement.json`. The rest of that file, in short:", ""] + scout_table() + radar_numbers()
    else:
        lines += ["### Scout: the pucks as a radar (SIMULATED)", ""] + scout_table() + radar_numbers()
    lines += crews_section()
    lines += runs_section()
    text = README.read_text() if README.exists() else ""
    block = "\n".join(lines)
    if re.search(r"^## Results", text, flags=re.M):
        text = re.sub(r"^## Results.*?(?=^## )", lambda _m: block + "\n", text, count=1, flags=re.S | re.M)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    scout = "\n".join(["<!-- scout-ablation:start (generated by scripts/make_results_md.py) -->"] + (radar_block or scout_table()) + ["<!-- scout-ablation:end -->"])
    text = re.sub(r"<!-- scout-ablation:start.*?<!-- scout-ablation:end -->", lambda _m: scout, text, count=1, flags=re.S)
    README.write_text(text)
    print(block)


if __name__ == "__main__":
    main()
