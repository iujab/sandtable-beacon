"""Section figure for the Beacon puck: cut at x = 6 mm, antenna keep-out, centre of mass, righting geometry.

Reads hardware/puck/render/parts/*.stl and hardware/puck/results/puck_build.json (both written by
build_puck.py) and writes figures/puck_section.png. Run after build_puck.py with the same venv
(+ matplotlib).
"""
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh
from matplotlib.patches import PathPatch, Rectangle, FancyArrowPatch
from matplotlib.path import Path as MPath
from matplotlib import font_manager

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
R = json.loads((HERE / "results" / "puck_build.json").read_text())
D, P, S = R["derived"], R["params"], R["stability"]
G = np.array(R["com_mm"])
CUT_X = 6.0

for f in ("barlow-500.woff2",):
    pass  # matplotlib cannot read woff2; use DejaVu Sans (always available)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10.5, "axes.edgecolor": "#8a8f95", "axes.labelcolor": "#2b2f33",
                     "xtick.color": "#5b6168", "ytick.color": "#5b6168"})

STYLE = {  # part: (face, edge, label)
    "shell_bottom": ("#e8661f", "#9c3d0a", "PETG shell"),
    "shell_top": ("#e8661f", "#9c3d0a", None),
    "boot_tpu": ("#3a3e44", "#1d2023", "TPU boot"),
    "board_sled": ("#c9cdd1", "#7d838a", "sled"),
    "board_pcb": ("#1b3a6b", "#0e1f3a", "DWM3001CDK"),
    "board_dwm3001c_module": ("#d9dce0", "#7d838a", None),
    "board_rpi_header": ("#222428", "#111", None),
    "board_usb": ("#a7adb3", "#555", None),
    "board_jlink": ("#222428", "#111", None),
    "board_back_parts": ("#30343a", "#111", None),
    "cell": ("#2f7fc0", "#174a73", "18650"),
    "ballast": ("#b4b9be", "#5d636a", "steel washers"),
    "charger": ("#2c6b40", "#163a22", "charger"),
    "port_plug_tpu": ("#3a3e44", "#1d2023", None),
}
T2D = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, -CUT_X], [0, 0, 0, 1]], float)


def section_polys(name, x=CUT_X):
    m = trimesh.load(HERE / "render" / "parts" / f"{name}.stl", force="mesh")
    sec = m.section(plane_origin=[x, 0, 0], plane_normal=[1, 0, 0])
    if sec is None:
        return []
    p2, _ = sec.to_2D(to_2D=np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, -x], [0, 0, 0, 1]], float))
    return list(p2.polygons_full)


def poly_patch(poly, rot=None, **kw):
    def ring(coords):
        c = np.asarray(coords)
        if rot is not None:
            c = c @ rot[0].T + rot[1]
        codes = [MPath.MOVETO] + [MPath.LINETO] * (len(c) - 2) + [MPath.CLOSEPOLY]
        return c, codes
    verts, codes = ring(poly.exterior.coords)
    for h in poly.interiors:
        v, c = ring(h.coords)
        verts = np.vstack([verts, v]); codes += c
    return PathPatch(MPath(verts, codes), **kw)


fig = plt.figure(figsize=(16, 7.6), dpi=100, facecolor="#f4f3f0")
gs = fig.add_gridspec(1, 3, width_ratios=[1.25, 1.0, 1.15], left=0.04, right=0.985, top=0.83, bottom=0.09, wspace=0.18)

# ---------------- (a) section with keep-out and CoM
ax = fig.add_subplot(gs[0]); ax.set_facecolor("#f4f3f0")
for name, (fc, ec, lab) in STYLE.items():
    for poly in section_polys(name):
        ax.add_patch(poly_patch(poly, facecolor=fc, edgecolor=ec, lw=0.7, zorder=3 if "board" in name else 2))
ant_z1 = D["board_z1"]; ant_z0 = ant_z1 - P["ant_len"]; ay0 = P["board_y"] + P["pcb_t"]
ax.add_patch(Rectangle((ay0, ant_z0), 2.6, P["ant_len"], facecolor="#ffd23f", edgecolor="#8a6d00", lw=1.0, zorder=5))
keep = R["antenna_keepout_mm"]
# keep-out circle: radius = distance to the nearest metal part (the cell)
ac = np.array([ay0 + 1.3, ant_z0 + P["ant_len"] / 2])
rr = keep["cell"]
th = np.linspace(0, 2 * np.pi, 300)
ax.plot(ac[0] + (rr + 5) * np.cos(th), ac[1] + (rr + 5) * np.sin(th), color="#b08900", lw=0.0)
ax.add_patch(plt.Circle(ac, rr, fill=False, ls=(0, (5, 4)), lw=1.3, color="#8a6d00", zorder=6))
ax.annotate(f"antenna zone (module end)\nnearest other metal: the cell, {rr:.0f} mm\nPETG dome {keep['shell_top_plastic']:.0f} mm away",
            xy=(ac[0] + 2, ant_z1), xytext=(-98, 112), fontsize=10, color="#5c4700",
            arrowprops=dict(arrowstyle="-", color="#8a6d00", lw=1.0))
