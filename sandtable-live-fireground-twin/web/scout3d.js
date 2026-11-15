// The scan layer of the 3D twin: thrown pucks, their mesh, the registration, bistatic sweep ellipses, the coarse
// echo map (cells shown as cells), the located person with the uncertainty ellipse pulsing at the measured
// breathing rate, and the flags where the room differs from the twin. Drawn over everything (no depth test, no
// fog) so it stays readable in the smoke view. Rebuilt only when the scan's phase or data change; animated per frame.
import * as THREE from "three";
import { bistatic, sigmaEllipse, victimState, VICTIM_COLOR, PUCK_COLOR, FLAG_COLOR } from "./scout.js";

const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
const mat = (color, opacity = 1, extra = {}) => new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false, fog: false, side: THREE.DoubleSide, ...extra });
const RING = new THREE.RingGeometry(0.972, 1, 96);
const DISC = new THREE.CircleGeometry(1, 64);

export class Scout3D {
  constructor(t3) {
    this.t = t3;
    this.g = new THREE.Group(); this.g.renderOrder = 20;
    t3.scene.add(this.g);
    this.view = null; this.key = null; this.pick = [];
  }
  V(x, y, h = 0) { return this.t.V(x, y, h); }
  _o(obj, order = 22) { obj.renderOrder = order; this.g.add(obj); return obj; }
  _lab(text, color, bg, size = 0.042) { const s = this.t.label(text, color, 54, bg, true); s.scale.multiplyScalar(size / s.scale.y); s.renderOrder = 40; return s; }
  _clip(room) {
    const [x0, y0, x1, y1] = room.bbox, a = this.V(x0, y0), b = this.V(x1, y1);
    return [new THREE.Plane(new THREE.Vector3(1, 0, 0), -a.x), new THREE.Plane(new THREE.Vector3(-1, 0, 0), b.x), new THREE.Plane(new THREE.Vector3(0, 0, -1), a.z), new THREE.Plane(new THREE.Vector3(0, 0, 1), -b.z)];
  }
  _ellipse(e, color, opacity, h, clip) {
    const grp = new THREE.Group();
    const m = new THREE.Mesh(RING, mat(color, opacity, clip ? { clippingPlanes: clip } : {}));
    m.rotation.x = -Math.PI / 2; m.scale.set(e.a, e.b, 1); m.renderOrder = 24;
    grp.add(m); grp.position.copy(this.V(e.cx, e.cy, h)); grp.rotation.y = e.ang; grp.ring = m;
    this.g.add(grp);
    return grp;
  }
  _bar(p, q, h, w, material, order = 23) {
    const dx = q[0] - p[0], dy = q[1] - p[1], len = Math.hypot(dx, dy);
    const m = new THREE.Mesh(new THREE.BoxGeometry(len, 0.02, w), material);
    m.position.copy(this.V((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, h)); m.rotation.y = Math.atan2(dy, dx);
    return this._o(m, order);
  }
  _puck(id) {
    const g = new THREE.Group();
    g.body = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.09, 24), mat(PUCK_COLOR, 1));
    g.halo = new THREE.Mesh(DISC, mat(0xffc247, 0.28)); g.halo.rotation.x = -Math.PI / 2; g.halo.scale.setScalar(0.42); g.halo.position.y = -0.03;
    g.tag = this._lab(id, "#ffe3a3", null, 0.026); g.tag.position.y = 0.75;
    for (const o of [g.body, g.halo]) o.renderOrder = 30;
    g.add(g.halo, g.body, g.tag);
    this.g.add(g);
    return g;
  }
  _pickable(pos, radius, data) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(radius, 8, 6), new THREE.MeshBasicMaterial({ visible: false }));
    m.position.copy(pos); m.userData = data; this.g.add(m); this.pick.push(m);
  }

  set(view) {
    this.view = view;
    const key = view ? view.key : null;
    if (key === this.key) return;
    this.key = key;
    for (const o of [...this.g.children]) { this.g.remove(o); o.traverse?.((q) => { if (q.geometry && q.geometry !== RING && q.geometry !== DISC) q.geometry.dispose(); q.material?.map?.dispose(); q.material?.dispose?.(); }); }
    this.pick = []; this.anim = {};
    if (!view) return;
    const { room, phase } = view, A = this.anim, clip = this._clip(room);
    this.t.renderer.localClippingEnabled = true;
    const t0 = view.animate ? view.phaseAt : view.phaseAt - 60000;
    A.t0 = t0;

    // room outline: what the pucks registered into (the twin's room, or a coarse box without a twin)
    const outMat = mat(0xffc247, 0.55);
    A.outline = outMat;
    const P = room.outline;
    for (let i = 0; i < P.length; i++) this._bar(P[i], P[(i + 1) % P.length], 0.06, 0.09, outMat, 21);

    const reached = ["registered", "scanning", "done"].includes(phase);
    // the throw: where the pucks may be (thrown from the door, within a throw's reach) until they say where they are
    if (!reached) {
      const zone = new THREE.Mesh(new THREE.CircleGeometry(6.5, 64), mat(0xffc247, 0.07, { clippingPlanes: clip }));
      zone.rotation.x = -Math.PI / 2; zone.position.copy(this.V(room.door[0], room.door[1], 0.04)); this._o(zone, 21);
      const edge = new THREE.Mesh(RING, mat(0xffc247, 0.35, { clippingPlanes: clip })); edge.rotation.x = -Math.PI / 2; edge.scale.setScalar(6.5);
      edge.position.copy(zone.position); this._o(edge, 21);
      A.zone = zone.material;
      const n = view.n, inside = room.inside, dir = Math.atan2(room.centre[1] - room.door[1], room.centre[0] - room.door[0]);
      A.arcs = [];
      for (let i = 0; i < n; i++) {
        // illustrative trajectories into the throw zone; the landing spots are not known and are not kept
        const a = dir + (i - (n - 1) / 2) * 0.32, d = 2.2 + ((i * 7) % 5) * 0.75;
        const end = [inside[0] + d * Math.cos(a), inside[1] + d * Math.sin(a)];
        const curve = new THREE.QuadraticBezierCurve3(this.V(inside[0], inside[1], 1.2), this.V((inside[0] + end[0]) / 2, (inside[1] + end[1]) / 2, 2.6), this.V(end[0], end[1], 0.05));
        const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(40)), new THREE.LineBasicMaterial({ color: 0xffe3a3, transparent: true, opacity: 0, depthTest: false, fog: false }));
        this._o(line, 25);
        const dot = this._o(new THREE.Mesh(new THREE.SphereGeometry(0.14, 12, 8), mat(PUCK_COLOR, 1)), 30);
        const splash = new THREE.Mesh(RING, mat(0xffe3a3, 0)); splash.rotation.x = -Math.PI / 2; splash.position.copy(this.V(end[0], end[1], 0.05)); this._o(splash, 26);
        A.arcs.push({ curve, line, dot, splash, delay: i * 0.16 });
      }
      const note = this._lab(phase === "located" ? "shape known from the ranges, orientation not yet" : `${n} pucks in the room, positions not known yet`, "#ffe3a3", "rgba(20,16,6,.86)", 0.028);
      note.position.copy(this.V(room.centre[0], room.centre[1], 3.6)); this.g.add(note);
    }

    // the constellation: floating (shape only) while self-located, settling onto the twin when registered
    const ids = view.ids;
    if (phase === "located" && view.rel) {
      const rel = view.rel, c = [0, 0]; let k = 0;
      for (const id of ids) if (rel[id]) { c[0] += rel[id][0]; c[1] += rel[id][1]; k++; }
      c[0] /= k || 1; c[1] /= k || 1;
      A.float = { pivot: new THREE.Group(), centre: room.centre };
      A.float.pivot.position.copy(this.V(room.centre[0], room.centre[1], 2.4)); this.g.add(A.float.pivot);
      for (const id of ids) {
        if (!rel[id]) continue;
        const p = this._puck(id); this.g.remove(p); A.float.pivot.add(p);
        p.position.set(rel[id][0] - c[0], 0, -(rel[id][1] - c[1]));
      }
      const lm = mat(0xffc247, 0.7);
      for (const l of view.links) {
        if (!rel[l.a] || !rel[l.b]) continue;
        const a = [rel[l.a][0] - c[0], rel[l.a][1] - c[1]], b = [rel[l.b][0] - c[0], rel[l.b][1] - c[1]];
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
        const m = new THREE.Mesh(new THREE.BoxGeometry(len, 0.02, 0.04), lm); m.renderOrder = 23;
        m.position.set((a[0] + b[0]) / 2, 0, -(a[1] + b[1]) / 2); m.rotation.y = Math.atan2(b[1] - a[1], b[0] - a[0]);
        A.float.pivot.add(m);
      }
      A.linkMat = lm;
    }
    if (reached && view.pucks) {
      const pk = view.pucks;
      A.pucks = [];
      const c = [0, 0]; let k = 0;
      for (const id of ids) if (pk[id]) { c[0] += pk[id][0]; c[1] += pk[id][1]; k++; }
      c[0] /= k || 1; c[1] /= k || 1;
      for (const id of ids) {
        if (!pk[id]) continue;
        const g = this._puck(id), to = this.V(pk[id][0], pk[id][1], 0.06);
        // registration moment: the shape comes down from where it floated, turning into the twin's frame
        const d = [pk[id][0] - c[0], pk[id][1] - c[1]], r = 2.3;
        const from = this.V(c[0] + d[0] * Math.cos(r) - d[1] * Math.sin(r), c[1] + d[0] * Math.sin(r) + d[1] * Math.cos(r), 2.4);
        g.position.copy(phase === "registered" ? from : to);
        A.pucks.push({ g, from, to });
        this._pickable(to, 0.45, { tip: `Puck ${id}: registered into the twin at ${pk[id][0].toFixed(1)}, ${pk[id][1].toFixed(1)} m`, kind: "puck" });
      }
      const lm = mat(0xffc247, phase === "done" ? 0.32 : 0.65);
      A.linkMat = lm; A.links = [];
      for (const l of view.links) if (pk[l.a] && pk[l.b]) A.links.push(this._bar(pk[l.a], pk[l.b], 0.07, 0.035, l.used === false ? mat(0x8d99a6, 0.25) : lm, 23));
    }

    // the sweep: bistatic ellipses growing from each pair (a few at a time), clipped to the room
    if (phase === "scanning" && view.pucks) {
      const pk = view.pucks, pairs = view.links.filter((l) => pk[l.a] && pk[l.b]);
      A.sweep = pairs.map((l, i) => ({ f1: pk[l.a], f2: pk[l.b], d: Math.hypot(pk[l.b][0] - pk[l.a][0], pk[l.b][1] - pk[l.a][1]), g: this._ellipse(bistatic(pk[l.a], pk[l.b], 1), 0xffd27a, 0, 0.09, clip), delay: (i % pairs.length) * 0.34 }));
      A.sweepPeriod = Math.max(2.2, pairs.length * 0.34);
    }

    if (phase === "done" && view.scan) this._landed(view, clip);
  }

  _landed(view, clip) {
    const s = view.scan, A = this.anim, pk = view.pucks || {};
    // the coarse echo map: one texel per cell, nearest filtering, cell lines drawn: it is coarse and says so
    const em = s.echo_map;
    if (em && em.values?.length === em.nx * em.ny) {
      const data = new Uint8Array(em.nx * em.ny * 4);
      for (let i = 0; i < em.nx * em.ny; i++) {
        const v = Math.max(0, Math.min(1, em.values[i]));
        const r = v < 0.5 ? 120 + 270 * v : 255, gg = v < 0.5 ? 30 + 60 * v : 60 + 300 * (v - 0.5), b = v < 0.5 ? 150 + 20 * v : 160 + 140 * (v - 0.5);
        data.set([Math.min(255, r), Math.min(255, gg), Math.min(255, b), v < 0.04 ? 0 : Math.round(60 + 175 * v)], i * 4);
      }
      const tex = new THREE.DataTexture(data, em.nx, em.ny, THREE.RGBAFormat);
      tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestFilter; tex.needsUpdate = true;
      const w = em.nx * em.cell_m, h = em.ny * em.cell_m;
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat(0xffffff, 0.9, { map: tex, clippingPlanes: clip }));
      plane.rotation.x = -Math.PI / 2; plane.position.copy(this.V(em.origin[0] + w / 2, em.origin[1] + h / 2, 0.035));
      this._o(plane, 21); A.map = plane.material;
      const pts = [];
      for (let i = 0; i <= em.nx; i++) { const x = em.origin[0] + i * em.cell_m; pts.push(this.V(x, em.origin[1], 0.04), this.V(x, em.origin[1] + h, 0.04)); }
      for (let j = 0; j <= em.ny; j++) { const y = em.origin[1] + j * em.cell_m; pts.push(this.V(em.origin[0], y, 0.04), this.V(em.origin[0] + w, y, 0.04)); }
      const grid = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x8a6a9a, transparent: true, opacity: 0.22, depthTest: false, fog: false, clippingPlanes: clip }));
      this._o(grid, 22); A.grid = grid.material;
    }
    // how the person was placed: the ellipse of every used pair that passes through the estimate
    const v = (s.victims || []).find((q) => q.x != null);
    if (v) {
      A.fixes = [];
      for (const l of s.links || []) {
        if (l.used === false || !pk[l.a] || !pk[l.b]) continue;
        const L = Math.hypot(v.x - pk[l.a][0], v.y - pk[l.a][1]) + Math.hypot(v.x - pk[l.b][0], v.y - pk[l.b][1]);
        A.fixes.push(this._ellipse(bistatic(pk[l.a], pk[l.b], L), 0xff8cc6, 0, 0.08, clip));
      }
      const e = sigmaEllipse(v), vg = new THREE.Group();
      vg.position.copy(this.V(v.x, v.y, 0)); vg.rotation.y = e.ang; this.g.add(vg);
      const disc = new THREE.Mesh(DISC, mat(VICTIM_COLOR, 0.24)); disc.rotation.x = -Math.PI / 2; disc.scale.set(e.a, e.b, 1); disc.position.y = 0.1; disc.renderOrder = 27;
      const ring = new THREE.Mesh(RING, mat(VICTIM_COLOR, 0.95)); ring.rotation.x = -Math.PI / 2; ring.scale.set(e.a, e.b, 1); ring.position.y = 0.11; ring.renderOrder = 28;
      const ring2 = new THREE.Mesh(RING, mat(VICTIM_COLOR, 0.4)); ring2.rotation.x = -Math.PI / 2; ring2.scale.set(2 * e.a, 2 * e.b, 1); ring2.position.y = 0.11; ring2.renderOrder = 28;
      const breath = new THREE.Mesh(RING, mat(VICTIM_COLOR, 0)); breath.rotation.x = -Math.PI / 2; breath.position.y = 0.12; breath.renderOrder = 28;
      vg.add(disc, ring, ring2, breath);
      const core = new THREE.Mesh(new THREE.SphereGeometry(0.2, 20, 14), mat(0xffd0e6, 1)); core.position.copy(this.V(v.x, v.y, 0.3)); this._o(core, 32);
      const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 2.4), mat(VICTIM_COLOR, 0.8)); beam.position.copy(this.V(v.x, v.y, 1.5)); this._o(beam, 31);
      const tag = this._lab(`${v.id}: ${victimState(v)}`, "#fff", "rgba(150,20,80,.92)", 0.034); tag.position.copy(this.V(v.x, v.y, 3.0)); this.g.add(tag);
      const conf = this._lab(`within ${(v.sigma_m ?? e.a).toFixed(1)} m, not tagged`, "#ffd0e6", "rgba(40,8,24,.85)", 0.025); conf.position.copy(this.V(v.x, v.y, 2.35)); this.g.add(conf);
      A.victim = { v, vg, disc, ring, ring2, breath, core, tag, conf, e, f: v.state === "breathing" && v.breathing_bpm ? v.breathing_bpm / 60 : v.state === "moving" || v.moving ? 1.6 : 0 };
      this._pickable(this.V(v.x, v.y, 0.4), Math.max(0.6, e.a), { tip: `${v.id}: ${v.text || victimState(v)}`, pick: v.id, kind: "victim" });
    }
    // where the room differs from the twin
    A.flags = [];
    for (const m of s.mismatches || []) {
      const w = m.where || {};
      let at = w.at;
      if (w.kind === "segment" && w.a && w.b) {
        const dm = mat(FLAG_COLOR, 0.9);
        this._bar(w.a, w.b, 0.12, 0.12, dm, 29);
        at = [(w.a[0] + w.b[0]) / 2, (w.a[1] + w.b[1]) / 2];
      }
      if (!at) continue;
      if (w.radius_m) {
        const r = new THREE.Mesh(RING, mat(FLAG_COLOR, 0.8)); r.rotation.x = -Math.PI / 2; r.scale.setScalar(w.radius_m); r.position.copy(this.V(at[0], at[1], 0.09)); this._o(r, 29);
        const d = new THREE.Mesh(DISC, mat(FLAG_COLOR, 0.12)); d.rotation.x = -Math.PI / 2; d.scale.setScalar(w.radius_m); d.position.copy(this.V(at[0], at[1], 0.085)); this._o(d, 28);
      }
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 1.7), mat(0xffd9a0, 1)); pole.position.copy(this.V(at[0], at[1], 0.85)); this._o(pole, 33);
      const flagGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0.75, -0.22, 0), new THREE.Vector3(0, -0.44, 0)]);
      const flag = new THREE.Mesh(flagGeo, mat(FLAG_COLOR, 1)); flag.position.copy(this.V(at[0], at[1], 1.7)); this._o(flag, 34);
      const lab = this._lab(`${m.id}: differs from the twin`, "#1a1405", "rgba(255,159,10,.95)", 0.025); lab.position.copy(this.V(at[0], at[1], 2.25)); this.g.add(lab);
      A.flags.push(flag);
      this._pickable(this.V(at[0], at[1], 0.9), Math.max(0.6, w.radius_m || 0.6), { tip: `${m.id} (${m.kind.replace("_", " ")}, confidence ${m.confidence}): ${m.text}`, pick: m.id, kind: "flag" });
    }
  }

  frame(now) {
    const v = this.view, A = this.anim;
    if (!v || !A) return;
    const t = (now - A.t0) / 1000;
    if (A.outline) A.outline.opacity = v.phase === "registered" ? 0.55 + 0.45 * Math.max(0, 1 - Math.abs(t - 1.5) / 0.6) : 0.5;
    if (A.zone) A.zone.opacity = 0.05 + 0.04 * (0.5 + 0.5 * Math.sin(now / 420));
    for (const a of A.arcs || []) {
      const u = (t - a.delay) / 0.9;
      if (v.phase !== "thrown") { a.dot.visible = false; a.line.material.opacity = 0; continue; }
      a.line.material.opacity = u > 0 ? Math.max(0, 0.75 - Math.max(0, u - 1) * 0.5) : 0;
      a.dot.visible = u > 0 && u < 1.6;
      a.dot.position.copy(a.curve.getPoint(Math.min(1, Math.max(0, u))));
      a.dot.material.opacity = u < 1 ? 1 : Math.max(0, 1 - (u - 1) / 0.6);
      const k = Math.max(0, u - 1);
      a.splash.material.opacity = u > 1 ? Math.max(0, 0.8 - k * 0.8) : 0; a.splash.scale.setScalar(0.2 + k * 1.2);
    }
    if (A.float) { A.float.pivot.rotation.y = now / 2600; A.float.pivot.position.y = 2.4 + 0.08 * Math.sin(now / 500); }
    if (A.linkMat && (v.phase === "located" || v.phase === "scanning")) A.linkMat.opacity = 0.45 + 0.3 * (0.5 + 0.5 * Math.sin(now / 260));
    for (const p of A.pucks || []) {
      if (v.phase === "registered") { const u = ease((t - 0.2) / 1.3); p.g.position.lerpVectors(p.from, p.to, u); }
      else p.g.position.copy(p.to);
    }
    for (const l of A.links || []) l.visible = v.phase !== "registered" || t > 1.5;
    for (const s of A.sweep || []) {
      const u = (((t - s.delay) % A.sweepPeriod) + A.sweepPeriod) % A.sweepPeriod / 1.9;
      if (u > 1 || t < s.delay) { s.g.ring.material.opacity = 0; continue; }
      const e = bistatic(s.f1, s.f2, s.d + 0.15 + u * 9);
      s.g.ring.scale.set(e.a, e.b, 1); s.g.ring.material.opacity = 0.75 * (1 - u);
    }
    // the scan lands: cells fade in, then the ellipses that place the person, then the person
    const land = v.phase === "done" ? t : 99;
    if (A.map) A.map.opacity = 0.9 * ease(land / 0.9);
    if (A.grid) A.grid.opacity = 0.22 * ease(land / 0.9);
    (A.fixes || []).forEach((g, i) => { g.ring.material.opacity = land < 3.2 ? 0.8 * ease((land - 0.5 - i * 0.07) / 0.4) : 0.8 - 0.62 * ease((land - 3.2) / 1.2); });
    if (A.victim) {
      const V = A.victim, u = ease((land - 1.2) / 0.6), ph = V.f ? (t * V.f) % 1 : 0;
      const breathe = V.f ? 0.5 - 0.5 * Math.cos(2 * Math.PI * ph) : 0;
      V.vg.scale.setScalar(Math.max(0.001, u));
      V.core.scale.setScalar(Math.max(0.001, u * (1 + 0.45 * breathe)));
      V.disc.material.opacity = 0.16 + 0.16 * breathe;
      V.breath.scale.set(V.e.a * (0.4 + 1.4 * ph), V.e.b * (0.4 + 1.4 * ph), 1); V.breath.material.opacity = V.f ? 0.7 * (1 - ph) * u : 0;
      for (const o of [V.tag, V.conf]) o.material.opacity = u;
    }
    for (const f of A.flags || []) f.rotation.y = 0.25 * Math.sin(now / 300);
  }
}
