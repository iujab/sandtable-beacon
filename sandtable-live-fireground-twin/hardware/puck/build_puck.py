"""Sandtable scout puck: parametric enclosure for one UWB board and one 18650 cell.

This is the geometry that was actually built and checked here. `puck.scad` encodes the same
construction for OpenSCAD (same parameter names); it was written to the same numbers but was NOT
rendered on this machine (OpenSCAD is not installed).

Run (any Python 3.10+ with the wheels below; nothing else):
    python -m venv v && v/bin/pip install trimesh manifold3d numpy scipy shapely mapbox-earcut
    v/bin/python hardware/puck/build_puck.py            # writes STLs, results JSON, preview data

Outputs (all in hardware/puck/):
    stl/puck_shell_bottom.stl  stl/puck_shell_top.stl  stl/puck_boot_tpu.stl  stl/puck_board_sled.stl
    stl/puck_port_plug_tpu.stl stl/puck_assembly_preview.stl (every part incl. stand-ins, assembled)
    results/puck_build.json    mass properties, stability numbers, clearance and watertightness checks
    render/parts/*.stl         per-part meshes (assembled frame) for the figure scripts

Frame: millimetres, z up, z = 0 is the flat bottom (the floor when the puck sits upright). The board
stands in the x-z plane (its face normal is +y), antenna end up.

Mass and stability numbers depend on ASSUMED component masses (see COMPONENTS); weigh the real parts
and re-run. Printed-part masses are solid-volume x density (walls are printed as solid perimeters).
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import CrossSection, Manifold, Mesh

HERE = Path(__file__).resolve().parent

# ----------------------------------------------------------------------------------------------
# Parameters (identical names and defaults in puck.scad)
# ----------------------------------------------------------------------------------------------
P = dict(
    # outer form: one sphere with a flat bottom. The convex hull of the whole puck is exactly this
    # sphere-with-flat (every other feature is recessed or flush), which is what makes it self-right.
    R=56.0,            # outer sphere radius
    rf=40.0,           # radius of the flat bottom (upright resting face)
    wall=2.0,          # PETG wall (5 perimeters of 0.4 mm)
    floor_t=2.4,       # bottom wall at the flat
    tb=2.4,            # TPU boot (bumper) thickness; shells are recessed by this under the boot
    lat_boot_lo=-25.0, # boot lower edge, latitude in degrees from the sphere centre
    split_dz=-6.0,     # shell split plane, relative to the sphere centre height
    boot_ov=7.0,       # boot overlap onto the top shell skirt (covers seam and screw heads)
    lip_h=7.0,         # bottom-shell lip that the top shell slides over
    lip_t=1.8,
    fit=0.25,          # sliding clearance between lip and skirt
    # board: Qorvo DWM3001CDK. Qorvo publishes no outline drawing (see README): every board_* value
    # below is a placeholder TO BE MEASURED; the sled is a separate 10-minute print for the real numbers.
    board_w=32.0, board_h=64.0, pcb_t=1.6,      # TO BE MEASURED
    comp_front=9.0,    # front envelope incl. the 26-pin Raspberry Pi header if fitted (TO BE MEASURED)
    comp_back=2.0,     # back side envelope (TO BE MEASURED)
    board_y=10.0,      # y of the PCB back face
    ant_len=12.0,      # antenna zone: the DWM3001C module end of the board, held up (TO BE MEASURED)
    ant_w=20.0,
    # board sled (separate part; reprint it for another board outline)
    rail_w=5.0, rail_h=40.0, groove_d=1.2, groove_clear=0.3,
    sled_base_t=2.0, sled_pocket=0.6,
    sled_lift=10.0,    # pedestal: lifts the board so the antenna is further from the cell (no metal below the antenna)
    # cell: 18650 lying along x, beside the board's back
    cell_d=18.4, cell_len=67.0, cell_y=-7.5,
    # ballast: DIN 9021 M8 steel washers (24 x 8.4 x 2.0) in two wells under the cell
    wash_od=24.0, wash_id=8.4, wash_t=2.0, wash_n=4, wash_x=13.0, well_wall=1.2,
    # charge + 5 V boost module (generic 1S USB-C charger/boost board, ASSUMED 17 x 22 mm)
    chg_w=17.0, chg_l=22.0, chg_standoff=4.0, chg_y0=16.6,
    data_az=45.0,      # second window: panel USB-C socket on a pigtail to the board's J-Link micro-USB
    usb_w=13.0, usb_h=7.5,
    # joint screws: M3 radial, into brass heat-set inserts in bosses on the lip
    n_screws=3, screw_az0=30.0, insert_d=4.0, insert_l=5.7, screw_d=3.4,
    # power switch: 7 mm latching push switch, pressed through a thin TPU membrane in the boot
    sw_d=7.2, sw_az=225.0, sw_z=24.0, membrane=1.0,
    # status light pipe: 3 mm clear rod or 3 mm LED, through the dome on the far side from the antenna
    lp_d=3.2, lp_y=-30.0,
    # id pad on the dome top (recessed below the hull), debossed text
    id_r=7.5, id_depth=0.8, id_lat=35.0, id_az=180.0, id_text="P1",   # recessed pad on the dome flank
    # lanyard: soft cord loop through two holes in the boot, running in a channel under it
    cord_d=3.5, cord_az=315.0, cord_z=24.0, cord_span=18.0,
    seg=160,
)

DENS = {"PETG": 1.27, "TPU": 1.21, "steel": 7.85}  # g/cm3

# ASSUMED masses of bought parts (g) — weigh yours and re-run
COMPONENTS = {
    "board": 14.0,       # DWM3001CDK incl. the J-Link section (assumed; weigh it)
    "cell": 46.0,        # 18650, typical 45-48 g
    "charger": 4.0,      # 1S charger/boost module
    "switch": 1.5,
    "screws_inserts": 3.0,
    "led_wires": 2.0,
    "usb_pigtails": 8.0, # right-angle micro-USB lead + panel USB-C pigtail
}


# ----------------------------------------------------------------------------------------------
# derived geometry
# ----------------------------------------------------------------------------------------------
def derived(p):
    d = {}
    d["zc"] = math.sqrt(p["R"] ** 2 - p["rf"] ** 2)          # sphere centre height
    d["H"] = d["zc"] + p["R"]
    d["z_split"] = d["zc"] + p["split_dz"]
    d["z_boot_lo"] = d["zc"] + p["R"] * math.sin(math.radians(p["lat_boot_lo"]))
    d["z_boot_hi"] = d["z_split"] + p["boot_ov"]
    d["S0"] = p["R"]
    d["S1"] = p["R"] - p["tb"]
    d["S2"] = p["R"] - p["wall"]
    d["S3"] = p["R"] - p["tb"] - p["wall"]
    d["S4"] = d["S3"] - p["fit"]
    d["S5"] = d["S4"] - p["lip_t"]
    d["floor_top"] = p["floor_t"]
    d["board_z0"] = p["floor_t"] - p["sled_pocket"] + p["sled_base_t"] + p["sled_lift"]
    d["board_z1"] = d["board_z0"] + p["board_h"]
    d["wash_top"] = p["floor_t"] + p["wash_n"] * p["wash_t"]
    d["cell_zc"] = d["wash_top"] + 0.6 + p["cell_d"] / 2
    return d


D = derived(P)
SEG = P["seg"]
BIG = 400.0


def sph(r, z=None):
    return Manifold.sphere(r, SEG).translate([0, 0, D["zc"] if z is None else z])


def slab(z0, z1):
    return Manifold.cube([BIG, BIG, z1 - z0]).translate([-BIG / 2, -BIG / 2, z0])


def box(x0, x1, y0, y1, z0, z1):
    return Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def cyl_z(r, z0, z1, x=0.0, y=0.0, r2=None, seg=64):
    return Manifold.cylinder(z1 - z0, r, r if r2 is None else r2, seg).translate([x, y, z0])


def radial(r, length, r0, az, z, seg=48):
    """A cylinder along the horizontal radial direction at azimuth az (deg), starting at radius r0."""
    c = Manifold.cylinder(length, r, r, seg).rotate([0, 90, 0]).translate([r0, 0, z])
    return c.rotate([0, 0, az])


def radial_box(w, h, r0, length, az, z):
    b = Manifold.cube([length, w, h]).translate([r0, -w / 2, z - h / 2])
    return b.rotate([0, 0, az])


def sphere_r_at(Rs, z):
    """radius of sphere Rs (centred at zc) at height z"""
    dz = z - D["zc"]
    return math.sqrt(max(Rs ** 2 - dz ** 2, 0.0))


# 7-segment-ish glyphs for the id (OpenSCAD uses text(); here a stroke font so no font engine is needed)
SEGS = {  # a b c d e f g (standard 7-seg order)
    "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
    "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "0": "abcdef", "P": "abefg", "N": "abcef",
}


def glyphs(text, h=11.0, stroke=1.8, gap=2.4):
    w = h * 0.55
    parts = []
    xoff = 0.0
    for ch in text:
        segs = SEGS.get(ch.upper(), "")
        s = stroke
        rects = {
            "a": (0, h - s, w, h), "g": (0, h / 2 - s / 2, w, h / 2 + s / 2), "d": (0, 0, w, s),
            "f": (0, h / 2, s, h), "b": (w - s, h / 2, w, h), "e": (0, 0, s, h / 2), "c": (w - s, 0, w, h / 2),
        }
        for k in segs:
            x0, y0, x1, y1 = rects[k]
            parts.append(CrossSection.square([x1 - x0, y1 - y0]).translate([x0 + xoff, y0]))
        xoff += w + gap
    cs = CrossSection.batch_boolean(parts, __import__("manifold3d").OpType.Add) if parts else CrossSection()
    return cs.translate([-(xoff - gap) / 2, -h / 2])


# ----------------------------------------------------------------------------------------------
# parts
# ----------------------------------------------------------------------------------------------
def hull_solid():
    return sph(D["S0"]) ^ slab(0, D["H"] + 1)


def bottom_shell():
    p, d = P, D
    zs, zlo = d["z_split"], d["z_boot_lo"]
    # outer: full sphere below the boot, recessed (S1) where the boot sits
    outer = (sph(d["S0"]) ^ slab(0, zlo)) + (sph(d["S1"]) ^ slab(zlo, zs))
    cavity = sph(d["S3"]) ^ slab(p["floor_t"], zs + 1)
    shell = outer - cavity
    # lip: attachment band (S5..S3) below the split, and the lip proper (S5..S4) above it
    lip = ((sph(d["S3"] + 0.3) - sph(d["S5"])) ^ slab(zs - 4, zs)) + ((sph(d["S4"]) - sph(d["S5"])) ^ slab(zs, zs + p["lip_h"]))
    shell = shell + lip
    # screw bosses on the lip with heat-set insert holes
    zscr = zs + p["lip_h"] / 2
    for i in range(p["n_screws"]):
        az = p["screw_az0"] + i * 360.0 / p["n_screws"]
        boss = radial_box(10.0, p["lip_h"] + 8.0, d["S5"] - 6.5, 7.0, az, zscr - 4.0)
        boss = boss ^ sph(d["S4"])  # stay inside the skirt
        shell = shell + boss
        shell = shell - radial(p["insert_d"] / 2, p["insert_l"] + 1.0, d["S4"] - p["insert_l"] - 0.5, az, zscr)
    # interior: washer wells with locating pegs (ballast), cell saddles, end stops, charger rails
    inner_clip = sph(d["S3"] + 0.3) ^ slab(p["floor_t"] - 0.5, zs)
    feats = []
    for sx in (-1, 1):
        x = sx * p["wash_x"]
        ring = cyl_z(p["wash_od"] / 2 + 0.3 + p["well_wall"], p["floor_t"] - 0.4, d["wash_top"], x, p["cell_y"]) - \
            cyl_z(p["wash_od"] / 2 + 0.3, p["floor_t"] - 0.1, d["wash_top"] + 1, x, p["cell_y"])
        peg = cyl_z(p["wash_id"] / 2 - 0.25, p["floor_t"] - 0.4, d["wash_top"] - 0.2, x, p["cell_y"])
        feats += [ring, peg]
        # saddle with cylindrical seat and a cable-tie slot
        x0 = sx * 25.0
        xa, xb = (min(x0, x0 + sx * 5.0), max(x0, x0 + sx * 5.0))
        sad = box(xa, xb, p["cell_y"] - 10.0, p["cell_y"] + 10.0, p["floor_t"] - 0.4, d["cell_zc"] + 2.0)
        sad = sad - Manifold.cylinder(80, p["cell_d"] / 2 + 0.3, p["cell_d"] / 2 + 0.3, 96).rotate([0, 90, 0]).translate([-40, p["cell_y"], d["cell_zc"]])
        sad = sad - box(xa + 1.5, xb - 1.5, p["cell_y"] - 11, p["cell_y"] + 11, p["floor_t"] + 1.5, p["floor_t"] + 4.0)
        feats.append(sad)
        # end stop
        xe = sx * (p["cell_len"] / 2 + 0.5)
        xa, xb = (min(xe, xe + sx * 1.6), max(xe, xe + sx * 1.6))
        stop = box(xa, xb, p["cell_y"] - 6.0, p["cell_y"] + 6.0, p["floor_t"] - 0.4, d["cell_zc"] + 4.0)
        stop = stop - box(xa - 1, xb + 1, p["cell_y"] - 2.0, p["cell_y"] + 2.0, d["cell_zc"] - 2, d["cell_zc"] + 5)  # tab/wire notch
        feats.append(stop)
        # charger standoff rail
        feats.append(box(sx * (p["chg_w"] / 2 - 0.5) - 1.0, sx * (p["chg_w"] / 2 - 0.5) + 1.0,
                         p["chg_y0"], p["chg_y0"] + p["chg_l"] - 1.0, p["floor_t"] - 0.4, p["floor_t"] + p["chg_standoff"]))
        # charger end stop behind the module
    feats.append(box(-6, 6, p["chg_y0"] - 1.2, p["chg_y0"] - 0.2, p["floor_t"] - 0.4, p["floor_t"] + p["chg_standoff"] + 3.0))
    for f in feats:
        shell = shell + (f ^ inner_clip)
    # sled pocket in the floor and two M3 nylon screw holes from below (counterbored into the flat)
    sx0, sx1, sy0, sy1 = sled_outline()
    shell = shell - box(sx0 - 0.2, sx1 + 0.2, sy0 - 0.2, sy1 + 0.2, p["floor_t"] - p["sled_pocket"], p["floor_t"] + 0.01)
    for x in (-12.0, 12.0):
        shell = shell - cyl_z(1.7, -1, p["floor_t"] + 1, x, (sy0 + sy1) / 2)
        shell = shell - cyl_z(3.2, -1, 1.2, x, (sy0 + sy1) / 2)
    # USB-C port for the charger (radial window at +y)
    zusb = p["floor_t"] + p["chg_standoff"] + 1.6 + 1.6
    port = radial_box(p["usb_w"], p["usb_h"], p["chg_y0"] + p["chg_l"] - 2.0, 30.0, 90.0, zusb)
    shell = shell - port
    # power switch hole
    shell = shell - radial(p["sw_d"] / 2, 20.0, sphere_r_at(d["S3"], p["sw_z"]) - 4.0, p["sw_az"], p["sw_z"])
    # data window (panel USB-C socket on a pigtail to the board's J-Link micro-USB)
    shell = shell - radial_box(p["usb_w"], p["usb_h"], 30.0, 30.0, p["data_az"], zusb)
    # cord channel under the boot (groove in the recessed outer surface) between the two cord holes
    shell = shell - cord_channel()
    return shell


def sled_outline():
    p = P
    half = p["board_w"] / 2 - p["groove_d"] + p["rail_w"] + 0.5
    return (-half, half, 6.5, 15.0)


def cord_channel():
    p, d = P, D
    span_deg = math.degrees(p["cord_span"] / d["S1"])
    a0, a1 = p["cord_az"] - span_deg / 2, p["cord_az"] + span_deg / 2
    ring = (sph(d["S1"] + 1.0) - sph(d["S1"] - 1.6)) ^ slab(p["cord_z"] - p["cord_d"] / 2, p["cord_z"] + p["cord_d"] / 2)
    # angular wedge
    wedge_pts = [(0, 0)] + [(200 * math.cos(math.radians(a)), 200 * math.sin(math.radians(a))) for a in np.linspace(a0, a1, 12)]
    wedge = CrossSection([wedge_pts]).extrude(BIG).translate([0, 0, -BIG / 2])
    return ring ^ wedge


def id_pad(boss: bool):
    """boss=True: the thickening inside the wall; boss=False: the recess plus debossed id text."""
    p, d = P, D
    r = p["id_r"]
    plane = math.sqrt(d["S0"] ** 2 - r ** 2) - p["id_depth"]  # distance of the pad plane from the centre
    if boss:
        m = Manifold.cylinder(6.0, r + 2.5, r + 2.5, 96).translate([0, 0, d["S2"] - 2.6]) ^ Manifold.sphere(d["S2"] + 0.05, SEG)
    else:
        m = Manifold.cylinder(10.0, r, r, 96).translate([0, 0, plane])
        txt = glyphs(p["id_text"], h=8.0, stroke=1.4, gap=1.8).rotate(90).extrude(2.0).translate([0, 0, plane - 0.6])
        m = m + txt
    return m.rotate([0, 90.0 - p["id_lat"], 0]).rotate([0, 0, p["id_az"]]).translate([0, 0, d["zc"]])


def top_shell():
    p, d = P, D
    zs, zhi = d["z_split"], d["z_boot_hi"]
    # outer: skirt at S1 under the boot, 45-degree step out to S0 (printable, no ledge), then the dome
    step = cyl_z(sphere_r_at(d["S1"], zhi), zhi, zhi + p["tb"], r2=sphere_r_at(d["S1"], zhi) + p["tb"], seg=SEG)
    outer = (sph(d["S1"]) ^ slab(zs, zhi + p["tb"])) + ((sph(d["S0"]) ^ slab(zhi, d["H"] + 1)) ^ (step + slab(zhi + p["tb"], d["H"] + 1)))
    # inner: S3 in the skirt, S2 in the dome, joined by a 45-degree cone (support-free when printed dome-up)
    rin0 = sphere_r_at(d["S3"], zhi)
    cone = cyl_z(rin0, zhi, zhi + p["tb"], r2=rin0 + p["tb"], seg=SEG)
    cavity = (sph(d["S3"]) ^ slab(zs - 1, zhi)) + (cone ^ sph(d["S2"])) + (sph(d["S2"]) ^ slab(zhi + p["tb"], d["H"] + 1))
    shell = outer - cavity
    # id pad on the dome flank (latitude id_lat). Recessed below the hull: the hull bridges it with a small
    # facet whose foot-of-CoM falls outside it (checked in stability()), so it is NOT a resting face.
    shell = shell + id_pad(boss=True)
    shell = shell - id_pad(boss=False)
    # light pipe: vertical hole + a tube boss below for a 3 mm LED / clear rod
    zdome = D["zc"] + math.sqrt(d["S2"] ** 2 - p["lp_y"] ** 2)
    tube = cyl_z(p["lp_d"] / 2 + 1.6, zdome - 8.0, zdome + 2.0, 0, p["lp_y"]) ^ sph(d["S2"] + 0.5)
    shell = shell + tube
    shell = shell - cyl_z(p["lp_d"] / 2, zdome - 9.0, d["H"] + 2, 0, p["lp_y"])
    # radial screw holes through the skirt (heads sit under the boot)
    zscr = zs + p["lip_h"] / 2
    for i in range(p["n_screws"]):
        az = p["screw_az0"] + i * 360.0 / p["n_screws"]
        shell = shell - radial(p["screw_d"] / 2, 10.0, d["S3"] - 2.0, az, zscr)
    return shell


def boot():
    p, d = P, D
    zlo, zhi = d["z_boot_lo"], d["z_boot_hi"]
    rin = sphere_r_at(d["S1"], zhi)
    band = (sph(d["S0"]) - sph(d["S1"])) ^ slab(zlo, zhi)
    # the 45-degree inner chamfer at the top that mates with the top shell's step
    ch = (cyl_z(rin, zhi, zhi + p["tb"], r2=rin + p["tb"], seg=SEG) - cyl_z(rin, zhi - 0.01, zhi + p["tb"] + 0.01, seg=SEG)) if False else None
    top_ring = (sph(d["S0"]) ^ slab(zhi, zhi + p["tb"])) - cyl_z(rin + 0.15, zhi - 0.01, zhi + p["tb"] + 0.01, r2=rin + p["tb"] + 0.15, seg=SEG)
    b = band + top_ring
    # pockets for the screw heads (button head M3: 5.7 dia, 1.65 high)
    zscr = d["z_split"] + p["lip_h"] / 2
    for i in range(p["n_screws"]):
        az = p["screw_az0"] + i * 360.0 / p["n_screws"]
        b = b - radial(3.4, 2.9, d["S1"] - 1.0, az, zscr)
    # switch membrane: pocket from inside leaving `membrane` mm of TPU, plus a debossed ring outside
    r1 = sphere_r_at(d["S1"], p["sw_z"])
    b = b - radial(5.5, p["tb"] - p["membrane"] + 1.0, r1 - 1.0, p["sw_az"], p["sw_z"])
    r0 = sphere_r_at(d["S0"], p["sw_z"])
    ring = radial(6.5, 2.0, r0 - 0.5, p["sw_az"], p["sw_z"]) - radial(5.5, 4.0, r0 - 1.5, p["sw_az"], p["sw_z"])
    b = b - ring
    # two cord holes
    span_deg = math.degrees(p["cord_span"] / d["S1"])
    for a in (p["cord_az"] - span_deg / 2, p["cord_az"] + span_deg / 2):
        b = b - radial(p["cord_d"] / 2, 10.0, sphere_r_at(d["S1"], p["cord_z"]) - 1.5, a, p["cord_z"])
    return b


def port_plug():
    """Push-in TPU plug for the USB-C charge window, flush with the hull (printed with the boot)."""
    p, d = P, D
    zusb = p["floor_t"] + p["chg_standoff"] + 3.2
    r_out = sphere_r_at(d["S0"], zusb) + 3.0
    w, h = p["usb_w"] - 0.3, p["usb_h"] - 0.3
    body = Manifold.cube([9.0, w, h]).translate([r_out - 9.0, -w / 2, zusb - h / 2])
    body = body.rotate([0, 0, 90.0]) ^ sph(d["S0"] - 0.05)
    core = Manifold.cube([5.5, w - 2.4, h - 2.4]).translate([sphere_r_at(d["S3"], zusb) - 3.0, -(w - 2.4) / 2, zusb - (h - 2.4) / 2]).rotate([0, 0, 90.0])
    nail = Manifold.cube([3.0, 6.0, 1.0]).translate([sphere_r_at(d["S0"], zusb) - 1.0, -3.0, zusb + h / 2 - 1.6]).rotate([0, 0, 90.0])
    return body - core - nail


def board_sled():
    p, d = P, D
    x0, x1, y0, y1 = sled_outline()
    zb = p["floor_t"] - p["sled_pocket"]
    base = box(x0, x1, y0, y1, zb, zb + p["sled_base_t"])
    sled = base
    ypcb = p["board_y"]
    for sx in (-1, 1):
        xin = sx * (p["board_w"] / 2 - p["groove_d"])
        xa, xb = sorted([xin, xin + sx * p["rail_w"]])
        rail = box(xa, xb, ypcb - 2.6, ypcb + p["pcb_t"] + 2.6, zb, d["board_z0"] + p["rail_h"])
        gx0, gx1 = sorted([xin - sx * 0.01, sx * (p["board_w"] / 2 + p["groove_clear"])])
        rail = rail - box(gx0, gx1, ypcb - p["groove_clear"] / 2, ypcb + p["pcb_t"] + p["groove_clear"] / 2,
                          d["board_z0"], d["board_z0"] + p["rail_h"] + 1)
        # ledge the PCB bottom corners stand on (the pedestal), 4 mm wide
        xl0, xl1 = sorted([sx * (p["board_w"] / 2 - 4.0), sx * (p["board_w"] / 2 - p["groove_d"])])
        rail = rail + box(xl0, xl1, ypcb - 2.6, ypcb + p["pcb_t"] + 2.6, zb, d["board_z0"])
        # tie slot near the top of each rail
        rail = rail - box(xa - 1, xb + 1, ypcb - 1.2, ypcb + p["pcb_t"] + 1.2, d["board_z0"] + p["rail_h"] - 8, d["board_z0"] + p["rail_h"] - 5)
        sled = sled + rail
    for x in (-12.0, 12.0):
        sled = sled - cyl_z(1.25, zb - 1, zb + p["sled_base_t"] - 0.6, x, (y0 + y1) / 2)
    return sled


# stand-ins for bought parts (for clearance checks, mass and figures)
def board_parts():
    """DWM3001CDK stand-in (outline and part positions TO BE MEASURED): DWM3001C module with its UWB and
    BLE antennas at the top end, J-Link section in the middle, two micro-USB at the bottom end, the 26-pin
    Raspberry Pi header along one long edge on the front."""
    p, d = P, D
    y0 = p["board_y"]
    z0, z1 = d["board_z0"], d["board_z1"]
    W = p["board_w"]
    pcb = box(-W / 2, W / 2, y0, y0 + p["pcb_t"], z0, z1)
    yf = y0 + p["pcb_t"]
    module = box(-p["ant_w"] / 2, p["ant_w"] / 2, yf, yf + 2.6, z1 - 26.0, z1 - 0.5)
    jlink = box(-7.0, 7.0, yf, yf + 2.0, z0 + 14.0, z0 + 26.0)
    usb = box(-W / 2 + 4.0, -W / 2 + 12.0, yf, yf + 3.0, z0, z0 + 5.5) + box(W / 2 - 12.0, W / 2 - 4.0, yf, yf + 3.0, z0, z0 + 5.5)
    header = box(W / 2 - 7.5, W / 2 - 2.5, yf, yf + p["comp_front"], z0 + 8.0, z0 + 8.0 + 33.0)
    back = box(-W / 2 + 2.5, W / 2 - 2.5, y0 - p["comp_back"], y0, z0 + 4.0, z1 - 4.0)
    return {"pcb": pcb, "dwm3001c_module": module, "jlink": jlink, "usb": usb, "rpi_header": header, "back_parts": back}


def cell():
    p, d = P, D
    return Manifold.cylinder(p["cell_len"], p["cell_d"] / 2, p["cell_d"] / 2, 96).rotate([0, 90, 0]).translate(
        [-p["cell_len"] / 2, p["cell_y"], d["cell_zc"]])


def washers():
    p = P
    out = []
    for sx in (-1, 1):
        for i in range(p["wash_n"]):
            z = p["floor_t"] + i * p["wash_t"]
            w = cyl_z(p["wash_od"] / 2, z + 0.02, z + p["wash_t"] - 0.02, sx * p["wash_x"], p["cell_y"]) - \
                cyl_z(p["wash_id"] / 2, z - 1, z + p["wash_t"] + 1, sx * p["wash_x"], p["cell_y"])
            out.append(w)
    return out


def charger():
    p = P
    z0 = p["floor_t"] + p["chg_standoff"]
    pcb = box(-p["chg_w"] / 2, p["chg_w"] / 2, p["chg_y0"], p["chg_y0"] + p["chg_l"], z0, z0 + 1.6)
    usb = box(-4.5, 4.5, p["chg_y0"] + p["chg_l"] - 7.0, p["chg_y0"] + p["chg_l"], z0 + 1.6, z0 + 4.8)
    ind = box(-5.0, 1.0, p["chg_y0"] + 2.0, p["chg_y0"] + 8.0, z0 + 1.6, z0 + 5.0)  # inductor
    return pcb + usb + ind


def switch_body():
    p, d = P, D
    r1 = sphere_r_at(d["S1"], p["sw_z"])
    return radial(3.4, 8.6, r1 - 9.3, p["sw_az"], p["sw_z"]) + radial_box(8.0, 8.0, r1 - 15.5, 5.5, p["sw_az"], p["sw_z"])


def data_socket():
    """panel USB-C socket stand-in, glued into the data window from inside"""
    p = P
    zusb = p["floor_t"] + p["chg_standoff"] + 3.2
    r_in = sphere_r_at(D["S3"], zusb)
    return radial_box(9.0, 3.4, r_in - 9.0, 9.0 + (sphere_r_at(D["S0"], zusb) - r_in) - 1.0, p["data_az"], zusb) + \
        radial_box(15.0, 6.0, r_in - 13.0, 4.0, p["data_az"], zusb)


# ----------------------------------------------------------------------------------------------
# helpers: mesh conversion, checks, mass
# ----------------------------------------------------------------------------------------------
def to_tm(m: Manifold) -> trimesh.Trimesh:
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    f = np.asarray(mesh.tri_verts)
    return trimesh.Trimesh(vertices=v, faces=f, process=True)


def checks(name, m: Manifold):
    t = to_tm(m)
    return {
        "part": name,
        "manifold_status": str(m.status()).split(".")[-1],
        "genus": int(m.genus()),
        "bodies": int(len(m.decompose())),
        "watertight": bool(t.is_watertight),
        "winding_consistent": bool(t.is_winding_consistent),
        "positive_volume": bool(t.volume > 0),
        "euler_number": int(t.euler_number),
        "volume_cm3": round(m.volume() / 1000.0, 2),
        "triangles": int(len(t.faces)),
        "bbox_mm": [[round(float(x), 2) for x in t.bounds[0]], [round(float(x), 2) for x in t.bounds[1]]],
    }


def com(m: Manifold):
    return to_tm(m).center_mass


def main():
    out_stl = HERE / "stl"
    out_res = HERE / "results"
    out_parts = HERE / "render" / "parts"
    for o in (out_stl, out_res, out_parts):
        o.mkdir(parents=True, exist_ok=True)

    parts = {
        "shell_bottom": (bottom_shell(), "PETG"),
        "shell_top": (top_shell(), "PETG"),
        "boot_tpu": (boot(), "TPU"),
        "board_sled": (board_sled(), "PETG"),
        "port_plug_tpu": (port_plug(), "TPU"),
    }
    bp = board_parts()
    board = Manifold.batch_boolean(list(bp.values()), __import__("manifold3d").OpType.Add)
    stand_ins = {
        "board": board,
        "cell": cell(),
        "ballast": Manifold.batch_boolean(washers(), __import__("manifold3d").OpType.Add),
        "charger": charger(),
        "switch": switch_body(),
        "data_socket": data_socket(),
    }

    # ---- export printable parts (print orientation is described in the README; STLs are in the
    #      assembled frame except the top shell, which is exported flipped? no: all assembled frame)
    report = {"params": P, "derived": {k: round(v, 3) for k, v in D.items()}, "parts": [], "stand_ins": []}
    for name, (m, mat) in parts.items():
        c = checks(name, m)
        c["material"] = mat
        c["mass_g_solid"] = round(m.volume() / 1000.0 * DENS[mat], 1)
        report["parts"].append(c)
        to_tm(m).export(out_parts / f"{name}.stl")
    for name, m in stand_ins.items():
        to_tm(m).export(out_parts / f"{name}.stl")
        report["stand_ins"].append({"part": name, "volume_cm3": round(m.volume() / 1000.0, 2)})
    # board sub-parts for the figures (antenna module coloured separately)
    for k, m in bp.items():
        to_tm(m).export(out_parts / f"board_{k}.stl")

    # print-orientation STLs: bottom shell and boot as assembled (flat / lower edge on the bed), top shell
    # as assembled (opening on the bed: translate so z_split -> 0), sled as assembled (base on bed)
    zs = D["z_split"]
    to_tm(parts["shell_bottom"][0]).export(out_stl / "puck_shell_bottom.stl")
    to_tm(parts["shell_top"][0].translate([0, 0, -zs])).export(out_stl / "puck_shell_top.stl")
    to_tm(parts["boot_tpu"][0].translate([0, 0, -D["z_boot_lo"]])).export(out_stl / "puck_boot_tpu.stl")
    to_tm(parts["board_sled"][0].translate([0, 0, -(P["floor_t"] - P["sled_pocket"])])).export(out_stl / "puck_board_sled.stl")
    plug = parts["port_plug_tpu"][0]
    # plug: lay the flange on the bed (flange faces +y after the 90 deg rotation)
    pl = plug.rotate([90, 0, 0])
    bb = pl.bounding_box()
    to_tm(pl.translate([0, 0, -bb[2]])).export(out_stl / "puck_port_plug_tpu.stl")
    asm = Manifold.batch_boolean([m for m, _ in parts.values()] + list(stand_ins.values()), __import__("manifold3d").OpType.Add)
    to_tm(asm).export(out_stl / "puck_assembly_preview.stl")

    # re-check the exported STL files from disk
    stl_checks = []
    for f in sorted(out_stl.glob("*.stl")):
        t = trimesh.load(f, force="mesh")
        stl_checks.append({"file": f"stl/{f.name}", "watertight": bool(t.is_watertight),
                           "winding_consistent": bool(t.is_winding_consistent), "volume_cm3": round(float(t.volume) / 1000, 2),
                           "body_count": int(len(t.split(only_watertight=False)))})
    report["stl_file_checks"] = stl_checks

    # ---- interference: every pair of solids in the assembly
    solids = {k: v[0] for k, v in parts.items()} | stand_ins
    names = list(solids)
    inter = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            v = (solids[names[i]] ^ solids[names[j]]).volume()
            inter.append({"a": names[i], "b": names[j], "overlap_mm3": round(v, 3)})
    report["interference"] = inter
    report["max_overlap_mm3"] = max(x["overlap_mm3"] for x in inter)

    # minimum gaps between key pairs (manifold min_gap, search radius 15 mm)
    gaps = {}
    for a, b in [("shell_top", "board"), ("shell_bottom", "board"), ("shell_bottom", "cell"), ("shell_top", "cell"),
                 ("board", "cell"), ("shell_top", "shell_bottom"), ("board_sled", "board"), ("charger", "shell_bottom"),
                 ("charger", "board_sled"), ("ballast", "cell")]:
        gaps[f"{a}|{b}"] = round(solids[a].min_gap(solids[b], 15.0), 2)
    report["min_gap_mm"] = gaps

    # ---- antenna keep-out: distances from the antenna zone to metal and to plastic
    ant = box(-P["ant_w"] / 2, P["ant_w"] / 2, P["board_y"] + P["pcb_t"], P["board_y"] + P["pcb_t"] + 2.9,
              D["board_z1"] - P["ant_len"], D["board_z1"])
    metal = {"cell": stand_ins["cell"], "ballast": stand_ins["ballast"], "charger": stand_ins["charger"], "switch": stand_ins["switch"],
             "data_socket": stand_ins["data_socket"]}
    screws = Manifold.batch_boolean([radial(1.5, 10.0, D["S4"] - P["insert_l"] - 1.0, P["screw_az0"] + i * 360 / P["n_screws"],
                                            D["z_split"] + P["lip_h"] / 2) for i in range(P["n_screws"])], __import__("manifold3d").OpType.Add)
    metal["screws_inserts"] = screws
    keep = {}
    for k, m in metal.items():
        keep[k] = round(ant.min_gap(m, 120.0), 1)
    keep["shell_top_plastic"] = round(ant.min_gap(parts["shell_top"][0], 40.0), 1)
    keep["board_sled_plastic"] = round(ant.min_gap(parts["board_sled"][0], 40.0), 1)
    report["antenna_keepout_mm"] = keep

    # ---- mass properties
    items = []
    for name, (m, mat) in parts.items():
        items.append((name, m.volume() / 1000.0 * DENS[mat], com(m)))
    items.append(("ballast_washers", stand_ins["ballast"].volume() / 1000.0 * DENS["steel"], com(stand_ins["ballast"])))
    items.append(("board", COMPONENTS["board"], com(bp["pcb"])))
    items.append(("cell", COMPONENTS["cell"], com(stand_ins["cell"])))
    items.append(("charger", COMPONENTS["charger"], com(stand_ins["charger"])))
    items.append(("switch", COMPONENTS["switch"], com(stand_ins["switch"])))
    items.append(("screws_inserts", COMPONENTS["screws_inserts"], np.array([0, 0, D["z_split"] + P["lip_h"] / 2])))
    items.append(("led_wires", COMPONENTS["led_wires"], np.array([0, 0, 30.0])))
    items.append(("usb_pigtails", COMPONENTS["usb_pigtails"], np.array([10.0, 10.0, 8.0])))
    M = sum(m for _, m, _ in items)
    G = sum(m * c for _, m, c in items) / M
    report["mass_items"] = [{"item": n, "mass_g": round(m, 1), "com_mm": [round(float(x), 1) for x in c]} for n, m, c in items]
    report["mass_total_g"] = round(M, 1)
    report["com_mm"] = [round(float(x), 2) for x in G]

    # ---- stability on the convex hull of the outer parts
    outer_v = np.vstack([to_tm(parts[k][0]).vertices for k in ("shell_bottom", "shell_top", "boot_tpu")])
    hull = trimesh.convex.convex_hull(outer_v)
    HV = hull.vertices
    stab = stability(HV, G, M)
    # sensitivity: no ballast, and the ballast needed
    items_nb = [it for it in items if it[0] != "ballast_washers"]
    Mnb = sum(m for _, m, _ in items_nb)
    Gnb = sum(m * c for _, m, c in items_nb) / Mnb

    stab["sensitivity"] = {
        "no_ballast": {"mass_g": round(Mnb, 1), "com_z_mm": round(float(Gnb[2]), 2), "margin_mm": round(D["zc"] - float(Gnb[2]), 2),
                       "self_rights": bool(D["zc"] - float(Gnb[2]) > 0)},
        "board_mass_for_zero_margin_g": board_mass_limit(items, M, G),
    }
    report["stability"] = stab

    # ---- print estimates
    report["print"] = print_estimates(report)
    report["brick"] = build_brick(out_stl, out_parts)
    report["brick"]["print"] = print_estimates({"parts": report["brick"]["parts"]})

    with open(out_res / "puck_build.json", "w") as fh:
        json.dump(report, fh, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))

    # GZ curves for the section figure
    np.save(out_res / "gz_curves.npy", np.array(stab.pop("_curves")))
    with open(out_res / "puck_build.json", "w") as fh:
        json.dump(report, fh, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    summary(report)
    return report


# ----------------------------------------------------------------------------------------------
# Variant B: the UNO Q "command brick" (placed, not thrown): UNO Q + battery bank + USB-C dongle inside,
# one DWM3001CDK standing on a mast on the lid, on a short USB lead from the dongle.
# ----------------------------------------------------------------------------------------------
B = dict(
    b_wall=2.4, b_floor=2.4, b_lid=2.4, b_r=8.0, b_clear=1.0,
    b_inner_l=176.0, b_inner_w=96.0, b_inner_h=20.0,
    # owner's battery bank: TO BE MEASURED (placeholder: a slim 5000 mAh bank)
    bank_l=100.0, bank_w=63.0, bank_h=15.0,
    # Arduino UNO Q: 68.58 x 53.34 mm, UNO hole pattern, 4 x R1.6 (datasheet ABX00162, section 12)
    unoq_l=68.58, unoq_w=53.34, unoq_t=1.6, unoq_top=10.0, unoq_bottom=2.0, unoq_standoff=4.0,
    unoq_holes=((13.97, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56)),
    # USB-C dongle with PD pass-through: TO BE MEASURED
    dongle_l=60.0, dongle_w=26.0, dongle_h=12.0,
    # mast for the DWM3001CDK on the lid (same rail profile as the puck sled)
    mast_h=30.0,
    n_vents=9,
)
BMASS = {"bank": 110.0, "unoq": 30.0, "dongle": 25.0, "dwm3001cdk": 14.0, "cables": 25.0, "screws_inserts": 4.0}  # ASSUMED


def rbox(l, w, h, r, z0=0.0):
    """rounded-corner box centred in x, y"""
    pts = [(sx * (l / 2 - r), sy * (w / 2 - r)) for sx in (-1, 1) for sy in (-1, 1)]
    return Manifold.batch_hull([Manifold.cylinder(h, r, r, 64).translate([x, y, z0]) for x, y in pts])


def brick_layout():
    b = B
    L, W = b["b_inner_l"], b["b_inner_w"]
    lay = {}
    lay["bank"] = (-L / 2 + 1.0, -L / 2 + 1.0 + b["bank_l"], -W / 2 + 9.0, -W / 2 + 9.0 + b["bank_w"])  # clear of the corner bosses
    ux0 = lay["bank"][1] + 4.0
    lay["unoq"] = (ux0, ux0 + b["unoq_l"], -W / 2 + 9.0, -W / 2 + 9.0 + b["unoq_w"])
    dy0 = lay["unoq"][3] + 3.0
    lay["dongle"] = (L / 2 - 9.0 - b["dongle_l"], L / 2 - 9.0, dy0, dy0 + b["dongle_w"])
    lay["mast"] = (L / 2 - 30.0, W / 2 - 12.0)  # mast centre on the lid (above the dongle corner)
    return lay


def brick_tray():
    b = B
    L, W, H = b["b_inner_l"], b["b_inner_w"], b["b_inner_h"]
    t = b["b_wall"]
    outer = rbox(L + 2 * t, W + 2 * t, b["b_floor"] + H, b["b_r"])
    inner = rbox(L, W, H + 1, b["b_r"] - t, b["b_floor"])
    tray = outer - inner
    lay = brick_layout()
    ux0, ux1, uy0, uy1 = lay["unoq"]
    # UNO Q standoffs (mirror the UNO frame so its USB edge (x = 0 in the UNO frame) faces the +x end wall)
    for hx, hy in b["unoq_holes"]:
        x, y = ux1 - hx, uy0 + hy
        tray = tray + cyl_z(3.2, b["b_floor"] - 0.5, b["b_floor"] + b["unoq_standoff"], x, y)
        tray = tray - cyl_z(2.0, b["b_floor"] + 0.5, b["b_floor"] + b["unoq_standoff"] + 1, x, y)  # M3 heat-set insert
    # corner screw bosses for the lid
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * (L / 2 - 4.5), sy * (W / 2 - 4.5)
            tray = tray + cyl_z(4.0, b["b_floor"] - 0.5, b["b_floor"] + H, x, y)
            tray = tray - cyl_z(2.0, b["b_floor"] + 2.0, b["b_floor"] + H + 1, x, y)
    # bank retaining ribs
    bx0, bx1, by0, by1 = lay["bank"]
    tray = tray + box(bx1 + 0.5, bx1 + 2.0, by0, by0 + 20, b["b_floor"] - 0.5, b["b_floor"] + 6)
    tray = tray + box(bx0 + 10, bx1 - 10, by1 + 0.5, by1 + 2.0, b["b_floor"] - 0.5, b["b_floor"] + 6)
    # windows: UNO Q USB-C on the +x end wall (centred on the board edge, TO BE MEASURED), bank ports on the -x end
    zc_u = b["b_floor"] + b["unoq_standoff"] + b["unoq_t"] + 1.6
    tray = tray - box(L / 2 - 5, L / 2 + 5, (uy0 + uy1) / 2 - 7.0, (uy0 + uy1) / 2 + 7.0, zc_u - 4.0, zc_u + 4.0)
    tray = tray - box(-L / 2 - 5, -L / 2 + 5, (by0 + by1) / 2 - 16.0, (by0 + by1) / 2 + 16.0, b["b_floor"] + 2.0, b["b_floor"] + b["bank_h"] - 1.0)
    # feet recesses for TPU pads (4 x 12 mm) on the underside
    for sx in (-1, 1):
        for sy in (-1, 1):
            tray = tray - cyl_z(6.2, -1, 0.8, sx * (L / 2 - 14), sy * (W / 2 - 12))
    return tray


def brick_lid():
    b = B
    L, W, H = b["b_inner_l"], b["b_inner_w"], b["b_inner_h"]
    t = b["b_wall"]
    z0 = b["b_floor"] + H
    lid = rbox(L + 2 * t, W + 2 * t, b["b_lid"], b["b_r"], z0)
    lid = lid + (rbox(L - 0.5, W - 0.5, 3.0, b["b_r"] - t, z0 - 3.0) - rbox(L - 4.5, W - 4.5, 4.0, b["b_r"] - t - 2, z0 - 3.5))  # locating lip
    lay = brick_layout()
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * (L / 2 - 4.5), sy * (W / 2 - 4.5)
            lid = lid - cyl_z(4.2, z0 - 3.5, z0 - 0.0 + 0.01, x, y)  # clear the tray bosses
            lid = lid - cyl_z(1.7, z0 - 1, z0 + b["b_lid"] + 1, x, y)
            lid = lid - cyl_z(3.1, z0 + b["b_lid"] - 1.2, z0 + b["b_lid"] + 1, x, y)
    # vents over the UNO Q
    ux0, ux1, uy0, uy1 = lay["unoq"]
    n = b["n_vents"]
    for i in range(n):
        x = ux0 + 8 + i * (ux1 - ux0 - 16) / (n - 1)
        lid = lid - box(x - 1.2, x + 1.2, uy0 + 10, uy1 - 10, z0 - 1, z0 + b["b_lid"] + 1)
    mx, my = lay["mast"]
    lid = lid - box(mx - 9, mx + 9, my - 4, my + 4, z0 - 1, z0 + b["b_lid"] + 1)  # cable hole into the brick
    for x in (mx - 15.0, mx + 15.0):
        lid = lid - cyl_z(1.7, z0 - 1, z0 + b["b_lid"] + 1, x, my + 9.0)  # mast screws (M3 + nut)
    return lid


def brick_mast():
    """Separate part (prints base-down): hollow column with a flange, plus the same rail pair as the puck
    sled, holding the DWM3001CDK upright with its antenna end up. Two M3 screws into the lid."""
    b = B
    lay = brick_layout()
    mx, my = lay["mast"]
    z0 = b["b_floor"] + b["b_inner_h"] + b["b_lid"]
    ztop = z0 + b["mast_h"]
    col = box(mx - 21, mx + 21, my - 6, my + 6, z0, ztop) - box(mx - 17, mx + 17, my - 4, my + 4, z0 - 1, ztop - 2.0)
    col = col - box(mx - 9, mx + 9, my - 7, my + 7, z0 + 6, ztop - 6)  # window: plug the USB lead in
    flange = box(mx - 21, mx + 21, my - 6, my + 13, z0, z0 + 2.4)
    m = col + flange
    for x in (mx - 15.0, mx + 15.0):
        m = m - cyl_z(1.7, z0 - 1, z0 + 3.4, x, my + 9.0)
    m = m - box(mx - 9, mx + 9, my - 4, my + 4, z0 - 1, z0 + 3)
    for sx in (-1, 1):
        xin = mx + sx * (P["board_w"] / 2 - P["groove_d"])
        xa, xb = sorted([xin, xin + sx * P["rail_w"]])
        rail = box(xa, xb, my - P["pcb_t"] / 2 - 2.6, my + P["pcb_t"] / 2 + 2.6, ztop - 1, ztop + 30.0)
        gx0, gx1 = sorted([xin - sx * 0.01, mx + sx * (P["board_w"] / 2 + P["groove_clear"])])
        rail = rail - box(gx0, gx1, my - P["pcb_t"] / 2 - 0.15, my + P["pcb_t"] / 2 + 0.15, ztop, ztop + 31)
        m = m + rail
    return m


def brick_stand_ins():
    b = B
    lay = brick_layout()
    bx0, bx1, by0, by1 = lay["bank"]
    ux0, ux1, uy0, uy1 = lay["unoq"]
    dx0, dx1, dy0, dy1 = lay["dongle"]
    zf = b["b_floor"]
    bank = box(bx0, bx1, by0, by1, zf, zf + b["bank_h"])
    zu = zf + b["unoq_standoff"]
    unoq = box(ux0, ux1, uy0, uy1, zu, zu + b["unoq_t"]) + box(ux0 + 3, ux1 - 3, uy0 + 3, uy1 - 3, zu + b["unoq_t"], zu + b["unoq_t"] + b["unoq_top"] - 1.0)
    for hx, hy in b["unoq_holes"]:
        unoq = unoq - cyl_z(1.6, zu - 1, zu + 5, ux1 - hx, uy0 + hy)
    dongle = box(dx0, dx1, dy0, dy1, zf, zf + b["dongle_h"])
    mx, my = lay["mast"]
    ztop = zf + b["b_inner_h"] + b["b_lid"] + b["mast_h"]
    W = P["board_w"]
    board = box(mx - W / 2, mx + W / 2, my - P["pcb_t"] / 2, my + P["pcb_t"] / 2, ztop, ztop + P["board_h"])
    module = box(mx - P["ant_w"] / 2, mx + P["ant_w"] / 2, my + P["pcb_t"] / 2, my + P["pcb_t"] / 2 + 2.6, ztop + P["board_h"] - 26, ztop + P["board_h"] - 0.5)
    return {"bank": bank, "unoq": unoq, "dongle": dongle, "dwm3001cdk": board + module, "dwm_module": module}


def build_brick(out_stl, out_parts):
    tray, lid, mast = brick_tray(), brick_lid(), brick_mast()
    si = brick_stand_ins()
    rep = {"params": {k: (list(v) if isinstance(v, tuple) else v) for k, v in B.items()}, "parts": []}
    for name, m, mat in (("brick_tray", tray, "PETG"), ("brick_lid", lid, "PETG"), ("brick_mast", mast, "PETG")):
        c = checks(name, m)
        c["material"] = mat
        c["mass_g_solid"] = round(m.volume() / 1000.0 * DENS[mat], 1)
        rep["parts"].append(c)
        to_tm(m).export(out_parts / f"{name}.stl")
    to_tm(tray).export(out_stl / "brick_tray.stl")
    zl = B["b_floor"] + B["b_inner_h"]
    to_tm(lid.rotate([180, 0, 0]).translate([0, 0, zl + B["b_lid"]])).export(out_stl / "brick_lid.stl")  # print orientation: top face on the bed
    to_tm(mast.translate([0, 0, -(zl + B["b_lid"])])).export(out_stl / "brick_mast.stl")
    for k, m in si.items():
        to_tm(m).export(out_parts / f"brick_{k}.stl")
    solids = {"tray": tray, "lid": lid, "mast": mast, "bank": si["bank"], "unoq": si["unoq"], "dongle": si["dongle"], "dwm3001cdk": si["dwm3001cdk"]}
    names = list(solids)
    rep["interference"] = [{"a": names[i], "b": names[j], "overlap_mm3": round((solids[names[i]] ^ solids[names[j]]).volume(), 3)}
                           for i in range(len(names)) for j in range(i + 1, len(names))]
    rep["max_overlap_mm3"] = max(x["overlap_mm3"] for x in rep["interference"])
    items = [("tray", tray.volume() / 1000 * DENS["PETG"], com(tray)), ("lid", lid.volume() / 1000 * DENS["PETG"], com(lid)),
             ("mast", mast.volume() / 1000 * DENS["PETG"], com(mast)),
             ("bank", BMASS["bank"], com(si["bank"])), ("unoq", BMASS["unoq"], com(si["unoq"])), ("dongle", BMASS["dongle"], com(si["dongle"])),
             ("dwm3001cdk", BMASS["dwm3001cdk"], com(si["dwm3001cdk"])), ("cables", BMASS["cables"], np.array([0, 0, B["b_floor"] + 6])),
             ("screws_inserts", BMASS["screws_inserts"], np.array([0, 0, B["b_floor"] + B["b_inner_h"] / 2]))]
    M = sum(m for _, m, _ in items)
    G = sum(m * c for _, m, c in items) / M
    L2 = B["b_inner_l"] / 2 + B["b_wall"]
    W2 = B["b_inner_w"] / 2 + B["b_wall"]
    # tip-over angle about each bottom edge: atan(distance from the CoM's foot to the edge / CoM height)
    tips = {"+x": math.degrees(math.atan2(L2 - G[0], G[2])), "-x": math.degrees(math.atan2(L2 + G[0], G[2])),
            "+y": math.degrees(math.atan2(W2 - G[1], G[2])), "-y": math.degrees(math.atan2(W2 + G[1], G[2]))}
    ant_top = B["b_floor"] + B["b_inner_h"] + B["b_lid"] + B["mast_h"] + P["board_h"]
    rep.update({"mass_items": [{"item": n, "mass_g": round(m, 1), "com_mm": [round(float(x), 1) for x in c]} for n, m, c in items],
                "mass_total_g": round(M, 1), "com_mm": [round(float(x), 2) for x in G],
                "tip_over_angle_deg": {k: round(v, 1) for k, v in tips.items()},
                "outer_mm": [round(2 * L2, 1), round(2 * W2, 1), round(B["b_floor"] + B["b_inner_h"] + B["b_lid"], 1)],
                "antenna_top_above_floor_mm": round(ant_top, 1),
                "antenna_to_bank_mm": round(si["dwm_module"].min_gap(si["bank"], 200.0), 1),
                "antenna_to_unoq_mm": round(si["dwm_module"].min_gap(si["unoq"], 200.0), 1),
                "antenna_to_dongle_mm": round(si["dwm_module"].min_gap(si["dongle"], 200.0), 1)})
    asm = Manifold.batch_boolean([tray, lid, mast] + [si[k] for k in ("bank", "unoq", "dongle", "dwm3001cdk")], __import__("manifold3d").OpType.Add)
    to_tm(asm).export(out_stl / "brick_assembly_preview.stl")
    return rep


def fib_sphere(n):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)


def stability(HV, G, M, coarse=False):
    """Resting poses of a rigid convex body on a flat floor.

    For an outward floor-contact normal n (unit vector in the body frame pointing from the body towards
    the floor), the height of the centre of mass above the floor is h(n) = max_v (v - G).n.
    Stable resting poses are the local minima of h over the sphere of directions. The righting arm
    GZ(theta) for a tilt theta away from upright is dh/dtheta: positive means gravity turns the puck back
    towards upright.
    """
    rel = HV - G
    n = 3000 if coarse else 20000
    dirs = fib_sphere(n)
    h = (rel @ dirs.T).max(axis=0)
    # neighbours via the convex hull of the direction points (a spherical triangulation)
    tri = trimesh.convex.convex_hull(dirs).faces
    nb = [set() for _ in range(n)]
    for a, b, c in tri:
        nb[a].update((b, c)); nb[b].update((a, c)); nb[c].update((a, b))
    minima = [i for i in range(n) if all(h[i] <= h[j] + 1e-9 for j in nb[i])]
    # merge minima that belong to the same flat face (h equal within 1e-3 and adjacent directions)
    poses = []
    for i in minima:
        d = dirs[i]
        if any(np.dot(d, q["n"]) > math.cos(math.radians(8)) for q in poses):
            continue
        # basin test: lowest h on rings 2-6 degrees away. If some direction within 6 degrees is lower, the
        # minimum is a facet of the tessellated sphere near an unstable top (depth of microns), not a pose.
        u = np.cross(d, [1, 0, 0] if abs(d[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u); w = np.cross(d, u)
        esc = []
        for ang in np.radians([2, 4, 6]):
            ring = np.array([math.cos(ang) * d + math.sin(ang) * (math.cos(a) * u + math.sin(a) * w) for a in np.linspace(0, 2 * np.pi, 72, endpoint=False)])
            esc.append(float((rel @ ring.T).max(axis=0).min() - h[i]))
        poses.append({"n": d, "h": float(h[i]), "basin_mm": min(esc)})
    real = [q for q in poses if q["basin_mm"] > 0.05]
    artefacts = [q for q in poses if q["basin_mm"] <= 0.05]
    fmt = lambda q: {"contact_normal": [round(float(x), 3) for x in q["n"]], "tilt_from_upright_deg": round(math.degrees(math.acos(-q["n"][2])), 1),
                     "com_height_mm": round(q["h"], 2), "basin_depth_mm": round(q["basin_mm"], 3)}
    res = {"method": "local minima of the CoM height over 20000 floor-normal directions on the convex hull of the shells and boot; "
                     "a minimum counts as a resting pose only if every direction 2-6 degrees away is higher by more than 0.05 mm",
           "n_stable_poses": len(real),
           "stable_poses": [fmt(q) for q in real],
           "tessellation_artefacts": [fmt(q) for q in artefacts]}
    if coarse:
        return res
    # GZ curves at 24 azimuths
    th = np.radians(np.arange(0.0, 180.01, 0.5))
    curves = []
    gz_min_mid = 1e9
    worst = None
    for az in np.radians(np.arange(0, 360, 15)):
        nvec = np.stack([np.sin(th) * np.cos(az), np.sin(th) * np.sin(az), -np.cos(th)], 1)
        hh = (rel @ nvec.T).max(axis=0)
        gz = np.gradient(hh, th)
        curves.append(np.stack([np.degrees(th), hh, gz], 1))
        sel = (np.degrees(th) >= 5) & (np.degrees(th) <= 170)
        k = np.argmin(gz[sel])
        if gz[sel][k] < gz_min_mid:
            gz_min_mid = float(gz[sel][k]); worst = (float(np.degrees(az)), float(np.degrees(th[sel][k])))
    curves = np.array(curves)
    h_up = float(curves[:, 0, 1].mean())
    h_down = float(curves[:, -1, 1].mean())
    g = 9.81e-3
    res.update({
        "com_height_upright_mm": round(h_up, 2),
        "com_height_upside_down_mm": round(h_down, 2),
        "sphere_centre_height_mm": round(D["zc"], 2),
        "metacentric_margin_mm": round(D["zc"] - float(G[2]), 2),
        "com_lateral_offset_mm": round(float(math.hypot(G[0], G[1])), 2),
        "gz_max_mm": round(float(curves[:, :, 2].max()), 2),
        "gz_min_between_5_and_170_deg_mm": round(gz_min_mid, 2),
        "gz_note": "beyond 170 deg the mesh GZ wobbles around zero by about 1 mm: the tessellated sphere (160 segments) near the unstable top equilibrium",
        "gz_min_at_azimuth_deg_tilt_deg": worst,
        "gz_at_90_deg_mm_mean": round(float(curves[:, 180, 2].mean()), 2),
        "gz_at_170_deg_mm_min": round(float(curves[:, 340, 2].min()), 2),
        "righting_energy_from_upside_down_mJ": round(M * g * (h_down - h_up), 1),
        "flat_edge_tilt_deg": round(math.degrees(math.atan2(P["rf"], D["zc"])), 1),
        "analytic": "hull = sphere(R, centre height zc) with a flat bottom; on the sphere part h(n) = R + (C - G).n, whose only "
                    "minimum n = -(C-G)/|C-G| lies inside the flat's cone of normals when zc > zG and the CoM's lateral offset < rf, "
                    "so the puck has exactly one resting pose (upright on the flat) and the upside-down equilibrium is unstable; "
                    "righting arm on the sphere part GZ(theta) = (zc - zG) sin(theta) for a CoM on the axis",
        "_curves": curves,
    })
    return res


def board_mass_limit(items, M, G):
    """How heavy could the board (centroid as modelled) get before the margin reaches zero."""
    zb = [c for n, m, c in items if n == "board"][0][2]
    # zG(m) = (M*zG + dm*zb)/(M+dm) = zc  ->  dm = M (zc - zG)/(zb - zc)
    if zb <= D["zc"]:
        return None
    dm = M * (D["zc"] - G[2]) / (zb - D["zc"])
    return round(COMPONENTS["board"] + float(dm), 0)


def print_estimates(report):
    """Rough print time from extruded volume at an assumed average volumetric rate.

    Not a slicer result. PETG at an average 4 mm3/s and TPU at 1.5 mm3/s (0.4 mm nozzle, 0.2 mm layers,
    including travel and slow outer walls) are typical for a stock bed-slinger; supports add 10 percent
    on the top shell.
    """
    rate = {"PETG": 4.0, "TPU": 1.5}
    out = []
    tot = 0.0
    for p in report["parts"]:
        mm3 = p["volume_cm3"] * 1000
        t_h = mm3 / rate[p["material"]] / 3600
        if p["part"] == "shell_top":
            t_h *= 1.10
        tot += t_h
        out.append({"part": p["part"], "material": p["material"], "mass_g": p["mass_g_solid"], "time_h_est": round(t_h, 1)})
    return {"method": "solid volume / assumed average volumetric rate (PETG 4 mm3/s, TPU 1.5 mm3/s), +10% supports on the top shell; not sliced",
            "parts": out, "total_h_est": round(tot, 1)}


def summary(r):
    s = r["stability"]
    print("parts:")
    for p in r["parts"]:
        print(f"  {p['part']:<15} {p['material']:<5} {p['volume_cm3']:>7} cm3 {p['mass_g_solid']:>6} g  watertight={p['watertight']} genus={p['genus']} status={p['manifold_status']}")
    print("stl files:", [(c["file"], c["watertight"]) for c in r["stl_file_checks"]])
    print("max overlap mm3:", r["max_overlap_mm3"])
    for x in r["interference"]:
        if x["overlap_mm3"] > 0.001:
            print("   overlap", x)
    print("min gaps:", r["min_gap_mm"])
    print("antenna keep-out:", r["antenna_keepout_mm"])
    print("mass total g:", r["mass_total_g"], "CoM:", r["com_mm"])
    print("stability:", {k: v for k, v in s.items() if k not in ("stable_poses",)})
    print("stable poses:", s["stable_poses"])
    print("print:", r["print"])
    bk = r["brick"]
    print("brick parts:", [(p["part"], p["volume_cm3"], p["mass_g_solid"], p["watertight"], p["bodies"]) for p in bk["parts"]])
    print("brick overlap:", bk["max_overlap_mm3"], [x for x in bk["interference"] if x["overlap_mm3"] > 0.001])
    print("brick:", {k: bk[k] for k in ("mass_total_g", "com_mm", "tip_over_angle_deg", "outer_mm", "antenna_top_above_floor_mm", "antenna_to_bank_mm", "antenna_to_unoq_mm", "antenna_to_dongle_mm")})
    print("brick print:", bk["print"])


if __name__ == "__main__":
    main()
