// Beacon mode on the board: occupants located by their phones, relayed by the server from the Beacon module
// (feed contract: ../sandtable-beacon-victim-phones/docs/BEACON_FEED.md, `sandtable.beacons/1`). The model keeps the
// latest Beacons object, the board's accountability table and the link state, and notices the moment a phone's
// geometry improves to a fix. Honesty of the drawing: a ring is a ring, candidates are candidates, a point only for
// geometry "fix", cold devices are a count per room, never people.
// ?devbeacons=1 plays a hand-made sample story (marked SAMPLE everywhere) built from the twin, for development only.
export const PHONE_COLOR = "#ff4fa3";
export const STATUS_COLOR = { trapped: "#e0245e", injured: "#e8890c", sheltering: "#3f6fa8", ok: "#2fa872", unknown: "#5b6673" };
// where to put a room's device-count chip: the inside corner farthest from every phone and puck (deterministic)
export function chipSpot(room, b) {
  const xs = room.polygon.map((p) => p[0]), ys = room.polygon.map((p) => p[1]);
  const x0 = Math.min(...xs) + 2.4, x1 = Math.max(...xs) - 2.4, y0 = Math.min(...ys) + 0.9, y1 = Math.max(...ys) - 0.9;
  const pts = [];
  for (const p of b.pucks || []) pts.push([p.x, p.y]);
  for (const p of b.phones || []) { if (p.x != null) pts.push([p.x, p.y]); for (const c of p.candidates || []) pts.push([c.x, c.y]); for (const q of p.pruned || []) pts.push([q.x, q.y]); }
  const spots = [[x0, y0], [x1, y0], [x0, y1], [x1, y1]];
  if (!pts.length) return spots[0];
  return spots.map((s) => [s, Math.min(...pts.map((p) => Math.hypot(p[0] - s[0], p[1] - s[1])))]).sort((a, c) => c[1] - a[1])[0][0];
}
// the two-puck case in words, only from what the phone object says (shown on the phone's card)
export function mirrorCaption(p) {
  const uwb = (p.ranges || []).filter((r) => r.kind === "uwb");
  if (p.geometry === "fix" && (p.pruned || []).length && uwb.length === 2)
    return `2 pucks give 2 possible places. One is ${p.pruned[0].reason}: the twin rules it out.`;
  if (p.geometry === "candidates" && (p.candidates || []).length === 2) {
    const rs = [...new Set(p.candidates.map((c) => c.room_label || "an unknown room"))];
    return rs.length === 1 ? `2 possible places, both inside ${rs[0]}: cannot choose until a crew looks.` : `2 possible places, in ${rs.join(" and in ")}: cannot choose until a crew looks.`;
  }
  return "";
}
// the line through the two pucks a phone is ranged from: its two places are mirror images across it
export function mirrorPair(p, pucks) {
  const uwb = (p.ranges || []).filter((r) => r.kind === "uwb" && pucks[r.puck]);
  if (uwb.length !== 2 || !(p.geometry === "candidates" || (p.geometry === "fix" && (p.pruned || []).length))) return null;
  return [pucks[uwb[0].puck], pucks[uwb[1].puck]];
}
export const tagText = (p) => `${p.id} ${p.report?.status && p.report.status !== "unknown" ? p.report.status : "?"}`;
export const STATUS_WORD = { trapped: "trapped", injured: "injured", sheltering: "sheltering", ok: "ok", unknown: "no status" };
export const STATE_WORD = { "against-record": "record disagrees", "not-covered": "no puck there", "people-reported": "people reported", "devices-heard": "devices heard", silent: "silent", "vacant-on-record": "vacant on record", unscanned: "not scanned" };
export const MODES = {
  beacon: { word: "Beacon", line: "Occupants located by their phones: the opt-in app gives a ring or a fix; without it, only a count of devices." },
  scout: { word: "Scout", line: "Simulated radar: pucks listen for a person who breathes or moves. Simulation only: the boards lack the echo firmware." },
  crews: { word: "Crews", line: "Simulated tags on firefighters, and the mayday drill. Simulation only on these boards." },
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const ago = (s) => (s == null ? "" : s < 1.5 ? "just now" : s < 90 ? `${Math.round(s)} s ago` : `${Math.round(s / 60)} min ago`);

export function geomWords(p) {
  switch (p.geometry) {
    case "fix": return `located to ±${(p.sigma_m ?? 0).toFixed(1)} m`;
    case "candidates": return `${p.candidates.length} places, cannot choose (${p.candidates.map((c) => Math.round(c.weight * 100) + "%").join(" / ")})`;
    case "ring": return `${p.ring.kind === "ble" ? "Bluetooth" : "UWB"} ring: ${p.ring.r_m.toFixed(1)} m from ${p.ring.puck}${p.ring.kind === "ble" ? ` ±${p.ring.sigma_m.toFixed(1)} m` : ""}`;
    case "zone": return `somewhere in ${p.zone.room_label} (${Math.round((p.zone.prob ?? 0) * 100)}%)`;
    default: return p.report?.safe_outside ? "says they are out" : "heard, no position";
  }
}
export function reportWords(r) {
  if (!r) return "";
  const bits = [STATUS_WORD[r.status] || r.status];
  if (r.people != null) bits.push(`${r.people} ${r.people === 1 ? "person" : "people"}`);
  if (r.can_move === false) bits.push("cannot move"); else if (r.can_move) bits.push("can move");
  return bits.join(", ");
}

export class BeaconModel {
  constructor(bundle, onChange) {
    this.twin = bundle.twin; this.site = bundle.site; this.onChange = onChange;
    this.dev = new URLSearchParams(location.search).has("devbeacons");
    this.b = null; this.acc = null; this.link = null; this.collapses = {}; this.prevGeom = {}; this.devStep = -1; this.hand = {};
  }
  reset() { this.b = null; this.acc = null; this.collapses = {}; this.prevGeom = {}; clearTimeout(this._devTimer); this.devStep = -1; this.onChange?.(this); }
  get connected() { return this.dev || !!(this.link && this.link.connected); }
  get sample() { return this.dev || !!this.b?.sample || !!this.link?.sample; }
  phones() { return this.b?.phones || []; }
  phone(id) { return this.phones().find((p) => p.id === id) || null; }
  onBeacons(m, live = true) {
    const now = performance.now();
    if (m.link) this.link = m.link;
    for (const p of m.phones || []) {
      const was = this.prevGeom[p.id];
      if (live && p.geometry === "fix" && was && was.geometry !== "fix") this.collapses[p.id] = { t: now, from: was };
      this.prevGeom[p.id] = { geometry: p.geometry, r: p.ring?.r_m, cx: p.ring?.cx, cy: p.ring?.cy };
    }
    for (const [id, c] of Object.entries(this.collapses)) if (now - c.t > 6000) delete this.collapses[id];
    this.b = m;
    this.onChange?.(this);
  }
  onAccountability(m) { this.acc = m; this.onChange?.(this); }
  // state.beacon: {connected, source, simulated, phones, fixes, people_reported}
  onState(sb) {
    if (!sb) return false;
    const was = JSON.stringify(this.link || {});
    this.link = { ...(this.link || {}), ...sb };
    return JSON.stringify(this.link) !== was;
  }
  // the Beacon module may run on a different building than this board's twin (its own copy of another bundle):
  // then its phones and suites are not drawn here, and the board says so
  get foreign() {
    const b = this.b; if (!b || this.dev) return false;
    const rooms = new Set(this.twin.rooms.map((r) => r.id));
    return (!!b.floor && !this.twin.floors.some((f) => f.id === b.floor)) || (!!b.room && !rooms.has(b.room));
  }
  rows() {
    const rooms = new Set(this.twin.rooms.map((r) => r.id));
    const all = this.acc?.rows || this.b?.accountability?.suites || [];
    return all.filter((r) => !r.room || rooms.has(r.room));
  }
  totals() { return this.acc?.totals || this.b?.accountability?.totals || null; }
  view() {
    if (!this.b || this.foreign) return null;
    const key = `${this.b.seq ?? ""}|${this.b.t ?? ""}|${(this.b.phones || []).length}|${JSON.stringify((this.b.phones || []).map((p) => [p.geometry, p.x, p.ring?.r_m, p.candidates?.length, p.stale]))}|${JSON.stringify(this.b.pucks || [])}|${JSON.stringify(this.b.presence || [])}`;
    const hand = new Set((this.b.pucks || []).filter((pk) => pk.source === "hand" || (this.hand[pk.id] && Math.hypot(this.hand[pk.id][0] - pk.x, this.hand[pk.id][1] - pk.y) < 0.15)).map((pk) => pk.id));
    return { key: key + [...hand].join(","), b: this.b, collapses: this.collapses, sample: this.sample, hand };
  }

  // ---------------------------------------------------------------- the hand-made sample story (?devbeacons=1)
  devPlay(auto = true) { this.devStep = -1; this._devAuto = auto; this.devNext(); }
  devNext() {
    clearTimeout(this._devTimer);
    const frames = this._frames ||= devStory(this.twin, this.site);
    const k = Math.min(++this.devStep, frames.length - 1);
    this.onBeacons(JSON.parse(JSON.stringify(frames[k])), true);
    if (this._devAuto && k < frames.length - 1) this._devTimer = setTimeout(() => this.devNext(), 2600);
  }
}

// ---------------------------------------------------------------- sample construction (development only)
function inside(poly, x, y) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}
function arcsInside(poly, cx, cy, r) {
  const ok = []; for (let a = 0; a < 360; a += 5) ok.push(inside(poly, cx + r * Math.cos(a * Math.PI / 180), cy + r * Math.sin(a * Math.PI / 180)));
  if (ok.every(Boolean)) return [[0, 360]];
  const arcs = []; let start = null;
  const n = ok.length, s0 = ok.findIndex((v) => !v);
  for (let k = 1; k <= n; k++) {
    const i = (s0 + k) % n;
    if (ok[i] && start === null) start = i * 5;
    if (!ok[i] && start !== null) { arcs.push([start, ((i * 5) || 360)]); start = null; }
  }
  if (start !== null) arcs.push([start, s0 * 5 + 360]);
  return arcs;
}
const mirror = (p, a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1], t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy); const f = [a[0] + t * dx, a[1] + t * dy]; return [2 * f[0] - p[0], 2 * f[1] - p[1]]; };
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