C = np.array([0.0, D["zc"]])
gy, gz = G[1], G[2]
ax.plot(*C, marker="+", ms=14, mew=2, color="#2b2f33", zorder=8)
ax.annotate("C  sphere centre", xy=C, xytext=(-98, C[1] + 6), fontsize=10, color="#2b2f33",
            arrowprops=dict(arrowstyle="-", color="#2b2f33", lw=0.8))
ax.plot(gy, gz, marker="o", ms=11, mfc="white", mec="#c1121f", mew=2.4, zorder=9)
ax.plot(gy, gz, marker="o", ms=4, color="#c1121f", zorder=10)
ax.annotate(f"G  centre of mass\nz = {gz:.1f} mm\n({R['mass_total_g']:.0f} g, assumed parts)", xy=(gy, gz), xytext=(-98, 12), fontsize=10,
            color="#c1121f", arrowprops=dict(arrowstyle="-", color="#c1121f", lw=0.8))
ax.annotate("", xy=(-24, C[1]), xytext=(-24, gz), arrowprops=dict(arrowstyle="<->", color="#2b2f33", lw=1.2))
ax.plot([-26, 0], [C[1], C[1]], color="#2b2f33", lw=0.6, ls=":"); ax.plot([-26, gy], [gz, gz], color="#2b2f33", lw=0.6, ls=":")
ax.text(-26, (C[1] + gz) / 2, f"d = {S['metacentric_margin_mm']:.1f} mm ", fontsize=10.5, va="center", ha="right", color="#2b2f33", fontweight="bold")
ax.plot([-P["rf"], P["rf"]], [-0.8, -0.8], color="#2b2f33", lw=2.2, solid_capstyle="butt")
ax.text(0, -5.5, f"flat bottom Ø {2 * P['rf']:.0f} mm (the only resting face)", ha="center", fontsize=9.5, color="#2b2f33")
ax.set_xlim(-100, 66); ax.set_ylim(-10, 128); ax.set_aspect("equal")
ax.set_xlabel("y (mm)"); ax.set_ylabel("z (mm)")
ax.set_title(f"(a) Section at x = {CUT_X:.0f} mm, upright", loc="left", fontsize=12.5, fontweight="bold", color="#1d2125")
for sp in ("top", "right"):
    ax.spines[sp].set_visible(False)

# ---------------- (b) righting geometry at a tilt of 150 degrees (nearly upside down, the worst case)
ax = fig.add_subplot(gs[1]); ax.set_facecolor("#f4f3f0")
theta = math.radians(150.0)
n = np.array([math.sin(theta), -math.cos(theta)])          # body-frame floor normal (towards the floor), tilting towards +y
ang = math.atan2(-1, 0) - math.atan2(n[1], n[0])           # rotate n onto (0, -1)
Rm = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
Pc = C + P["R"] * n                                       # contact point on the sphere
shift = -(Rm @ Pc)                                        # put the contact point at the origin
rot = (Rm, shift)
for name in ("shell_bottom", "shell_top", "boot_tpu", "board_pcb", "board_dwm3001c_module"):
    fc, ec, _ = STYLE[name]
    for poly in section_polys(name):
        ax.add_patch(poly_patch(poly, rot=rot, facecolor=fc, edgecolor=ec, lw=0.6, alpha=0.95))
Cw = Rm @ C + shift; Gw = Rm @ np.array([gy, gz]) + shift
ax.axhline(0, color="#2b2f33", lw=2.0)
ax.fill_between([-80, 80], -6, 0, color="#d9d6cf", zorder=0)
ax.plot([0, 0], [0, Cw[1] + 8], color="#2b2f33", lw=0.9, ls="--")
ax.plot([Gw[0], Gw[0]], [0, Gw[1] + 18], color="#c1121f", lw=0.9, ls="--")
ax.plot(*Cw, marker="+", ms=14, mew=2, color="#2b2f33", zorder=8)
ax.plot(*Gw, marker="o", ms=11, mfc="white", mec="#c1121f", mew=2.4, zorder=9); ax.plot(*Gw, marker="o", ms=4, color="#c1121f", zorder=10)
gzv = S["metacentric_margin_mm"] * math.sin(theta)
yb = Gw[1] + 12
ax.plot([0, Gw[0]], [yb, yb], color="#c1121f", lw=2.0)
ax.annotate(f"GZ = d sin 150° = {gzv:.1f} mm", xy=(Gw[0] / 2, yb), xytext=(-68, 112), fontsize=11, color="#c1121f", fontweight="bold",
            arrowprops=dict(arrowstyle="-", color="#c1121f", lw=0.8))
