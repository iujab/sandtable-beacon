// Sandtable scout puck family — OpenSCAD source.
//
// Same construction and parameter names as build_puck.py, which is the version that was actually built,
// meshed and checked (watertightness, interference, mass, stability). THIS FILE WAS NOT RENDERED: OpenSCAD is
// not installed on the machine it was written on. It uses only core primitives, hull(), difference(),
// intersection(), modules and text(), and should open in OpenSCAD 2021.01 or newer; if a dimension disagrees
// with the STLs, the STLs (from build_puck.py) are the checked reference.
//
// Variant A, "Beacon puck": self-righting sphere-with-flat for one Qorvo DWM3001CDK + one 18650 cell.
// Variant B, "command brick": flat box for an Arduino UNO Q + battery bank + USB-C dongle, with a mast that
// holds one DWM3001CDK upright. Placed, not thrown.
//
// Frame: mm, z up, z = 0 = the floor when the part sits as used.
// Pick what to render:
part = "assembly";   // assembly | shell_bottom | shell_top | boot | sled | plug | brick | brick_tray | brick_lid | brick_mast

/* [Outer form] */
R = 56.0;            // outer sphere radius (the convex hull is exactly this sphere + the flat)
rf = 40.0;           // flat bottom radius: the only resting face
wall = 2.0;          // PETG wall
floor_t = 2.4;
tb = 2.4;            // TPU boot thickness; shells are recessed by this under the boot
lat_boot_lo = -25;   // boot lower edge (latitude, degrees)
split_dz = -6.0;     // split plane relative to the sphere centre
boot_ov = 7.0;       // boot overlap onto the top shell skirt
lip_h = 7.0; lip_t = 1.8; fit = 0.25;

/* [Board: DWM3001CDK — outline NOT published by Qorvo: every value TO BE MEASURED] */
board_w = 32.0; board_h = 64.0; pcb_t = 1.6;
comp_front = 9.0;    // incl. the 26-pin Raspberry Pi header if fitted
comp_back = 2.0;
board_y = 10.0;      // y of the PCB back face
ant_len = 12.0; ant_w = 20.0;
rail_w = 5.0; rail_h = 40.0; groove_d = 1.2; groove_clear = 0.3;
sled_base_t = 2.0; sled_pocket = 0.6; sled_lift = 10.0;

/* [Cell, ballast, charger] */
cell_d = 18.4; cell_len = 67.0; cell_y = -7.5;
wash_od = 24.0; wash_id = 8.4; wash_t = 2.0; wash_n = 4; wash_x = 13.0; well_wall = 1.2;   // DIN 9021 M8
chg_w = 17.0; chg_l = 22.0; chg_standoff = 4.0; chg_y0 = 16.6;
usb_w = 13.0; usb_h = 7.5; data_az = 45;

/* [Joint, switch, light pipe, id, cord] */
n_screws = 3; screw_az0 = 30; insert_d = 4.0; insert_l = 5.7; screw_d = 3.4;
sw_d = 7.2; sw_az = 225; sw_z = 24.0; membrane = 1.0;
lp_d = 3.2; lp_y = -30.0;
id_r = 7.5; id_depth = 0.8; id_lat = 35; id_az = 180; id_text = "P1";
cord_d = 3.5; cord_az = 315; cord_z = 24.0; cord_span = 18.0;

$fn = 120;
BIG = 400;

// ---------------- derived
zc = sqrt(R * R - rf * rf);
H = zc + R;
z_split = zc + split_dz;
z_boot_lo = zc + R * sin(lat_boot_lo);
z_boot_hi = z_split + boot_ov;
S0 = R; S1 = R - tb; S2 = R - wall; S3 = R - tb - wall; S4 = S3 - fit; S5 = S4 - lip_t;
board_z0 = floor_t - sled_pocket + sled_base_t + sled_lift;
board_z1 = board_z0 + board_h;
wash_top = floor_t + wash_n * wash_t;
cell_zc = wash_top + 0.6 + cell_d / 2;
zusb = floor_t + chg_standoff + 3.2;
function hr(S, z) = sqrt(max(S * S - (z - zc) * (z - zc), 0));   // horizontal radius of sphere S at height z
function sled_half() = board_w / 2 - groove_d + rail_w + 0.5;

