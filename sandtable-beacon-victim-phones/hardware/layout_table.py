"""Where to put the two pucks, in building metres and on the demo table, from `beacon.layout.recommend`.

    python hardware/layout_table.py                      # scale 3 (a 3.17 m x 2.88 m taped rectangle for Suite 310)
    python hardware/layout_table.py --scale 1            # the real room
    python hardware/layout_table.py --scale 4 --room r310
    python hardware/layout_table.py --svg hardware/two_puck_layout.svg    # also redraw the figure

Pure geometry on the building twin (fixtures/building/twin.json); no hardware, no network. The example phone
positions in the figure are illustrations, not measurements.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beacon import config, layout          # noqa: E402
from beacon.twin import Twin                 # noqa: E402

def mirror(p, a, b):
    """Reflect point p across the line through a and b."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
    fx, fy = ax + t * dx, ay + t * dy
    return (2 * fx - p[0], 2 * fy - p[1])


def bbox(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _where(label: str | None) -> str:
    if not label:
        return "outside the building"
    return label if label.lower().startswith("suite") else "the " + label


def mirror_fate(twin, ctx, room_id, pucks, phone):
    """What the real locator (beacon.locate) and the statement rule (beacon.prune) do with the mirror image, on EXACT
    ranges (no noise: an illustration of the geometry, not a measurement)."""
    from beacon import locate, prune
    room = twin.room(room_id)
    pk = [{"id": f"P{i + 1}", "x": x, "y": y} for i, (x, y) in enumerate(pucks)]
    rs = [{"puck": q["id"], "kind": "uwb", "d_m": math.dist((q["x"], q["y"]), phone), "n": 5} for q in pk]
    est = locate.estimate(ctx, pk, rs)
    m = mirror(phone, pucks[0], pucks[1])
    where = _where(twin.locate(m[0], m[1], room.get("floor")).get("room_label"))
    if est.get("geometry") == "fix":
        reasons = [q.get("reason") for q in est.get("pruned") or [] if q.get("reason")]
        if not reasons:
            return m, "open", "too close to the phone to separate: one blurred fix (the phone is near the line through the pucks: the worst geometry)"
        return m, "removed", f"in {where}: the twin removes it ({reasons[0]})"
    if est.get("geometry") == "candidates":
        stated = prune.apply_statement(est, room_id, room.get("label"), f"the occupant says they are in {room.get('label')}")
        if stated.get("geometry") == "fix":
            return m, "dismissed", f"in {where}: possible in the twin, dismissed on the occupant's stated suite (shown as dismissed)"
        return m, "open", f"also inside {room.get('label')}: two candidates, nothing can decide"
    return m, "open", f"locator geometry '{est.get('geometry')}'"


def table(twin, room_id: str, scale: float) -> str:
    room = twin.room(room_id)
    x0, y0, x1, y1 = bbox(room["polygon"])
    rec = layout.recommend(twin, room_id, strategy="door-wall")
    door = next((d for d in twin.data.get("doors", []) if room_id in (d.get("rooms") or [])), None)
    lines = []
    w, h = (x1 - x0), (y1 - y0)
    lines.append(f"Room {room_id} ({room.get('label')}): {w:.2f} m x {h:.2f} m in the building; exercise scale {scale:g}")
    lines.append(f"Taped rectangle: {w / scale:.2f} m x {h / scale:.2f} m (x along the {w:.2f} m side, y along the {h:.2f} m side)")
    lines.append(f"Origin (0, 0) = the room's corner at building ({x0:g}, {y0:g}).")
    if door:
        dx, dy = door["at"]
        lines.append(f"Door: centre at building ({dx:g}, {dy:g}), {door.get('width_m', 0.9):g} m wide -> on the tape ({(dx - x0) / scale:.2f}, {(dy - y0) / scale:.2f}), "
                     f"{door.get('width_m', 0.9) / scale:.2f} m wide")
    lines.append(f"Layout: {rec['strategy']}, {rec['wall']}; {rec['why']}")
    lines.append("")
    lines.append("| Puck | building x, y (m) | on the tape x, y (m) |")
    lines.append("|---|---|---|")
    for p in rec["pucks"]:
        lines.append(f"| {p['id']} | {p['x']:.2f}, {p['y']:.2f} | {(p['x'] - x0) / scale:.2f}, {(p['y'] - y0) / scale:.2f} |")
    lines.append("")
    lines.append(f"Inset from the door wall: {layout.INSET_M:.2f} m in the building = {layout.INSET_M / scale:.2f} m on the tape; "
                 f"spacing {rec['spacing_m']:.2f} m = {rec['spacing_m'] / scale:.2f} m on the tape.")
    lines.append(f"Phones: anywhere inside the rectangle, best more than {3.0 / scale:.2f} m on the tape (3 m in the building) from the door wall "
                 f"(the closer a phone is to the line through the pucks, the worse the geometry).")
    care = layout.recommend(twin, room_id, strategy="careless")
    lines.append("")
    lines.append("For comparison, the careless layout (both pucks mid-room): " +
                 ", ".join(f"{p['id']} ({p['x']:.2f}, {p['y']:.2f})" for p in care["pucks"]) + f": {care['why']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------- SVG
def svg(twin, room_id: str) -> str:
    room = twin.room(room_id)
    floor = room.get("floor")
    rx0, ry0, rx1, ry1 = bbox(room["polygon"])
    rec = layout.recommend(twin, room_id, strategy="door-wall")
    care = layout.recommend(twin, room_id, strategy="careless")
    door = next((d for d in twin.data.get("doors", []) if room_id in (d.get("rooms") or [])), None)
    from beacon import locate
    ctx = locate.Context(twin, floor=floor, cell_m=0.25, use_twin=True)
    # view: the room plus a strip beyond the door wall
    vx0, vy0, vx1, vy1 = rx0 - 0.6, ry0 - 0.6, rx1 + 6.5, ry1 + 1.0
    S = 34.0                                 # px per metre
    PW = (vx1 - vx0) * S
    PH = (vy1 - vy0) * S
    GAP, TOP, LEFT = 40, 120, 20
    W = int(LEFT * 2 + PW * 2 + GAP)
    H = int(TOP + PH + 150)

    def P(x, y, ox):                         # building -> px (y up)
        return (ox + (x - vx0) * S, TOP + (vy1 - y) * S)

    others = [r for r in twin.data.get("rooms", []) if r.get("floor") == floor and r["id"] != room_id]

    def clip_poly(poly):
        x0, y0, x1, y1 = bbox(poly)
        return not (x1 < vx0 or x0 > vx1 or y1 < vy0 or y0 > vy1)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="Helvetica, Arial, sans-serif" font-size="13">',
           "<title>Two pucks: at the door wall (recommended) versus mid-room (careless)</title>",
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>',
           '<defs><clipPath id="cl0"><rect x="{:.1f}" y="{}" width="{:.1f}" height="{:.1f}"/></clipPath>'
           '<clipPath id="cl1"><rect x="{:.1f}" y="{}" width="{:.1f}" height="{:.1f}"/></clipPath></defs>'.format(
               LEFT, TOP, PW, PH, LEFT + PW + GAP, TOP, PW, PH),
           f'<text x="{LEFT}" y="30" font-size="18" font-weight="bold" fill="#111">Two pucks, two phones: where to put the pucks</text>',
           f'<text x="{LEFT}" y="52" fill="#555">Two rings cross twice: the phone and its mirror image across the line through the pucks. '
           f'Building twin, {room.get("label")}; positions from beacon/layout.py.</text>',
           f'<text x="{LEFT}" y="70" fill="#555">Example phones are illustrations; outcomes from beacon/locate.py and beacon/prune.py on exact (noise-free) ranges. Drawn by hardware/layout_table.py.</text>']

    def panel(ox, cid, pucks, phones, title, sub):
        o = [f'<text x="{ox}" y="{TOP - 22}" font-size="15" font-weight="bold" fill="#111">{title}</text>',
             f'<text x="{ox}" y="{TOP - 6}" fill="#444">{sub}</text>',
             f'<g clip-path="url(#{cid})">',
             f'<rect x="{ox}" y="{TOP}" width="{PW:.1f}" height="{PH:.1f}" fill="#f7f7f7"/>']
        for r in others:
            if not clip_poly(r["polygon"]):
                continue
            pts = " ".join(f"{P(x, y, ox)[0]:.1f},{P(x, y, ox)[1]:.1f}" for x, y in r["polygon"])
            o.append(f'<polygon points="{pts}" fill="#ececec" stroke="#555" stroke-width="2"/>')
            cx, cy = sum(p[0] for p in r["polygon"]) / len(r["polygon"]), sum(p[1] for p in r["polygon"]) / len(r["polygon"])
            cx, cy = min(max(cx, vx0 + 1.2), vx1 - 1.2), min(max(cy, vy0 + 0.4), vy1 - 0.4)
            lx, ly = P(cx, cy, ox)
            o.append(f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#777" text-anchor="middle" font-size="12">{r.get("label")}</text>')
        pts = " ".join(f"{P(x, y, ox)[0]:.1f},{P(x, y, ox)[1]:.1f}" for x, y in room["polygon"])
        o.append(f'<polygon points="{pts}" fill="#ffffff" stroke="#222" stroke-width="3"/>')
        lx, ly = P((rx0 + rx1) / 2, ry1 - 0.5, ox)
        o.append(f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#222" text-anchor="middle" font-weight="bold">{room.get("label")}</text>')
        if door:
            dx, dy = door["at"]
            half = door.get("width_m", 0.9) / 2
            a, b = P(dx, dy - half, ox), P(dx, dy + half, ox)
            o.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="#f7f7f7" stroke-width="5"/>')
            t = P(dx + 0.25, dy + half + 0.1, ox)
            o.append(f'<text x="{t[0]:.1f}" y="{t[1]:.1f}" fill="#222" font-size="12">door</text>')
        # line through the pucks
        (ax, ay), (bx, by) = pucks
        dxl, dyl = bx - ax, by - ay
        L = math.hypot(dxl, dyl)
        ux, uy = dxl / L, dyl / L
        e0, e1 = P(ax - ux * 30, ay - uy * 30, ox), P(ax + ux * 30, ay + uy * 30, ox)
        o.append(f'<line x1="{e0[0]:.1f}" y1="{e0[1]:.1f}" x2="{e1[0]:.1f}" y2="{e1[1]:.1f}" stroke="#999" stroke-width="1" stroke-dasharray="6 4"/>')
        colours = ["#1f6fb2", "#8a3ab9"]
        notes = []
        for k, ph in enumerate(phones):
            col = colours[k % 2]
            m, kind, fate = mirror_fate(twin, ctx, room_id, pucks, ph)
            for pk in pucks:
                c = P(pk[0], pk[1], ox)
                r = math.dist(pk, ph) * S
                o.append(f'<circle cx="{c[0]:.1f}" cy="{c[1]:.1f}" r="{r:.1f}" fill="none" stroke="{col}" stroke-width="1.3" stroke-opacity="0.55"/>')
            p = P(ph[0], ph[1], ox)
            o.append(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="7" fill="{col}"/>')
            o.append(f'<text x="{p[0] + 10:.1f}" y="{p[1] - 8:.1f}" fill="{col}" font-weight="bold">V{k + 1}</text>')
            q = P(m[0], m[1], ox)
            if kind == "open":
                o.append(f'<circle cx="{q[0]:.1f}" cy="{q[1]:.1f}" r="7" fill="#e08a00"/>')
                o.append(f'<text x="{q[0] + 10:.1f}" y="{q[1] - 8:.1f}" fill="#b36b00" font-weight="bold">V{k + 1}?</text>')
            else:
                o.append(f'<circle cx="{q[0]:.1f}" cy="{q[1]:.1f}" r="7" fill="none" stroke="#c0392b" stroke-width="2"/>')
                o.append(f'<line x1="{q[0] - 6:.1f}" y1="{q[1] - 6:.1f}" x2="{q[0] + 6:.1f}" y2="{q[1] + 6:.1f}" stroke="#c0392b" stroke-width="2"/>')
                o.append(f'<line x1="{q[0] - 6:.1f}" y1="{q[1] + 6:.1f}" x2="{q[0] + 6:.1f}" y2="{q[1] - 6:.1f}" stroke="#c0392b" stroke-width="2"/>')
            notes.append((col, f"V{k + 1}: mirror image {fate}"))
        for i, pk in enumerate(pucks):
            c = P(pk[0], pk[1], ox)
            o.append(f'<rect x="{c[0] - 7:.1f}" y="{c[1] - 7:.1f}" width="14" height="14" fill="#2f7d3a" stroke="#123" stroke-width="1.5"/>')
            o.append(f'<text x="{c[0] - 12:.1f}" y="{c[1] + 4:.1f}" fill="#2f7d3a" font-weight="bold" text-anchor="end">P{i + 1}</text>')
        o.append("</g>")
        o.append(f'<rect x="{ox}" y="{TOP}" width="{PW:.1f}" height="{PH:.1f}" fill="none" stroke="#bbb"/>')
        y = TOP + PH + 24
        for col, text in notes:
            for chunk in _wrap(text, 80):
                o.append(f'<text x="{ox}" y="{y}" fill="{col}">{chunk}</text>')
                y += 17
        return o

    good_pucks = [(p["x"], p["y"]) for p in rec["pucks"]]
    bad_pucks = [(p["x"], p["y"]) for p in care["pucks"]]
    # example phones (illustrations): one deep in the room, one nearer the door
    phones = [(rx0 + 0.42 * (rx1 - rx0), ry0 + 0.52 * (ry1 - ry0)), (rx0 + 0.72 * (rx1 - rx0), ry0 + 0.86 * (ry1 - ry0))]
    out += panel(LEFT, "cl0", good_pucks, phones, "A. Recommended: both pucks just inside the door wall",
                 f"{layout.INSET_M:.2f} m inside, {rec['spacing_m']:.1f} m apart: every mirror image lands beyond that wall")
    careless_phone = [(rx0 + 0.26 * (rx1 - rx0), ry0 + 0.75 * (ry1 - ry0))]      # well off the line through the mid-room pucks
    out += panel(LEFT + PW + GAP, "cl1", bad_pucks, careless_phone, "B. Careless: both pucks mid-room",
                 "the mirror image stays inside the room: two candidates")
    out.append(f'<text x="{LEFT}" y="{H - 14}" fill="#555">Green squares: pucks. Filled dots: the phone. Red cross: the mirror candidate the board dismisses (and shows as dismissed). '
               f'Orange: a mirror candidate nothing can dismiss.</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _wrap(text: str, n: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--room", default="r310")
    ap.add_argument("--scale", type=float, default=3.0, help="building metres per metre of the demo table (default 3)")
    ap.add_argument("--svg", help="also write the two-panel figure to this path")
    a = ap.parse_args(argv)
    twin = Twin(config.twin_raw())
    print(table(twin, a.room, a.scale))
    if a.svg:
        Path(a.svg).write_text(svg(twin, a.room), encoding="utf-8")
        print(f"\nwrote {a.svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