ax.annotate("G (weight line)", xy=Gw, xytext=(-68, 98), fontsize=10, color="#c1121f", arrowprops=dict(arrowstyle="-", color="#c1121f", lw=0.8))
ax.annotate("C (above the contact)", xy=Cw, xytext=(28, 104), fontsize=10, color="#2b2f33", arrowprops=dict(arrowstyle="-", color="#2b2f33", lw=0.8))
ax.text(1.5, -4.2, "contact", fontsize=9, color="#2b2f33")
ax.set_xlim(-72, 72); ax.set_ylim(-30, 125); ax.set_aspect("equal"); ax.axis("off")
ax.set_title("(b) Tipped 150° (almost upside down)", loc="left", fontsize=12.5, fontweight="bold", color="#1d2125")
ax.text(-70, -16, "On a sphere the contact point is always straight below C.\nG is offset from that vertical by GZ, on the side that\nrolls the puck back towards upright.",
        fontsize=9.5, color="#3b4046", va="top")

# ---------------- (c) GZ curves
ax = fig.add_subplot(gs[2]); ax.set_facecolor("#ffffff")
cur = np.load(HERE / "results" / "gz_curves.npy")
for c in cur:
    ax.plot(c[:, 0], c[:, 2], color="#e8661f", lw=0.8, alpha=0.35)
thd = np.linspace(0, 180, 361)
d = S["metacentric_margin_mm"]
ana = np.where(thd < S["flat_edge_tilt_deg"],
               P["rf"] * np.cos(np.radians(thd)) - G[2] * np.sin(np.radians(thd)),
               d * np.sin(np.radians(thd)))
ana = np.where(thd < S["flat_edge_tilt_deg"], P["rf"] * np.cos(np.radians(thd)) - (G[2]) * np.sin(np.radians(thd)) , ana)
ax.plot(thd, np.where(thd < S["flat_edge_tilt_deg"], np.nan, ana), color="#1d2125", lw=2.0, label="analytic, sphere part: d sin θ")
ax.plot([], [], color="#e8661f", lw=1.5, label="from the mesh hull, 24 tipping directions")
ax.axhline(0, color="#2b2f33", lw=0.9)
ax.axvspan(0, S["flat_edge_tilt_deg"], color="#e8e6e1", zorder=0)
ax.text(2, 38, f"pivoting on the\nflat's edge\n(0-{S['flat_edge_tilt_deg']:.0f}°)", fontsize=9.5, color="#4a4f55", va="top")
ax.axvspan(170, 180, color="#f3dede", zorder=0)
ax.text(171, 15, "mesh\nfacets", fontsize=8.5, color="#8a2b2b", ha="left")
ax.set_xlim(0, 180); ax.set_ylim(-4, 44)
ax.set_xlabel("tilt from upright θ (degrees)"); ax.set_ylabel("righting arm GZ (mm)")
ax.legend(loc="upper right", fontsize=9.5, frameon=False)
ax.set_title("(c) Righting arm vs tilt: positive everywhere", loc="left", fontsize=12.5, fontweight="bold", color="#1d2125")
ax.grid(color="#ecebe7", lw=0.8)
for sp in ("top", "right"):
    ax.spines[sp].set_visible(False)
ax.annotate("id pad facet:\nstill > 3 mm", xy=(125, 4.5), xytext=(128, -2.5), fontsize=8.5, color="#4a4f55")
ax.annotate("charge window", xy=(55, 15.5), xytext=(56, 19), fontsize=8.5, color="#4a4f55")

fig.text(0.04, 0.945, "Beacon puck: why it lands antenna-up", fontsize=21, fontweight="bold", color="#1d2125")
fig.text(0.04, 0.905, f"Hull: a sphere (R {P['R']:.0f} mm, centre C {D['zc']:.1f} mm up) with a flat base. G sits {d:.1f} mm below C, so the only resting pose is upright on the flat "
         f"({S['righting_energy_from_upside_down_mJ']:.0f} mJ to right from upside down). Bought-part masses are assumed.",
         fontsize=10, color="#4a4f55")
out = ROOT / "figures" / "puck_section.png"
fig.savefig(out, dpi=100, facecolor=fig.get_facecolor())
print("wrote", out)