// ---------------- helpers
module sph(r) { translate([0, 0, zc]) sphere(r = r); }
module slab(z0, z1) { translate([-BIG / 2, -BIG / 2, z0]) cube([BIG, BIG, z1 - z0]); }
module box(x0, x1, y0, y1, z0, z1) { translate([x0, y0, z0]) cube([x1 - x0, y1 - y0, z1 - z0]); }
module cylz(r, z0, z1, x = 0, y = 0, r2 = -1) { translate([x, y, z0]) cylinder(h = z1 - z0, r1 = r, r2 = (r2 < 0 ? r : r2)); }
module radial(r, len, r0, az, z) { rotate([0, 0, az]) translate([r0, 0, z]) rotate([0, 90, 0]) cylinder(h = len, r = r, $fn = 48); }
module radial_box(w, h, r0, len, az, z) { rotate([0, 0, az]) translate([r0, -w / 2, z - h / 2]) cube([len, w, h]); }
module shell_between(ro, ri, z0, z1) { intersection() { difference() { sph(ro); sph(ri); } slab(z0, z1); } }

// ================= Variant A: Beacon puck =================
module shell_bottom() {
    difference() {
        union() {
            difference() {
                union() {
                    intersection() { sph(S0); slab(0, z_boot_lo); }
                    intersection() { sph(S1); slab(z_boot_lo, z_split); }
                }
                intersection() { sph(S3); slab(floor_t, z_split + 1); }
            }
            shell_between(S3 + 0.3, S5, z_split - 4, z_split);                 // lip attachment band
            shell_between(S4, S5, z_split, z_split + lip_h);                   // lip
            for (i = [0 : n_screws - 1]) intersection() {                      // insert bosses
                radial_box(10, lip_h + 8, S5 - 6.5, 7, screw_az0 + i * 360 / n_screws, z_split + lip_h / 2 - 4);
                sph(S4);
            }
            intersection() {                                                   // interior features
                union() {
                    for (sx = [-1, 1]) {
                        x = sx * wash_x;
                        difference() {                                         // washer well
                            cylz(wash_od / 2 + 0.3 + well_wall, floor_t - 0.4, wash_top, x, cell_y);
                            cylz(wash_od / 2 + 0.3, floor_t - 0.1, wash_top + 1, x, cell_y);
                        }
                        cylz(wash_id / 2 - 0.25, floor_t - 0.4, wash_top - 0.2, x, cell_y);   // locating peg
                        xa = min(sx * 25, sx * 30); xb = max(sx * 25, sx * 30);
                        difference() {                                         // cell saddle with tie slot
                            box(xa, xb, cell_y - 10, cell_y + 10, floor_t - 0.4, cell_zc + 2);
                            translate([-40, cell_y, cell_zc]) rotate([0, 90, 0]) cylinder(h = 80, r = cell_d / 2 + 0.3);
                            box(xa + 1.5, xb - 1.5, cell_y - 11, cell_y + 11, floor_t + 1.5, floor_t + 4);
                        }
                        xe = sx * (cell_len / 2 + 0.5);
                        difference() {                                         // end stop with wire notch
                            box(min(xe, xe + sx * 1.6), max(xe, xe + sx * 1.6), cell_y - 6, cell_y + 6, floor_t - 0.4, cell_zc + 4);
                            box(min(xe, xe + sx * 1.6) - 1, max(xe, xe + sx * 1.6) + 1, cell_y - 2, cell_y + 2, cell_zc - 2, cell_zc + 5);
                        }
                        cx = sx * (chg_w / 2 - 0.5);                           // charger standoff rail
                        box(min(cx - 1, cx + 1), max(cx - 1, cx + 1), chg_y0, chg_y0 + chg_l - 1, floor_t - 0.4, floor_t + chg_standoff);
                    }
                    box(-6, 6, chg_y0 - 1.2, chg_y0 - 0.2, floor_t - 0.4, floor_t + chg_standoff + 3);
                }
                intersection() { sph(S3 + 0.3); slab(floor_t - 0.5, z_split); }
            }
        }
        for (i = [0 : n_screws - 1])
            radial(insert_d / 2, insert_l + 1, S4 - insert_l - 0.5, screw_az0 + i * 360 / n_screws, z_split + lip_h / 2);
        box(-sled_half() - 0.2, sled_half() + 0.2, 6.3, 15.2, floor_t - sled_pocket, floor_t + 0.01);   // sled pocket
        for (x = [-12, 12]) { cylz(1.7, -1, floor_t + 1, x, 10.75); cylz(3.2, -1, 1.2, x, 10.75); }     // M3 nylon from below
        radial_box(usb_w, usb_h, chg_y0 + chg_l - 2, 30, 90, zusb);              // charge window
        radial_box(usb_w, usb_h, 30, 30, data_az, zusb);                         // data window
        radial(sw_d / 2, 20, hr(S3, sw_z) - 4, sw_az, sw_z);                     // power switch
        cord_channel();
    }
}

