// "Where did the beacon land?": pick the floor, tap where P1 landed, tap toward P2. When the Beacons object carries a
// measured baseline for the pair (its optional `baselines`), P2 sits at that distance along the tapped direction;
// otherwise P2 goes where it was tapped. Then POST /api/beacon/pucks with placed_by on each puck.
import { esc, plural, baselineFor, rangeScale } from "./common.js";
import { storeys } from "./know.js";

const inside = (poly, x, y) => { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c; } return c; };
const r2 = (v) => +v.toFixed(2);

export class Placer {
  constructor(ctx) { this.c = ctx; this.p = null; }
  get on() { return !!this.p; }
  start() {
    const t = this.c.bundle.twin;
    this.p = { floor: t.exercise_floor, pucks: [], n: 2, room: null, hover: null };
    this.c.plan.setPlacing(this.view()); this.c.onChange();
  }
  stop() { this.p = null; this.c.plan.setPlacing(null); this.c.onChange(); }
  roomAt(x, y) {
    const t = this.c.bundle.twin, f = this.p?.floor || t.exercise_floor;
    return t.rooms.find((r) => r.floor === f && r.kind !== "corridor" && inside(r.polygon, x, y)) || t.rooms.find((r) => r.floor === f && inside(r.polygon, x, y)) || null;
  }
  nextId() { const used = new Set(this.p.pucks.map((q) => q.id)); let k = 1; while (used.has(`P${k}`)) k++; return `P${k}`; }
  // the measured spacing between the next puck and one already placed (P1 first), if the pucks reported one
  anchorFor(id) {
    const b = this.c.beacon.b, k = rangeScale(b);
    for (const q of this.p.pucks) { const bl = baselineFor(b, id, q.id); if (bl) return { q, bl, plan: bl.d_m * k, scale: k }; }
    return null;
  }
  snap(q, d, x, y) {
    let dx = x - q.x, dy = y - q.y; const L = Math.hypot(dx, dy);
    if (L < 1e-3) { dx = 1; dy = 0; } else { dx /= L; dy /= L; }
    return [r2(q.x + dx * d), r2(q.y + dy * d)];
  }
  view() {
    const P = this.p; if (!P) return null;
    const v = { pucks: P.pucks };
    if (P.pucks.length && P.pucks.length < P.n) {
      const id = this.nextId(), a = this.anchorFor(id);
      if (a) v.ring = { x: a.q.x, y: a.q.y, r: a.plan };
      if (P.hover) {
        const from = a ? a.q : P.pucks[0];
        const [x, y] = a ? this.snap(a.q, a.plan, P.hover[0], P.hover[1]) : P.hover;
        v.preview = { from: [from.x, from.y], x, y, d: Math.hypot(x - from.x, y - from.y) };
      }
    }
    return v;
  }
  onPlace(kind, id, x, y) {
    const P = this.p; if (!P) return;
    if (kind === "hover") { P.hover = [x, y]; this.c.plan.setPlacing(this.view()); return; }
    if (kind === "add") {
      if (P.pucks.length >= P.n) { this.c.toast(`${plural(P.n, "puck")} placed: drag one to move it, or add a puck`); return; }
      const nid = this.nextId(), a = P.pucks.length ? this.anchorFor(nid) : null;
      if (!P.pucks.length) { P.room = this.roomAt(x, y); this.c.onRoom?.(P.room); }
      if (a) { const [sx, sy] = this.snap(a.q, a.plan, x, y); P.pucks.push({ id: nid, x: sx, y: sy, by: "baseline", base: { with: a.q.id, d: a.bl.d_m, plan: a.plan, scale: a.scale, sigma: a.bl.sigma_m, samples: a.bl.samples, source: a.bl.source } }); }
      else P.pucks.push({ id: nid, x: r2(x), y: r2(y), by: "hand" });
      P.hover = null;
    } else if (kind === "move") {
      const q = P.pucks.find((z) => z.id === id); if (!q) return;
      if (q.by === "baseline") { const a = P.pucks.find((z) => z.id === q.base.with); if (a) [q.x, q.y] = this.snap(a, q.base.plan ?? q.base.d, x, y); }
      else {
        const dx = x - q.x, dy = y - q.y; q.x = r2(x); q.y = r2(y);
        for (const z of P.pucks) if (z.by === "baseline" && z.base.with === q.id) { z.x = r2(z.x + dx); z.y = r2(z.y + dy); }   // the pair moves together
        if (P.pucks[0] === q) P.room = this.roomAt(q.x, q.y) || P.room;
      }
    }
    this.c.plan.setPlacing(this.view()); this.c.onChange();
  }
  // the guide on the table
  html(active) {
    const P = this.p; if (!P) return "";
    const { bundle } = this.c, t = bundle.twin, n = P.pucks.length;
    const fl = storeys(bundle);
    const planOK = (f) => f.plan && f.plan.id === t.exercise_floor;
    const r = P.room, ox = r ? Math.min(...r.polygon.map((q) => q[0])) : 0, oy = r ? Math.min(...r.polygon.map((q) => q[1])) : 0;
    const nextId = n < P.n ? this.nextId() : null, a = n && nextId ? this.anchorFor(nextId) : null;
    const s2 = n === 0 ? "now" : "done", s3 = n === 1 ? "now" : n >= 2 ? "done" : "";
    const p1 = P.pucks[0];
    const where = p1 ? `${r ? esc(r.label) : "outside the rooms"}, ${(p1.x - ox).toFixed(1)} m from the west wall, ${(p1.y - oy).toFixed(1)} m from the south wall` : "";
    let spacing = "";
    if (n >= 2) {
      const q = P.pucks[1];
      const k = rangeScale(this.c.beacon.b);
      spacing = q.by === "baseline" ? `Spacing measured by the pucks: ${q.base.d.toFixed(2)} m ±${(q.base.sigma ?? 0).toFixed(2)}${q.base.samples ? ` (${q.base.samples} samples)` : ""}${q.base.scale && q.base.scale !== 1 ? `, ${q.base.plan.toFixed(1)} m on the plan at the exercise scale` : ""}`
        : `Spacing set by hand: ${(Math.hypot(q.x - p1.x, q.y - p1.y) / k).toFixed(2)} m${k !== 1 ? " (exercise scale applied)" : ""}`;
    }
    const twoBl = baselineFor(this.c.beacon.b, "P1", "P2"), k2 = rangeScale(this.c.beacon.b);
    const step3 = n < 2 ? (twoBl ? `P2 goes ${twoBl.d_m.toFixed(2)} m from P1, the spacing the pucks measured (±${(twoBl.sigma_m ?? 0).toFixed(2)} m)${k2 !== 1 ? `, ${(twoBl.d_m * k2).toFixed(1)} m on the plan at the exercise scale` : ""}. Tap in its direction.` : "The pucks have not measured their spacing: P2 goes where you tap.") : esc(spacing);
    return `<h3>Where did the beacon land?</h3><ol class="steps">` +
      `<li class="done"><span class="n">1</span><div><b>Pick the floor</b><div class="floors" role="group" aria-label="Floor">${fl.map((f) => `<button data-act="pl-floor" data-floor="${f.n}" aria-pressed="${planOK(f)}" ${planOK(f) ? "" : `disabled title="No interior plan for floor ${f.n}"`}>${f.n}</button>`).join("")}</div>` +
      `${fl.length > 1 ? `<small>Floors without an interior plan cannot take pucks.</small>` : ""}</div></li>` +
      `<li class="${s2}"><span class="n">2</span><div><b>Tap where P1 landed</b>${p1 ? `<small>${where}</small>` : "<small>on the plan</small>"}</div></li>` +
      `<li class="${s3}"><span class="n">3</span><div><b>Tap toward P2</b><small>${step3}</small></div></li></ol>` +
      (n ? `<div class="pp-rows">${P.pucks.map((q) => `<div class="pp-row" data-id="${q.id}"><span class="pk">${q.id}</span>` +
        `<label>From the west wall, m<input type="number" step="0.05" inputmode="decimal" data-k="x" value="${(q.x - ox).toFixed(2)}"></label>` +
        `<label>From the south wall, m<input type="number" step="0.05" inputmode="decimal" data-k="y" value="${(q.y - oy).toFixed(2)}"></label>` +
        `<button class="x" data-act="pl-del" data-id="${q.id}" aria-label="Remove ${q.id}">×</button>` +
        `<span class="by">${q.by === "baseline" ? `distance from ${q.base.with}: measured by the pucks` : "placed by hand"}</span></div>`).join("")}</div>` : "") +
      (n >= 2 ? `<p class="spacing-l">${esc(spacing)}</p>` : "") +
      `<div class="pp-act"><button class="btn small" data-act="pl-add"${n < P.n ? " disabled" : ""}>Add a puck</button><button class="btn small" data-act="pl-restart"${n ? "" : " disabled"}>Start over</button><button class="btn small" data-act="pl-cancel">Cancel</button>` +
      `<button class="btn small primary" data-act="pl-send"${n && active ? "" : " disabled"}>Send ${n ? plural(n, "puck") : "pucks"}</button></div>` +
      (active ? "" : `<p class="warn">Dispatch the alarm first: the Beacon module takes pucks only during an incident.</p>`);
  }
  onInput(inp) {
    const P = this.p; if (!P) return;
    const q = P.pucks.find((z) => z.id === inp.closest(".pp-row")?.dataset.id), v = Number(inp.value); if (!q || !Number.isFinite(v)) return;
    const r = P.room, ox = r ? Math.min(...r.polygon.map((z) => z[0])) : 0, oy = r ? Math.min(...r.polygon.map((z) => z[1])) : 0;
    if (inp.dataset.k === "x") q.x = r2(ox + v); else q.y = r2(oy + v);
    if (q.by === "baseline") { q.by = "hand"; q.base = null; }        // typed in: the distance is no longer the measured one
    this.c.plan.setPlacing(this.view()); this.c.onChange();
  }
  act(a, el) {
    const P = this.p; if (!P) return;
    if (a === "pl-del") { P.pucks = P.pucks.filter((q) => q.id !== el.dataset.id); for (const q of P.pucks) if (q.base?.with === el.dataset.id) { q.by = "hand"; q.base = null; } if (!P.pucks.length) P.room = null; }
    else if (a === "pl-add") P.n = Math.min(5, P.n + 1);
    else if (a === "pl-restart") { P.pucks = []; P.room = null; P.n = 2; }
    else if (a === "pl-cancel") { this.stop(); return; }
    else if (a === "pl-floor") { /* one plan is drawn: its floor is the only one that can take pucks */ }
    else if (a === "pl-send") { this.send(); return; }
    this.c.plan.setPlacing(this.view()); this.c.onChange();
  }
  async send() {
    const P = this.p, { bundle, beacon } = this.c;
    const pucks = P.pucks.map((q) => ({ id: q.id, x: q.x, y: q.y, sigma_m: 0.05, source: "hand", placed_by: q.by }));
    beacon.hand = Object.fromEntries(pucks.map((q) => [q.id, [q.x, q.y]]));
    beacon.sent = Object.fromEntries(pucks.map((q) => [q.id, { x: q.x, y: q.y, by: q.placed_by }]));
    if (beacon.dev) { this.c.toast("Sample mode: the pucks stay on the board, nothing is sent"); this.stop(); return; }
    try {
      await this.c.api("/api/beacon/pucks", { pucks, room: P.room?.id, floor: P.floor || bundle.twin.exercise_floor });
      const bl = pucks.some((q) => q.placed_by === "baseline");
      this.c.toast(`Sent ${pucks.map((q) => q.id).join(" and ")} to the Beacon module: ${bl ? "P1 placed by hand, P2 at the measured spacing" : "placed by hand"}`);
      this.stop();
    } catch { /* the toast says why */ }
  }
}
