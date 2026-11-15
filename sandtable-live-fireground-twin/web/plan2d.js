// 2D plan of the exercise floor (SVG). Also owns the small client-side graph search used by the selection card,
// and draws the scout scan and the Beacon phones in the same building frame as the 3D twin.
// Colours come from style.css (classes and custom properties), so the plan follows the day and night themes.
import { bistatic, sigmaEllipse, victimState } from "./scout.js";
import { chipSpot, mirrorPair } from "./beacon.js";
import { personStatus, SHAPE_PATH, puckOrigin, REDUCED } from "./common.js";
const NS = "http://www.w3.org/2000/svg";
const ORIGIN_SHORT = { hand: "by hand", baseline: "measured spacing", layout: "rig layout", sim: "simulated" };
const el = (name, attrs = {}, text) => {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
};

// "HealthCareAndSocialAssist" (a record's code) -> "Health care and social assist"; plain text is left alone
export function prettyIndustry(s) {
  if (!s) return "";
  if (/\s/.test(s)) return s;
  const w = s.replace(/([a-z])([A-Z])/g, "$1 $2");
  return w.charAt(0) + w.slice(1).toLowerCase();
}

// a point well inside a room (the centroid of an L-shaped room can fall outside it): the grid point farthest from the edges
export function labelPoint(poly) {
  const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
  const inside = (x, y) => { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c; } return c; };
  const cx = xs.reduce((a, b) => a + b, 0) / xs.length, cy = ys.reduce((a, b) => a + b, 0) / ys.length;
  const edge = (x, y) => { let m = 1e9; for (let i = 0; i < poly.length; i++) { const a = poly[i], b = poly[(i + 1) % poly.length], dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy || 1, t = Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / L)); m = Math.min(m, Math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)); } return m; };
  if (inside(cx, cy) && edge(cx, cy) > 0.25 * Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys))) return [cx, cy];
  let best = [cx, cy], bd = -1;
  for (let i = 1; i < 24; i++) for (let j = 1; j < 24; j++) {
    const x = Math.min(...xs) + (Math.max(...xs) - Math.min(...xs)) * i / 24, y = Math.min(...ys) + (Math.max(...ys) - Math.min(...ys)) * j / 24;
    if (!inside(x, y)) continue;
    const d = edge(x, y) - 0.02 * Math.hypot(x - cx, y - cy);
    if (d > bd) { bd = d; best = [x, y]; }
  }
  return best;
}