module cord_channel() {
    span = cord_span / S1 * 180 / PI;
    intersection() {
        shell_between(S1 + 1, S1 - 1.6, cord_z - cord_d / 2, cord_z + cord_d / 2);
        linear_extrude(height = BIG, center = true)
            polygon(concat([[0, 0]], [for (a = [cord_az - span / 2 : span / 11 : cord_az + span / 2 + 0.001]) [200 * cos(a), 200 * sin(a)]]));
    }
}

module id_pad(boss) {
    plane = sqrt(S0 * S0 - id_r * id_r) - id_depth;
    translate([0, 0, zc]) rotate([0, 0, id_az]) rotate([0, 90 - id_lat, 0]) {
        if (boss) {
            intersection() { translate([0, 0, S2 - 2.6]) cylinder(h = 6, r = id_r + 2.5); sphere(r = S2 + 0.05); }
        } else {
            translate([0, 0, plane]) cylinder(h = 10, r = id_r);
            translate([0, 0, plane - 0.6]) rotate([0, 0, 90]) linear_extrude(height = 2)
                text(id_text, size = 7, font = "Liberation Sans:style=Bold", halign = "center", valign = "center");
        }
    }
}

module shell_top() {
    rin0 = hr(S3, z_boot_hi);
    zdome = zc + sqrt(S2 * S2 - lp_y * lp_y);
    difference() {
        union() {
            difference() {
                union() {
                    intersection() { sph(S1); slab(z_split, z_boot_hi + tb); }
                    intersection() {                                            // 45-degree step out to the dome
                        intersection() { sph(S0); slab(z_boot_hi, H + 1); }
                        union() { cylz(hr(S1, z_boot_hi), z_boot_hi, z_boot_hi + tb, 0, 0, hr(S1, z_boot_hi) + tb); slab(z_boot_hi + tb, H + 1); }
                    }
                }
                union() {                                                       // cavity: skirt, 45-degree cone, dome
                    intersection() { sph(S3); slab(z_split - 1, z_boot_hi); }
                    intersection() { cylz(rin0, z_boot_hi, z_boot_hi + tb, 0, 0, rin0 + tb); sph(S2); }
                    intersection() { sph(S2); slab(z_boot_hi + tb, H + 1); }
                }
            }
            id_pad(true);
            intersection() { cylz(lp_d / 2 + 1.6, zdome - 8, zdome + 2, 0, lp_y); sph(S2 + 0.5); }   // light-pipe boss
        }
        id_pad(false);
        cylz(lp_d / 2, zdome - 9, H + 2, 0, lp_y);
        for (i = [0 : n_screws - 1]) radial(screw_d / 2, 10, S3 - 2, screw_az0 + i * 360 / n_screws, z_split + lip_h / 2);
    }
}

