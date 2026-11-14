// 2-D plan of the twin's floor with Beacon's layer on top: pucks, cold device counts, and each opt-in phone's
// geometry EXACTLY as published (a ring stays a ring; x, y are drawn only for geometry "fix").
// The floor drawing (rooms, walls, doors, labels, side letters, north arrow) follows Sandtable's plan2d.js
// (sandtable-live-fireground-twin/web/plan2d.js), copied and adapted. Credit: the Sandtable lead and builders.
import { isNum, num, pct, statusOf } from "./common.js";

const NS = "http://www.w3.org/2000/svg";
const el = (name, attrs = {}, text) => {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
};
const D2R = Math.PI / 180;
const ease = (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);
const lerp = (a, b, k) => a + (b - a) * k;
const clone = (o) => JSON.parse(JSON.stringify(o));
const centroid = (poly) => [poly.reduce((s, p) => s + p[0], 0) / poly.length, poly.reduce((s, p) => s + p[1], 0) / poly.length];
const bboxOf = (pts) => [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))];
const reduceMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// arcs as published: [[a0, a1], ...] in degrees CCW from +x; normalise so a1 >= a0
function normArcs(arcs) {
  const out = [];
  for (const a of arcs || []) {
    if (!Array.isArray(a) || a.length < 2) continue;
    let a0 = Number(a[0]), a1 = Number(a[1]);
    while (a1 < a0) a1 += 360;
    out.push([a0, a1]);
  }
  return out.length ? out : [[0, 360]];
}
const arcSpan = (arcs) => arcs.reduce((s, a) => s + Math.min(360, a[1] - a[0]), 0);
// put angle th (deg) into the frame of arc [a0, a1] (th + 360k closest to the arc)
function unwrapInto(th, a) {
  let best = th, bd = Infinity;
  for (let k = -2; k <= 2; k++) {
    const t = th + 360 * k, d = t < a[0] ? a[0] - t : t > a[1] ? t - a[1] : 0;
    if (d < bd) { bd = d; best = t; }
  }
  return best;
}

export class Plan {
  constructor(svg, bundle, opts = {}) {
    this.svg = svg; this.opts = opts;
    this.twin = bundle.twin; this.site = bundle.site || { tenants: [] };
    this.u = 0.05; this.box = null; this.focusMode = "room"; this.focusRoom = null; this.data = null;
    this.phones = new Map(); this.pucks = new Map(); this.selected = null;
    this.svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    this.setFloor(this.twin.exercise_floor);
    new ResizeObserver(() => { this._focusKey = null; this._applyFocus(); this.kick(); }).observe(svg);
    svg.addEventListener("pointermove", (ev) => this._dragMove(ev));
    svg.addEventListener("pointerup", (ev) => this._dragEnd(ev));
    svg.addEventListener("pointercancel", () => { this.drag = null; });
    svg.addEventListener("click", (ev) => {
      if (!this.placing) return;
      const p = this.toBldg(ev);
      this.opts.onPlace?.(p[0], p[1]);
    });
  }

