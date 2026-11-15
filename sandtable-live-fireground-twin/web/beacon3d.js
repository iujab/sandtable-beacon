// The phones layer of the 3D twin (Beacon). Each opt-in phone is drawn as what its geometry supports: a thin UWB
// ring or a fat Bluetooth band on the arcs the twin allows, candidates with their weights and the crossed-out pruned
// ghosts, a fix with its uncertainty, a zone as a shaded room. Cold devices are a count chip per room, never people.
// Rebuilt when the relayed object changes (at most a few times a second); the ring-to-fix collapse is animated per frame.
// Colours come from the twin's palette (style.css), so the layer follows the day and night themes.
import * as THREE from "three";
import { sigmaEllipse } from "./scout.js";
import { chipSpot, mirrorPair } from "./beacon.js";
import { personStatus, puckOrigin, REDUCED } from "./common.js";

const mat = (color, opacity = 1, extra = {}) => new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false, fog: false, side: THREE.DoubleSide, ...extra });

export class Beacon3D {
  constructor(t3) { this.t = t3; this.g = new THREE.Group(); t3.scene.add(this.g); this.key = null; this.pick = []; this.anim = []; this.strikes = []; }
  V(x, y, h = 0) { return this.t.V(x, y, h); }
  _o(o, order = 26) { o.renderOrder = order; this.g.add(o); return o; }
  _lab(text, color, bg, size) { const s = this.t.label(text, color, 54, bg, true); s.scale.multiplyScalar(size / s.scale.y); s.renderOrder = 45; this.g.add(s); return s; }
  _flat(geo, material, x, y, h, order) { const m = new THREE.Mesh(geo, material); m.rotation.x = -Math.PI / 2; m.position.copy(this.V(x, y, h)); return this._o(m, order); }
  _pickable(x, y, r, data) { const m = new THREE.Mesh(new THREE.SphereGeometry(r, 8, 6), new THREE.MeshBasicMaterial({ visible: false })); m.position.copy(this.V(x, y, 0.4)); m.userData = data; this.g.add(m); this.pick.push(m); }
  _ghost(q, strike, t0) {
    const P = this.t.pal, m = mat(new THREE.Color(P.ink2), 0.9);
    for (const a of [Math.PI / 4, -Math.PI / 4]) { const b = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.04, 0.16), m); b.position.copy(this.V(q.x, q.y, 0.12)); b.rotation.y = a; this._o(b, 27); }
    this._flat(new THREE.RingGeometry(0.66, 0.76, 40), mat(new THREE.Color(P.ink2), 0.7), q.x, q.y, 0.11, 27);
    this._lab(`ruled out: ${q.reason.length > 44 ? q.reason.slice(0, 42).trim() + "…" : q.reason}`, "#ffffff", "rgba(60,66,74,.92)", 0.024).position.copy(this.V(q.x, q.y, 0.8));
    this._pickable(q.x, q.y, 0.45, { tip: `Ruled out: ${q.reason}` });
    if (strike && !REDUCED) {
      // the resolution moment: the twin deletes the mirror place (big X, then the grey ghost stays)
      const xm = mat(new THREE.Color(P.danger), 1), grp = new THREE.Group(); grp.position.copy(this.V(q.x, q.y, 0.2)); this.g.add(grp);
      for (const a of [Math.PI / 4, -Math.PI / 4]) { const b = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.04, 0.22), xm); b.rotation.y = a; b.renderOrder = 36; grp.add(b); }
      const was = new THREE.Mesh(new THREE.CircleGeometry(0.32, 24), mat(new THREE.Color(P.person), 0.9)); was.rotation.x = -Math.PI / 2; was.position.copy(this.V(q.x, q.y, 0.13)); this._o(was, 35);
      this.strikes.push({ t0, grp, xm, was });
    }
  }
  set(view, selected) {
    const key = view ? view.key + "|" + selected + "|" + Object.keys(view.collapses).join(",") : null;
    if (key === this.key) return;
    this.key = key;
    for (const o of [...this.g.children]) { this.g.remove(o); o.traverse?.((q) => { q.geometry?.dispose(); q.material?.map?.dispose(); q.material?.dispose?.(); }); }
    this.pick = []; this.anim = []; this.strikes = [];
    if (!view) return;
    const b = view.b, t = this.t, P = t.pal, PERSON = new THREE.Color(P.person), dark = P.theme === "night";
    const TAG = { trapped: [P.onDanger, P.danger], injured: [P.onCaution, P.caution], mobile: [P.ink, P.paper], unknown: [P.ink2, P.paper], out: [P.ink2, P.paper2] };
    const pucks = Object.fromEntries((b.pucks || []).map((pk) => [pk.id, pk]));
    for (const pk of b.pucks || []) {
      const origin = puckOrigin(pk, view), placed = origin === "hand" || origin === "baseline" || origin === "layout";
      const m = new THREE.Mesh(placed ? new THREE.BoxGeometry(0.4, 0.1, 0.4) : new THREE.CylinderGeometry(0.2, 0.2, 0.09, 24), mat(new THREE.Color(P.puck), 1)); m.position.copy(this.V(pk.x, pk.y, 0.06)); this._o(m, 30);
      this._flat(new THREE.CircleGeometry(0.42, 24), mat(new THREE.Color(P.puckInk), 0.35), pk.x, pk.y, 0.03, 29);
      this._lab(origin === "hand" ? `${pk.id} by hand` : origin === "baseline" ? `${pk.id} measured spacing` : pk.id, P.puckInk, P.puck, placed ? 0.02 : 0.024).position.copy(this.V(pk.x, pk.y, 0.7));
      this._pickable(pk.x, pk.y, 0.4, { tip: `Puck ${pk.id} (${(pk.radios || []).join(" and ") || "radio"})${origin === "hand" ? ": position tape-measured and placed by hand, not located by the system" : origin === "baseline" ? ": direction placed by hand, distance from the spacing the pucks measured" : ""}` });
    }
    for (const c of (b.presence || []).filter((q) => (q.count || 0) > 0 || (q.count_range?.[1] || 0) > 0)) {
      const r = t.twin.rooms.find((q) => q.id === c.room); if (!r) continue;
      const [cx, ylo] = chipSpot(r, b);
      const n = c.count_range ? (c.count_range[0] === c.count_range[1] ? c.count_range[0] : `${c.count_range[0]} to ${c.count_range[1]}`) : c.count;
      const s = this._lab(`${n} ${n === 1 ? "device" : "devices"}, not people`, "#dce6f0", "rgba(40,52,66,.92)", 0.02);
      s.position.copy(this.V(cx, ylo, 0.5));
      this._pickable(cx, ylo, 0.9, { tip: c.text || "Devices heard with no app: a count, never people, no identities" });
    }
    const mirrors = new Set();
    (b.phones || []).forEach((ph, idx) => {
      // the mirror line: through the two pucks; the phone's two places are mirror images across it
      const pair = mirrorPair(ph, pucks), pk2 = pair && pair.map((q) => q.id).sort().join("-");
      if (pair && !mirrors.has(pk2)) {
        mirrors.add(pk2);
        const [a, c] = pair, dx = c.x - a.x, dy = c.y - a.y, L = Math.hypot(dx, dy) || 1, ext = 30;
        const pts = [this.V(a.x - dx / L * ext, a.y - dy / L * ext, 0.07), this.V(c.x + dx / L * ext, c.y + dy / L * ext, 0.07)];
        const ln = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: new THREE.Color(P.ink2), dashSize: 0.35, gapSize: 0.22, transparent: true, opacity: 0.85, depthTest: false, fog: false }));
        ln.computeLineDistances(); this._o(ln, 26);
        this._lab(`mirror line ${a.id} to ${c.id}`, P.ink2, null, 0.017).position.copy(this.V(c.x + dx / L * 1.6, c.y + dy / L * 1.6, 0.35));
      }
      const r0 = ph.report || {}, st = personStatus(r0), sel = ph.id === selected, dim = ph.stale ? 0.5 : 1;
      const head = `${ph.id} ${st.word.toLowerCase()}${r0.people != null ? `, ${r0.people} ${r0.people === 1 ? "person" : "people"}` : ""}`;
      let lab = null;
      if (ph.geometry === "candidates" || ph.geometry === "fix")
        for (const rg of ph.ranges || []) { const pk = pucks[rg.puck]; if (!pk || rg.kind !== "uwb" || !rg.d_m) continue;
          const rm = new THREE.Mesh(new THREE.RingGeometry(Math.max(0.02, rg.d_m - 0.035), rg.d_m + 0.035, 160), mat(PERSON, 0.5 * dim)); rm.rotation.x = -Math.PI / 2; rm.position.copy(this.V(pk.x, pk.y, 0.085)); this._o(rm, 26); }
      if (ph.geometry === "fix" && ph.x != null) {
        const e = sigmaEllipse(ph), grp = new THREE.Group(); grp.position.copy(this.V(ph.x, ph.y, 0)); grp.rotation.y = e.ang; this.g.add(grp);
        for (const [k, op, ord] of [[2, 0.12, 27], [1, 0.32, 28]]) { const d = new THREE.Mesh(new THREE.CircleGeometry(1, 48), mat(PERSON, op * dim)); d.rotation.x = -Math.PI / 2; d.position.y = 0.1; d.scale.set(Math.max(0.25, k * e.a), Math.max(0.25, k * e.b), 1); d.renderOrder = ord; grp.add(d); }
        const ring = new THREE.Mesh(new THREE.RingGeometry(0.94, 1, 48), mat(PERSON, 0.95 * dim)); ring.rotation.x = -Math.PI / 2; ring.position.y = 0.11; ring.scale.set(Math.max(0.2, e.a), Math.max(0.2, e.b), 1); ring.renderOrder = 28; grp.add(ring);
        const core = new THREE.Mesh(new THREE.SphereGeometry(0.2, 18, 12), mat(PERSON, dim)); core.position.copy(this.V(ph.x, ph.y, 0.3)); this._o(core, 32);
        const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 2.4), mat(PERSON, 0.8 * dim)); beam.position.copy(this.V(ph.x, ph.y, 1.5)); this._o(beam, 31);
        const c = view.collapses[ph.id];
        for (const q of ph.pruned || []) this._ghost(q, c && c.from?.geometry === "candidates", c?.t);
        if (c) {
          const mk = (w, color) => { const m = new THREE.Mesh(new THREE.RingGeometry(1 - w, 1, 128), mat(color, 1)); m.rotation.x = -Math.PI / 2; m.position.copy(this.V(ph.x, ph.y, 0.14)); return this._o(m, 34); };
          const pillar = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 5, 16, 1, true), mat(PERSON, 0.6)); pillar.position.copy(this.V(ph.x, ph.y, 2.5)); this._o(pillar, 33);
          const fl = this._lab(`${c.from?.geometry === "candidates" ? "two places" : c.from?.geometry || "ring"} to a fix, within ${(ph.sigma_m ?? 0).toFixed(1)} m`, dark ? "#121a22" : "#ffffff", P.person, 0.032); fl.position.copy(this.V(ph.x, ph.y, 1.1));
          this.anim.push({ t0: REDUCED ? c.t - 6000 : c.t, ring: mk(0.1, new THREE.Color(P.ink)), ring2: mk(0.05, PERSON), pillar, from: Math.max(2.5, c.from?.r || 3.5), to: Math.max(0.2, ph.sigma_m || 0.3), flash: fl });
        }
        lab = [ph.x, ph.y, "", 2.6];
        this._pickable(ph.x, ph.y, Math.max(0.6, e.a), { tip: ph.text || head, pick: ph.id });
      } else if (ph.geometry === "candidates" && ph.candidates?.length) {
        const cs = ph.candidates;
        for (let i = 1; i < cs.length; i++) {
          const pts = [this.V(cs[0].x, cs[0].y, 0.1), this.V(cs[i].x, cs[i].y, 0.1)];
          const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: PERSON, dashSize: 0.25, gapSize: 0.18, transparent: true, opacity: 0.7, depthTest: false, fog: false }));
          l.computeLineDistances(); this._o(l, 27);
        }
        cs.forEach((c) => {
          const r = Math.max(0.45, c.sigma_m || 0.3);
          this._flat(new THREE.CircleGeometry(r, 32), mat(PERSON, (0.12 + 0.45 * c.weight) * dim), c.x, c.y, 0.1, 28);
          this._flat(new THREE.RingGeometry(r * 0.9, r, 32), mat(PERSON, 0.95 * dim), c.x, c.y, 0.11, 28);
          this._lab(`${Math.round(c.weight * 100)}%`, P.person, null, 0.017).position.copy(this.V(c.x, c.y, 0.55));
          this._pickable(c.x, c.y, 0.6, { tip: `${ph.id}: one of ${cs.length} places, ${Math.round(c.weight * 100)}% (${c.room_label || "?"}). ${ph.text || ""}`, pick: ph.id });
        });
        for (const q of ph.pruned || []) this._ghost(q);
        if (cs.length > 1) this._lab(`one of ${cs.length} places: cannot choose`, dark ? "#121a22" : "#ffffff", P.person, 0.024).position.copy(this.V((cs[0].x + cs[1].x) / 2, (cs[0].y + cs[1].y) / 2, 0.6));
        const top = cs.reduce((a, q) => (q.y > a.y ? q : a), cs[0]);
        lab = [top.x, top.y, "", 1.6];
      } else if (ph.geometry === "ring" && ph.ring) {
        const R = ph.ring, ble = R.kind === "ble", w = ble ? Math.max(0.25, R.sigma_m || 1) : Math.max(0.06, R.sigma_m || 0.05);
        const off = new THREE.Mesh(new THREE.RingGeometry(R.r_m - 0.02, R.r_m + 0.02, 128), mat(PERSON, 0.22 * dim)); off.rotation.x = -Math.PI / 2; off.position.copy(this.V(R.cx, R.cy, 0.08)); this._o(off, 26);
        for (const [a0, a1] of R.arcs || [[0, 360]]) {
          const g = new THREE.RingGeometry(Math.max(0.05, R.r_m - w), R.r_m + w, 128, 1, a0 * Math.PI / 180, (a1 - a0) * Math.PI / 180);
          this._flat(g, mat(PERSON, (ble ? 0.3 : 0.95) * dim), R.cx, R.cy, 0.09, 27);
        }
        const [a0, a1] = (R.arcs || [[0, 360]])[0], am = ((a0 + a1) / 2) * Math.PI / 180, rr = R.r_m + w + 0.3;
        lab = [R.cx + rr * Math.cos(am), R.cy + rr * Math.sin(am), "", 1.2];
        this._pickable(lab[0], lab[1], 0.8, { tip: `${ph.text || head}. A ring is a distance from one puck, not a place.`, pick: ph.id });
      } else if (ph.geometry === "zone" && ph.zone) {
        const poly = ph.zone.polygon || t.twin.rooms.find((q) => q.id === ph.zone.room)?.polygon;
        if (poly) {
          const z = t._poly(poly, 0.07, mat(PERSON, 0.14 * dim)); z.renderOrder = 25; this.g.add(z);
          const cx = poly.reduce((s, p) => s + p[0], 0) / poly.length, cy = poly.reduce((s, p) => s + p[1], 0) / poly.length;
          lab = [cx, cy, "", 1.6];
          this._pickable(cx, cy, 1.2, { tip: ph.text || head, pick: ph.id });
        }
      }
      if (lab) {
        const h = lab[3] + (idx % 4) * 0.8;   // deterministic offset so tags of phones in one room do not sit on each other
        const [fg, bg] = TAG[st.key] || TAG.unknown;
        const mark = { tri: "▲ ", plus: "+ ", dot: "● ", out: "✓ ", q: "? " }[st.shape] || "";
        const hd = this._lab(mark + head, fg, bg, sel ? 0.027 : 0.023); hd.position.copy(this.V(lab[0], lab[1], h));
        if (h > 1.0) { const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, h - 0.2), mat(new THREE.Color(P.ink2), 0.7)); stem.position.copy(this.V(lab[0], lab[1], (h - 0.2) / 2)); this._o(stem, 26); }
        if (ph.stale) hd.material.opacity = 0.6;
      }
    });
  }
  frame(now) {
    for (const k of this.strikes || []) {
      const dt = now - k.t0, u = Math.min(1, Math.max(0, dt / 500));
      k.grp.scale.setScalar(2.2 - 1.2 * (1 - Math.pow(1 - u, 3)));
      k.xm.opacity = dt < 2800 ? Math.min(1, dt / 250) : Math.max(0, 1 - (dt - 2800) / 1700);
      k.was.material.opacity = Math.max(0, 0.9 - dt / 1800);
    }
    for (const a of this.anim) {
      const dt = now - a.t0, sh = (d) => { const u = Math.min(1, Math.max(0, (dt - d) / 1600)); return 1 - Math.pow(1 - u, 3); };
      a.ring.scale.setScalar(Math.max(0.05, a.from + (a.to - a.from) * sh(0)));
      a.ring2.scale.setScalar(Math.max(0.05, a.from * 1.15 + (a.to - a.from * 1.15) * sh(250)));
      const fade = dt < 1900 ? 1 : Math.max(0, 1 - (dt - 1900) / 2600);
      a.ring.material.opacity = fade; a.ring2.material.opacity = fade * 0.9;
      a.pillar.material.opacity = dt < 1600 ? 0.15 + 0.45 * sh(0) : Math.max(0, 0.6 - (dt - 1600) / 2000);
      a.flash.material.opacity = Math.max(0, Math.min(1, 1 - (dt - 4500) / 1500));
    }
  }
}