module boot() {
    rin = hr(S1, z_boot_hi);
    span = cord_span / S1 * 180 / PI;
    difference() {
        union() {
            shell_between(S0, S1, z_boot_lo, z_boot_hi);
            difference() {
                intersection() { sph(S0); slab(z_boot_hi, z_boot_hi + tb); }
                cylz(rin + 0.15, z_boot_hi - 0.01, z_boot_hi + tb + 0.01, 0, 0, rin + tb + 0.15);
            }
        }
        for (i = [0 : n_screws - 1]) radial(3.4, 2.9, S1 - 1, screw_az0 + i * 360 / n_screws, z_split + lip_h / 2);   // screw heads
        radial(5.5, tb - membrane + 1, hr(S1, sw_z) - 1, sw_az, sw_z);                                              // switch membrane
        difference() { radial(6.5, 2, hr(S0, sw_z) - 0.5, sw_az, sw_z); radial(5.5, 4, hr(S0, sw_z) - 1.5, sw_az, sw_z); }
        for (a = [cord_az - span / 2, cord_az + span / 2]) radial(cord_d / 2, 10, hr(S1, cord_z) - 1.5, a, cord_z);
    }
}

module sled() {
    zb = floor_t - sled_pocket;
    difference() {
        union() {
            box(-sled_half(), sled_half(), 6.5, 15.0, zb, zb + sled_base_t);
            for (sx = [-1, 1]) {
                xin = sx * (board_w / 2 - groove_d);
                xa = min(xin, xin + sx * rail_w); xb = max(xin, xin + sx * rail_w);
                difference() {
                    union() {
                        box(xa, xb, board_y - 2.6, board_y + pcb_t + 2.6, zb, board_z0 + rail_h);
                        box(min(sx * (board_w / 2 - 4), xin), max(sx * (board_w / 2 - 4), xin), board_y - 2.6, board_y + pcb_t + 2.6, zb, board_z0);
                    }
                    box(min(xin - sx * 0.01, sx * (board_w / 2 + groove_clear)), max(xin - sx * 0.01, sx * (board_w / 2 + groove_clear)),
                        board_y - groove_clear / 2, board_y + pcb_t + groove_clear / 2, board_z0, board_z0 + rail_h + 1);
                    box(xa - 1, xb + 1, board_y - 1.2, board_y + pcb_t + 1.2, board_z0 + rail_h - 8, board_z0 + rail_h - 5);
                }
            }
        }
        for (x = [-12, 12]) cylz(1.25, zb - 1, zb + sled_base_t - 0.6, x, 10.75);
    }
}

module plug() {   // push-in TPU plug for the charge window, flush with the hull
    w = usb_w - 0.3; h = usb_h - 0.3;
    difference() {
        intersection() { radial_box(w, h, hr(S0, zusb) + 3 - 9, 9, 90, zusb); sph(S0 - 0.05); }
        radial_box(w - 2.4, h - 2.4, hr(S3, zusb) - 3, 5.5, 90, zusb);
    }
}

// stand-ins (not printed)
module board_standin() {
    color("#1b3a6b") box(-board_w / 2, board_w / 2, board_y, board_y + pcb_t, board_z0, board_z1);
    color("#d9dce0") box(-ant_w / 2, ant_w / 2, board_y + pcb_t, board_y + pcb_t + 2.6, board_z1 - 26, board_z1 - 0.5);
    color("#222") box(board_w / 2 - 7.5, board_w / 2 - 2.5, board_y + pcb_t, board_y + pcb_t + comp_front, board_z0 + 8, board_z0 + 41);
}
module cell_standin() { color("#2f7fc0") translate([-cell_len / 2, cell_y, cell_zc]) rotate([0, 90, 0]) cylinder(h = cell_len, r = cell_d / 2); }
module washers() { color("#b4b9be") for (sx = [-1, 1], i = [0 : wash_n - 1]) difference() {
    cylz(wash_od / 2, floor_t + i * wash_t, floor_t + (i + 1) * wash_t, sx * wash_x, cell_y);
    cylz(wash_id / 2, floor_t + i * wash_t - 1, floor_t + (i + 1) * wash_t + 1, sx * wash_x, cell_y); } }

