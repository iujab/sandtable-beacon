"""Write the simulated numbers into the documents, from the files in results/ (so no number is typed by hand).

    python scripts/fill_numbers.py            # rewrites the marked blocks in docs/ and hardware/
    python scripts/fill_numbers.py --print    # prints the blocks only

A block is marked in a document either by a placeholder comment (`<!-- LAYOUT_NUMBERS ... -->`,
`<!-- TWO_PUCK_NUMBERS ... -->`) or, after the first run, by a BEGIN / END pair of the same name.
Everything here is SIMULATED; the blocks say so.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pct(x) -> str:
    return "n/a" if x is None else f"{100.0 * float(x):.0f}%"


def pct1(x) -> str:
    return "n/a" if x is None else f"{100.0 * float(x):.1f}%"


def m(x) -> str:
    return "n/a" if x is None else f"{float(x):.2f} m"


def two_puck_block() -> str:
    d = json.loads((ROOT / "results" / "eval_two_puck.json").read_text(encoding="utf-8"))
    names = [("door-wall", "Both pucks just inside the wall the door is in (recommended)"),
             ("exterior-wall", "Both pucks along the outside wall"),
             ("careless", "Both pucks mid-room (careless)")]
    three = next((k for k in d["layouts"] if k.startswith("three")), None)
    if three:
        names.append((three, "Three pucks: the door-wall pair plus one in the far corner"))
    lines = [f"**Simulated** (`results/eval_two_puck.json`; {d['layouts']['door-wall']['n']} phone positions per layout over the fire room; "
             "what a real iPhone would deliver: UWB distances only, no line-of-sight flag; noise model with sources in `beacon/models.py`):",
             "",
             "| Layout | One position | of which by the twin alone | of which after the occupant's stated suite | Cannot choose (two spots) | Ring or less | Wrong position: share of the positions shown (share of all trials) | Position error, median / 90th percentile |",
             "|---|---|---|---|---|---|---|---|"]
    for key, label in names:
        L = d["layouts"][key]
        o, a = L["outcome_share"], L["all_fixes"]
        lines.append(f"| {label} | {pct(a['share_of_trials'])} | {pct(o['twin_fix'])} | {pct(o['statement_fix'])} | {pct(o['cannot_choose'])} | "
                     f"{pct(o['ring_or_other'])} | {pct1(a.get('wrong_fix_rate_of_fixes'))} ({pct1(a['wrong_fix_rate_of_trials'])}) | {m(a['fix_err_median_m'])} / {m(a['fix_err_p90_m'])} |")
    fs = d.get("false_statement", {}).get("door-wall")
    if fs:
        worst = max((v.get("statement_fix", 0.0) for v in (fs.get("by_true_room") or {}).values()), default=None)
        ts = turns_sentence()
        if ts:
            lines += ["", ts]
        lines += ["",
                  f"\"Ring or less\" is mostly a phone that only one puck reached (a blocked link in the simulation). "
                  f"A wrong position is one more than three sigma or 1.5 m from the truth.",
                  "",
                  f"When the occupant's stated suite is false (the phone is really in a neighbouring room, {fs['n']} trials): with the door-wall layout the board mostly shows a ring, "
                  f"but it shows a position in {pct1(fs['all_fixes']['share_of_trials'])} of trials and {pct(fs['all_fixes'].get('wrong_fix_rate_of_fixes'))} of those positions are wrong "
                  f"({pct1(fs.get('statement_produced_wrong_fix_rate_of_trials'))} of trials are wrong positions produced by the statement"
                  + (f"; {pct1(worst)} for the suite that shares that wall" if worst is not None else "")
                  + "). That is the price of dismissing a spot on a statement, and why the board shows the dismissal."]
    return "\n".join(lines)


def layouts_block() -> str:
    d = json.loads((ROOT / "results" / "eval_layouts.json").read_text(encoding="utf-8"))
    r = d["recommendation"]
    order = [k for k in r if "+ bearing" not in k and "bearing" not in k.split(",")[0]]
    lines = [f"**Simulated** (`results/eval_layouts.json`; {next(iter(r.values()))['n']} trials per row, phone 2 to 8 m away in the fire room, pucks thrown not placed; "
             "noise model with sources in `beacon/models.py`). Bearing error is measured after choosing the nearer of the two mirror solutions, so the mirror ambiguity is on top of it:",
             "",
             "| Layout | Bearing error to the phone, median | Sideways (cross-range) error, median | Mirror ambiguity left | A position is published | Position error when published, median | Area the phone is confined to, median |",
             "|---|---|---|---|---|---|---|"]
    for k in order:
        v = r[k]
        be = "n/a" if v.get("bearing_err_median_deg") is None else f"{v['bearing_err_median_deg']:.0f} degrees"
        lines.append(f"| {k[0].upper() + k[1:]} | {be} | {m(v.get('cross_range_median_m'))} | {pct(v.get('mirror_ambiguity_rate'))} | {pct(v.get('fix_share'))} | "
                     f"{m(v.get('fix_err_median_m'))} | {v['hpd_area_median_m2']:.1f} m2 |")
    lines += ["",
              "Two boards on one device never produce a position: the locator publishes a ring. Two separate pucks a few metres apart confine the phone to a much smaller area "
              "with a several times better bearing, and the twin removes the mirror solution in part of the cases even with thrown pucks (the placed layouts are in `results/eval_two_puck.json`)."]
    return "\n".join(lines)


def main_block() -> str:
    d = json.loads((ROOT / "results" / "eval_beacon.json").read_text(encoding="utf-8"))
    arms = {(a["exp"], a["method"], a["pucks"], a["twin"]): a for a in d["arms"]}
    n = d["arms"][0]["n"]
    nz = d["noise_model"]["noise"]
    lines = [f"**Simulated** (`results/eval_beacon.json`; {n} trials per cell; pucks thrown from the door, not placed; the phone is in the fire room in 60% of trials and in a neighbouring suite in 40%; "
             f"noise model: UWB {nz['uwb']['sigma_los_m']:.2f} m white noise plus a static {nz['uwb']['link_bias_sigma_m']:.2f} m bias per link, +{nz['uwb']['wall_bias_m']:.2f} m per wall, "
             f"a body in the way on {100 * nz['uwb']['body_block_prob']:.0f}% of links; Bluetooth {nz['ble']['shadow_sigma_db']:.0f} dB shadowing; sources in `beacon/models.py`):",
             "",
             "| Method | Pucks | A position is published | Position error, median | Area the phone is confined to, median | Wrong position: share of the positions shown (share of all trials) |",
             "|---|---|---|---|---|---|"]
    names = {"ble-rssi": "Bluetooth signal strength", "uwb-twr": "UWB ranging"}
    for method in ("ble-rssi", "uwb-twr"):
        for k in (1, 2, 3, 4, 5):
            a = arms.get(("main", method, k, True))
            if not a:
                continue
            lines.append(f"| {names[method]} | {k} | {pct(a['geometry_share']['fix'])} | {m(a.get('fix_err_median_m'))} | {a['hpd_area_median_m2']:.1f} m2 | {pct1(a.get('wrong_fix_rate_of_fixes'))} ({pct1(a['wrong_fix_rate_of_trials'])}) |")
    mm = [arms[k] for k in arms if k[0] == "mismatch" and k[1] == "uwb-twr"]
    if mm:
        worst = max(a["wrong_fix_rate_of_trials"] for a in mm)
        worst_f = max((a.get("wrong_fix_rate_of_fixes") or 0.0) for a in mm)
        lines += ["", f"When the simulated world is 1.5 times noisier than the locator assumes, wrong positions rise to at most {pct1(worst_f)} of the positions shown ({pct1(worst)} of trials; UWB, any puck count)."]
    c = d.get("cold")
    if c:
        lines += ["", f"Cold Bluetooth count (no app): over {c['n_windows']} windows the count runs {abs(c['count_minus_true_advertising_mean']):.1f} devices low on average, the published band contains the true number of advertising devices "
                      f"in {pct(c['band_covers_true_advertising'])} of windows, and {pct(c['count_zero_while_devices_present'])} of windows read zero while devices are in the room. It is a weak signal and the board treats it as one."]
    return "\n".join(lines)


def honesty_block() -> str:
    d = json.loads((ROOT / "results" / "honesty_check.json").read_text(encoding="utf-8"))
    sc = d["scenarios"]
    items = sc.items() if isinstance(sc, dict) else [(x.get("name"), x) for x in sc]
    worst_name, worst = None, -1.0
    steps = 0
    for name, v in items:
        steps += int(v.get("steps") or 0)
        w = float(v.get("wrong_share_of_fix_steps") or 0.0)
        if w > worst:
            worst_name, worst = name, w
    n = len(list(sc))
    return (f"**Simulated self-check** (`scripts/honesty_check.py`, results in `results/honesty_check.json`): {n} adversarial scenes on the simulator "
            f"(the walk-through, the mid-room layout held for two minutes, a puck moved while ranging, the phone dragged into the next room, the exercise scale changed, "
            f"a phone going quiet, stop sharing, two phones on shared boards), {steps:,} steps. In every scene: no coordinates without a position, no dot for a stale phone, "
            f"nothing republished after stop sharing. A position is counted wrong when it is more than three sigma and more than a metre from the truth: "
            f"{'no scene shows any' if worst <= 0 else f'the worst scene ({worst_name}) shows {pct1(worst)} of its position steps wrong, and the others none' if sum(1 for _n, v in items if float(v.get('wrong_share_of_fix_steps') or 0) > 0) == 1 else f'the worst scene ({worst_name}) shows {pct1(worst)} of its position steps wrong'}. "
            f"The check fails above {pct(d['thresholds']['max_wrong_share_of_fix_steps'])}; it currently {'passes' if d.get('pass') else 'FAILS'}.")


def turns_sentence() -> str:
    d = json.loads((ROOT / "results" / "eval_two_puck.json").read_text(encoding="utf-8"))
    t = (d.get("two_phones") or {}).get("turns")
    if not t:
        return ""
    return (f"Two phones taking turns on two boards (simulated, {t['phone_scenes']} phone scenes): the wait for a position is {t['wait_first_fix']['median_s']:.1f} s in the median "
            f"and {t['wait_first_fix']['p90_s']:.0f} s at the 90th percentile; {pct(t['wait_first_fix']['never_within_scene'])} get none within the scene; "
            f"{pct1(t['wrong_fix_rate_of_fixes'])} of the positions shown are wrong.")


TARGETS = {
    "docs/PHONE_FEASIBILITY.md": {"LAYOUT_NUMBERS": layouts_block, "TWO_PUCK_NUMBERS": two_puck_block},
    "hardware/BRINGUP.md": {"LAYOUT_NUMBERS": two_puck_block},
    "hardware/LAYOUT.md": {"LAYOUT_NUMBERS": two_puck_block},
    "hardware/PUCK.md": {"LAYOUT_NUMBERS": layouts_block},
    "README.md": {"TWO_PUCK_NUMBERS": two_puck_block, "LAYOUT_NUMBERS": layouts_block, "MAIN_NUMBERS": main_block, "HONESTY_NUMBERS": honesty_block},
}


def fill(text: str, name: str, block: str) -> tuple[str, bool]:
    begin, end = f"<!-- {name}:BEGIN -->", f"<!-- {name}:END -->"
    body = f"{begin}\n{block}\n{end}"
    if begin in text and end in text:
        return re.sub(re.escape(begin) + r".*?" + re.escape(end), lambda _m: body, text, count=1, flags=re.S), True
    pat = re.compile(r"<!-- " + re.escape(name) + r"(?::[^>]*| [^>]*)? ?-->")
    if pat.search(text):
        return pat.sub(lambda _m: body, text, count=1), True
    return text, False


def main() -> int:
    if "--print" in sys.argv:
        print(two_puck_block(), "\n\n", layouts_block(), "\n\n", main_block())
        return 0
    for rel, blocks in TARGETS.items():
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        done = []
        for name, fn in blocks.items():
            text, ok = fill(text, name, fn())
            if ok:
                done.append(name)
        path.write_text(text, encoding="utf-8")
        print(f"{rel}: {', '.join(done) if done else 'no marker found'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
