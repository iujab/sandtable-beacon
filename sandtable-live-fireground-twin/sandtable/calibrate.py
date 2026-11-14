"""Two-point registration of the UWB rig to the twin, for real hardware:  python -m sandtable.calibrate

The default registration assumes the anchors sit on the corners of the printed or taped plan.
When they do not, stand the Engine 1 tag on two (or more) marks whose place in the twin is known,
press Enter at each, and the server fits scale, rotation and offset from those pairs.
The marks offered are the two stair doors; any room centre can be added by its suite number.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request


def call(base: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sandtable.calibrate")
    ap.add_argument("--base", default="http://127.0.0.1:1810")
    ap.add_argument("--unit", default="E1", help="the unit whose tag you carry to the marks")
    ap.add_argument("--marks", nargs="*", default=[], help="extra marks: suite numbers whose centre you will stand on")
    args = ap.parse_args(argv)
    twin = call(args.base, "/api/bundle")["twin"]
    marks = [(f"the door of {s['label']}", next(d for d in twin["doors"] if d["id"] == s["door"])["at"]) for s in twin["stairs"] if s.get("door")]
    for suite in args.marks:
        room = next((r for r in twin["rooms"] if r.get("suite") == suite), None)
        if room:
            poly = room["polygon"]
            marks.append((f"the centre of Suite {suite}", [sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)]))
    if len(marks) < 2:
        print("Need two marks; pass suite numbers with --marks.")
        return 2
    pairs = []
    for name, bldg in marks:
        input(f"Put the {args.unit} tag on {name} (twin position {bldg[0]:.1f}, {bldg[1]:.1f} m) and hold it still. Press Enter...")
        samples = []
        for _ in range(15):                                   # average 1.5 s of fixes
            u = next(q for q in call(args.base, "/api/state")["units"] if q["id"] == args.unit)
            if u.get("site"):
                samples.append(u["site"])
            time.sleep(0.1)
        if not samples:
            print("No position for that unit: is the hub connected and the tag powered?")
            return 1
        site = [sum(s[0] for s in samples) / len(samples), sum(s[1] for s in samples) / len(samples)]
        print(f"  rig position {site[0]:.2f}, {site[1]:.2f} m")
        pairs.append({"site": site, "bldg": bldg})
    reg = call(args.base, "/api/registration", {"pairs": pairs})["registration"]
    print(f"Registration set: 1 m of rig = {reg['scale']:.2f} m of building, rotation {reg['rotation_deg']:.1f} deg, residual {reg['residual_m']:.2f} m (building metres).")
    print("Walk the tag to a third known place and check it on the board. Run again to redo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