module puck_assembly() {
    color("#e8661f") shell_bottom();
    color("#e8661f") shell_top();
    color("#3a3e44") boot();
    color("#3a3e44") plug();
    color("#c9cdd1") sled();
    board_standin(); cell_standin(); washers();
}

// ================= Variant B: command brick =================
/* [Brick] */
b_wall = 2.4; b_floor = 2.4; b_lid = 2.4; b_r = 8.0;
b_inner_l = 176.0; b_inner_w = 96.0; b_inner_h = 20.0;
bank_l = 100.0; bank_w = 63.0; bank_h = 15.0;            // owner's battery bank: TO BE MEASURED
unoq_l = 68.58; unoq_w = 53.34; unoq_t = 1.6; unoq_standoff = 4.0;   // Arduino UNO Q datasheet ABX00162, section 12
unoq_holes = [[13.97, 2.54], [15.24, 50.8], [66.04, 7.62], [66.04, 35.56]];   // UNO pattern, 4 x R1.6
dongle_l = 60.0; dongle_w = 26.0; dongle_h = 12.0;      // USB-C PD dongle: TO BE MEASURED
mast_h = 30.0; n_vents = 9;

bL = b_inner_l; bW = b_inner_w;
bank_x0 = -bL / 2 + 1; bank_y0 = -bW / 2 + 9;
ux0 = bank_x0 + bank_l + 4; ux1 = ux0 + unoq_l; uy0 = -bW / 2 + 9; uy1 = uy0 + unoq_w;
mast_x = bL / 2 - 30; mast_y = bW / 2 - 12;

module rbox(l, w, h, r, z0 = 0) { hull() for (sx = [-1, 1], sy = [-1, 1]) translate([sx * (l / 2 - r), sy * (w / 2 - r), z0]) cylinder(h = h, r = r); }

module brick_tray() {
    difference() {
        union() {
            difference() { rbox(bL + 2 * b_wall, bW + 2 * b_wall, b_floor + b_inner_h, b_r); rbox(bL, bW, b_inner_h + 1, b_r - b_wall, b_floor); }
            for (h = unoq_holes) cylz(3.2, b_floor - 0.5, b_floor + unoq_standoff, ux1 - h[0], uy0 + h[1]);
            for (sx = [-1, 1], sy = [-1, 1]) cylz(4, b_floor - 0.5, b_floor + b_inner_h, sx * (bL / 2 - 4.5), sy * (bW / 2 - 4.5));
            box(bank_x0 + bank_l + 0.5, bank_x0 + bank_l + 2, bank_y0, bank_y0 + 20, b_floor - 0.5, b_floor + 6);
            box(bank_x0 + 10, bank_x0 + bank_l - 10, bank_y0 + bank_w + 0.5, bank_y0 + bank_w + 2, b_floor - 0.5, b_floor + 6);
        }
        for (h = unoq_holes) cylz(2.0, b_floor + 0.5, b_floor + unoq_standoff + 1, ux1 - h[0], uy0 + h[1]);   // M3 inserts
        for (sx = [-1, 1], sy = [-1, 1]) cylz(2.0, b_floor + 2, b_floor + b_inner_h + 1, sx * (bL / 2 - 4.5), sy * (bW / 2 - 4.5));
        zcu = b_floor + unoq_standoff + unoq_t + 1.6;
        box(bL / 2 - 5, bL / 2 + 5, (uy0 + uy1) / 2 - 7, (uy0 + uy1) / 2 + 7, zcu - 4, zcu + 4);                 // UNO Q USB-C
        box(-bL / 2 - 5, -bL / 2 + 5, bank_y0 + bank_w / 2 - 16, bank_y0 + bank_w / 2 + 16, b_floor + 2, b_floor + bank_h - 1);   // bank ports
        for (sx = [-1, 1], sy = [-1, 1]) cylz(6.2, -1, 0.8, sx * (bL / 2 - 14), sy * (bW / 2 - 12));             // TPU feet
    }
}

