"""``uwbkit anchors``: tell the hub where the anchors are, and calibrate ranges.

    uwbkit anchors show
    uwbkit anchors rect 6.0 4.5 --z 2.0          anchors on the corners of a W x D rectangle (the fastest tape-measure path):
                                                 A1 = (0,0)  A2 = (W,0)  A3 = (W,D)  A4 = (0,D)
    uwbkit anchors set A1=0,0,2 A2=6,0,2 A3=6,4.5,2 A4=0,4.5,2     any layout, coordinates in metres
    uwbkit anchors tape --z 2.0                  type the tape-measured distances between anchors; the kit solves the layout
    uwbkit anchors tape A1-A2=6.02 A1-A3=7.51 A1-A4=4.49 A2-A3=4.5 A2-A4=7.5 A3-A4=6.0
    uwbkit anchors auto --z 2.0                  self-calibration: the anchors range each other (POST /calibrate)
    uwbkit anchors antdelay --node T1 --peer A1 --true 2.00        firmware antenna-delay calibration at a taped distance
    uwbkit anchors bias --pair T1-A1 --true 2.00                   hub-side offset for one link (no reflash needed)
    uwbkit anchors bias --clear

Frame convention for `rect`, `tape` and `auto`: A1 at the origin, A2 on +x, A3 with y > 0.
All commands talk to a running hub (--url, default $UWBKIT_URL or http://127.0.0.1:1800). Real-board
hubs save the result in uwbkit_anchors.json and reload it at the next start.
"""
from __future__ import annotations

import argparse
import math
import sys
import time

from ..client import Kit, KitError


def _show(kit: Kit) -> None:
    a = kit.state().get("anchors") or {}
    print(f"anchors ({a.get('calibrated', '?')}, frame {a.get('frame', '?')}):")
    if not a.get("anchors"):
        print("  none set")
    for k, v in sorted((a.get("anchors") or {}).items()):
        print(f"  {k}: x={v['x']:.3f}  y={v['y']:.3f}  z={v['z']:.3f}")


def _pairs_report(anchors: dict) -> None:
    ids = sorted(anchors)
    print("implied distances (check a couple with the tape):")
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = anchors[ids[i]], anchors[ids[j]]
            d = math.dist((a["x"], a["y"], a.get("z", 0)), (b["x"], b["y"], b.get("z", 0)))
            print(f"  {ids[i]}-{ids[j]}: {d:.3f} m")


def cmd_set(kit, a) -> int:
    anchors = {}
    for item in a.items:
        name, eq, coords = item.partition("=")
        parts = [p for p in coords.split(",") if p != ""]
        if not eq or len(parts) not in (2, 3):
            print(f"bad anchor {item!r}: use A1=x,y or A1=x,y,z")
            return 2
        x, y = float(parts[0]), float(parts[1])
        anchors[name.strip()] = {"x": x, "y": y, "z": float(parts[2]) if len(parts) == 3 else a.z}
    kit.set_anchors(anchors)
    _show(kit)
    _pairs_report(anchors)
    return 0


def cmd_rect(kit, a) -> int:
    w, d, z = a.width, a.depth, a.z
    anchors = {"A1": {"x": 0.0, "y": 0.0, "z": z}, "A2": {"x": w, "y": 0.0, "z": z},
               "A3": {"x": w, "y": d, "z": z}, "A4": {"x": 0.0, "y": d, "z": z}}
    if a.three:
        anchors.pop("A4")
    kit.set_anchors(anchors)
    _show(kit)
    print("Stand at A1 looking at A2: +x is ahead of you, +y is to your LEFT. A3 is diagonal from A1.")
    _pairs_report(anchors)
    return 0


def cmd_tape(kit, a) -> int:
    from ..solver import solve_layout
    hello = kit.state().get("hello") or {}
    ids = sorted(n for n, v in (hello.get("nodes") or {}).items() if v.get("role") == "anchor") or ["A1", "A2", "A3", "A4"]
    dists = {}
    for item in a.items:
        name, eq, val = item.partition("=")
        if not eq:
            print(f"bad distance {item!r}: use A1-A2=6.02")
            return 2
        dists[name.strip()] = float(val)
    if not dists:
        print(f"Tape-measure the straight-line distance between each pair of anchors ({', '.join(ids)}), in metres.")
        print("Enter to skip a pair you cannot measure (4 anchors need at least 5 of the 6).")
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                try:
                    txt = input(f"  {ids[i]}-{ids[j]} [m]: ").strip()
                except EOFError:
                    txt = ""
                if txt:
                    dists[f"{ids[i]}-{ids[j]}"] = float(txt)
    try:
        layout = solve_layout(dists, ids, dims=2, z=None)
    except Exception as e:
        print(f"could not solve the layout: {e}")
        return 2
    missing = [i for i in ids if i not in layout]
    if missing:
        print(f"not enough distances to place {missing}")
        return 2
    worst = 0.0
    for key, d in dists.items():
        p, q = key.split("-")
        est = math.dist((layout[p]["x"], layout[p]["y"]), (layout[q]["x"], layout[q]["y"]))
        worst = max(worst, abs(est - d))
    for v in layout.values():
        v["z"] = a.z
    kit.set_anchors(layout)
    _show(kit)
    print(f"worst mismatch between your distances and the solved layout: {worst * 100:.1f} cm"
          + ("  <- re-measure: over 10 cm" if worst > 0.10 else ""))
    return 0


