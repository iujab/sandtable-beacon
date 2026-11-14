// Scout mode on the board: the state of a puck scan, driven by the server's `scout` progress messages and
// the `scan` object (docs/SCOUT_CONTRACT.md 2.3), or by the hand-made sample behind ?devscan=1. It produces
// one view model that the 3D twin (scout3d.js) and the plan (plan2d.js) draw, and the words the card, the
// "here" strip and the board use. Nothing here invents a measurement: what a phase does not know is not drawn.
export const PHASES = ["thrown", "ranging", "located", "registered", "scanning", "done"];
export const PHASE_WORD = { thrown: "Thrown", ranging: "Ranging", located: "Self-located", registered: "Registered", scanning: "Scanning", done: "Scan landed", failed: "Failed" };
export const RUNG_WORD = {
  "cir-radar": ["Radar", "echoes between pucks; finds a person who breathes or moves"],
  "link-shadow": ["Link shadow", "a body blocking puck-to-puck links: presence and a rough place only"],
  "tags-only": ["Tags only", "crews' own tags; cannot see anyone without a tag"],
};
export const METHOD_WORD = { "twin-echo-match": "wall echoes matched to the twin", "door-prior": "placed from the door it was thrown through", "box-fit": "no twin: a coarse box fitted to echoes" };
export const VICTIM_COLOR = "#ff4fa3", PUCK_COLOR = "#ffe3a3", FLAG_COLOR = "#ff9f0a";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function victimState(v) {
  if (!v) return "";
  if (v.state === "breathing" && v.breathing_bpm) return `breathing ${Math.round(v.breathing_bpm)}/min`;
  if (v.state === "moving" || v.moving) return "moving";
  if (v.state === "breathing") return "breathing";
  return "no movement or breath detected";
}
// 1-sigma ellipse from cov [sxx, sxy, syy] (or a circle of sigma_m): semi-axes and the angle of the first axis.
export function sigmaEllipse(v) {
  if (Array.isArray(v.cov) && v.cov.length === 3) {
    const [a, b, c] = v.cov, m = (a + c) / 2, d = Math.sqrt(((a - c) / 2) ** 2 + b * b);
    return { a: Math.sqrt(Math.max(m + d, 1e-4)), b: Math.sqrt(Math.max(m - d, 1e-4)), ang: 0.5 * Math.atan2(2 * b, a - c) };
  }
  const s = v.sigma_m || 0.5;
  return { a: s, b: s, ang: 0 };
}
// Bistatic geometry: points whose path tx -> p -> rx has length L form an ellipse with the pucks as foci.
export function bistatic(f1, f2, L) {
  const c = Math.hypot(f2[0] - f1[0], f2[1] - f1[1]) / 2, a = Math.max(L / 2, c + 1e-3);
  return { cx: (f1[0] + f2[0]) / 2, cy: (f1[1] + f2[1]) / 2, a, b: Math.sqrt(a * a - c * c), ang: Math.atan2(f2[1] - f1[1], f2[0] - f1[0]) };
}
const centroid = (pts) => [pts.reduce((s, p) => s + p[0], 0) / pts.length, pts.reduce((s, p) => s + p[1], 0) / pts.length];
function parsePucks(list) {
  if (!Array.isArray(list) || !list.length) return null;
  const ids = [], xy = {}, rel = {};
  for (const p of list) {
    if (typeof p === "string") { ids.push(p); continue; }
    if (!p || !p.id) continue;
    ids.push(p.id);
    if (Array.isArray(p.rel)) rel[p.id] = p.rel;
    else if (p.x_rel != null) rel[p.id] = [p.x_rel, p.y_rel];
    if (p.x != null && p.y != null) xy[p.id] = [p.x, p.y];
  }
  return { ids, xy: Object.keys(xy).length ? xy : null, rel: Object.keys(rel).length ? rel : null };
}

export class ScoutModel {
  constructor(bundle, onChange) {
    this.twin = bundle.twin; this.onChange = onChange;
    this.dev = new URLSearchParams(location.search).has("devscan");
    this.supported = false; this.smoke = 0;
    this.reset(true);
  }
  reset(silent) {
    clearTimeout(this._devTimer);
    Object.assign(this, { vstatus: {}, regText: "", phase: null, text: "", roomId: null, ids: null, rel: null, pucks: null, scan: null, sample: false, phaseAt: 0, animate: false, devStep: -1 });
    if (!silent) this._emit();
  }
  get running() { return !!this.phase && !["done", "failed"].includes(this.phase); }
  get onScreen() { return !!this.phase; }
  _emit() { this.onChange?.(this); }