function devStory(twin, site) {
  // the owner's live demo: TWO pucks placed by hand, two phones with the app, distance only (no bearing).
  // B1's mirror candidate falls outside the building outline and is pruned (a fix); B2's does not (two places).
  const fid = twin.exercise_floor, outline = twin.floors.find((f) => f.id === fid).outline;
  const rooms = twin.rooms.filter((r) => r.floor === fid);
  const room = (id) => rooms.find((r) => r.id === id);
  const roomAt = (x, y) => rooms.find((r) => inside(r.polygon, x, y));
  const fire = room("r310") ? "r310" : rooms.find((r) => r.kind === "suite").id, R = room(fire);
  const xs = R.polygon.map((p) => p[0]), ys = R.polygon.map((p) => p[1]);
  const at = (u, v) => [Math.min(...xs) + u * (Math.max(...xs) - Math.min(...xs)), Math.min(...ys) + v * (Math.max(...ys) - Math.min(...ys))];
  const P = { P1: at(0.84, 0.3), P2: at(0.34, 0.25) };
  const T = { B1: at(0.21, 0.76), B2: at(0.67, 0.1) };
  const floorNo = Number(twin.floors.find((f) => f.id === fid).label);
  const inOutline = (p) => inside(outline, p[0], p[1]);
  const rng = (id, pk) => ({ puck: pk, kind: "uwb", d_m: +dist(T[id], P[pk]).toFixed(2), sigma_m: 0.1, rssi: -72, los: 0.85, age_s: 0.3 });
  const base = (seq, pucks, phones, presence) => ({
    type: "beacons", schema: "sandtable.beacons/1", seq, t: 0, sample: true, simulated: true, source: "sample",
    note: "HAND-MADE SAMPLE drawn on the board for development (?devbeacons=1). Not produced by the Beacon module.",
    floor: fid, room: pucks.length ? fire : null, room_label: pucks.length ? R.label : null,
    rungs: { cold: true, ble: true, uwb: true, bearing: false },
    pucks: pucks.map((id) => ({ id, x: P[id][0], y: P[id][1], floor: fid, sigma_m: 0.05, room: fire, radios: ["ble", "uwb"], source: "hand", simulated: true })),
    phones, presence, accountability: acc(pucks.length, phones, presence),
    limits: ["Two pucks give two places for each phone, mirror images across the line between them, unless the twin rules one out", "A ring is a distance from one puck, not a place", "Devices are not people: a count of devices heard is not a head count"],
    privacy: { opt_in_only: true, cold_identities_kept: false, retention_s: 900, text: "Only phones whose owners opted in are located; cold devices are counted, never identified" } });
  const rep = (status, people, can_move, battery_pct, text) => ({ status, text, people, can_move, battery_pct, floor_reported: floorNo, still: !can_move, still_s: 40, platform: "sim", home_suite: R.suite, safe_outside: false, age_s: 1.2 });
  const phone = (id, geometry, more, report) => ({ id, eid: (0xa3f19c02 + id.charCodeAt(1)).toString(16).slice(-8), opt_in: true, simulated: true, method: "uwb-twr", geometry, x: null, y: null, floor: fid, floor_source: "barometer",
    sigma_m: null, cov: null, confidence: 0.8, room: null, room_label: null, room_prob: null, node: null, geo: null, ring: null, candidates: [], pruned: [], zone: null, ranges: [], bearing: null, report,
    track: { first_seen_s: 20, last_seen_s: 0.4, updates: 40, moving: false, speed: 0 }, text: "", ...more });
  const ring = (id, pk) => ({ puck: pk, cx: P[pk][0], cy: P[pk][1], r_m: +dist(T[id], P[pk]).toFixed(2), sigma_m: 0.1, arcs: arcsInside(outline, P[pk][0], P[pk][1], dist(T[id], P[pk])), kind: "uwb" });
  const r1 = rep("trapped", 2, false, 41, "Two of us in the back office, smoke under the door");
  const r2 = rep("injured", 1, false, 18, "Leg hurt, near the window");
  const presence = [{ id: "C1", method: "ble-presence", opt_in: false, simulated: true, puck: "P1", floor: fid, room: fire, room_label: R.label, count: 3, count_range: [2, 5], zones: { near: 2, mid: 1, far: 4 }, window_s: 30, confidence: 0.5,
    text: `About 3 devices advertising near puck P1 in ${R.label}; devices, not people` }];
  const cand = (id, w) => { const m = mirror(T[id], P.P1, P.P2); return { m, list: [{ x: T[id][0], y: T[id][1], sigma_m: 0.3, weight: w, room: fire, room_label: R.label }, { x: m[0], y: m[1], sigma_m: 0.3, weight: +(1 - w).toFixed(2), room: roomAt(...m)?.id || null, room_label: roomAt(...m)?.label || "outside the building" }] }; };
  const c1 = cand("B1", 0.5), c2 = cand("B2", 0.55);
  const B1ring = phone("B1", "ring", { ring: ring("B1", "P1"), ranges: [rng("B1", "P1")], text: "Phone B1: 2 people, cannot move. UWB ring from P1" }, r1);
  const B2ring = phone("B2", "ring", { ring: ring("B2", "P1"), ranges: [rng("B2", "P1")], text: "Phone B2: 1 person, injured. UWB ring from P1" }, r2);
  const B1cand = phone("B1", "candidates", { candidates: c1.list, ranges: [rng("B1", "P1"), rng("B1", "P2")], text: "Phone B1: two places, mirror images across P1-P2" }, r1);
  const B1fix = inOutline(c1.m) ? B1cand : phone("B1", "fix", { x: T.B1[0], y: T.B1[1], sigma_m: 0.3, cov: [0.09, 0.01, 0.08], confidence: 0.9, room: fire, room_label: R.label, room_prob: 0.97, ranges: [rng("B1", "P1"), rng("B1", "P2")],
    pruned: [{ x: c1.m[0], y: c1.m[1], reason: "outside the building outline" }], text: `Phone B1: 2 people, cannot move. Fix in ${R.label}, within 0.3 m: the mirror place is outside the building` }, r1);
  const B2cand = phone("B2", "candidates", { candidates: c2.list, ranges: [rng("B2", "P1"), rng("B2", "P2")], room: fire, room_label: R.label, text: "Phone B2: 1 person, injured. Two places in the room, the twin cannot choose" }, r2);
  function acc(np, phones, pres) {
    const suites = rooms.filter((r) => r.kind === "suite").map((r) => {
      const ten = site.tenants.filter((t) => t.floor === floorNo && t.suite === r.suite);
      const exp = Math.round(r.area_m2 * 10.764 / 100);
      const inRoom = phones.filter((p) => (p.room || p.zone?.room || (p.geometry === "ring" && np ? fire : null)) === r.id);
      const ppl = inRoom.reduce((s, p) => s + (p.report?.people || 0), 0);
      const cold = pres.filter((c) => c.room === r.id).reduce((s, c) => s + c.count, 0);
      const state = !ten.length ? "vacant-on-record" : ppl ? "people-reported" : cold ? "devices-heard" : np && r.id === fire ? "silent" : "unscanned";
      return { suite: r.suite, room: r.id, room_label: r.label, tenant: ten.length ? `${(ten[0].industry || "tenant").replace(/\s*\(.*?\)/g, "")} (name withheld)` : null, industry: ten[0]?.industry || null, vacant: !ten.length,
        life_hazard: ten.find((t) => t.life_hazard)?.life_hazard || null,
        expected: ten.length ? { people: exp, low: Math.round(exp * 0.45), high: Math.round(exp * 1.6), basis: `${Math.round(r.area_m2 * 10.764)} sf at 100 sf per person (outpatient load factor), open hours` } : { people: 0, low: 0, high: 2, basis: "no tenant on record" },
        app_registered: 0, safe_outside: 0, phones_opt_in: inRoom.length, people_reported: ppl, located: inRoom.filter((p) => p.geometry === "fix").length,
        cold_devices: cold, cold_range: cold ? [Math.max(0, cold - 1), cold + 2] : [0, 0],
        unaccounted: ten.length ? { low: Math.max(0, Math.round(exp * 0.45) - ppl), high: Math.max(0, Math.round(exp * 1.6) - ppl) } : { low: 0, high: 2 }, state,
        priority: state === "people-reported" ? 1 : ten.some((t) => t.life_hazard) ? 3 : 4, why: "" };
    });
    return { source: { costar_mode: "fixture", real_sponsor_data: false, text: "Building record: public open data + synthetic fields. Not CoStar data." }, floor: fid, suites,
      totals: { expected_people: suites.reduce((s, q) => s + q.expected.people, 0), phones_opt_in: phones.length, people_reported: phones.reduce((s, p) => s + (p.report?.people || 0), 0), located: phones.filter((p) => p.geometry === "fix").length, cold_devices: pres.reduce((s, c) => s + c.count, 0), suites_unscanned: suites.filter((q) => q.state === "unscanned").length } };
  }
  return [
    base(1, [], [], []),
    base(2, ["P1"], [B1ring, B2ring], presence),
    base(3, ["P1", "P2"], [B1cand, B2cand], presence),
    base(4, ["P1", "P2"], [B1fix, B2cand], presence),
  ];
}