export class Plan {
  constructor(svg, bundle, opts) {
    this.svg = svg; this.opts = opts; this.twin = bundle.twin; this.site = bundle.site;
    const t = this.twin;
    this.floor = t.floors.find((f) => f.id === t.exercise_floor);
    const xs = this.floor.outline.map((p) => p[0]), ys = this.floor.outline.map((p) => p[1]);
    this.fx = Math.max(...xs); this.fy = Math.max(...ys);
    // text and markers are sized in building metres: scale them down on a small floor (the fixture floor is k = 1)
    this.k = Math.max(0.35, Math.min(1, Math.max(this.fx, this.fy) / 28));
    svg.style.setProperty("--k", this.k);
    this.z = 1; svg.style.setProperty("--z", 1);          // text follows the zoom: about the same size on screen at any close-up
    const pad = 4.2 * this.k;
    this.homeBox = [-pad, -pad, this.fx + 2 * pad, this.fy + 2 * pad]; this.box = [...this.homeBox];
    svg.setAttribute("viewBox", this.homeBox.join(" "));
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    this.rooms = Object.fromEntries(t.rooms.map((r) => [r.id, r]));
    this.nodes = Object.fromEntries(t.graph.nodes.map((n) => [n.id, n]));
    this.adj = {};
    for (const e of t.graph.edges) { (this.adj[e.a] ||= []).push([e.b, e.len_m]); (this.adj[e.b] ||= []).push([e.a, e.len_m]); }
    this.roomEls = {}; this.unitEls = {}; this.routeEls = {};
    // stairs drawn as treads, lost areas as a hatch (architectural conventions, readable without colour)
    const defs = el("defs");
    const treads = el("pattern", { id: "p-treads", width: 0.42, height: 0.42, patternUnits: "userSpaceOnUse" });
    treads.append(el("rect", { width: 0.42, height: 0.42, class: "tread-bg" }), el("line", { x1: 0, y1: 0.21, x2: 0.42, y2: 0.21, class: "tread" }));
    const hatch = el("pattern", { id: "p-hatch", width: 0.7, height: 0.7, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
    hatch.append(el("rect", { width: 0.7, height: 0.7, class: "hatch-bg" }), el("line", { x1: 0, y1: 0, x2: 0, y2: 0.7, class: "hatch" }));
    defs.append(treads, hatch); svg.appendChild(defs);
    this.g = {};
    for (const k of ["outside", "rooms", "beaconlo", "scanlo", "walls", "labels", "tags", "preplan", "hazards", "routes", "scanhi", "beaconhi", "anchors", "units", "orient", "place"]) { this.g[k] = el("g", { class: `g-${k}` }); svg.appendChild(this.g[k]); }
    const floorNo = Number(this.floor.label);
    const hazardSuites = new Set(this.site.tenants.filter((q) => q.floor === floorNo && q.life_hazard).map((q) => q.suite));
    // a scan of one suite has several rooms with the same suite number: name rooms by their label, the suite once
    const suiteRooms = {};
    for (const r of t.rooms.filter((q) => q.floor === this.floor.id && q.suite)) (suiteRooms[r.suite] ||= []).push(r);
    for (const r of t.rooms.filter((q) => q.floor === this.floor.id)) {
      const p = el("polygon", { points: r.polygon.map((q) => this.P(q).join(",")).join(" "), class: `r ${r.kind}`, "data-room": r.id });
      p.addEventListener("click", (ev) => this._click(ev, r));
      this.g.rooms.appendChild(p); this.roomEls[r.id] = p;
      const c = labelPoint(r.polygon), k = this.k;
      // labels fit their room: a small scanned room gets a smaller name instead of one that spills into the next room
      const rw = Math.max(...r.polygon.map((q) => q[0])) - Math.min(...r.polygon.map((q) => q[0]));
      const fit = (text, em) => Math.min(1, (0.9 * rw) / Math.max(1, String(text).length * 0.5 * em * k)).toFixed(3);
      if (r.kind === "suite") {
        const group = suiteRooms[r.suite] || [r], shared = group.length > 1;
        const main = !shared || group.reduce((a, q) => ((q.area_m2 || 0) > (a.area_m2 || 0) ? q : a), group[0]) === r;
        this.g.labels.appendChild(el("text", { x: c[0], y: this.fy - c[1] - 0.5 * k, class: "rl", "data-room": r.id, style: `--fit:${fit(shared ? r.label : r.suite, 1.45)}` }, shared ? r.label : r.suite));
        const ten = this.site.tenants.filter((q) => q.floor === floorNo && q.suite === r.suite);
        const ind = prettyIndustry(ten[0]?.industry);
        const line = ten.length && ind ? ind.replace(/^Offices of /, "").replace(/\s*\(.*?\)/g, "").slice(0, 24) : (r.registered === false ? "no registration" : "");
        const sub = shared ? (main ? `Suite ${r.suite}${line ? " · " + line : ""}` : "") : line;
        if (sub) this.g.labels.appendChild(el("text", { x: c[0], y: this.fy - c[1] + 1.0 * k, class: "rl small", "data-room": r.id, style: `--fit:${fit(sub, 0.95)}` }, sub));
        if (main && hazardSuites.has(r.suite)) {
          const y = this.fy - c[1] + 2.15 * k, d = 0.26 * k;
          this.g.labels.appendChild(el("path", { d: `M${c[0] - 2.05 * k} ${y - d} l${d} ${d} l${-d} ${d} l${-d} ${-d} Z`, class: "rl-dia", "data-room": r.id }));
          this.g.labels.appendChild(el("text", { x: c[0] + 0.2 * k, y, class: "rl hz", "data-room": r.id }, "Life hazard"));
        }
      } else if (r.kind !== "corridor") {
        this.g.labels.appendChild(el("text", { x: c[0], y: this.fy - c[1] - 1.5 * k, class: "rl small", style: `--fit:${fit(r.label, 0.95)}` }, r.label));
      }
    }
    for (const w of t.walls.filter((q) => q.floor === this.floor.id)) {
      const a = this.P(w.a), b = this.P(w.b);
      this.g.walls.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: "wall" }));
    }
    for (const d of t.doors.filter((q) => q.floor === this.floor.id)) {
      const dir = this._wallDir(d.at), w = (d.width_m || 0.9) / 2 + 0.15, p = this.P(d.at);
      this.g.walls.appendChild(el("line", { x1: p[0] - dir[0] * w, y1: p[1] + dir[1] * w, x2: p[0] + dir[0] * w, y2: p[1] - dir[1] * w, class: "door" }));
    }
    for (const tg of t.tags || []) this.addTag(tg);
    this._orientation();
    svg.addEventListener("pointermove", (ev) => this._drag(ev));
    svg.addEventListener("pointerup", () => { this.dragging = null; this.dragPuck = null; });
    svg.addEventListener("pointerleave", () => { this.dragging = null; this.dragPuck = null; });
    // placing pucks by hand: a click puts the next puck, a drag moves a placed one
    svg.addEventListener("pointerdown", (ev) => {
      if (!this.placing) return;
      ev.preventDefault(); ev.stopPropagation();
      const id = ev.target.closest?.("[data-puck]")?.dataset.puck, p = this.toBldg(ev);
      if (id) this.dragPuck = id; else this.opts.onPlace?.("add", null, p[0], p[1]);
    }, true);
    svg.addEventListener("pointermove", (ev) => {
      if (!this.placing) return;
      const now = performance.now(); if (now - (this._lastPl || 0) < 40) return; this._lastPl = now;
      const p = this.toBldg(ev);
      if (this.dragPuck) this.opts.onPlace?.("move", this.dragPuck, p[0], p[1]);
      else if (ev.pointerType === "mouse") this.opts.onPlace?.("hover", null, p[0], p[1]);
    });
  }
  // the crews' colours follow the theme: drop the drawn markers, the next update draws them again
  setColors(colors) {
    this.opts.color = colors;
    for (const g of Object.values(this.unitEls)) g.remove();
    for (const r of Object.values(this.routeEls)) r.remove();
    this.unitEls = {}; this.routeEls = {};
    if (this._lastSt) this.update(this._lastSt, this._lastSel);
  }
  P(p) { return [p[0], this.fy - p[1]]; }
  centre(poly) { return [poly.reduce((s, p) => s + p[0], 0) / poly.length, poly.reduce((s, p) => s + p[1], 0) / poly.length]; }
  _wallDir(at) {
    let best = null;
    for (const w of this.twin.walls) {
      const dx = w.b[0] - w.a[0], dy = w.b[1] - w.a[1], len = Math.hypot(dx, dy);
      if (len < 0.01) continue;
      const tt = Math.max(0, Math.min(1, ((at[0] - w.a[0]) * dx + (at[1] - w.a[1]) * dy) / (len * len)));
      const d = Math.hypot(at[0] - (w.a[0] + tt * dx), at[1] - (w.a[1] + tt * dy));
      if (!best || d < best[0]) best = [d, [dx / len, dy / len]];
    }
    return best ? best[1] : [1, 0];
  }
  toBldg(ev) {
    const pt = this.svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const q = pt.matrixTransform(this.svg.getScreenCTM().inverse());
    return [q.x, this.fy - q.y];
  }
  addTag(tg) {
    if (tg.floor && tg.floor !== this.floor.id) return;
    const p = this.P(tg.position), obs = tg.kind === "observation";
    const m = el(obs ? "circle" : "rect", obs ? { cx: p[0], cy: p[1], r: 0.32, class: "tagm obs" } : { x: p[0] - 0.4, y: p[1] - 0.4, width: 0.8, height: 0.8, class: "tagm", transform: `rotate(45 ${p[0]} ${p[1]})` });
    m.appendChild(el("title", {}, `${tg.label}${tg.description ? " — " + tg.description : ""}`));
    this.g.tags.appendChild(m);
    if (obs) (this._obs ||= []).push(m);
  }
  highlightSuite(suite, on) {
    for (const r of this.twin.rooms.filter((q) => q.suite === suite)) this.roomEls[r.id]?.classList.toggle("hot", on);
  }
  setInject(on) { this.inject = on; for (const e of Object.values(this.roomEls)) e.classList.toggle("clickable", on); }
  _click(ev, room) {
    if (!this.inject) return;
    const p = this.toBldg(ev);
    let ref = null;
    if (room.kind === "stair") ref = (this.twin.stairs.find((s) => s.room === room.id) || {}).id;
    else if (room.kind === "corridor") {
      let best = null;
      for (const e of this.twin.graph.edges) {
        const a = this.nodes[e.a], b = this.nodes[e.b];
        if (this.rooms[a.room]?.kind !== "corridor" || this.rooms[b.room]?.kind !== "corridor" || a.kind !== "sweep" || b.kind !== "sweep") continue;
        const d = Math.hypot(p[0] - (a.at[0] + b.at[0]) / 2, p[1] - (a.at[1] + b.at[1]) / 2);
        if (!best || d < best[0]) best = [d, `${e.a}-${e.b}`];
      }
      ref = best && best[1];
    } else ref = (this.twin.doors.find((d) => d.rooms[0] === room.id) || {}).id;
    if (ref) this.opts.onInject(ref);
  }
  _drag(ev) {
    if (!this.dragging) return;
    const now = performance.now();
    if (now - (this._lastMove || 0) < 110) return;
    this._lastMove = now;
    const p = this.toBldg(ev);
    this.opts.onMove(this.dragging, Math.max(0.3, Math.min(this.fx - 0.3, p[0])), Math.max(0.3, Math.min(this.fy - 0.3, p[1])));
  }
  setPreplan(pp) {
    this.g.preplan.replaceChildren();
    for (const e of Object.values(this.roomEls)) e.classList.remove("lifehaz");
    if (!pp) { for (const m of this._obs || []) m.remove(); this._obs = []; return; }
    const t = pp.tactical || {};
    for (const h of t.life_hazards || []) this.roomEls[h.room]?.classList.add("lifehaz");
    const seen = {};
    for (const [key, color, word] of [["attack_stair", "var(--e1)", "Attack"], ["evac_stair", "var(--rit)", "Evacuation"]]) {
      const st = this.twin.stairs.find((s) => s.id === t[key]);
      if (!st) continue;
      const r = this.rooms[st.room], door = this.twin.doors.find((d) => d.id === st.door);
      if (r?.kind === "stair" || !door) {
        this.g.preplan.appendChild(el("polygon", { points: r.polygon.map((q) => this.P(q).join(",")).join(" "), class: "pp-stair", style: `stroke:${color}` }));
        const c = this.centre(r.polygon);
        this.g.preplan.appendChild(el("text", { x: c[0], y: this.fy - c[1] + 1.7 * this.k, class: "pp-lab", style: `fill:${color}` }, word));
      } else {
        // the scan has no stair: the way in and out is the suite entrance, marked once at its door
        if (seen[st.id]) { seen[st.id].textContent = `Attack and evacuation: ${st.label}`; continue; }
        const p = this.P(door.at), k = this.k;
        this.g.preplan.appendChild(el("circle", { cx: p[0], cy: p[1], r: 0.9 * k, class: "pp-door", style: `stroke:${color}` }));
        seen[st.id] = el("text", { x: p[0], y: p[1] + 1.9 * k, class: "pp-lab", style: `fill:${color}` }, `${word}: ${st.label}`);
        this.g.preplan.appendChild(seen[st.id]);
      }
    }
    for (const a of t.anchor_drops || []) {
      const p = this.P(a.at);
      const q = 0.7 * this.k, m = el("path", { d: `M${p[0]} ${p[1] - q} L${p[0] + q} ${p[1]} L${p[0]} ${p[1] + q} L${p[0] - q} ${p[1]} Z`, class: "pp-drop" });
      m.appendChild(el("title", {}, `Planned UWB anchor drop ${a.id}: ${a.near}`));
      this.g.preplan.appendChild(m);
    }
  }
  blocked(hazards) {
    const nodes = new Set(), edges = new Set();
    for (const h of hazards || []) {
      const ref = h.ref;
      if (this.nodes[ref]) nodes.add(ref);
      else if (this.rooms[ref]) Object.values(this.nodes).filter((n) => n.room === ref).forEach((n) => nodes.add(n.id));
      else if (this.twin.stairs.some((s) => s.id === ref)) {
        const s = this.twin.stairs.find((q) => q.id === ref);
        Object.values(this.nodes).filter((n) => n.room === s.room).forEach((n) => nodes.add(n.id));
        const d = this.twin.doors.find((q) => q.id === s.door); if (d) nodes.add(d.node);
      } else if (this.twin.doors.some((d) => d.id === ref)) nodes.add(this.twin.doors.find((d) => d.id === ref).node);
      else if (ref.includes("-")) { const [a, b] = ref.split("-"); edges.add(`${a}-${b}`); edges.add(`${b}-${a}`); }
    }
    return { nodes, edges };
  }
  exitDistances(src, hazards) {
    if (!src || !this.nodes[src]) return [];
    const { nodes, edges } = this.blocked(hazards);
    const dist = { [src]: 0 }, todo = new Set([src]);
    while (todo.size) {
      let u = null; for (const q of todo) if (u === null || dist[q] < dist[u]) u = q;
      todo.delete(u);
      for (const [v, w] of this.adj[u] || []) {
        if (nodes.has(v) || edges.has(`${u}-${v}`)) continue;
        if (dist[v] === undefined || dist[u] + w < dist[v]) { dist[v] = dist[u] + w; todo.add(v); }
      }
    }
    return this.twin.stairs.map((s) => {
      const n = Object.values(this.nodes).find((q) => q.room === s.room);
      return { label: s.label, m: n && dist[n.id] !== undefined ? Math.round(dist[n.id]) : null };
    });
  }
  _orientation() {
    const b = this.twin.georef.bearing_deg * Math.PI / 180;
    const dirOf = (brg) => { const r = brg * Math.PI / 180, e = Math.sin(r), n = Math.cos(r); return [e * Math.sin(b) + n * Math.cos(b), -e * Math.cos(b) + n * Math.sin(b)]; };
    const cx = this.fx / 2, cy = this.fy / 2;
    for (const [k, s] of Object.entries(this.site.sides || {})) {
      const d = dirOf(s.bearing_deg);
      const tt = Math.min(Math.abs(d[0]) > 1e-3 ? cx / Math.abs(d[0]) : 1e9, Math.abs(d[1]) > 1e-3 ? cy / Math.abs(d[1]) : 1e9) + 1.35 * this.k;
      const p = this.P([cx + d[0] * tt, cy + d[1] * tt]);
      const g = el("g", { class: "side" });
      g.append(el("circle", { cx: p[0], cy: p[1], r: 0.95 * this.k }), el("text", { x: p[0], y: p[1] }, k), el("title", {}, `Side ${k}${s.street ? ": " + s.street : ""}`));
      this.g.orient.appendChild(g);
    }
    const n = dirOf(0), a = this.P([-1.3 * this.k, this.fy - 1.4 * this.k]), ang = -Math.atan2(n[1], n[0]) * 180 / Math.PI + 90;
    const g = el("g", { class: "north", transform: `translate(${a[0]} ${a[1]}) rotate(${ang}) scale(${this.k})` });
    g.append(el("path", { d: "M0 -1.3 L0.65 0.8 L0 0.4 L-0.65 0.8 Z" }), el("text", { x: 0, y: -1.9 }, "N"));
    this.g.orient.appendChild(g);
  }
  // bbox [x0, y0, x1, y1] in building metres, or null for the whole floor
  setFocus(bbox) {
    const m = 2.4;
    const to = bbox ? [bbox[0] - m, this.fy - bbox[3] - m, bbox[2] - bbox[0] + 2 * m, bbox[3] - bbox[1] + 2 * m] : this.homeBox;
    const k = to.map((v) => v.toFixed(2)).join(" ");
    if (k === this._focusKey) return;
    this._focusKey = k;
    const z = Math.max(0.28, Math.min(1, to[2] / 24));
    this.zoomed = z < 0.75;
    this.svg.classList.toggle("zoomed", this.zoomed);
    if (Math.abs(z - this.z) > 0.02) {
      this.z = z; this.svg.style.setProperty("--z", z.toFixed(3));
      this._beaconKey = null; this._scoutKey = undefined;
      if (this._bArgs) this.setBeacons(...this._bArgs);
      if (this._sView !== undefined) this.setScout(this._sView);
      if (this.placing) this.setPlacing(this.placing);
    }
    if (this._lastSt) this.update(this._lastSt, this._lastSel);
    const from = [...this.box], t0 = performance.now();
    cancelAnimationFrame(this._fr);
    const step = () => {
      const u = Math.min(1, (performance.now() - t0) / 700), e = u * u * (3 - 2 * u);
      this.box = from.map((v, i) => v + (to[i] - v) * e);
      this.svg.setAttribute("viewBox", this.box.join(" "));
      if (u < 1) this._fr = requestAnimationFrame(step);
    };
    step();
  }
  nearestNode(x, y, room) {
    let best = null;
    for (const n of Object.values(this.nodes)) {
      if (room && n.room !== room) continue;
      const d = Math.hypot(n.at[0] - x, n.at[1] - y);
      if (!best || d < best[0]) best = [d, n.id];
    }
    return best ? best[1] : (room ? this.nearestNode(x, y, null) : null);
  }
  // ---------------------------------------------------------------- the scout scan
  setScout(view) {
    this._sView = view;
    const key = view ? view.key + "|" + this.z : null;
    if (key === this._scoutKey) return;
    this._scoutKey = key;
    for (const t of this.g.labels.querySelectorAll("[data-room]")) t.style.display = view && t.dataset.room === view.room.id ? "none" : "";
    this.g.scanlo.replaceChildren(); this.g.scanhi.replaceChildren();
    if (!view) return;
    const { room, phase } = view, lo = this.g.scanlo, hi = this.g.scanhi, P = (p) => this.P(p);
    const pts = (poly) => poly.map((q) => P(q).join(",")).join(" ");
    const cid = `clip-${room.id}`;
    const defs = el("defs"); const cp = el("clipPath", { id: cid }); cp.appendChild(el("polygon", { points: pts(room.outline) })); defs.appendChild(cp); lo.appendChild(defs);
    const clipped = el("g", { "clip-path": `url(#${cid})` }); lo.appendChild(clipped);
    hi.appendChild(el("polygon", { points: pts(room.outline), class: `sc-outline${phase === "registered" ? " flash" : ""}` }));
    const reached = ["registered", "scanning", "done"].includes(phase);
    const fs = 0.5 * this.z;
    if (!reached) {
      const d = P(room.door);
      clipped.appendChild(el("circle", { cx: d[0], cy: d[1], r: 6.5, class: "sc-zone" }));
      const n = view.n, ins = room.inside, dir = Math.atan2(room.centre[1] - room.door[1], room.centre[0] - room.door[0]);
      if (phase === "thrown" && view.animate && !REDUCED) for (let i = 0; i < n; i++) {
        const a = dir + (i - (n - 1) / 2) * 0.32, dd = 2.2 + ((i * 7) % 5) * 0.75;
        const s0 = P(ins), e = P([ins[0] + dd * Math.cos(a), ins[1] + dd * Math.sin(a)]);
        const path = `M${s0[0]} ${s0[1]} Q${(s0[0] + e[0]) / 2 + 0.8} ${(s0[1] + e[1]) / 2 - 1.2} ${e[0]} ${e[1]}`;
        clipped.appendChild(el("path", { d: path, class: "sc-arc" }));
        const dot = el("circle", { r: 0.22, class: "sc-flying", opacity: 0 });
        dot.appendChild(el("animateMotion", { dur: "0.9s", begin: "indefinite", fill: "freeze", path, "data-delay": i * 0.16 }));
        dot.appendChild(el("animate", { attributeName: "opacity", values: "1;1;0", keyTimes: "0;0.75;1", dur: "1.4s", begin: "indefinite", fill: "freeze", "data-delay": i * 0.16 }));
        clipped.appendChild(dot);
        for (const a of dot.querySelectorAll("[data-delay]")) a.beginElementAt?.(Number(a.dataset.delay));
      }
      const c = P(room.centre);
      hi.appendChild(el("text", { x: c[0], y: c[1] - 2.6, class: "sc-note", style: `font-size:${fs}px` }, phase === "located" ? "shape known from the ranges, orientation not yet" : `${n} pucks in the room, positions not known yet`));
    }
    const pk = view.pucks;
    if (reached && pk) {
      for (const l of view.links) {
        if (!pk[l.a] || !pk[l.b]) continue;
        const a = P(pk[l.a]), b = P(pk[l.b]);
        hi.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: `sc-link${l.used === false ? " unused" : ""}${phase === "done" ? " faint" : ""}` }));
      }
      if (phase === "scanning" && !REDUCED) {
        const pairs = view.links.filter((l) => pk[l.a] && pk[l.b]), per = Math.max(2.2, pairs.length * 0.34);
        pairs.forEach((l, i) => {
          const f1 = pk[l.a], f2 = pk[l.b], d = Math.hypot(f2[0] - f1[0], f2[1] - f1[1]), e0 = bistatic(f1, f2, d + 0.15), m = P([e0.cx, e0.cy]);
          const g = el("g", { transform: `translate(${m[0]} ${m[1]}) rotate(${(-e0.ang * 180 / Math.PI).toFixed(1)})` });
          const ks = [0, 0.25, 0.5, 0.75, 1].map((u) => bistatic(f1, f2, d + 0.15 + u * 9));
          const ell = el("ellipse", { cx: 0, cy: 0, rx: e0.a, ry: e0.b, class: "sc-sweep", opacity: 0 });
          const frac = 1.9 / per, kt = `0;${(frac / 4).toFixed(3)};${(frac / 2).toFixed(3)};${(3 * frac / 4).toFixed(3)};${frac.toFixed(3)};1`;
          const begin = (i * 0.34).toFixed(2) + "s", dur = per.toFixed(2) + "s";
          ell.appendChild(el("animate", { attributeName: "rx", values: ks.map((k) => k.a.toFixed(2)).join(";") + ";" + ks[4].a.toFixed(2), keyTimes: kt, dur, begin, repeatCount: "indefinite" }));
          ell.appendChild(el("animate", { attributeName: "ry", values: ks.map((k) => k.b.toFixed(2)).join(";") + ";" + ks[4].b.toFixed(2), keyTimes: kt, dur, begin, repeatCount: "indefinite" }));
          ell.appendChild(el("animate", { attributeName: "opacity", values: `0.8;0.6;0.4;0.2;0;0`, keyTimes: kt, dur, begin, repeatCount: "indefinite" }));
          g.appendChild(ell); clipped.appendChild(g);
        });
      }
      for (const id of view.ids) {
        if (!pk[id]) continue;
        const p = P(pk[id]);
        const g = el("g", { class: "sc-puck" });
        g.append(el("circle", { cx: p[0], cy: p[1], r: 0.42, class: "halo" }), el("circle", { cx: p[0], cy: p[1], r: 0.22, class: "body" }),
          el("text", { x: p[0], y: p[1] - 0.6, style: `font-size:${fs}px` }, id), el("title", {}, `Puck ${id}: registered into the twin at ${pk[id][0].toFixed(1)}, ${pk[id][1].toFixed(1)} m`));
        hi.appendChild(g);
      }
    }
    if (phase === "done" && view.scan) this._landed(view, clipped, hi, fs);
  }
  _landed(view, lo, hi, fs) {
    const s = view.scan, P = (p) => this.P(p), pk = view.pucks || {};
    const em = s.echo_map;
    if (em && em.values?.length === em.nx * em.ny) {
      const g = el("g", { class: "sc-map" });
      for (let j = 0; j < em.ny; j++) for (let i = 0; i < em.nx; i++) {
        const v = em.values[j * em.nx + i];
        const x = em.origin[0] + i * em.cell_m, y = em.origin[1] + (j + 1) * em.cell_m, p = P([x, y]);
        g.appendChild(el("rect", { x: p[0], y: p[1], width: em.cell_m, height: em.cell_m, class: "cell", "fill-opacity": v < 0.04 ? 0 : (0.12 + 0.8 * v).toFixed(2) }));
      }
      g.appendChild(el("title", {}, `Coarse echo map: ${em.cell_m} m cells, returns that change over time (breathing or moving). Not a picture of the room.`));
      lo.appendChild(g);
    }
    const v = (s.victims || []).find((q) => q.x != null);
    if (v) {
      for (const l of s.links || []) {
        if (l.used === false || !pk[l.a] || !pk[l.b]) continue;
        const L = Math.hypot(v.x - pk[l.a][0], v.y - pk[l.a][1]) + Math.hypot(v.x - pk[l.b][0], v.y - pk[l.b][1]);
        const e = bistatic(pk[l.a], pk[l.b], L), m = P([e.cx, e.cy]);
        lo.appendChild(el("ellipse", { cx: 0, cy: 0, rx: e.a, ry: e.b, class: "sc-fix", transform: `translate(${m[0]} ${m[1]}) rotate(${(-e.ang * 180 / Math.PI).toFixed(1)})` }));
      }
      const e = sigmaEllipse(v), p = P([v.x, v.y]), rot = `translate(${p[0]} ${p[1]}) rotate(${(-e.ang * 180 / Math.PI).toFixed(1)})`;
      const g = el("g", { class: "sc-victim", "data-pick": v.id });
      const per = v.state === "breathing" && v.breathing_bpm ? 60 / v.breathing_bpm : (v.state === "moving" || v.moving) ? 0.6 : 0;
      const e2 = el("ellipse", { cx: 0, cy: 0, rx: 2 * e.a, ry: 2 * e.b, class: "s2", transform: rot });
      const e1 = el("ellipse", { cx: 0, cy: 0, rx: e.a, ry: e.b, class: "s1", transform: rot });
      const core = el("circle", { cx: p[0], cy: p[1], r: 0.24, class: "core" });
      if (per && !REDUCED) {
        core.appendChild(el("animate", { attributeName: "r", values: "0.2;0.34;0.2", keyTimes: "0;0.5;1", calcMode: "spline", keySplines: ".45 0 .55 1;.45 0 .55 1", dur: per.toFixed(2) + "s", repeatCount: "indefinite" }));
        e1.appendChild(el("animate", { attributeName: "fill-opacity", values: "0.18;0.4;0.18", dur: per.toFixed(2) + "s", repeatCount: "indefinite" }));
      }
      g.append(e2, e1, core, el("text", { x: p[0], y: p[1] - 2 * e.b - 0.5, class: "sc-vlab", style: `font-size:${fs * 1.25}px` }, `${v.id}: ${victimState(v)}`),
        el("text", { x: p[0], y: p[1] - 2 * e.b - 0.5 + 0.75, class: "sc-vsub", style: `font-size:${fs * 0.9}px` }, `within ${(v.sigma_m ?? e.a).toFixed(1)} m, not tagged`), el("title", {}, `${v.id}: ${v.text || victimState(v)}`));
      g.addEventListener("click", () => this.opts.onPick?.(v.id));
      hi.appendChild(g);
    }
    for (const m of s.mismatches || []) {
      const w = m.where || {};
      let at = w.at;
      const g = el("g", { class: "sc-flag" });
      if (w.kind === "segment" && w.a && w.b) { const a = P(w.a), b = P(w.b); g.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: "seg" })); at = [(w.a[0] + w.b[0]) / 2, (w.a[1] + w.b[1]) / 2]; }
      if (!at) continue;
      const p = P(at);
      if (w.radius_m) g.appendChild(el("circle", { cx: p[0], cy: p[1], r: w.radius_m, class: "area" }));
      g.append(el("line", { x1: p[0], y1: p[1], x2: p[0], y2: p[1] - 1.5, class: "pole" }), el("path", { d: `M${p[0]} ${p[1] - 1.5} L${p[0] + 0.9} ${p[1] - 1.22} L${p[0]} ${p[1] - 0.94} Z`, class: "pennant" }),
        el("text", { x: p[0] + 0.25, y: p[1] - 1.75, style: `font-size:${fs}px` }, `${m.id}: differs from the twin`), el("title", {}, `${m.id} (${m.kind.replace("_", " ")}, confidence ${m.confidence}): ${m.text}`));
      g.addEventListener("click", () => this.opts.onPick?.(m.id));
      hi.appendChild(g);
    }
  }
  // ---------------------------------------------------------------- the phones (Beacon): what each geometry supports
  _arcPath(cx, cy, r, a0, a1) {
    if (a1 - a0 >= 359.9) { const c = this.P([cx, cy]); return `M${c[0] + r} ${c[1]} A${r} ${r} 0 1 0 ${c[0] - r} ${c[1]} A${r} ${r} 0 1 0 ${c[0] + r} ${c[1]}`; }
    const pt = (a) => this.P([cx + r * Math.cos(a * Math.PI / 180), cy + r * Math.sin(a * Math.PI / 180)]);
    const s = pt(a0), e = pt(a1);
    return `M${s[0].toFixed(3)} ${s[1].toFixed(3)} A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 0 ${e[0].toFixed(3)} ${e[1].toFixed(3)}`;
  }
  setBeacons(view, selected) {
    this._bArgs = [view, selected];
    const key = view ? view.key + "|" + selected + "|" + Object.keys(view.collapses).join(",") : null;
    if (key === this._beaconKey) return;
    this._beaconKey = key;
    const lo = this.g.beaconlo, hi = this.g.beaconhi;
    lo.replaceChildren(); hi.replaceChildren();
    // the pucks' room is named on the selection card and in the story; its own labels would sit under the phones
    if (!this._scoutKey) for (const t of this.g.labels.querySelectorAll("[data-room]")) t.style.display = view?.b?.pucks?.length && t.dataset.room === view.b.room ? "none" : "";
    if (!view) return;
    const b = view.b, P = (p) => this.P(p), z = this.z, fs = 0.62 * z;
    const tip = (g, text) => g.appendChild(el("title", {}, text));
    const pts = (poly) => poly.map((q) => P(q).join(",")).join(" ");
    for (const c of (b.presence || []).filter((q) => (q.count || 0) > 0 || (q.count_range?.[1] || 0) > 0)) {
      const r = this.rooms[c.room]; if (!r) continue;
      const p = P(chipSpot(r, b));
      const g = el("g", { class: "bc-presence" });
      const w = 3.3 * z, y = p[1] - 0.1, n = c.count_range ? (c.count_range[0] === c.count_range[1] ? c.count_range[0] : `${c.count_range[0]} to ${c.count_range[1]}`) : c.count;
      g.append(el("rect", { x: p[0] - w / 2, y: y - 0.5 * z, width: w, height: 1.16 * z, rx: 0.22 * z }),
        el("text", { x: p[0], y: y, class: "n" }, `${n} ${n === 1 ? "device" : "devices"} heard`),
        el("text", { x: p[0], y: y + 0.45 * z, class: "s" }, "devices, not people"));
      tip(g, c.text || ""); hi.appendChild(g);
    }
    const pucks = Object.fromEntries((b.pucks || []).map((pk) => [pk.id, pk]));
    const obstacles = [];          // boxes (SVG units) the phone tags keep clear of: pucks, ruled-out ghosts, the phones' own marks
    this._puckPts = (b.pucks || []).map((pk) => P([pk.x, pk.y]));
    for (const pk of b.pucks || []) {
      const p = P([pk.x, pk.y]), origin = puckOrigin(pk, view), placed = origin === "hand" || origin === "baseline" || origin === "layout";
      const g = el("g", { class: "bc-puck" });
      if (placed) g.append(el("rect", { x: p[0] - 0.3, y: p[1] - 0.3, width: 0.6, height: 0.6, class: "body" }));
      else g.append(el("circle", { cx: p[0], cy: p[1], r: 0.42, class: "halo" }), el("circle", { cx: p[0], cy: p[1], r: 0.24, class: "body" }));
      g.append(el("text", { x: p[0] + 0.45, y: p[1] + 0.16 * z, style: `font-size:${fs * 0.85}px;text-anchor:start` }, pk.id));
      if (origin) g.append(el("text", { x: p[0] + 0.45, y: p[1] + 0.68 * z, class: "by", "text-anchor": "start" }, ORIGIN_SHORT[origin] || origin));
      obstacles.push([p[0] - 0.45, p[1] - 0.6 * Math.max(z, 0.6), p[0] + 0.5 + (origin ? 2.4 : 1) * z, p[1] + (origin ? 0.85 : 0.45) * Math.max(z, 0.6)]);
      tip(g, `Puck ${pk.id} (${(pk.radios || []).join(" and ") || "radio"})${origin === "hand" ? ": position tape-measured and placed by hand, not located by the system" : origin === "baseline" ? ": direction placed by hand, distance from the spacing the pucks measured" : origin === "sim" ? ": simulated position" : ""}`);
      hi.appendChild(g);
    }
    const labels = [], mirrors = new Set();
    for (const ph of b.phones || []) {
      const st = personStatus(ph.report || {});
      const g = el("g", { class: `bc-phone ${ph.geometry}${ph.stale ? " stale" : ""}${ph.id === selected ? " sel" : ""}`, "data-pick": ph.id, tabindex: 0, role: "button",
        "aria-label": `${ph.id}, ${st.word}${ph.room_label ? ", " + ph.room_label : ""}` });
      g.addEventListener("click", () => this.opts.onPick?.(ph.id));
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.opts.onPick?.(ph.id); } });
      // the mirror line: through the two pucks; the phone's two places are mirror images across it
      const pair = mirrorPair(ph, pucks), pk2 = pair && pair.map((q) => q.id).sort().join("-");
      if (pair && !mirrors.has(pk2)) {
        mirrors.add(pk2);
        const [a, c] = pair, dx = c.x - a.x, dy = c.y - a.y, L = Math.hypot(dx, dy) || 1, ext = 30;
        const p1 = P([a.x - dx / L * ext, a.y - dy / L * ext]), p2 = P([c.x + dx / L * ext, c.y + dy / L * ext]);
        lo.appendChild(el("line", { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1], class: "mirror-line" }));
        const m = P([c.x + dx / L * 2.4 * z, c.y + dy / L * 2.4 * z]);
        hi.appendChild(el("text", { x: m[0], y: m[1] - 0.3 * z, class: "mirror-l", "text-anchor": "middle", style: `font-size:${fs * 0.55}px` }, `mirror line ${a.id} to ${c.id}`));
        const mw = 18 * fs * 0.55 * 0.5; obstacles.push([m[0] - mw / 2, m[1] - 0.3 * z - fs * 0.55, m[0] + mw / 2, m[1] - 0.2 * z]);
      }
      if (ph.geometry === "candidates" || ph.geometry === "fix")
        for (const rg of ph.ranges || []) { const pk = pucks[rg.puck]; if (!pk || rg.kind !== "uwb" || !rg.d_m) continue; g.appendChild(el("path", { d: this._arcPath(pk.x, pk.y, rg.d_m, 0, 360), class: "ring-x" })); }
      let lab = null, anchor = null;
      if (ph.geometry === "fix" && ph.x != null) {
        const e = sigmaEllipse(ph), p = P([ph.x, ph.y]), rot = `translate(${p[0]} ${p[1]}) rotate(${(-e.ang * 180 / Math.PI).toFixed(1)})`;
        g.append(el("ellipse", { cx: 0, cy: 0, rx: Math.max(0.25, 2 * e.a), ry: Math.max(0.25, 2 * e.b), class: "s2", transform: rot }), el("ellipse", { cx: 0, cy: 0, rx: Math.max(0.18, e.a), ry: Math.max(0.18, e.b), class: "s1", transform: rot }), el("circle", { cx: p[0], cy: p[1], r: 0.2, class: "core" }));
        const c = view.collapses[ph.id];
        for (const q of ph.pruned || []) this._ghost(g, q, c && c.from?.geometry === "candidates");
        if (c && !REDUCED) {
          const from = Math.max(2.5, c.from?.r || 3.5), to = Math.max(0.2, ph.sigma_m || 0.3);
          for (const [k, sw] of [[0, 0.2], [0.25, 0.09]]) {
            const ring = el("circle", { cx: p[0], cy: p[1], r: from, class: "collapse", "stroke-width": sw });
            ring.appendChild(el("animate", { attributeName: "r", values: `${from};${to};${to}`, keyTimes: "0;0.4;1", dur: "4s", begin: "indefinite", fill: "freeze", calcMode: "spline", keySplines: ".2 0 .1 1;0 0 1 1", "data-delay": k }));
            ring.appendChild(el("animate", { attributeName: "opacity", values: "1;1;0", keyTimes: "0;0.45;1", dur: "4s", begin: "indefinite", fill: "freeze", "data-delay": k }));
            g.appendChild(ring);
          }
        }
        if (c) {
          const t = el("text", { x: p[0], y: p[1] + 1.25, class: "flash", style: `font-size:${fs * 0.85}px` }, `${c.from?.geometry === "candidates" ? "two places" : c.from?.geometry || "ring"} to a fix, within ${(ph.sigma_m ?? 0).toFixed(1)} m`);
          if (!REDUCED) t.appendChild(el("animate", { attributeName: "opacity", values: "1;1;0", keyTimes: "0;0.75;1", dur: "5s", begin: "indefinite", fill: "freeze" }));
          g.appendChild(t);
        }
        const rr = Math.max(0.6, 2 * Math.max(e.a, e.b));
        lab = [p[0], p[1] - Math.max(0.6, 2 * e.b) - 0.15]; anchor = [p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr];
        for (const q of ph.pruned || []) { const gq = P([q.x, q.y]); obstacles.push([gq[0] - 0.8, gq[1] - 0.8, gq[0] + 0.8, gq[1] + 0.8], [gq[0] - 2.2 * z, gq[1] + 0.85 * z, gq[0] + 2.2 * z, gq[1] + 1.95 * z], [gq[0] - 2.2 * z, gq[1] - 0.85 - 1.1 * z, gq[0] + 2.2 * z, gq[1] - 0.75]); }
      } else if (ph.geometry === "candidates" && ph.candidates?.length) {
        const cs = ph.candidates.map((c) => P([c.x, c.y]));
        for (let i = 1; i < cs.length; i++) g.appendChild(el("line", { x1: cs[0][0], y1: cs[0][1], x2: cs[i][0], y2: cs[i][1], class: "pair" }));
        ph.candidates.forEach((c, i) => {
          const p = cs[i];
          g.append(el("circle", { cx: p[0], cy: p[1], r: Math.max(0.45, c.sigma_m || 0.3), class: "cand", "fill-opacity": (0.12 + 0.45 * c.weight).toFixed(2) }), el("circle", { cx: p[0], cy: p[1], r: 0.16, class: "core" }),
            el("text", { x: p[0] + 0.5, y: p[1] + 0.18, class: "w" }, `${Math.round(c.weight * 100)}%`));
        });
        for (const q of ph.pruned || []) this._ghost(g, q);
        if (cs.length > 1) {
          const m = [(cs[0][0] + cs[1][0]) / 2, (cs[0][1] + cs[1][1]) / 2], t = `one of ${cs.length} places`, tw = t.length * fs * 0.85 * 0.5;
          g.appendChild(el("text", { x: m[0], y: m[1] - 0.25, class: "undecided", style: `font-size:${fs * 0.85}px` }, t));
          obstacles.push([m[0] - tw / 2, m[1] - 0.25 - fs * 0.85, m[0] + tw / 2, m[1] - 0.05]);
        }
        const top = cs.reduce((a, q) => (q[1] < a[1] ? q : a), cs[0]);
        lab = [top[0], top[1] - 0.6]; anchor = [top[0] - 0.6, top[1] - 0.6, top[0] + 0.6, top[1] + 0.6];
        for (const q of cs) obstacles.push([q[0] - 0.6, q[1] - 0.6, q[0] + 1.4, q[1] + 0.6]);
        for (const q of ph.pruned || []) { const gq = P([q.x, q.y]); obstacles.push([gq[0] - 0.8, gq[1] - 0.8, gq[0] + 0.8, gq[1] + 0.8], [gq[0] - 2.2 * z, gq[1] + 0.85 * z, gq[0] + 2.2 * z, gq[1] + 1.95 * z], [gq[0] - 2.2 * z, gq[1] - 0.85 - 1.1 * z, gq[0] + 2.2 * z, gq[1] - 0.75]); }
      } else if (ph.geometry === "ring" && ph.ring) {
        const R = ph.ring, ble = R.kind === "ble", w = ble ? Math.max(0.5, 2 * (R.sigma_m || 1)) : Math.max(0.1, 2 * (R.sigma_m || 0.05));
        g.appendChild(el("path", { d: this._arcPath(R.cx, R.cy, R.r_m, 0, 360), class: "ring-off", "stroke-width": ble ? 0.05 : 0.04 }));
        for (const [a0, a1] of R.arcs || [[0, 360]]) g.appendChild(el("path", { d: this._arcPath(R.cx, R.cy, R.r_m, a0, a1), class: ble ? "ring ble" : "ring uwb", "stroke-width": w }));
        const [a0, a1] = (R.arcs || [[0, 360]])[0], am = ((a0 + a1) / 2) * Math.PI / 180;
        const p = P([R.cx + (R.r_m + (ble ? R.sigma_m : 0.2) + 0.4) * Math.cos(am), R.cy + (R.r_m + (ble ? R.sigma_m : 0.2) + 0.4) * Math.sin(am)]);
        lab = [p[0], p[1]];
      } else if (ph.geometry === "zone" && ph.zone) {
        const poly = ph.zone.polygon || this.rooms[ph.zone.room]?.polygon;
        if (poly) {
          lo.appendChild(el("polygon", { points: pts(poly), class: "bc-zone" }));
          const c = this.centre(poly), p = P(c);
          lab = [p[0], p[1] - 1.9];
        }
      }
      const head = `${ph.id} ${st.word.toLowerCase()}`;
      if (lab) { if (anchor) obstacles.push(anchor); labels.push({ g, st, x: lab[0], y: lab[1], ax: lab[0], ay: lab[1] + 0.35, anchor: anchor || [lab[0] - 0.3, lab[1] - 0.3, lab[0] + 0.3, lab[1] + 0.3], head, w: head.length * fs * 0.42 + fs * 1.5, h: fs * 1.3 }); }
      tip(g, `${ph.text || head}${ph.stale ? " (not heard recently)" : ""}`);
      if (g.childNodes.length > 1) { hi.appendChild(g); if (!REDUCED) for (const a of g.querySelectorAll('[begin="indefinite"]')) a.beginElementAt?.(Number(a.dataset.delay || 0)); }
    }
    // tags: try spots around each phone's mark (above, below, right, left), clear of pucks, ghosts, other phones and tags
    const placed = [], hitBox = (a, b) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
    for (const L of labels.sort((a, b) => a.y - b.y)) {
      const A = L.anchor, w = L.w, h = L.h, cx = (A[0] + A[2]) / 2, cy = (A[1] + A[3]) / 2, gap = 0.18;
      // [left, top, push x, push y]: each spot moves outward on later tries
      const spots = [[cx - w / 2, A[1] - gap - h, 0, -1], [cx - w / 2, A[3] + gap, 0, 1], [A[2] + gap, cy - h / 2, 1, 0], [A[0] - gap - w, cy - h / 2, -1, 0],
        [A[2] + gap, A[1] - gap - h, 1, -1], [A[0] - gap - w, A[1] - gap - h, -1, -1], [A[2] + gap, A[3] + gap, 1, 1], [A[0] - gap - w, A[3] + gap, -1, 1]];
      const others = obstacles.filter((o) => o !== A);
      let best = null;
      for (let ring = 0; ring < 4 && !best; ring++) for (const [x0, y0, dx, dy] of spots) {
        const bx0 = x0 + dx * ring * 0.9, by0 = y0 + dy * ring * 0.7, box = [bx0, by0, bx0 + w, by0 + h];
        if (!others.some((o) => hitBox(box, o)) && !placed.some((q) => hitBox(box, q))) { best = box; break; }
      }
      if (!best) { best = [cx - w / 2, A[1] - gap - h, cx + w / 2, A[1] - gap]; while (placed.some((q) => hitBox(best, q))) { best[1] -= h + 0.1; best[3] -= h + 0.1; } }
      placed.push(best);
      const [x0, y0] = best, bx = x0 + w / 2, by = y0 + h / 2;
      const near = [Math.max(A[0], Math.min(bx, A[2])), Math.max(A[1], Math.min(by, A[3]))];
      if (Math.hypot(bx - near[0], by - near[1]) > h * 1.4) L.g.appendChild(el("line", { x1: (A[0] + A[2]) / 2, y1: (A[1] + A[3]) / 2, x2: Math.max(x0, Math.min(near[0], x0 + w)), y2: Math.max(y0, Math.min(near[1], y0 + h)), class: "leader" }));
      const on = L.st.key === "trapped" ? " on-dark" : L.st.key === "injured" ? " on-caution" : "", ic = fs * 0.42, ix = x0 + fs * 0.62, yc = y0 + h / 2;
      L.g.append(el("rect", { x: x0, y: y0, width: w, height: h, rx: 0.2, class: `tag ps-${L.st.key}` }),
        el("path", { d: SHAPE_PATH[L.st.shape], transform: `translate(${ix} ${yc}) scale(${ic})`, class: `tagi ${L.st.shape}${on}` }),
        el("text", { x: ix + fs * 0.62, y: yc + 0.02, class: `tagt${on}`, style: `font-size:${fs}px` }, L.head));
    }
  }
  _ghost(g, q, strike) {
    const p = this.P([q.x, q.y]), d = 0.55, z = this.z;
    // the caption goes under the mark, or over it when a puck (and its label) sits just below
    const up = (this._puckPts || []).some((k) => Math.abs(k[0] - p[0]) < 2.6 * Math.max(z, 0.6) && k[1] > p[1] && k[1] - p[1] < 0.75 + 1.6 * z);
    const y1 = up ? p[1] - 0.8 - 0.55 * z : p[1] + 0.75 + 0.5 * z, y2 = up ? p[1] - 0.8 - 0.05 * z : p[1] + 0.75 + 1.0 * z;
    const x = el("g", { class: `ghost${strike ? " strike" : ""}` });
    x.append(el("circle", { cx: p[0], cy: p[1], r: 0.7 }), el("line", { x1: p[0] - d, y1: p[1] - d, x2: p[0] + d, y2: p[1] + d }), el("line", { x1: p[0] - d, y1: p[1] + d, x2: p[0] + d, y2: p[1] - d }),
      el("text", { x: p[0], y: y1, class: "why" }, "ruled out"), el("text", { x: p[0], y: y2, class: "why2" }, q.reason.length > 34 ? q.reason.slice(0, 32).trim() + "…" : q.reason), el("title", {}, `Ruled out: ${q.reason}`));
    if (strike && !REDUCED) {
      // the resolution moment: the twin deletes the mirror place
      const big = el("g", { class: "strike-x" });
      big.append(el("line", { x1: p[0] - 1.1, y1: p[1] - 1.1, x2: p[0] + 1.1, y2: p[1] + 1.1 }), el("line", { x1: p[0] - 1.1, y1: p[1] + 1.1, x2: p[0] + 1.1, y2: p[1] - 1.1 }));
      big.appendChild(el("animate", { attributeName: "opacity", values: "0;1;1;0", keyTimes: "0;0.1;0.6;1", dur: "4.5s", begin: "indefinite", fill: "freeze" }));
      const was = el("circle", { cx: p[0], cy: p[1], r: 0.3, class: "was" });
      was.appendChild(el("animate", { attributeName: "opacity", values: "1;1;0", keyTimes: "0;0.3;1", dur: "2.5s", begin: "indefinite", fill: "freeze" }));
      x.append(was, big);
    }
    g.appendChild(x);
  }
  // placing pucks by hand: the pucks, the circle the measured spacing allows, and a preview of the next one
  setPlacing(pl) {
    this.placing = pl; this.svg.classList.toggle("placing", !!pl);
    this.g.place.replaceChildren();
    if (!pl) return;
    const P = (p) => this.P(p);
    if (pl.ring) {
      const c = P([pl.ring.x, pl.ring.y]);
      this.g.place.appendChild(el("circle", { cx: c[0], cy: c[1], r: pl.ring.r, class: "place-ring" }));
    }
    if (pl.preview) {
      const a = pl.preview.from && P(pl.preview.from), b = P([pl.preview.x, pl.preview.y]);
      if (a) {
        this.g.place.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: "place-line" }));
        if (pl.preview.d) this.g.place.appendChild(el("text", { x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2 - 0.35 * this.z, class: "place-d" }, `${pl.preview.d.toFixed(2)} m`));
      }
      this.g.place.appendChild(el("rect", { x: b[0] - 0.32, y: b[1] - 0.32, width: 0.64, height: 0.64, class: "place-ghost" }));
    }
    for (let i = 1; i < pl.pucks.length; i++) {
      const a = P([pl.pucks[0].x, pl.pucks[0].y]), b = P([pl.pucks[i].x, pl.pucks[i].y]);
      if (i === 1) this.g.place.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: "place-line" }));
    }
    for (const pk of pl.pucks) {
      const p = P([pk.x, pk.y]), g = el("g", { class: "place-pk", "data-puck": pk.id });
      g.append(el("circle", { cx: p[0], cy: p[1], r: 0.9, class: "grab" }), el("rect", { x: p[0] - 0.32, y: p[1] - 0.32, width: 0.64, height: 0.64, class: "body" }),
        el("text", { x: p[0] + 0.5, y: p[1] - 0.12 * this.z, class: "id", style: `font-size:${(0.62 * this.z).toFixed(3)}px` }, pk.id),
        el("text", { x: p[0] + 0.5, y: p[1] + 0.42 * this.z, class: "sub" }, pk.by === "baseline" ? "measured spacing" : "by hand"));
      this.g.place.appendChild(g);
    }
  }
  // the outside: the aerial image under the plan, through the twin's georeference (x, y -> lat, lon -> pixel)
  setOutside(im, geo) {
    this.g.outside.replaceChildren();
    if (!im) return;
    const g0 = this.twin.georef, b = g0.bearing_deg * Math.PI / 180, lat0 = g0.origin.lat, lon0 = g0.origin.lon;
    const toB = (lat, lon) => { const n = (lat - lat0) * 111320, e = (lon - lon0) * 111320 * Math.cos(lat0 * Math.PI / 180); return [e * Math.sin(b) + n * Math.cos(b), -e * Math.cos(b) + n * Math.sin(b)]; };
    const [w, s, e, n] = im.bbox, tl = this.P(toB(n, w)), tr = this.P(toB(n, e)), bl = this.P(toB(s, w));
    const W = Math.hypot(tr[0] - tl[0], tr[1] - tl[1]), H = Math.hypot(bl[0] - tl[0], bl[1] - tl[1]), ang = Math.atan2(tr[1] - tl[1], tr[0] - tl[0]) * 180 / Math.PI;
    this.g.outside.appendChild(el("image", { href: im.url || "/" + im.path, x: 0, y: 0, width: W, height: H, preserveAspectRatio: "none", transform: `translate(${tl[0]} ${tl[1]}) rotate(${ang})`, class: "outside-img" }));
    this.g.outside.appendChild(el("rect", { x: -400, y: -400, width: 900, height: 900, class: "outside-dim" }));
    const site = this.site;
    for (const h of site.context?.hydrants || []) { const p = this.P(toB(h.lat, h.lon)); const c = el("circle", { cx: p[0], cy: p[1], r: 0.7, class: "o-hyd" }); c.appendChild(el("title", {}, `Hydrant ${h.id}: ${h.distance_m} m. ${h.source}`)); this.g.outside.appendChild(c); }
    for (const a of site.context?.apparatus_positions || []) { const p = this.P(toB(a.lat, a.lon)); this.g.outside.append(el("rect", { x: p[0] - 1.6, y: p[1] - 0.9, width: 3.2, height: 1.8, rx: 0.3, class: "o-eng" }), el("text", { x: p[0] + 2.1, y: p[1] + 0.5, class: "o-lab" }, a.label)); }
    for (const en of site.context?.entrances || []) { const p = this.P(toB(en.lat, en.lon)); this.g.outside.append(el("path", { d: `M${p[0] - 0.9} ${p[1] + 0.6} L${p[0]} ${p[1] - 0.8} L${p[0] + 0.9} ${p[1] + 0.6} Z`, class: "o-ent" })); }
    for (const [k, sd] of Object.entries(site.sides || {})) if (sd.street) {
      const t = this.g.orient.querySelector(`.side title`); // side letters are drawn by _orientation; the street name sits beside its letter
      const letter = [...this.g.orient.querySelectorAll(".side")].find((q) => q.querySelector("text")?.textContent === k);
      if (letter) {
        // the street name sits beside its letter, on the side away from the view's edge
        const c = letter.querySelector("circle"), cx = Number(c.getAttribute("cx")), right = cx > this.fx * 0.8;
        this.g.orient.appendChild(el("text", { x: cx + (right ? -1.4 : 1.4) * this.k, y: Number(c.getAttribute("cy")) + (right ? 1.6 * this.k : 0.05), class: "street", "text-anchor": right ? "end" : "start" }, sd.street));
      }
    }
  }
  update(st, selected) {
    this._lastSt = st; this._lastSel = selected;
    // anchors
    if (this._anchorKey !== JSON.stringify(st.anchors)) {
      this._anchorKey = JSON.stringify(st.anchors);
      this.g.anchors.replaceChildren();
      for (const a of st.anchors || []) {
        const p = this.P([a.x, a.y]);
        const k = this.k;
        this.g.anchors.appendChild(el("path", { d: `M${p[0]} ${p[1] - 0.9 * k} L${p[0] + 0.8 * k} ${p[1] + 0.6 * k} L${p[0] - 0.8 * k} ${p[1] + 0.6 * k} Z`, class: "anchor" }));
        this.g.anchors.appendChild(el("text", { x: p[0], y: p[1] + 1.9 * k, class: "anchor-l" }, a.id));
      }
    }
    // hazards
    const hk = JSON.stringify(st.hazards.map((h) => h.id));
    if (hk !== this._hazKey) {
      this._hazKey = hk;
      this.g.hazards.replaceChildren();
      for (const h of st.hazards) {
        const g = h.geometry || {};
        let node;
        if (g.kind === "room") node = el("polygon", { points: g.polygon.map((q) => this.P(q).join(",")).join(" "), class: "hazard" });
        else if (g.kind === "edge") {
          const a = this.P(g.polyline[0]), b = this.P(g.polyline[1]), m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
          node = el("g");
          node.appendChild(el("circle", { cx: m[0], cy: m[1], r: 1.5, class: "hazard" }));
          node.appendChild(el("line", { x1: m[0] - 0.8, y1: m[1] - 0.8, x2: m[0] + 0.8, y2: m[1] + 0.8, class: "hazard-x" }));
          node.appendChild(el("line", { x1: m[0] - 0.8, y1: m[1] + 0.8, x2: m[0] + 0.8, y2: m[1] - 0.8, class: "hazard-x" }));
        } else if (g.at) { const p = this.P(g.at); node = el("circle", { cx: p[0], cy: p[1], r: 1.1, class: "hazard" }); }
        if (node) { node.appendChild(el("title", {}, `${h.label}: ${h.kind}${h.note ? " — " + h.note : ""}`)); this.g.hazards.appendChild(node); }
      }
    }
    // routes and units
    for (const u of st.units) {
      const color = this.opts.color[u.id] || "currentColor";
      let r = this.routeEls[u.id];
      if (!r) { r = this.routeEls[u.id] = el("polyline", { class: "route", stroke: color }); this.g.routes.appendChild(r); }
      if (u.route && !u.route.done) { r.setAttribute("points", u.route.polyline.map((q) => this.P(q).join(",")).join(" ")); r.classList.toggle("invalid", !u.route.valid); r.style.display = ""; }
      else r.style.display = "none";
      let g = this.unitEls[u.id];
      if (!g) {
        g = this.unitEls[u.id] = el("g", { class: "unit" });
        g.sig = el("circle", { class: "unit-sig", fill: color }); g.dot = el("circle", { class: "unit-dot", r: 0.95, fill: color }); g.lab = el("text", { class: "unit-l", fill: color }, u.id);
        g.append(g.sig, g.dot, g.lab);
        g.dot.addEventListener("pointerdown", (ev) => { this.opts.onSelect(u.id); if (this.opts.canDrag()) { this.dragging = u.id; ev.preventDefault(); } });
        this.g.units.appendChild(g);
      }
      if (u.x == null) { g.style.display = "none"; continue; }
      g.style.display = "";
      const p = this.P([u.x, u.y]);
      g.dot.setAttribute("cx", p[0]); g.dot.setAttribute("cy", p[1]); g.dot.setAttribute("r", Math.max(0.28, 0.95 * this.k * this.z));
      g.sig.setAttribute("cx", p[0]); g.sig.setAttribute("cy", p[1]); g.sig.setAttribute("r", Math.max(1.0 * this.k, u.sigma_m || 0));
      g.lab.setAttribute("x", p[0]); g.lab.setAttribute("y", p[1] - Math.max(0.5, 1.5 * this.k * this.z));
      g.classList.toggle("mayday", u.status === "mayday");
      g.dot.classList.toggle("sel", u.id === selected);
    }
  }
}