def cmd_auto(kit, a) -> int:
    body = {"duration_s": a.duration}
    if a.z is not None:
        body["z"] = a.z
    t0 = time.time()
    r = kit.post("/calibrate", body)
    wait = float(r.get("duration_s", a.duration)) + 6.0
    print(f"self-calibration started: anchors range each other for {r.get('duration_s', a.duration):g} s ...")
    while time.time() - t0 < wait:
        time.sleep(0.5)
        cal = kit.diag().get("calibration") or {}
        if cal.get("t", 0) >= t0 - 0.5:
            if cal.get("ok"):
                print(f"ok: layout solved from {cal.get('pairs')} anchor pairs, residual {cal.get('rms_m', 0) * 100:.1f} cm rms")
                _show(kit)
                _pairs_report((kit.state().get("anchors") or {}).get("anchors") or {})
                print("Frame: A1 at the origin, A2 on +x, A3 with y > 0. Anchor height z was "
                      + ("set to %.2f m." % a.z if a.z is not None else "kept as it was (set it with --z)."))
                return 0
            print(f"calibration failed: {cal.get('error')}")
            return 2
    print("no calibration result arrived (is the hub still running?)")
    return 2


def cmd_antdelay(kit, a) -> int:
    line = f"CAL {a.peer} {a.true:.3f}" + (f" {a.n}" if a.n else "")
    before = ((kit.diag().get("nodes") or {}).get(a.node) or {}).get("kv", {}).get("antdly")
    r = kit.post("/command", {"line": line, "node": a.node})
    if not r.get("ok"):
        print("the hub could not reach the board (no back-channel on this source)")
        return 2
    print(f"sent `{line}` to {a.node}; keep both boards still at {a.true:.2f} m ...")
    t0 = time.time()
    while time.time() - t0 < a.wait:
        time.sleep(0.5)
        now = ((kit.diag().get("nodes") or {}).get(a.node) or {}).get("kv", {}).get("antdly")
        if now is not None and now != before:
            kit.post("/command", {"line": "SAVE", "node": a.node})
            print(f"{a.node} antenna delay: {before} -> {now} (saved on the board)")
            return 0
    print(f"{a.node} did not report a new antenna delay within {a.wait:g} s (value: {before}). "
          "Check the link is ranging: uwbkit doctor")
    return 1


def cmd_bias(kit, a) -> int:
    bias = kit.diag().get("bias") or {"offsets": {}, "scale": 1.0}
    offsets = dict(bias.get("offsets") or {})
    if a.clear:
        kit.post("/bias", {"offsets": {}, "scale": 1.0})
        print("hub-side range offsets cleared")
        return 0
    if not a.pair or a.true is None:
        print(f"current offsets: {offsets or 'none'}  scale: {bias.get('scale', 1.0)}")
        return 0
    print(f"measuring {a.pair} for {a.seconds:g} s: hold both boards still at {a.true:.2f} m ...")
    time.sleep(a.seconds)
    links = kit.diag().get("links") or {}
    p, _, q = a.pair.partition("-")
    st = links.get(a.pair) or links.get(f"{q}-{p}")
    if not st or st.get("d_mean") is None or (st.get("age_s") or 99) > 2:
        print(f"link {a.pair} is not ranging (uwbkit doctor shows why)")
        return 2
    key = a.pair if a.pair in links else f"{q}-{p}"
    new = offsets.get(key, 0.0) + (st["d_mean"] - a.true)
    offsets[key] = round(new, 4)
    kit.post("/bias", {"offsets": offsets})
    print(f"{key}: measured {st['d_mean']:.3f} m (std {st.get('d_std', 0):.3f}) vs true {a.true:.3f} m "
          f"-> offset {offsets[key]:+.3f} m applied by the hub (not persisted: re-run after a hub restart)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uwbkit anchors", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("show")
    s = sub.add_parser("set"); s.add_argument("items", nargs="+"); s.add_argument("--z", type=float, default=2.0)
    r = sub.add_parser("rect"); r.add_argument("width", type=float); r.add_argument("depth", type=float)
    r.add_argument("--z", type=float, default=2.0); r.add_argument("--three", action="store_true", help="only A1..A3 (3A2T)")
    t = sub.add_parser("tape"); t.add_argument("items", nargs="*"); t.add_argument("--z", type=float, default=2.0)
    au = sub.add_parser("auto"); au.add_argument("--duration", type=float, default=5.0); au.add_argument("--z", type=float, default=None)
    ad = sub.add_parser("antdelay"); ad.add_argument("--node", required=True); ad.add_argument("--peer", required=True)
    ad.add_argument("--true", type=float, required=True); ad.add_argument("--n", type=int, default=0)
    ad.add_argument("--wait", type=float, default=20.0)
    b = sub.add_parser("bias"); b.add_argument("--pair"); b.add_argument("--true", type=float)
    b.add_argument("--seconds", type=float, default=5.0); b.add_argument("--clear", action="store_true")
    a = ap.parse_args(argv)
    kit = Kit(a.url)
    try:
        if a.cmd in (None, "show"):
            _show(kit)
            return 0
        return {"set": cmd_set, "rect": cmd_rect, "tape": cmd_tape, "auto": cmd_auto,
                "antdelay": cmd_antdelay, "bias": cmd_bias}[a.cmd](kit, a)
    except KitError as e:
        print(f"uwbkit anchors: {e}")
        return 2
    except ValueError as e:
        print(f"uwbkit anchors: bad number ({e})")
        return 2


if __name__ == "__main__":
    sys.exit(main())