  onScout(m, live = true) {
    const ph = m.phase;
    if (!ph || !(PHASES.includes(ph) || ph === "failed")) return;
    if (ph === "thrown" && this.phase !== "thrown") { this.scan = null; this.rel = null; this.pucks = null; this.ids = null; }
    this.roomId = m.room || this.roomId || null;
    this.text = m.text || "";
    this.sample = !!m.sample || this.sample;
    const ps = parsePucks(m.pucks);
    if (ps) {
      this.ids = ps.ids;
      if (ps.rel) this.rel = ps.rel;
      const frame = m.frame || (ph === "located" || ph === "ranging" ? "rel" : "twin");
      if (ps.xy) { if (frame === "rel") this.rel = ps.xy; else this.pucks = ps.xy; }
    }
    if (ph === "registered") this.regText = this.text;
    if (ph !== this.phase) { this.phase = ph; this.phaseAt = performance.now(); this.animate = live; }
    this._emit();
  }
  onScan(scan, live = true) {
    if (!scan || scan.type !== "scan") return;
    this.scan = scan; this.roomId = scan.room; this.sample = !!scan.sample;
    this.ids = (scan.pucks || []).map((p) => p.id);
    this.pucks = Object.fromEntries((scan.pucks || []).map((p) => [p.id, [p.x, p.y]]));
    if (this.phase !== "done") { this.phase = "done"; this.phaseAt = performance.now(); this.animate = live; }
    this._emit();
  }
  // state.scout arrives at 8 Hz: only adopt a phase we have not heard about (a client that joined mid-run).
  onState(sc) {
    if (!sc) return false;
    this.supported = true;
    const prev = this.smoke; this.smoke = Number(sc.smoke) || 0;
    this.vstatus = Object.fromEntries((sc.victims || []).map((v) => [v.id, v]));
    if (!this.phase && !this.dev && PHASES.includes(sc.phase) && sc.phase !== "done") {
      this.phase = sc.phase; this.roomId = sc.room || null; this.phaseAt = performance.now() - 5000; this.animate = false; this._emit();
    }
    return Math.abs(prev - this.smoke) > 0.005;
  }

  room() {
    const t = this.twin;
    const r = t.rooms.find((q) => q.id === this.roomId);
    if (!r) return null;
    const poly = this.scan?.outline?.polygon || r.polygon;
    const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
    const c = centroid(r.polygon);
    const d = t.doors.find((q) => q.rooms[0] === r.id) || t.doors.find((q) => q.rooms.includes(r.id));
    let door = d ? d.at : c;
    const k = Math.hypot(c[0] - door[0], c[1] - door[1]) || 1;
    const inside = [door[0] + (c[0] - door[0]) / k * 0.45, door[1] + (c[1] - door[1]) / k * 0.45];
    return { id: r.id, label: r.label, suite: r.suite, polygon: r.polygon, outline: poly, outlineSource: this.scan?.outline?.source || "twin", centre: c, door, inside, bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] };
  }
  links() {
    if (this.scan?.links) return this.scan.links;
    const ids = this.ids || [], out = [];
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) out.push({ a: ids[i], b: ids[j], used: true });
    return out;
  }
  view() {
    if (!this.phase) return null;
    const room = this.room();
    if (!room) return null;
    const n = this.ids?.length || this.scan?.n_pucks || 5;
    const key = [this.phase, this.scan?.id || "", JSON.stringify(this.pucks || {}), JSON.stringify(this.rel || {}), room.id].join("|");
    return { key, phase: this.phase, phaseAt: this.phaseAt, animate: this.animate, room, n, ids: this.ids || Array.from({ length: n }, (_, i) => `P${i + 1}`),
      rel: this.rel, pucks: this.pucks, links: this.links(), scan: this.scan, sample: this.sample || this.dev, dev: this.dev, text: this.text };
  }
  victim(id) { return (this.scan?.victims || []).find((v) => v.id === id && v.x != null) || null; }
  mainVictim() { return (this.scan?.victims || []).find((v) => v.x != null) || null; }

  // ---------------------------------------------------------------- the hand-made sample (?devscan=1)
  async devLoad() {
    if (this._sampleScan) return this._sampleScan;
    const r = await fetch("/fixtures/scout/sample_scan.json");
    this._sampleScan = await r.json();
    return this._sampleScan;
  }
  async devPlay(auto = true) {
    const s = await this.devLoad();
    this.reset(true); this.devStep = -1; this._devAuto = auto;
    this.devNext(s);
  }
  async devNext(sIn) {
    const s = sIn || await this.devLoad();
    clearTimeout(this._devTimer);
    const step = ++this.devStep;
    const P = s.pucks.map((p) => ({ id: p.id, x: p.x, y: p.y }));
    const c = centroid(P.map((p) => [p.x, p.y])), ang = 2.3;
    // what self-location knows: the same shape, in an arbitrary rotation and mirror (no twin frame yet)
    const rel = P.map((p) => { const dx = -(p.x - c[0]), dy = p.y - c[1]; return { id: p.id, rel: [dx * Math.cos(ang) - dy * Math.sin(ang), dx * Math.sin(ang) + dy * Math.cos(ang)] }; });
    const msgs = [
      { phase: "thrown", text: "Engine 1 threw 5 pucks into Suite 310 from the door", pucks: P.map((p) => p.id) },
      { phase: "ranging", text: "Pucks ranging each other: 10 links", pucks: P.map((p) => p.id) },
      { phase: "located", text: "Self-located from the ranges: shape known, orientation not yet (rms 0.06 m)", pucks: rel },
      { phase: "registered", text: s.registration.text + ` (residual ${s.registration.residual_m} m)`, pucks: P },
      { phase: "scanning", text: "Scanning: 10 transmitter-receiver pairs listening for echoes", pucks: P },
    ];
    if (step < msgs.length) this.onScout({ type: "scout", room: s.room, sample: true, ...msgs[step] });
    else if (step === msgs.length) this.onScan(s);
    else return;
    if (this._devAuto && step < msgs.length) this._devTimer = setTimeout(() => this.devNext(), step === 4 ? 3200 : 2000);
  }
}