module brick_lid() {
    z0 = b_floor + b_inner_h;
    difference() {
        union() {
            rbox(bL + 2 * b_wall, bW + 2 * b_wall, b_lid, b_r, z0);
            difference() { rbox(bL - 0.5, bW - 0.5, 3, b_r - b_wall, z0 - 3); rbox(bL - 4.5, bW - 4.5, 4, b_r - b_wall - 2, z0 - 3.5); }
        }
        for (sx = [-1, 1], sy = [-1, 1]) {
            x = sx * (bL / 2 - 4.5); y = sy * (bW / 2 - 4.5);
            cylz(4.2, z0 - 3.5, z0 + 0.01, x, y); cylz(1.7, z0 - 1, z0 + b_lid + 1, x, y); cylz(3.1, z0 + b_lid - 1.2, z0 + b_lid + 1, x, y);
        }
        for (i = [0 : n_vents - 1]) { x = ux0 + 8 + i * (unoq_l - 16) / (n_vents - 1); box(x - 1.2, x + 1.2, uy0 + 10, uy1 - 10, z0 - 1, z0 + b_lid + 1); }
        box(mast_x - 9, mast_x + 9, mast_y - 4, mast_y + 4, z0 - 1, z0 + b_lid + 1);
        for (x = [mast_x - 15, mast_x + 15]) cylz(1.7, z0 - 1, z0 + b_lid + 1, x, mast_y + 9);
    }
}

module brick_mast() {
    z0 = b_floor + b_inner_h + b_lid; ztop = z0 + mast_h;
    difference() {
        union() {
            difference() {
                box(mast_x - 21, mast_x + 21, mast_y - 6, mast_y + 6, z0, ztop);
                box(mast_x - 17, mast_x + 17, mast_y - 4, mast_y + 4, z0 - 1, ztop - 2);
                box(mast_x - 9, mast_x + 9, mast_y - 7, mast_y + 7, z0 + 6, ztop - 6);
            }
            box(mast_x - 21, mast_x + 21, mast_y - 6, mast_y + 13, z0, z0 + 2.4);
            for (sx = [-1, 1]) {
                xin = mast_x + sx * (board_w / 2 - groove_d);
                difference() {
                    box(min(xin, xin + sx * rail_w), max(xin, xin + sx * rail_w), mast_y - pcb_t / 2 - 2.6, mast_y + pcb_t / 2 + 2.6, ztop - 1, ztop + 30);
                    box(min(xin - sx * 0.01, mast_x + sx * (board_w / 2 + groove_clear)), max(xin - sx * 0.01, mast_x + sx * (board_w / 2 + groove_clear)),
                        mast_y - pcb_t / 2 - 0.15, mast_y + pcb_t / 2 + 0.15, ztop, ztop + 31);
                }
            }
        }
        for (x = [mast_x - 15, mast_x + 15]) cylz(1.7, z0 - 1, z0 + 3.4, x, mast_y + 9);
        box(mast_x - 9, mast_x + 9, mast_y - 4, mast_y + 4, z0 - 1, z0 + 3);
    }
}

module brick_assembly() {
    color("#3a3f45") brick_tray();
    color("#e8661f") brick_lid();
    color("#e8661f") brick_mast();
    color("#e9e9e6") box(bank_x0, bank_x0 + bank_l, bank_y0, bank_y0 + bank_w, b_floor, b_floor + bank_h);
    color("#127a83") box(ux0, ux1, uy0, uy1, b_floor + unoq_standoff, b_floor + unoq_standoff + unoq_t);
}

// ---------------- dispatch (print orientations are in README.md; these are assembled positions)
if (part == "assembly") puck_assembly();
else if (part == "shell_bottom") shell_bottom();
else if (part == "shell_top") translate([0, 0, -z_split]) shell_top();
else if (part == "boot") translate([0, 0, -z_boot_lo]) boot();
else if (part == "sled") sled();
else if (part == "plug") plug();
else if (part == "brick") brick_assembly();
else if (part == "brick_tray") brick_tray();
else if (part == "brick_lid") brick_lid();
else if (part == "brick_mast") brick_mast();