  // ------------------------------------------------------------------ static floor
  setFloor(floorId) {
    const t = this.twin;
    const floor = t.floors.find((f) => f.id === floorId) || t.floors.find((f) => f.id === t.exercise_floor) || t.floors[0];
    if (this.floor && this.floor.id === floor.id) return;
    this.floor = floor;
    const xs = floor.outline.map((p) => p[0]), ys = floor.outline.map((p) => p[1]);
    this.fx0 = Math.min(...xs); this.fy0 = Math.min(...ys); this.fx = Math.max(...xs); this.fy = Math.max(...ys);
    this.svg.replaceChildren();
    this.g = {};
    for (const k of ["defs", "rooms", "zones", "layout", "walls", "labels", "focus", "orient", "presence", "next", "phones", "pucks"]) { this.g[k] = el("g", { class: `g-${k}` }); this.svg.appendChild(this.g[k]); }
    const pat = el("pattern", { id: "hatch", patternUnits: "userSpaceOnUse", width: 0.5, height: 0.5, patternTransform: "rotate(45)" });
    pat.appendChild(el("line", { x1: 0, y1: 0, x2: 0, y2: 0.5, stroke: "#ffc247", "stroke-width": 0.06, "stroke-opacity": 0.35 }));
    const defs = el("defs"); defs.appendChild(pat); this.g.defs.appendChild(defs);
    this.rooms = Object.fromEntries(t.rooms.filter((r) => r.floor === floor.id).map((r) => [r.id, r]));
    this.roomEls = {}; this.roomLabelEls = {};
    const floorNo = Number(floor.label);
    const tenants = (this.site.tenants || []).filter((q) => q.floor === floorNo);
    for (const r of Object.values(this.rooms)) {
      const p = el("polygon", { points: this.pts(r.polygon), class: `r ${r.kind}`, "data-room": r.id });
      p.appendChild(el("title", {}, r.label));
      this.g.rooms.appendChild(p); this.roomEls[r.id] = p;
      const c = centroid(r.polygon), labs = [];
      if (r.kind === "suite") {
        labs.push(el("text", { x: c[0], y: this.fy - c[1] - 0.5, class: "rl" }, r.suite));
        const ten = tenants.filter((q) => q.suite === r.suite);
        const line = ten.length ? ten[0].industry.replace(/^Offices of /, "").replace(/\s*\(.*?\)/g, "").slice(0, 24) : (r.registered === false ? "no registration" : "");
        if (line) labs.push(el("text", { x: c[0], y: this.fy - c[1] + 0.9, class: "rl small" }, line));
        if (ten.some((q) => q.life_hazard)) labs.push(el("text", { x: c[0], y: this.fy - c[1] + 1.9, class: "rl hz" }, "LIFE HAZARD"));
      } else if (r.kind !== "corridor") {
        labs.push(el("text", { x: c[0], y: this.fy - c[1], class: "rl small" }, r.label));
      }
      for (const l of labs) this.g.labels.appendChild(l);
      this.roomLabelEls[r.id] = labs;
    }
    for (const w of t.walls.filter((q) => q.floor === floor.id)) {
      const a = this.P(w.a), b = this.P(w.b);
      this.g.walls.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: "wall" }));
    }
    for (const d of t.doors.filter((q) => q.floor === floor.id)) {
      const dir = this._wallDir(d.at), w = (d.width_m || 0.9) / 2, p = this.P(d.at);
      this.g.walls.appendChild(el("line", { x1: p[0] - dir[0] * w, y1: p[1] + dir[1] * w, x2: p[0] + dir[0] * w, y2: p[1] - dir[1] * w, class: "door" }));
    }
    this._orientation();
    this.phones.clear(); this.pucks.clear();
    this.focusRoom = null; this._focusKey = null;
    this.box = this._floorBox();
    this.svg.setAttribute("viewBox", this.box.join(" "));
    this.kick();
  }
  P(p) { return [p[0], this.fy - p[1]]; }
  pts(poly) { return poly.map((q) => this.P(q).join(",")).join(" "); }
  toBldg(ev) {
    const pt = this.svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const q = pt.matrixTransform(this.svg.getScreenCTM().inverse());
    return [q.x, this.fy - q.y];
  }
  _wallDir(at) {
    let best = null;
    for (const w of this.twin.walls) {
      if (w.floor && w.floor !== this.floor.id) continue;
      const dx = w.b[0] - w.a[0], dy = w.b[1] - w.a[1], len = Math.hypot(dx, dy);
      if (len < 0.01) continue;
      const tt = Math.max(0, Math.min(1, ((at[0] - w.a[0]) * dx + (at[1] - w.a[1]) * dy) / (len * len)));
      const d = Math.hypot(at[0] - (w.a[0] + tt * dx), at[1] - (w.a[1] + tt * dy));
      if (!best || d < best[0]) best = [d, [dx / len, dy / len]];
    }
    return best ? best[1] : [1, 0];
  }
  _orientation() {
    const geo = this.twin.georef || {};
    if (!isNum(geo.bearing_deg)) return;
    const b = geo.bearing_deg * D2R;
    const dirOf = (brg) => { const r = brg * D2R, e = Math.sin(r), n = Math.cos(r); return [e * Math.sin(b) + n * Math.cos(b), -e * Math.cos(b) + n * Math.sin(b)]; };
    const cx = (this.fx0 + this.fx) / 2, cy = (this.fy0 + this.fy) / 2, hx = (this.fx - this.fx0) / 2, hy = (this.fy - this.fy0) / 2;
    for (const [k, s] of Object.entries(this.site.sides || {})) {
      const d = dirOf(s.bearing_deg);
      const tt = Math.min(Math.abs(d[0]) > 1e-3 ? hx / Math.abs(d[0]) : 1e9, Math.abs(d[1]) > 1e-3 ? hy / Math.abs(d[1]) : 1e9) + 1.35;
      const p = this.P([cx + d[0] * tt, cy + d[1] * tt]);
      const g = el("g", { class: "side" });
      g.append(el("circle", { cx: p[0], cy: p[1], r: 0.95 }), el("text", { x: p[0], y: p[1] }, k), el("title", {}, `Side ${k}${s.street ? ": " + s.street : ""}`));
      this.g.orient.appendChild(g);
    }
    const n = dirOf(0), a = this.P([this.fx0 - 1.3, this.fy - 1.4]), ang = -Math.atan2(n[1], n[0]) * 180 / Math.PI + 90;
    const g = el("g", { class: "north", transform: `translate(${a[0]} ${a[1]}) rotate(${ang})` });
    g.append(el("path", { d: "M0 -1.3 L0.65 0.8 L0 0.4 L-0.65 0.8 Z" }), el("text", { x: 0, y: -1.9 }, "N"));
    this.g.orient.appendChild(g);
  }

  // ------------------------------------------------------------------ focus
  // svg y = fy - building y, so the floor spans svg y 0 .. fy - fy0
  _floorBox() { const pad = 2.6; return [this.fx0 - pad, -pad, this.fx - this.fx0 + 2 * pad, this.fy - this.fy0 + 2 * pad]; }
  setFocusMode(mode) { this.focusMode = mode; this._focusKey = null; this._applyFocus(); }
  // building-frame bbox [x0, y0, x1, y1] of what must be visible around the focus room
  _focusBBox() {
    const r = this.focusRoom && this.rooms[this.focusRoom];
    if (!r) return null;
    const pts = [...r.polygon];
    const d = this.data || {};
    for (const p of d.pucks || []) if (isNum(p.x) && (!p.floor || p.floor === this.floor.id)) pts.push([p.x, p.y]);
    for (const ph of d.phones || []) {
      if (ph.floor && ph.floor !== this.floor.id) continue;
      if (ph.stale || ph.geometry === "none" || (ph.report && ph.report.safe_outside)) continue;   // a stale phone must not zoom the plan out
      if (ph.geometry === "fix" && isNum(ph.x)) pts.push([ph.x - (ph.sigma_m || 0), ph.y - (ph.sigma_m || 0)], [ph.x + (ph.sigma_m || 0), ph.y + (ph.sigma_m || 0)]);
      for (const c of (ph.geometry === "candidates" ? ph.candidates : []) || []) pts.push([c.x, c.y]);
      if (ph.geometry === "ring" && ph.ring) {
        const g = ph.ring, rr = g.r_m + (g.kind === "ble" ? Math.min(g.sigma_m || 0, 2) : 0);
        for (const a of normArcs(g.arcs)) for (let t = a[0]; t <= a[1]; t += 10) pts.push([g.cx + rr * Math.cos(t * D2R), g.cy + rr * Math.sin(t * D2R)]);
      }
      if (ph.geometry === "zone" && ph.zone) pts.push(...(ph.zone.polygon || this.rooms[ph.zone.room]?.polygon || []));
    }
    const b = bboxOf(pts);
    // never wider than the floor itself
    return [Math.max(b[0], this.fx0 - 1), Math.max(b[1], this.fy0 - 1), Math.min(b[2], this.fx + 1), Math.min(b[3], this.fy + 1)];
  }
  _applyFocus() {
    const bb = this.focusMode === "room" ? this._focusBBox() : null;
    const pad = 1.4;
    let to = this._floorBox();
    const fit = bb || [this.fx0 - 1.2, this.fy0 - 1.2, this.fx + 1.2, this.fy + 1.2];
    if (fit) {
      const bb = fit;
      // fit the box below a band kept free for the caption (opts.topReserve, in screen px)
      const rect = this.svg.getBoundingClientRect(), W = Math.max(rect.width, 50), H = Math.max(rect.height, 50);
      const res = Math.min(H * 0.45, (this.opts.topReserve ? this.opts.topReserve() : 0) + 30);
      const bw = bb[2] - bb[0] + 2 * pad, bh = bb[3] - bb[1] + 2 * pad;
      const u = Math.max(bw / W, bh / (H - res));
      const vw = W * u, vh = H * u, cx = (bb[0] + bb[2]) / 2;
      const free = (H - res) * u - bh;           // spare height below the caption band, split evenly
      to = [cx - vw / 2, this.fy - bb[3] - pad - res * u - free / 2, vw, vh];
    }
    const k = to.map((v) => (Math.round(v * 2) / 2).toFixed(1)).join(" ");
    if (k === this._focusKey) return;
    this._focusKey = k;
    this.boxFrom = [...this.box]; this.boxTo = to; this.boxT0 = performance.now();
    // dim everything but the focus room (and the rooms where something was heard)
    const lit = new Set();
    const d = this.data || {};
    for (const p of d.presence || []) if (p.room) lit.add(p.room);
    for (const ph of d.phones || []) { if (ph.zone?.room) lit.add(ph.zone.room); for (const c of ph.candidates || []) if (c.room) lit.add(c.room); if (ph.geometry === "fix" && ph.room) lit.add(ph.room); }
    for (const [id, e] of Object.entries(this.roomEls)) {
      const isF = id === this.focusRoom, dim = !!this.focusRoom && !isF;
      e.classList.toggle("focus", isF);
      e.classList.toggle("dim", dim && !lit.has(id));
      e.classList.toggle("near", dim && lit.has(id));
      for (const l of this.roomLabelEls[id] || []) { l.classList.toggle("dim", dim); l.style.display = isF ? "none" : ""; }
    }
    this.g.orient.style.display = bb ? "none" : "";
    this.g.focus.replaceChildren();
    const fr = this.focusRoom && this.rooms[this.focusRoom];
    if (fr) {
      this.g.focus.appendChild(el("polygon", { points: this.pts(fr.polygon), class: "focus-outline" }));
      const b = bboxOf(fr.polygon);
      this._focusTag = el("text", { class: "rl", "text-anchor": "start", style: "fill: var(--e1); text-anchor: start" }, fr.label);
      this._focusTag.dataset.x = b[0]; this._focusTag.dataset.y = b[3];
      this.g.focus.appendChild(this._focusTag);
    } else this._focusTag = null;
    this.kick();
  }

  // ------------------------------------------------------------------ data
  update(beacons) {
    this.data = beacons;
    if (beacons.floor && (!this.floor || beacons.floor !== this.floor.id)) this.setFloor(beacons.floor);
    const room = beacons.room && this.rooms[beacons.room] ? beacons.room : null;
    if (room !== this.focusRoom) { this.focusRoom = room; this._focusKey = null; }
    this._applyFocus();
    this._updatePucks(beacons.pucks || []);
    this._updatePresence(beacons.presence || []);
    this._updateZones(beacons.phones || []);
    this._updatePhones(beacons.phones || []);
    this.kick();
  }
  select(id) { this.selected = id; for (const [k, ph] of this.phones) ph.g.classList.toggle("sel", k === id); }
  assumptionTag(a) {
    const where = (a.room && this.rooms[a.room] && this.rooms[a.room].label) || a.room || "the stated room";
    if (a.kind === "occupant-statement") return `mirror spot dismissed: occupant says ${where}`;
    if (a.kind === "search-room") return `mirror spot dismissed: command is searching ${where}`;
    return a.text ? a.text.slice(0, 60) : "assumption";
  }
  // recommended puck layout (GET /api/layout): ghost pucks, the line through them, the side the mirror image falls on
  setLayout(lay) {
    const g = this.g.layout;
    g.replaceChildren(); this.layEls = null;
    if (!lay || !(lay.pucks || []).length) { this.kick(); return; }
    const pts = lay.pucks.filter((q) => isNum(q.x) && isNum(q.y));
    const room = lay.room && this.rooms[lay.room];
    if (pts.length >= 2 && room) {
      const A = [pts[0].x, pts[0].y], B = [pts[1].x, pts[1].y], dx = B[0] - A[0], dy = B[1] - A[1], L = Math.hypot(dx, dy) || 1;
      const n = [-dy / L, dx / L], c = centroid(room.polygon), side = Math.sign(n[0] * (c[0] - A[0]) + n[1] * (c[1] - A[1])) || 1;
      const pad = 4, box = [[this.fx0 - pad, this.fy0 - pad], [this.fx + pad, this.fy0 - pad], [this.fx + pad, this.fy + pad], [this.fx0 - pad, this.fy + pad]];
      // keep the half-plane on the far side of the line from the room (Sutherland-Hodgman against one edge)
      const keep = (q) => -side * (n[0] * (q[0] - A[0]) + n[1] * (q[1] - A[1])) >= 0, out = [];
      for (let i = 0; i < box.length; i++) {
        const P1 = box[i], P2 = box[(i + 1) % box.length], k1 = keep(P1), k2 = keep(P2);
        if (k1) out.push(P1);
        if (k1 !== k2) {
          const f1 = n[0] * (P1[0] - A[0]) + n[1] * (P1[1] - A[1]), f2 = n[0] * (P2[0] - A[0]) + n[1] * (P2[1] - A[1]), t = f1 / (f1 - f2);
          out.push([P1[0] + t * (P2[0] - P1[0]), P1[1] + t * (P2[1] - P1[1])]);
        }
      }
      if (out.length >= 3) g.appendChild(el("polygon", { points: this.pts(out), class: "lay-mirror" }));
      const ext = 60, a = this.P([A[0] - dx / L * ext, A[1] - dy / L * ext]), b = this.P([A[0] + dx / L * ext, A[1] + dy / L * ext]);
      g.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: "lay-line" }));
      // the note runs along the line, on the mirror side, so it never sits across the room
      const mid = [(A[0] + B[0]) / 2 - side * n[0] * 0.9, (A[1] + B[1]) / 2 - side * n[1] * 0.9], mp = this.P(mid);
      let ang = Math.atan2(-dy, dx) * 180 / Math.PI;
      if (ang > 90) ang -= 180; if (ang < -90) ang += 180;
      g.appendChild(el("text", { x: mp[0], y: mp[1], class: "lay-note", transform: `rotate(${ang.toFixed(1)} ${mp[0]} ${mp[1]})` }, "mirror images fall on this side"));
    }
    this.layEls = [];
    for (const q of pts) {
      const P = this.P([q.x, q.y]), pg = el("g", { class: "lay-puck" }), c = el("circle", { cx: P[0], cy: P[1] }), t = el("text", { x: P[0] }, `${q.id} here`);
      pg.append(c, t, el("title", {}, `Recommended spot for ${q.id}${lay.wall ? ` (${lay.wall})` : ""}`));
      g.appendChild(pg); this.layEls.push({ c, t, P });
    }
    this.kick();
  }
  highlightRoom(id, on) { this.roomEls[id]?.classList.toggle("hot", on); }
  // where the next puck should go (accountability.next_puck.door = [x, y]); null clears it
  setNextPuck(np) {
    const key = np && Array.isArray(np.door) ? JSON.stringify([np.door, np.suite]) : null;
    if (key === this._nextKey) return;
    this._nextKey = key;
    this.g.next.replaceChildren(); this.nextEl = null;
    if (!key) return;
    const g = el("g", { class: "nextpuck" }), ring = el("path"), dot = el("circle"), t = el("text", {}, `NEXT PUCK${np.suite ? ` · ${np.suite}` : ""}`);
    g.append(ring, dot, t, el("title", {}, np.text || `Next puck: ${np.suite ? "Suite " + np.suite : np.room || ""}${np.why ? ". " + np.why : ""}`));
    this.g.next.appendChild(g);
    this.nextEl = { ring, dot, t, x: np.door[0], y: np.door[1] };
    this.kick();
  }

  _updatePucks(list) {
    const seen = new Set();
    for (const p of list) {
      if (!isNum(p.x) || (p.floor && p.floor !== this.floor.id)) continue;
      seen.add(p.id);
      let e = this.pucks.get(p.id);
      if (!e) {
        const g = el("g", { class: "puck" });
        e = { g, halo: el("circle", { class: "halo" }), drop: el("circle", { class: "halo" }), body: el("circle", { class: "body" }), lab: el("text", {}, p.id), title: el("title"), t0: performance.now() };
        g.append(e.drop, e.halo, e.body, e.lab, e.title);
        g.addEventListener("pointerdown", (ev) => {
          if (!this.opts.canDragPuck?.()) return;
          ev.preventDefault(); ev.stopPropagation();
          this.drag = { id: p.id, e }; g.classList.add("drag");
          try { this.svg.setPointerCapture(ev.pointerId); } catch { /* ignore */ }
        });
        this.g.pucks.appendChild(g); this.pucks.set(p.id, e);
      }
      e.data = p;
      if (!(this.drag && this.drag.id === p.id)) { e.x = p.x; e.y = p.y; }
      e.title.textContent = `Puck ${p.id}${p.accessory ? ` · ${p.accessory}` : ""}${p.host ? ` · host: ${p.host}` : ""}${p.room ? ` · in ${this.rooms[p.room]?.label || p.room}` : ""}` +
        `${isNum(p.sigma_m) ? ` · placed to ±${num(p.sigma_m, 2)} m` : ""}${(p.radios || []).length ? ` · radios: ${p.radios.join(", ")}` : ""}${this.opts.canDragPuck?.() ? " · drag to correct" : ""}`;
    }
    for (const [id, e] of this.pucks) if (!seen.has(id)) { e.g.remove(); this.pucks.delete(id); }
  }
  _dragMove(ev) {
    if (!this.drag) return;
    const p = this.toBldg(ev);
    this.drag.e.x = Math.max(this.fx0, Math.min(this.fx, p[0])); this.drag.e.y = Math.max(this.fy0, Math.min(this.fy, p[1]));
    this.drag.moved = true;
    this.kick();
  }
  _dragEnd() {
    if (!this.drag) return;
    const { id, e, moved } = this.drag;
    e.g.classList.remove("drag");
    this.drag = null;
    if (moved) this.opts.onPuckMove?.(id, e.x, e.y, e.data);
  }
  setPlacing(on) { this.placing = !!on; this.svg.classList.toggle("placing", this.placing); }

  // rooms where an opt-in phone is heard now: the presence entry says so (opt_in_phones), or a live phone is ranged
  // by a puck in that room, or its published geometry sits in that room
  _optInRooms() {
    const d = this.data || {}, out = new Set();
    for (const q of d.presence || []) if (q.room && (q.opt_in_phones || 0) > 0) out.add(q.room);
    const puckRoom = Object.fromEntries((d.pucks || []).map((p) => [p.id, p.room]));
    for (const ph of d.phones || []) {
      if (ph.stale || (ph.report && ph.report.safe_outside)) continue;
      for (const r of ph.ranges || []) if (puckRoom[r.puck]) out.add(puckRoom[r.puck]);
      if (ph.ring && puckRoom[ph.ring.puck]) out.add(puckRoom[ph.ring.puck]);
      for (const r of [ph.room, ph.zone && ph.zone.room, ...(ph.candidates || []).map((c) => c.room)]) if (r) out.add(r);
    }
    return out;
  }
  _updatePresence(list) {
    // one badge per room: several pucks may hear the same devices, so counts are not summed; show the largest band
    const optIn = this._optInRooms();
    const byRoom = new Map();
    for (const p of list) {
      if (!p.room || !this.rooms[p.room] || (p.floor && p.floor !== this.floor.id)) continue;
      const cur = byRoom.get(p.room);
      if (!cur || (p.count ?? 0) > (cur.best.count ?? 0)) byRoom.set(p.room, { best: p, all: [...(cur?.all || []), p] });
      else cur.all.push(p);
    }
    const key = JSON.stringify([...byRoom].map(([r, v]) => [r, v.best.count, v.best.count_range, v.all.map((q) => q.puck), optIn.has(r)]));
    if (key === this._presKey) return;
    this._presKey = key;
    this.g.presence.replaceChildren();
    this.presEls = [];
    for (const [roomId, v] of byRoom) {
      const p = v.best, n = p.count ?? 0, rg = p.count_range || [n, n], phoneHere = optIn.has(roomId);
      const g = el("g", { class: "pres" });
      // "no devices heard" would be false where an opt-in phone is being ranged: say "no other devices" there
      const long = n === 0 ? (phoneHere ? "no other devices heard" : "no devices heard") : `about ${n} device${n === 1 ? "" : "s"}`;
      const short = n === 0 ? (phoneHere ? "no other devices" : "0 devices") : `~${n} device${n === 1 ? "" : "s"}`;
      const rect = el("rect", { rx: 0 }), t1 = el("text", { class: "n" }, long);
      const t2 = el("text", { class: "s" }, n === 0 ? (phoneHere ? "besides the opt-in phone · devices, not people" : "weak evidence of empty, never proof") : `${rg[0]} to ${rg[1]} · devices, not people`);
      g.append(rect, t1, t2, el("title", {}, v.all.map((q) => q.text || "").filter(Boolean).join("\n") || "Cold Bluetooth count: devices, not people; no identities kept"));
      this.g.presence.appendChild(g);
      this.presEls.push({ g, rect, t1, t2, long, short, room: this.rooms[roomId] });
    }
  }
  _updateZones(phones) {
    const zs = phones.filter((p) => p.geometry === "zone" && p.zone && !p.stale && !(p.report && p.report.safe_outside));
    const key = JSON.stringify(zs.map((p) => [p.id, p.zone.room, p.zone.band, p.zone.prob]));
    if (key === this._zoneKey) return;
    this._zoneKey = key;
    this.g.zones.replaceChildren();
    this.zoneEls = [];
    const stack = {};
    for (const p of zs) {
      const z = p.zone, poly = z.polygon && z.polygon.length >= 3 ? z.polygon : this.rooms[z.room]?.polygon;
      if (!poly) continue;
      const g = el("g", { class: "zone-g" });
      g.appendChild(el("polygon", { points: this.pts(poly), class: "zone" }));
      g.appendChild(el("title", {}, `${p.id}: ${p.text || ""}`));
      g.addEventListener("click", () => this.opts.onPick?.(p.id));
      this.g.zones.appendChild(g);
      const zb = bboxOf(poly), c = [(zb[0] + zb[2]) / 2, zb[1] + Math.min(1.2, (zb[3] - zb[1]) * 0.15)], i = (stack[z.room] = (stack[z.room] || 0) + 1) - 1;
      this.zoneEls.push({ c, i, id: p.id, words: `${z.band} band · ${pct(z.prob)}`, people: p.report?.people });
    }
  }

  // ------------------------------------------------------------------ phones: target states and transitions
  _target(p) {
    const S = {};
    const g = p.geometry;
    if (g === "ring" && p.ring && isNum(p.ring.r_m)) {
      const r = p.ring, arcs = normArcs(r.arcs);
      S.ring = { cx: r.cx, cy: r.cy, r: r.r_m, sg: Math.max(0.02, r.sigma_m || 0), arcs, op: 1, ghost: arcSpan(arcs) < 359 ? 1 : 0, kind: r.kind === "ble" ? "ble" : "uwb" };
    } else if (g === "candidates") {
      (p.candidates || []).forEach((c, i) => { if (isNum(c.x)) S[`c${i}`] = { x: c.x, y: c.y, s: c.sigma_m || 0.3, w: isNum(c.weight) ? c.weight : 0.5, op: 1 }; });
    } else if (g === "fix" && isNum(p.x) && isNum(p.y)) {
      let ea = p.sigma_m || 0.3, eb = ea, ang = 0;
      if (Array.isArray(p.cov) && p.cov.length === 3) {   // same maths as Sandtable's sigmaEllipse
        const [a, b, c] = p.cov, m = (a + c) / 2, d = Math.sqrt(((a - c) / 2) ** 2 + b * b);
        ea = Math.sqrt(Math.max(m + d, 1e-4)); eb = Math.sqrt(Math.max(m - d, 1e-4)); ang = 0.5 * Math.atan2(2 * b, a - c);
      }
      S.fix = { x: p.x, y: p.y, ea, eb, ang, op: 1 };
    }
    return S;
  }
  _updatePhones(list) {
    const seen = new Set();
    for (const p of list) {
      if (p.floor && p.floor !== this.floor.id) continue;
      if (p.report && p.report.safe_outside) continue;
      seen.add(p.id);
      let ph = this.phones.get(p.id);
      if (!ph) { ph = this._newPhone(p.id); this.phones.set(p.id, ph); }
      if (ph.leaving) { ph.leaving = false; ph.key = null; }
      ph.data = p;
      if (p.ring) ph.lastRing = p.ring;
      ph.g.classList.toggle("stale", !!p.stale);
      const tgt = p.stale ? {} : this._target(p);     // stale: the card says where it was last; the plan draws nothing
      const key = JSON.stringify(tgt, (k, v) => (typeof v === "number" ? Math.round(v * 20) / 20 : v));
      // structure = what kind of shape it is; a change of structure plays the collapse, a change of numbers only glides
      const sig = `${p.geometry}|${p.ring ? p.ring.puck + p.ring.kind : ""}|${(p.candidates || []).length}`;
      if (key !== ph.key) { ph.key = key; this._transition(ph, tgt, sig === ph.sig); ph.sig = sig; }
      this._drawPruned(ph, p.stale ? { ...p, pruned: [] } : p);
    }
    for (const [id, ph] of this.phones) if (!seen.has(id) && !ph.leaving) {
      ph.leaving = true; ph.key = null;
      this._transition(ph, {}, true);
    }
  }
  _newPhone(id) {
    const g = el("g", { class: "phone-g", "data-id": id });
    const ph = {
      id, g, cur: {}, from: {}, to: {}, t0: 0, dur: 1,
      pruned: el("g"), ghost: el("circle", { class: "ph-ghostring" }), band: el("path", { class: "ph-band" }), bandLine: el("path", { class: "ph-bandline" }),
      arc: el("path", { class: "ph-arc" }), links: el("g"), cands: el("g"), candEls: {},
      fixG: el("g", { class: "ph-fix" }), s1: el("ellipse", { class: "s1" }), s2: el("ellipse", { class: "s2" }), core: el("circle", { class: "core" }), pulse: el("circle", { class: "pulse" }),
      lab: el("text", { class: "ph-lab" }), sub: el("text", { class: "ph-sub" }), assume: el("text", { class: "ph-assume" }), title: el("title"), leader: el("line", { class: "ph-leader" }),
    };
    ph.fixG.append(ph.s2, ph.s1, ph.pulse, ph.core);
    g.append(ph.title, ph.pruned, ph.ghost, ph.band, ph.bandLine, ph.arc, ph.links, ph.cands, ph.fixG, ph.leader, ph.lab, ph.sub, ph.assume);
    g.addEventListener("click", () => this.opts.onPick?.(id));
    g.style.cursor = "pointer";
    this.g.phones.appendChild(g);
    return ph;
  }
  _snapshot(ph) { return clone(ph.cur || {}); }
  _transition(ph, tgt, smooth = false) {
    // a new measurement during a collapse: retarget the running animation instead of restarting it
    if (smooth && ph.structural && performance.now() < ph.t0 + ph.dur) {
      if (tgt.fix && ph.to.fix) ph.to.fix = { ...tgt.fix };
      if (tgt.ring && ph.to.ring && ph.to.ring.op > 0) ph.to.ring = { ...tgt.ring, arcs: ph.to.ring.arcs.length === tgt.ring.arcs.length ? tgt.ring.arcs : ph.to.ring.arcs };
      const live = Object.keys(ph.to).filter((k) => k[0] === "c" && ph.to[k].op > 0);
      for (const c of Object.keys(tgt).filter((k) => k[0] === "c").map((k) => tgt[k])) {
        let best = null;
        for (const k of live) { const d = Math.hypot(ph.to[k].x - c.x, ph.to[k].y - c.y); if (!best || d < best[0]) best = [d, k]; }
        if (best) ph.to[best[1]] = { ...c };
      }
      return;
    }
    const cur = this._snapshot(ph);
    // drop parts that have already faded out
    for (const k of Object.keys(cur)) if (!(cur[k].op > 0.02)) delete cur[k];
    const from = clone(cur), to = {};
    // match new candidates to the ones on screen (nearest first) so markers glide instead of swapping
    const curC = Object.keys(cur).filter((k) => k[0] === "c");
    const newC = Object.keys(tgt).filter((k) => k[0] === "c").map((k) => tgt[k]);
    const used = new Set();
    let next = 0;
    const freshKey = () => { while (cur[`c${next}`] || to[`c${next}`]) next++; return `c${next}`; };
    for (const c of newC) {
      let best = null;
      for (const k of curC) if (!used.has(k)) { const d = Math.hypot(cur[k].x - c.x, cur[k].y - c.y); if (!best || d < best[0]) best = [d, k]; }
      const k = best && best[0] < 4 ? best[1] : freshKey();
      used.add(k); to[k] = c;
    }
    if (tgt.ring) to.ring = tgt.ring;
    if (tgt.fix) to.fix = tgt.fix;
    let dur = 650, pulse = false;
    const ring = cur.ring;
    const toPts = Object.keys(to).filter((k) => k[0] === "c").map((k) => [k, to[k]]);
    if (to.fix) toPts.push(["fix", to.fix]);
    if (smooth) {
      dur = 450;
    } else if (ring && !to.ring && toPts.length) {
      // the ah-ha: the ring collapses onto the new point(s)
      const fromArcs = [], toArcs = [];
      let rsum = 0;
      for (const [k, q] of toPts) {
        const th = Math.atan2(q.y - ring.cy, q.x - ring.cx) / D2R;
        let src = ring.arcs[0], bd = Infinity;
        for (const a of ring.arcs) { const t = unwrapInto(th, a), d = t < a[0] ? a[0] - t : t > a[1] ? t - a[1] : 0; if (d < bd) { bd = d; src = a; } }
        const tu = unwrapInto(th, src);
        fromArcs.push([...src]); toArcs.push([tu - 3, tu + 3]);
        const rr = Math.hypot(q.x - ring.cx, q.y - ring.cy); rsum += rr;
        const onRing = [ring.cx + ring.r * Math.cos(th * D2R), ring.cy + ring.r * Math.sin(th * D2R)];
        if (!cur[k] || k === "fix") {
          from[k] = k === "fix" ? { ...q, x: onRing[0], y: onRing[1], ea: Math.max(q.ea, ring.sg * 3, 0.6), eb: Math.max(q.eb, ring.sg * 3, 0.6), op: 0 } : { ...q, x: onRing[0], y: onRing[1], s: 0, op: 0 };
        }
      }
      from.ring = { ...ring, arcs: fromArcs };
      to.ring = { ...ring, r: rsum / toPts.length, arcs: toArcs, op: 0, ghost: 0 };
      dur = to.fix ? 1500 : 1300; pulse = !!to.fix;
    } else if (curC.length && to.fix && !newC.length) {
      // two candidates become one fix: the nearer glides into the fix, the other fades out
      let best = null;
      for (const k of curC) { const d = Math.hypot(cur[k].x - to.fix.x, cur[k].y - to.fix.y); if (!best || d < best[0]) best = [d, k]; }
      for (const k of curC) to[k] = k === best[1] ? { ...cur[k], x: to.fix.x, y: to.fix.y, op: 0 } : { ...cur[k], s: 0, op: 0 };
      const c = cur[best[1]];
      from.fix = { ...to.fix, x: c.x, y: c.y, ea: Math.max(to.fix.ea, c.s), eb: Math.max(to.fix.eb, c.s), op: 0 };
      dur = 1200; pulse = true;
    } else if (ring && to.ring && ring.arcs.length !== to.ring.arcs.length) {
      from.ring = { ...ring, arcs: to.ring.arcs.map((a) => [...a]) };
    }
    for (const k of new Set([...Object.keys(from), ...Object.keys(to)])) {
      if (!(k in from)) from[k] = k === "ring" ? { ...to[k], op: 0, ghost: 0 } : k === "fix" ? { ...to[k], op: 0 } : { ...to[k], s: 0, op: 0 };
      if (!(k in to)) to[k] = { ...from[k], op: 0, ...(k === "ring" ? { ghost: 0 } : {}) };
    }
    ph.from = from; ph.to = to; ph.t0 = performance.now(); ph.dur = reduceMotion() ? 1 : dur; ph.structural = !smooth;
    if (pulse) ph.pulseT0 = ph.t0 + ph.dur * 0.75;
    this.kick();
  }
  _interp(ph, now) {
    const k = ease(Math.max(0, Math.min(1, (now - ph.t0) / ph.dur)));
    const out = {};
    for (const key of Object.keys(ph.to)) {
      const a = ph.from[key], b = ph.to[key], o = {};
      for (const f of Object.keys(b)) {
        if (f === "arcs") o.arcs = a.arcs && a.arcs.length === b.arcs.length ? b.arcs.map((q, i) => [lerp(a.arcs[i][0], q[0], k), lerp(a.arcs[i][1], q[1], k)]) : b.arcs;
        else if (typeof b[f] === "number" && typeof a[f] === "number") o[f] = lerp(a[f], b[f], k);
        else o[f] = k < 0.5 && a[f] !== undefined ? a[f] : b[f];
      }
      out[key] = o;
    }
    ph.cur = out;
    return k < 1;
  }
  _drawPruned(ph, p) {
    const list = (p.pruned || []).filter((q) => isNum(q.x) && isNum(q.y));
    const key = JSON.stringify(list.map((q) => [Math.round(q.x * 2) / 2, Math.round(q.y * 2) / 2, q.by || ""]));
    if (key === ph.prunedKey) {
      // same ghosts: only refresh their hover text
      (ph.prunedEls || []).forEach((e, i) => { if (list[i] && e.title) e.title.textContent = `${p.id}: ${e.stated ? "dismissed on a statement" : "ruled out by the twin"}: ${list[i].reason || ""}`; });
      return;
    }
    ph.prunedKey = key;
    ph.pruned.replaceChildren();
    ph.prunedEls = [];
    for (const q of list) {
      const stated = q.by === "occupant-statement" || q.by === "search-room";
      const g = el("g", { class: `ph-ghost${stated ? " stated" : ""}` });
      const c = el("circle"), l1 = el("line"), l2 = el("line");
      if (stated) l2.style.display = "none";
      const title = el("title", {}, `${p.id}: ${stated ? "dismissed on a statement" : "ruled out by the twin"}: ${q.reason || "removed by the twin"}`);
      g.append(c, l1, l2, title);
      g.animate?.([{ opacity: 0 }, { opacity: 1 }], { duration: 900, delay: 500, fill: "backwards" });
      ph.pruned.appendChild(g);
      ph.prunedEls.push({ c, l1, l2, x: q.x, y: q.y, title, stated });
    }
  }
  _arcPath(cx, cy, r, arcs) {
    const P = (a) => this.P([cx + r * Math.cos(a * D2R), cy + r * Math.sin(a * D2R)]);
    let d = "";
    for (const [a0, a1] of arcs) {
      const span = a1 - a0;
      if (span <= 0.05) continue;
      if (span >= 359.9) { const p0 = P(a0), p1 = P(a0 + 180); d += `M${p0[0]} ${p0[1]} A${r} ${r} 0 1 0 ${p1[0]} ${p1[1]} A${r} ${r} 0 1 0 ${p0[0]} ${p0[1]} `; continue; }
      const p0 = P(a0), p1 = P(a1);
      d += `M${p0[0].toFixed(3)} ${p0[1].toFixed(3)} A${r} ${r} 0 ${span > 180 ? 1 : 0} 0 ${p1[0].toFixed(3)} ${p1[1].toFixed(3)} `;
    }
    return d || "M0 0";
  }
  _drawPhone(ph) {
    const S = ph.cur, u = this.u, p = ph.data || {};
    const ring = S.ring;
    if (ring && ring.op > 0.01) {
      const P = this.P([ring.cx, ring.cy]);
      ph.ghost.setAttribute("cx", P[0]); ph.ghost.setAttribute("cy", P[1]); ph.ghost.setAttribute("r", Math.max(0.01, ring.r));
      ph.ghost.style.opacity = ring.ghost * ring.op;
      const d = this._arcPath(ring.cx, ring.cy, Math.max(0.01, ring.r), ring.arcs);
      if (ring.kind === "ble") {
        ph.band.setAttribute("d", d); ph.band.setAttribute("stroke-width", Math.max(2 * ring.sg, 3 * u)); ph.band.style.opacity = ring.op;
        ph.bandLine.setAttribute("d", d); ph.bandLine.style.opacity = ring.op;
        ph.arc.style.opacity = 0;
      } else {
        ph.arc.setAttribute("d", d); ph.arc.style.opacity = ring.op;
        ph.band.style.opacity = 0; ph.bandLine.style.opacity = 0;
      }
    } else { ph.ghost.style.opacity = 0; ph.arc.style.opacity = 0; ph.band.style.opacity = 0; ph.bandLine.style.opacity = 0; }
    // candidates
    const ck = Object.keys(S).filter((k) => k[0] === "c").sort();
    for (const k of Object.keys(ph.candEls)) if (!S[k]) { ph.candEls[k].g.remove(); delete ph.candEls[k]; }
    ph.links.replaceChildren();
    const vis = ck.filter((k) => S[k].op > 0.01);
    for (let i = 0; i + 1 < vis.length; i++) {
      const a = this.P([S[vis[i]].x, S[vis[i]].y]), b = this.P([S[vis[i + 1]].x, S[vis[i + 1]].y]);
      ph.links.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: "ph-link", style: `opacity:${0.75 * Math.min(S[vis[i]].op, S[vis[i + 1]].op)}` }));
    }
    for (const k of ck) {
      const c = S[k];
      let e = ph.candEls[k];
      if (!e) { e = ph.candEls[k] = { g: el("g", { class: "ph-cand" }), s: el("circle", { class: "s" }), m: el("circle", { class: "m" }), q: el("text", {}, "?"), w: el("text", { class: "ph-sub" }) }; e.g.append(e.s, e.m, e.q, e.w); ph.cands.appendChild(e.g); }
      const P = this.P([c.x, c.y]), rm = 12 * u * Math.min(1, c.op * 1.5);   // equal sizes: the heavier spot is not the answer
      e.g.style.opacity = c.op;
      e.s.setAttribute("cx", P[0]); e.s.setAttribute("cy", P[1]); e.s.setAttribute("r", Math.max(0.01, c.s));
      e.s.style.display = c.s > rm ? "" : "none";
      e.m.setAttribute("cx", P[0]); e.m.setAttribute("cy", P[1]); e.m.setAttribute("r", Math.max(0.001, rm));
      e.q.setAttribute("x", P[0]); e.q.setAttribute("y", P[1]); e.q.style.fontSize = `${Math.max(0.001, rm * 1.25)}px`;
      e.w.setAttribute("x", P[0]); e.w.setAttribute("y", P[1] + rm + 13 * u); e.w.textContent = `${Math.round(c.w * 100)}%`;
    }
    // fix
    const f = S.fix;
    if (f && f.op > 0.01) {
      const P = this.P([f.x, f.y]);
      ph.fixG.style.opacity = f.op;
      ph.s1.setAttribute("cx", 0); ph.s1.setAttribute("cy", 0); ph.s1.setAttribute("rx", Math.max(0.01, f.ea)); ph.s1.setAttribute("ry", Math.max(0.01, f.eb));
      ph.s1.setAttribute("transform", `translate(${P[0]} ${P[1]}) rotate(${(-f.ang / D2R).toFixed(1)})`);
      ph.s2.setAttribute("rx", Math.max(0.01, 2 * f.ea)); ph.s2.setAttribute("ry", Math.max(0.01, 2 * f.eb)); ph.s2.setAttribute("transform", ph.s1.getAttribute("transform"));
      ph.core.setAttribute("cx", P[0]); ph.core.setAttribute("cy", P[1]); ph.core.setAttribute("r", 7 * u);
      const now = performance.now(), pk = ph.pulseT0 ? (now - ph.pulseT0) / 1100 : 2;
      if (pk >= 0 && pk < 1) { ph.pulse.style.opacity = 1 - pk; ph.pulse.setAttribute("cx", P[0]); ph.pulse.setAttribute("cy", P[1]); ph.pulse.setAttribute("r", (8 + 40 * ease(pk)) * u); }
      else ph.pulse.style.opacity = 0;
    } else ph.fixG.style.opacity = 0;
    // ruled-out ghosts
    for (const q of ph.prunedEls || []) {
      const P = this.P([q.x, q.y]), r = 9 * u, x = r * 0.62;
      q.c.setAttribute("cx", P[0]); q.c.setAttribute("cy", P[1]); q.c.setAttribute("r", r);
      for (const [l, s] of [[q.l1, 1], [q.l2, -1]]) { l.setAttribute("x1", P[0] - x); l.setAttribute("y1", P[1] - s * x); l.setAttribute("x2", P[0] + x); l.setAttribute("y2", P[1] + s * x); }
    }
    // label: follows whichever part dominates right now
    const st = statusOf(p.report), n = p.report && isNum(p.report.people) ? p.report.people : null;
    const ppl = n === null ? "" : ` · ${n} ${n === 1 ? "person" : "people"}`;
    let at = null, lab = "", sub = "", op = 0, mark = null;
    const bestC = ck.filter((k) => S[k].op > 0.3).sort((a, b) => S[b].y - S[a].y)[0];   // the top-most candidate on screen
    if (f && f.op > 0.45) {
      const P = this.P([f.x, f.y]); at = [P[0], P[1] - Math.max(f.ea, f.eb, 10 * u) - 20 * u]; mark = P;
      lab = `${ph.id}${ppl}`; sub = `${st.word.toLowerCase()} · ±${num(p.sigma_m)} m`; op = f.op;
    } else if (bestC) {
      const c = S[bestC], P = this.P([c.x, c.y]), rm = 12 * u; at = [P[0], P[1] - rm - 20 * u]; mark = [P[0], P[1] - rm];
      lab = `${ph.id} ?${ppl}`; sub = `cannot choose between ${vis.length} spots`; op = c.op;
    } else if (ring && ring.op > 0.3) {
      let la = ring.arcs[0];
      for (const a of ring.arcs) if (a[1] - a[0] > la[1] - la[0]) la = a;
      const mid = (la[0] + la[1]) / 2 * D2R, rr = ring.r + (ring.kind === "ble" ? ring.sg : 0) + 16 * u;
      const P = this.P([ring.cx + rr * Math.cos(mid), ring.cy + rr * Math.sin(mid)]); at = [P[0], P[1]];
      mark = this.P([ring.cx + ring.r * Math.cos(mid), ring.cy + ring.r * Math.sin(mid)]);
      const rg = p.ring || ph.lastRing || {};
      lab = `${ph.id}${ppl}`; sub = ring.kind === "ble" ? `Bluetooth band · about ${num(rg.r_m)} m from ${rg.puck}` : `range only · ${num(rg.r_m)} m from ${rg.puck}`; op = ring.op;
    } else if (p.geometry === "zone" && this.zoneEls) {
      const z = this.zoneEls.find((q) => q.id === ph.id);
      if (z) { const P = this.P(z.c); at = [P[0], P[1] - z.i * 34 * u]; mark = P; lab = `${ph.id}${ppl}`; sub = `probably here · ${z.words}`; op = 1; }
    }
    ph.labAt = at; ph.labOp = op; ph.labMark = mark; ph.labRank = f && f.op > 0.45 ? 0 : bestC ? 1 : ring && ring.op > 0.3 ? 2 : 3;
    const as = (p.assumptions || [])[0];
    ph.assume.textContent = as ? this.assumptionTag(as) : "";
    if (at) { ph.lab.textContent = lab; ph.sub.textContent = sub; ph.lab.style.opacity = op; ph.sub.style.opacity = op; ph.assume.style.opacity = op; }
    else { ph.lab.style.opacity = 0; ph.sub.style.opacity = 0; ph.assume.style.opacity = 0; }
    // the ± numbers are the locator's model: say so on hover (and that they are simulated, when they are)
    const sim = !(this.data && this.data.simulated === false);
    ph.title.textContent = (p.text || ph.id) + (f && f.op > 0.45 ? `\n±${num(p.sigma_m)} m: 1 sigma, from the locator's model${sim ? "; simulated" : ""}` : "");
  }

  _placeLabels() {
    const u = this.u, placed = [];
    const bbox = (e) => { try { const b = e.getBBox(); return b.width ? [b.x, b.y, b.x + b.width, b.y + b.height] : null; } catch { return null; } };
    // fixed things a label must not cover: pucks with their ids, the device-count badges, the room title, the next-puck marker
    for (const e of this.pucks.values()) { const P = this.P([e.x, e.y]); placed.push([P[0] - 14 * u, P[1] - 26 * u, P[0] + 14 * u, P[1] + 10 * u]); }
    for (const b of this.presEls || []) { const q = bbox(b.rect); if (q) placed.push(q); }
    if (this._focusTag) { const q = bbox(this._focusTag); if (q) placed.push(q); }
    if (this.nextEl) { const q = bbox(this.nextEl.t); if (q) placed.push(q); }
    // and the phones' own markers (fix cores, candidate markers with their weights)
    for (const ph of this.phones.values()) for (const [k, q] of Object.entries(ph.cur || {})) {
      if (!(q.op > 0.3) || !isNum(q.x) || k === "ring") continue;
      const P = this.P([q.x, q.y]), r = (k === "fix" ? 9 : 13) * u;
      placed.push([P[0] - r, P[1] - r, P[0] + r, P[1] + r + (k === "fix" ? 0 : 14 * u)]);
    }
    const list = [...this.phones.values()].filter((ph) => ph.labAt && ph.labOp > 0.05).sort((a, b) => a.labRank - b.labRank || String(a.id).localeCompare(String(b.id)));
    const over = (b) => placed.reduce((s, q) => s + Math.max(0, Math.min(b[2], q[2]) - Math.max(b[0], q[0])) * Math.max(0, Math.min(b[3], q[3]) - Math.max(b[1], q[1])), 0);
    const bx = this.box || [0, 0, 1e9, 1e9];
    for (const ph of list) {
      const w = Math.max(ph.lab.getComputedTextLength(), ph.sub.getComputedTextLength(), ph.assume.textContent ? ph.assume.getComputedTextLength() : 0) / 2 + 4 * u;
      const h1 = 15 * u, h2 = (ph.assume.textContent ? 30 : 17) * u;
      const clampX = (x) => Math.max(bx[0] + w + 4 * u, Math.min(bx[0] + bx[2] - w - 4 * u, x));
      const clampY = (y) => Math.max(bx[1] + 20 * u, Math.min(bx[1] + bx[3] - 34 * u, y));
      const ax = ph.labAt[0], ay = ph.labAt[1];
      // candidate spots around the anchor: as placed, above, below, right, left, then farther out
      const steps = [[0, 0], [0, -1], [0, 1], [1, 0], [-1, 0], [1, -1], [-1, -1], [1, 1], [-1, 1], [0, -2], [0, 2], [2, 0], [-2, 0]];
      let best = null;
      for (const [sx, sy] of steps) {
        const x = clampX(ax + sx * (w + 12 * u)), y = clampY(ay + sy * 34 * u);
        const box = [x - w, y - h1, x + w, y + h2], o = over(box);
        if (!best || o < best[0] - 1e-9) best = [o, x, y, box];
        if (o === 0) break;
      }
      const [, x, y, box] = best;
      placed.push(box);
      ph.lab.setAttribute("x", x); ph.lab.setAttribute("y", y);
      ph.sub.setAttribute("x", x); ph.sub.setAttribute("y", y + 13 * u);
      ph.assume.setAttribute("x", x); ph.assume.setAttribute("y", y + 26 * u);
      // a label moved away from its marker keeps a thin leader line to it
      const m = ph.labMark, moved = m && Math.hypot(x - ax, y - ay) > 20 * u;
      if (moved) {
        const ly = m[1] < y - h1 ? y - h1 : m[1] > y + h2 ? y + h2 : m[1], lx = Math.max(x - w, Math.min(x + w, m[0]));
        ph.leader.setAttribute("x1", m[0]); ph.leader.setAttribute("y1", m[1]); ph.leader.setAttribute("x2", lx); ph.leader.setAttribute("y2", ly);
        ph.leader.style.opacity = ph.labOp * 0.8;
      } else ph.leader.style.opacity = 0;
    }
    for (const ph of this.phones.values()) if (!(ph.labAt && ph.labOp > 0.05)) ph.leader.style.opacity = 0;
  }

  // ------------------------------------------------------------------ frame loop
  kick() { if (!this._raf) this._raf = requestAnimationFrame((t) => this._frame(t)); }
  _frame() {
    this._raf = null;
    const now = performance.now();
    let more = false;
    // viewBox tween
    if (this.boxTo) {
      const k = reduceMotion() ? 1 : Math.min(1, (now - this.boxT0) / 750), e = ease(k);
      this.box = this.boxFrom.map((v, i) => v + (this.boxTo[i] - v) * e);
      this.svg.setAttribute("viewBox", this.box.map((v) => v.toFixed(3)).join(" "));
      if (k < 1) more = true; else this.boxTo = null;
    }
    const rect = this.svg.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0 && this.box) {
      this.u = Math.max(this.box[2] / rect.width, this.box[3] / rect.height);
      this.svg.style.setProperty("--u", this.u.toFixed(5));
    }
    const u = this.u;
    // pucks
    for (const e of this.pucks.values()) {
      const P = this.P([e.x, e.y]), age = (now - e.t0) / 1000;
      e.body.setAttribute("cx", P[0]); e.body.setAttribute("cy", P[1]); e.body.setAttribute("r", 5.5 * u);
      e.halo.setAttribute("cx", P[0]); e.halo.setAttribute("cy", P[1]); e.halo.setAttribute("r", 11 * u);
      e.lab.setAttribute("x", P[0]); e.lab.setAttribute("y", P[1] - 13 * u);
      if (age < 1.2 && !reduceMotion()) { const k = age / 1.2; e.drop.setAttribute("cx", P[0]); e.drop.setAttribute("cy", P[1]); e.drop.setAttribute("r", (11 + 40 * ease(k)) * u); e.drop.style.opacity = 1 - k; more = true; }
      else e.drop.style.opacity = 0;
    }
    // cold presence badges: inside the room, in the corner farthest from the pucks and the phones' points
    const busy = [...this.pucks.values()].map((e) => [e.x, e.y]);
    for (const ph of this.phones.values()) for (const [k, q] of Object.entries(ph.cur || {})) if (q.op > 0.1 && isNum(q.x)) busy.push([q.x, q.y]);
    for (const b of this.presEls || []) {
      const bb = bboxOf(b.room.polygon), pad = 7 * u, m = 0.25;
      b.t1.setAttribute("x", 0); b.t1.setAttribute("y", 0); b.t2.setAttribute("x", 0); b.t2.setAttribute("y", 0);
      b.t1.textContent = b.long; b.t2.style.display = "";       // measure the full badge every frame, then decide (no flip-flop)
      const w = Math.max(b.t1.getComputedTextLength(), b.t2.getComputedTextLength()) + 2 * pad, h = 37 * u;
      const compact = w > (bb[2] - bb[0]) * 0.75;
      b.t2.style.display = compact ? "none" : "";
      if (compact) b.t1.textContent = b.short; else b.t1.textContent = b.long;
      const w2 = compact ? b.t1.getComputedTextLength() + 2 * pad : w, h2 = compact ? 22 * u : h;
      let best = null;
      for (const [cx, cy] of [[0, 1], [1, 1], [0, 0], [1, 0]]) {      // building-frame corners: x left/right, y bottom/top
        const x0 = cx ? Math.max(bb[0] + m, bb[2] - m - w2) : bb[0] + m, y0b = cy ? bb[3] - m - h2 : bb[1] + m;   // y0b = bottom of the box, building frame
        const c = [x0 + w2 / 2, y0b + h2 / 2];
        const score = busy.length ? Math.min(...busy.map((q) => Math.max(Math.abs(q[0] - c[0]) - w2 / 2, Math.abs(q[1] - c[1]) - h2 / 2))) : (cy ? 1 : 0);
        if (!best || score > best[0] + 1e-6) best = [score, x0, y0b];
      }
      if (best[0] < 0.15) best = [0, Math.max(bb[0], bb[2] - w2), bb[3] + 4 * u];   // nothing free inside: sit on the top wall, outside
      const x = best[1], y = this.fy - best[2] - h2;
      b.rect.setAttribute("x", x); b.rect.setAttribute("y", y); b.rect.setAttribute("width", w2); b.rect.setAttribute("height", h2); b.rect.setAttribute("rx", 4 * u);
      b.t1.setAttribute("x", x + pad); b.t1.setAttribute("y", y + 16 * u); b.t2.setAttribute("x", x + pad); b.t2.setAttribute("y", y + 30 * u);
    }
    for (const q of this.layEls || []) { q.c.setAttribute("r", 10 * u); q.t.setAttribute("y", q.P[1] + 22 * u); }
    if (this.nextEl) {
      const n = this.nextEl, P = this.P([n.x, n.y]), r = 11 * u;
      n.ring.setAttribute("d", `M${P[0]} ${P[1] - r} L${P[0] + r} ${P[1]} L${P[0]} ${P[1] + r} L${P[0] - r} ${P[1]} Z`);
      n.dot.setAttribute("cx", P[0]); n.dot.setAttribute("cy", P[1]); n.dot.setAttribute("r", 3 * u);
      n.t.setAttribute("x", P[0]); n.t.setAttribute("y", P[1] - r - 5 * u);
    }
    if (this._focusTag) { const x = Number(this._focusTag.dataset.x), y = Number(this._focusTag.dataset.y); this._focusTag.setAttribute("x", x + 0.1); this._focusTag.setAttribute("y", this.fy - y - 12 * u); }
    // phones
    for (const [id, ph] of this.phones) {
      const moving = this._interp(ph, now);
      if (moving) more = true;
      this._drawPhone(ph);
      if (ph.leaving && !moving) { ph.g.remove(); this.phones.delete(id); }
    }
    this._placeLabels();
    if (more || [...this.phones.values()].some((ph) => ph.pulseT0 && now - ph.pulseT0 < 1100)) this.kick();
  }
}