// ---------------------------------------------------------------- words for the card, the strip and the board
const CARDINAL = ["north", "east", "south", "west"];
export function wallDistances(twin, room, x, y) {
  const b = twin.georef.bearing_deg * Math.PI / 180, poly = room.polygon, c = centroid(poly), out = [];
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i], q = poly[(i + 1) % poly.length], dx = q[0] - p[0], dy = q[1] - p[1], L = Math.hypot(dx, dy);
    if (L < 0.3) continue;
    const t = Math.max(0, Math.min(1, ((x - p[0]) * dx + (y - p[1]) * dy) / (L * L)));
    const d = Math.hypot(x - (p[0] + t * dx), y - (p[1] + t * dy));
    let nx = dy / L, ny = -dx / L;
    const mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
    if ((mx + nx - c[0]) ** 2 + (my + ny - c[1]) ** 2 < (mx - c[0]) ** 2 + (my - c[1]) ** 2) { nx = -nx; ny = -ny; }
    const east = nx * Math.sin(b) - ny * Math.cos(b), north = nx * Math.cos(b) + ny * Math.sin(b);
    const brg = (Math.atan2(east, north) * 180 / Math.PI + 360) % 360;
    out.push({ d, wall: CARDINAL[Math.round(brg / 90) % 4] });
  }
  out.sort((a, b2) => a.d - b2.d);
  const seen = new Set(), two = [];
  for (const o of out) { if (seen.has(o.wall)) continue; seen.add(o.wall); two.push(o); if (two.length === 2) break; }
  return two.map((o) => `${o.d.toFixed(1)} m from the ${o.wall} wall`).join(", ");
}

export function regQuality(r) {
  if (!r) return "";
  if (!r.ok) return "not registered";
  const amb = r.ambiguity ?? 0;
  return `${METHOD_WORD[r.method] || r.method}; residual ${r.residual_m} m; ${amb < 0.5 ? "one clear fit" : amb < 0.85 ? "a second fit is close" : "two fits look alike"}`;
}

// The mesh graph in the card: an abstract ring until positions are known, then the constellation's real shape.
export function meshSvg(view, now) {
  const ids = view.ids, n = ids.length, W = 132, H = 86;
  let pos = {};
  const src = view.pucks || view.rel;
  if (src && ids.every((id) => src[id])) {
    const pts = ids.map((id) => src[id]), xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const s = Math.min((W - 26) / (Math.max(...xs) - Math.min(...xs) || 1), (H - 22) / (Math.max(...ys) - Math.min(...ys) || 1));
    const ox = (W - s * (Math.max(...xs) + Math.min(...xs))) / 2, oy = (H + s * (Math.max(...ys) + Math.min(...ys))) / 2;
    for (const id of ids) pos[id] = [ox + s * src[id][0], oy - s * src[id][1]];
  } else ids.forEach((id, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / n; pos[id] = [W / 2 + 32 * Math.cos(a), H / 2 + 32 * Math.sin(a)]; });
  const ranging = view.phase === "ranging" || view.phase === "thrown";
  const lit = view.phase === "thrown" ? 0 : ranging ? Math.min(view.links.length, Math.floor((now - view.phaseAt) / 160) + 1) : view.links.length;
  const lines = view.links.map((l, i) => { const a = pos[l.a], b = pos[l.b]; if (!a || !b) return "";
    return `<line x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}" class="mesh-l${i < lit ? " on" : ""}${l.used === false ? " unused" : ""}"/>`; }).join("");
  const dots = ids.map((id) => { const p = pos[id]; return `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4.2" class="mesh-p"/><text x="${p[0].toFixed(1)}" y="${(p[1] - 7).toFixed(1)}" class="mesh-t">${esc(id)}</text>`; }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="mesh" role="img" aria-label="Puck mesh: ${lit} of ${view.links.length} links ranging">${lines}${dots}</svg>`;
}
