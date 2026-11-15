// 3D "dollhouse" of the exercise floor (three.js, vendored). Built from the twin bundle only:
// in fixture mode that is the synthetic interior; in live mode the normalised Matterport model.
// Layer 2 holds the physical building (slab, floors, walls): the smoke view's camera inset sees only that layer.
import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { Scout3D } from "./scout3d.js";
import { Beacon3D } from "./beacon3d.js";
import { labelPoint } from "./plan2d.js";

const ROOM_KEY = { suite: "mSuite", corridor: "mCorridor", stair: "mStair", lobby: "mLobby", elevator: "mService", restroom: "mService", mechanical: "mService" };
const WALL_H = 1.3, WALL_T = 0.16, SMOKE_COLOR = 0x7f8489, PHYS = 2;
const REDUCED = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
// is a CSS colour light (so it needs a dark backdrop on a light model)?
function isLight(c) {
  const m = String(c).match(/^#([0-9a-f]{6})$/i);
  let r, g, b;
  if (m) { const n = parseInt(m[1], 16); r = n >> 16; g = (n >> 8) & 255; b = n & 255; }
  else { const q = String(c).match(/rgba?\(([^)]+)\)/); if (!q) return false; [r, g, b] = q[1].split(",").map(Number); }
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.6;
}

export function label(text, color = "#dfe6ee", size = 64, bg = null, fixed = false, halo = "rgba(8,11,15,.9)") {
  const c = document.createElement("canvas"), ctx = c.getContext("2d");
  const font = `700 ${size}px "Barlow Condensed", "Arial Narrow", Arial, sans-serif`;
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + 28;
  c.width = w; c.height = size + 24;
  ctx.font = font; ctx.textBaseline = "middle"; ctx.textAlign = "center";
  if (bg) { ctx.fillStyle = bg; ctx.beginPath(); ctx.roundRect(0, 0, c.width, c.height, 14); ctx.fill(); }
  ctx.lineWidth = 8; ctx.strokeStyle = halo; ctx.lineJoin = "round"; if (!bg) ctx.strokeText(text, w / 2, c.height / 2 + 2);
  ctx.fillStyle = color; ctx.fillText(text, w / 2, c.height / 2 + 2);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true, fog: false, sizeAttenuation: !fixed }));
  s.scale.set(c.width / c.height * 1.5, 1.5, 1); s.renderOrder = 10;
  return s;
}
const phys = (o) => { o.layers.enable(PHYS); return o; };

export class Twin3D {
  constructor(container, bundle, colors, opts = {}) {
    this.el = container; this.twin = bundle.twin; this.site = bundle.site; this.colors = colors; this.opts = opts; this.bundle = bundle;
    this.pal = opts.pal || { theme: "night", mSuite: "#1d2631", mCorridor: "#2a3644", mStair: "#1b3a2b", mLobby: "#2c2e47", mService: "#22262f", mWall3d: "#b5c0cc", mSlab: "#141a21", mGround: "#7d858e", mLabel: "#b9c4d0", mLabelHz: "#ff9a80", ink: "#e8eef3", paper: "#151d25" };
    // labels on a light model get a white halo; light-coloured text gets a dark tag behind it
    this.label = (text, color, size = 64, bg = null, fixed = false) => {
      const day = this.pal.theme !== "night";
      if (!bg && day && isLight(color)) bg = "rgba(19,33,46,.9)";
      return label(text, color, size, bg, fixed, day ? "rgba(255,255,255,.95)" : "rgba(8,11,15,.9)");
    };
    const t = this.twin;
    this.floor = t.floors.find((f) => f.id === t.exercise_floor);
    const xs = this.floor.outline.map((p) => p[0]), ys = this.floor.outline.map((p) => p[1]);
    this.fx = Math.max(...xs); this.fy = Math.max(...ys); this.cx = this.fx / 2; this.cy = this.fy / 2;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.renderer.localClippingEnabled = true;
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(SMOKE_COLOR, 0);          // density 0 for the main view; the inset raises it
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.3, 600);
    const D = this.D = Math.max(this.fx, this.fy);
    this.k = Math.max(0.35, Math.min(1, D / 40));            // crew markers in metres: smaller on a small floor (fixture: 1)
    this.home = { pos: new THREE.Vector3(-0.32 * D, 1.02 * D, 0.98 * D), target: new THREE.Vector3(0, 0, 1.5) };
    this.camera.position.copy(this.home.pos);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; this.controls.maxPolarAngle = 1.32; this.controls.minDistance = 5; this.controls.maxDistance = 4 * D;
    this.controls.target.copy(this.home.target);
    this.hemi = phys(new THREE.HemisphereLight(0xdfe9f5, 0x0b0e12, 1.15)); this.scene.add(this.hemi);
    const sun = phys(new THREE.DirectionalLight(0xffffff, 1.3)); sun.position.set(-30, 60, 40); this.scene.add(sun);
    this.rooms = {}; this.units = {}; this.routes = {}; this.hazards = new THREE.Group(); this.pre = new THREE.Group(); this.anchors = new THREE.Group(); this.obs = new THREE.Group();
    this.scene.add(this.hazards, this.pre, this.anchors, this.obs);
    this._build();
    this._ground(bundle.aerial);
    this._orientation();
    this._applyPalette();
    this.scout = new Scout3D(this);
    this.beacon = new Beacon3D(this);
    this._smokeSetup();
    this._pickSetup();
    this.resize();
    const loop = () => { this._frame(); this._raf = requestAnimationFrame(loop); };
    loop();
  }
  V(x, y, h = 0) { return new THREE.Vector3(x - this.cx, h, -(y - this.cy)); }
  _poly(poly, h, mat) {
    const sh = new THREE.Shape(poly.map((p) => new THREE.Vector2(p[0] - this.cx, p[1] - this.cy)));
    const g = new THREE.ShapeGeometry(sh); g.rotateX(-Math.PI / 2);
    const m = new THREE.Mesh(g, mat); m.position.y = h; return m;
  }
  _build() {
    const t = this.twin, fid = this.floor.id;
    this.slabMat = new THREE.MeshStandardMaterial({ color: 0x141a21, roughness: 1 });
    const slab = phys(new THREE.Mesh(new THREE.BoxGeometry(this.fx + 0.8, 0.5, this.fy + 0.8), this.slabMat));
    slab.position.y = -0.27; this.scene.add(slab);
    const suiteRooms = {};
    for (const r of t.rooms.filter((q) => q.floor === fid && q.suite)) (suiteRooms[r.suite] ||= []).push(r);
    for (const r of t.rooms.filter((q) => q.floor === fid)) {
      const mat = new THREE.MeshStandardMaterial({ color: 0x202833, roughness: 0.95 });
      const m = phys(this._poly(r.polygon, 0.01, mat)); m.userData.kind = r.kind;
      this.scene.add(m); this.rooms[r.id] = m;
    }
    this.suiteRooms = suiteRooms;
    const wallMat = this.wallMat = new THREE.MeshStandardMaterial({ color: 0xb5c0cc, roughness: 0.8 });
    const doors = t.doors.filter((d) => d.floor === fid);
    for (const w of t.walls.filter((q) => q.floor === fid)) {
      const dx = w.b[0] - w.a[0], dy = w.b[1] - w.a[1], len = Math.hypot(dx, dy);
      if (len < 0.05) continue;
      const ux = dx / len, uy = dy / len;
      let cuts = [];
      for (const d of doors) {
        const tt = (d.at[0] - w.a[0]) * ux + (d.at[1] - w.a[1]) * uy;
        const off = Math.abs(-(d.at[0] - w.a[0]) * uy + (d.at[1] - w.a[1]) * ux);
        if (off < 0.06 && tt > -0.01 && tt < len + 0.01) cuts.push([tt - (d.width_m || 0.9) / 2 - 0.1, tt + (d.width_m || 0.9) / 2 + 0.1]);
      }
      cuts.sort((a, b) => a[0] - b[0]);
      let pos = 0; const pieces = [];
      for (const [a, b] of cuts) { if (a > pos) pieces.push([pos, Math.min(a, len)]); pos = Math.max(pos, b); }
      if (pos < len) pieces.push([pos, len]);
      for (const [a, b] of pieces) {
        if (b - a < 0.05) continue;
        const m = phys(new THREE.Mesh(new THREE.BoxGeometry(b - a, WALL_H, WALL_T), wallMat));
        const mid = (a + b) / 2;
        m.position.copy(this.V(w.a[0] + ux * mid, w.a[1] + uy * mid, WALL_H / 2));
        m.rotation.y = Math.atan2(dy, dx);
        this.scene.add(m);
      }
    }
    for (const tg of t.tags || []) this.addTag(tg);
  }
  // the outside: the aerial ortho image laid under the floor through the twin's georeference (a sand table)
  _ground(aerial) {
    const im = (aerial?.images || []).find((i) => i.id === "ortho") || aerial?.images?.[0];
    if (!im?.bbox) return;
    const g0 = this.twin.georef, b = g0.bearing_deg * Math.PI / 180, lat0 = g0.origin.lat, lon0 = g0.origin.lon;
    const toB = (lat, lon) => { const n = (lat - lat0) * 111320, e = (lon - lon0) * 111320 * Math.cos(lat0 * Math.PI / 180); return [e * Math.sin(b) + n * Math.cos(b), -e * Math.cos(b) + n * Math.sin(b)]; };
    const [w, s, e, n] = im.bbox, nw = toB(n, w), ne = toB(n, e), sw = toB(s, w), se = toB(s, e);
    const W = Math.hypot(ne[0] - nw[0], ne[1] - nw[1]), H = Math.hypot(nw[0] - sw[0], nw[1] - sw[1]);
    const tex = new THREE.TextureLoader().load(im.url || "/" + im.path); tex.colorSpace = THREE.SRGBColorSpace;
    this.groundMat = new THREE.MeshBasicMaterial({ map: tex, color: 0x7d858e, fog: false });
    const m = new THREE.Mesh(new THREE.PlaneGeometry(W, H), this.groundMat);
    m.rotation.x = -Math.PI / 2;
    const c = [(nw[0] + se[0]) / 2, (nw[1] + se[1]) / 2], holder = new THREE.Group();
    holder.position.copy(this.V(c[0], c[1], -0.56)); holder.rotation.y = Math.atan2(ne[1] - nw[1], ne[0] - nw[0]);
    holder.add(m); this.scene.add(holder);
  }
  // compass and the fire-service side letters (A = address side, then clockwise), from the twin's georeference
  _orientation() {
    const b = this.twin.georef.bearing_deg * Math.PI / 180;
    const dirOf = (brg) => { const r = brg * Math.PI / 180, e = Math.sin(r), n = Math.cos(r); return [e * Math.sin(b) + n * Math.cos(b), -e * Math.cos(b) + n * Math.sin(b)]; };
    if (this.orientG) { this.scene.remove(this.orientG); this.orientG.traverse((q) => { q.geometry?.dispose(); q.material?.map?.dispose(); q.material?.dispose?.(); }); }
    const g = this.orientG = new THREE.Group(); this.scene.add(g);
    for (const [k, s] of Object.entries(this.site.sides || {})) {
      const d = dirOf(s.bearing_deg), hx = this.fx / 2 + 0.4, hy = this.fy / 2 + 0.4;
      const tt = Math.min(Math.abs(d[0]) > 1e-3 ? hx / Math.abs(d[0]) : 1e9, Math.abs(d[1]) > 1e-3 ? hy / Math.abs(d[1]) : 1e9) + 2.6;
      const sp = label(k, this.pal.paper, 56, this.pal.ink, true); sp.scale.multiplyScalar(0.032 / sp.scale.y);
      sp.position.copy(this.V(this.cx + d[0] * tt, this.cy + d[1] * tt, 0.4)); g.add(sp);
    }
    const n = dirOf(0), at = [-4.2, this.fy + 3.2];
    const shape = new THREE.Shape([new THREE.Vector2(0, 1.6), new THREE.Vector2(0.75, -0.9), new THREE.Vector2(0, -0.4), new THREE.Vector2(-0.75, -0.9)]);
    const arrow = new THREE.Mesh(new THREE.ShapeGeometry(shape), new THREE.MeshBasicMaterial({ color: new THREE.Color(this.pal.ink), side: THREE.DoubleSide, fog: false }));
    arrow.rotation.x = -Math.PI / 2; arrow.rotation.z = Math.atan2(n[1], n[0]) - Math.PI / 2;
    const holder = new THREE.Group(); holder.position.copy(this.V(at[0], at[1], 0.05)); holder.add(arrow); g.add(holder);
    const nl = this.label("N", this.pal.ink, 56, null, true); nl.scale.multiplyScalar(0.032 / nl.scale.y); nl.position.copy(this.V(at[0] + n[0] * 2.6, at[1] + n[1] * 2.6, 0.3)); g.add(nl);
  }
  // colours of the model and its labels, from the palette (style.css); called at build and when the theme changes
  _applyPalette() {
    const P = this.pal, day = P.theme !== "night";
    for (const m of Object.values(this.rooms)) { m.userData.base = P[ROOM_KEY[m.userData.kind]] || P.mService; m.material.color.set(m.userData.tint ?? m.userData.base); }
    this.wallMat.color.set(P.mWall3d || P.mWall);
    this.slabMat.color.set(P.mSlab);
    if (this.groundMat) { this.groundMat.color.set(P.mGround); this.groundMat.transparent = day; this.groundMat.opacity = day ? 0.42 : 1; this.groundMat.needsUpdate = true; }
    this.hemi.color.set(day ? 0xffffff : 0xdfe9f5); this.hemi.groundColor.set(day ? 0x8a96a2 : 0x0b0e12); this.hemi.intensity = day ? 1.5 : 1.15;
    for (const s of this.roomLabels || []) { this.scene.remove(s); s.material.map?.dispose(); s.material.dispose(); }
    this.roomLabels = [];
    for (const r of this.twin.rooms.filter((q) => q.floor === this.floor.id)) {
      const c = labelPoint(r.polygon);
      if (r.kind === "suite") {
        const shared = (this.suiteRooms[r.suite] || []).length > 1;      // a scanned suite: rooms are named by their label
        const s = this.label(shared ? r.label : r.suite, P.mLabel, 60, null, true); s.scale.multiplyScalar(0.034 / s.scale.y); s.position.copy(this.V(c[0], c[1], 0.9)); this.scene.add(s);
        s.userData.room = r.id; this.roomLabels.push(s);
      } else if (r.kind === "stair") {
        const s = this.label(r.label, P.mLabel, 46, null, true); s.scale.multiplyScalar(0.026 / s.scale.y); s.position.copy(this.V(c[0], c[1], 1.9)); this.scene.add(s);
        this.roomLabels.push(s);
      }
    }
    if (this._scoutRoom) for (const s of this.roomLabels) s.visible = s.userData.room !== this._scoutRoom.id;
  }
  setTheme(pal, colors) {
    this.pal = pal; this.colors = colors;
    this._applyPalette();
    this._orientation();
    for (const g of Object.values(this.units)) { this.scene.remove(g); g.traverse((q) => { q.geometry?.dispose(); q.material?.map?.dispose(); q.material?.dispose?.(); }); }
    this.units = {};
    for (const g of Object.values(this.routes)) g.userData.key = null;
    this._hk = null; this._ak = null;
    this.setPreplan(this._pp || null);
    this.beacon.key = null; this.scout.key = null;
  }
  addTag(tg) {
    if (tg.floor && tg.floor !== this.floor.id) return;
    const obs = tg.kind === "observation";
    const m = new THREE.Mesh(new THREE.OctahedronGeometry(obs ? 0.55 : 0.4), new THREE.MeshStandardMaterial({ color: obs ? 0x45535f : 0x35d0ba, emissive: obs ? 0x1b2530 : 0x0f6e62, emissiveIntensity: 0.6 }));
    m.position.copy(this.V(tg.position[0], tg.position[1], obs ? 1.9 : 1.5));
    (obs ? this.obs : this.scene).add(m);
    if (obs) { const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 1.9), new THREE.MeshBasicMaterial({ color: 0xffffff })); stem.position.copy(this.V(tg.position[0], tg.position[1], 0.95)); this.obs.add(stem); }
  }
  highlightSuite(suite, on) {
    for (const r of this.twin.rooms.filter((q) => q.suite === suite)) { const m = this.rooms[r.id]; if (m) m.material.color.set(on ? this.pal.mHot : (m.userData.tint ?? m.userData.base)); }
  }
  setPreplan(pp) {
    this._pp = pp;
    this.pre.clear();
    for (const m of Object.values(this.rooms)) { m.userData.tint = undefined; m.material.color.set(m.userData.base); }
    if (!pp) { this.obs.clear(); return; }
    const t = pp.tactical || {};
    for (const h of t.life_hazards || []) { const m = this.rooms[h.room]; if (m) { m.userData.tint = this.pal.mLifehaz; m.material.color.set(this.pal.mLifehaz); } }
    const seen = {}, fg = this.pal.theme === "night" ? "#121a22" : "#ffffff";
    for (const [key, color, word] of [["attack_stair", new THREE.Color(this.pal.e1).getHex(), "Attack"], ["evac_stair", new THREE.Color(this.pal.rit).getHex(), "Evacuation"]]) {
      const st = this.twin.stairs.find((s) => s.id === t[key]); if (!st) continue;
      const r = this.twin.rooms.find((q) => q.id === st.room), door = this.twin.doors.find((d) => d.id === st.door);
      if (r?.kind !== "stair" && door) {
        // the scan has no stair: the way in and out is the suite entrance, marked once at its door, not by painting a room
        if (seen[st.id]) { const old = seen[st.id]; this.pre.remove(old); const s2 = label(`Attack and evacuation: ${st.label}`, fg, 44, this.pal.e1, true); s2.scale.multiplyScalar(0.024 / s2.scale.y); s2.position.copy(old.position); this.pre.add(s2); continue; }
        const n = 1;
        const ring = new THREE.Mesh(new THREE.RingGeometry(0.55, 0.75, 32), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthTest: false }));
        ring.rotation.x = -Math.PI / 2; ring.position.copy(this.V(door.at[0], door.at[1], 0.08)); ring.renderOrder = 16; this.pre.add(ring);
        const s = label(`${word}: ${st.label}`, fg, 44, "#" + color.toString(16).padStart(6, "0"), true); s.scale.multiplyScalar(0.024 / s.scale.y); s.position.copy(this.V(door.at[0], door.at[1], 1.2 + 0.7 * n)); this.pre.add(s); seen[st.id] = s;
        continue;
      }
      this.pre.add(this._poly(r.polygon, 0.04, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.28 })));
      const c = [r.polygon.reduce((s, p) => s + p[0], 0) / r.polygon.length, r.polygon.reduce((s, p) => s + p[1], 0) / r.polygon.length];
      const s = label(word, fg, 44, "#" + color.toString(16).padStart(6, "0"), true); s.scale.multiplyScalar(0.026 / s.scale.y); s.position.copy(this.V(c[0], c[1], 3.6)); this.pre.add(s);
    }
    for (const a of t.anchor_drops || []) {
      const m = new THREE.Mesh(new THREE.OctahedronGeometry(0.5), new THREE.MeshBasicMaterial({ color: new THREE.Color(this.pal.ink), wireframe: true }));
      m.position.copy(this.V(a.at[0], a.at[1], 2.3)); this.pre.add(m);
    }
  }
  _unit(u) {
    const color = new THREE.Color(this.colors[u.id] || "#ffffff");
    const g = new THREE.Group();
    const k = this.k;
    g.ball = new THREE.Mesh(new THREE.SphereGeometry(0.75 * k, 24, 16), new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.85, fog: false }));
    g.ball.position.y = 1.1 * Math.max(k, 0.6);
    g.beam = new THREE.Mesh(new THREE.CylinderGeometry(0.07 * k, 0.07 * k, 6 * k), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55, fog: false }));
    g.beam.position.y = 3 * k;
    g.disc = new THREE.Mesh(new THREE.CircleGeometry(1, 40), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.2, depthWrite: false, fog: false }));
    g.disc.rotation.x = -Math.PI / 2; g.disc.position.y = 0.06;
    g.tag = label(u.id, this.pal.theme === "night" ? "#121a22" : "#ffffff", 64, this.colors[u.id] || this.pal.ink, true); g.tag.position.y = 6.4 * k; g.tag.scale.multiplyScalar(0.034 / g.tag.scale.y);
    g.tag.center.set(u.id === "RIT" ? -0.08 : 1.08, 0.5);      // two crews at one spot: their call signs sit side by side
    for (const o of [g.ball, g.beam, g.disc]) o.renderOrder = 18;
    g.add(g.ball, g.beam, g.disc, g.tag);
    g.target = null; g.visible = false;
    this.scene.add(g);
    return g;
  }
  _route(u) {
    let g = this.routes[u.id];
    if (!g) { g = this.routes[u.id] = new THREE.Group(); this.scene.add(g); }
    const key = u.route && !u.route.done ? JSON.stringify(u.route.polyline) + u.route.valid : "";
    if (key === g.userData.key) return;
    g.userData.key = key; g.clear();
    if (!key) return;
    const mat = new THREE.MeshBasicMaterial({ color: u.route.valid ? new THREE.Color(this.colors[u.id] || "#fff") : new THREE.Color(this.pal.danger), transparent: true, opacity: u.route.valid ? 0.9 : 0.5, fog: false });
    const pts = u.route.polyline;
    for (let i = 0; i < pts.length - 1; i++) {
      const dx = pts[i + 1][0] - pts[i][0], dy = pts[i + 1][1] - pts[i][1], len = Math.hypot(dx, dy);
      if (len < 0.01) continue;
      const m = new THREE.Mesh(new THREE.BoxGeometry(len + 0.45, 0.1, 0.45), mat);
      m.position.copy(this.V((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2, 0.12)); m.rotation.y = Math.atan2(dy, dx);
      m.renderOrder = 17;
      g.add(m);
    }
  }
  update(st, selected) {
    for (const u of st.units) {
      const g = this.units[u.id] ||= this._unit(u);
      if (u.x == null) { g.visible = false; continue; }
      const p = this.V(u.x, u.y, 0);
      if (!g.visible) { g.position.copy(p); g.visible = true; }
      g.target = p;
      g.disc.scale.setScalar(Math.max(1.1 * this.k, u.sigma_m || 0));
      g.mayday = u.status === "mayday";
      this._route(u);
    }
    const hk = JSON.stringify(st.hazards.map((h) => h.id));
    if (hk !== this._hk) {
      this._hk = hk; this.hazards.clear();
      const mat = new THREE.MeshBasicMaterial({ color: new THREE.Color(this.pal.danger), transparent: true, opacity: 0.38, depthWrite: false });
      for (const h of st.hazards) {
        const g = h.geometry || {};
        if (g.kind === "room") { const m = this._poly(g.polygon, 0.05, mat); this.hazards.add(m); }
        const at = g.kind === "edge" ? [(g.polyline[0][0] + g.polyline[1][0]) / 2, (g.polyline[0][1] + g.polyline[1][1]) / 2] : g.kind === "point" ? g.at : null;
        if (at) { const m = new THREE.Mesh(new THREE.CylinderGeometry(1.5, 1.5, 2.2, 28), mat); m.position.copy(this.V(at[0], at[1], 1.1)); this.hazards.add(m);
          const s = label("Lost", this.pal.theme === "night" ? "#121a22" : "#ffffff", 44, this.pal.danger, true); s.scale.multiplyScalar(0.03 / s.scale.y); s.position.copy(this.V(at[0], at[1], 3.4)); this.hazards.add(s); }
      }
    }
    const ak = JSON.stringify(st.anchors);
    if (ak !== this._ak) {
      this._ak = ak; this.anchors.clear();
      for (const a of st.anchors || []) {
        const m = new THREE.Mesh(new THREE.ConeGeometry(0.5, 1.4, 4), new THREE.MeshStandardMaterial({ color: new THREE.Color(this.pal.ink3 || "#8593a0") }));
        m.position.copy(this.V(a.x, a.y, 0.7)); this.anchors.add(m);
        const s = this.label(a.id, this.pal.ink, 40, null, true); s.scale.multiplyScalar(0.022 / s.scale.y); s.position.copy(this.V(a.x, a.y, 2.2)); this.anchors.add(s);
      }
    }
  }

  // ---------------------------------------------------------------- scout: layer, framing, smoke, picking
  setScout(view) {
    this.scout.set(view);
    this._scoutRoom = view?.room || null;
    // a room label would sit under the scan's own labels: hide it while the room is being scanned
    for (const s of this.roomLabels || []) s.visible = !(view && s.userData.room === view.room.id);
    if (view?.room && view.room.id !== this._smokeRoom) this._smokeTexture(view.room);
  }
  setBeacons(view, selected) {
    this.beacon.set(view, selected);
    if (view?.b?.room && !this._scoutRoom && this._smokeRoom !== view.b.room) {
      const r = this.twin.rooms.find((q) => q.id === view.b.room);
      if (r) this._smokeTexture({ id: r.id, centre: [r.polygon.reduce((s, p) => s + p[0], 0) / r.polygon.length, r.polygon.reduce((s, p) => s + p[1], 0) / r.polygon.length] });
    }
  }
  // bbox [x0, y0, x1, y1] in building metres, or null for the whole floor
  setFocus(bbox) {
    const k = bbox ? bbox.map((v) => v.toFixed(1)).join(",") : "";
    if (k === this._focusKey) return;
    this._focusKey = k;
    let pos, target;
    if (!bbox) { pos = this.home.pos.clone(); target = this.home.target.clone(); }
    else ({ pos, target } = this._fit(bbox));
    this._tween = { t0: performance.now(), p0: this.camera.position.clone(), q0: this.controls.target.clone(), p1: pos, q1: target };
  }
  // camera on the home direction, as close as possible with the box (walls up to 2 m) inside the frame,
  // centred a little low so the scan's header at the top left does not cover it
  _fit(bbox) {
    const cam = this._fitCam ||= new THREE.PerspectiveCamera();
    cam.fov = this.camera.fov; cam.aspect = this.camera.aspect; cam.near = 0.3; cam.far = 600; cam.updateProjectionMatrix();
    const dir = this.home.pos.clone().sub(this.home.target).normalize(), fwd = new THREE.Vector3(-dir.x, 0, -dir.z).normalize();
    const pts = [];
    for (const x of [bbox[0], bbox[2]]) for (const y of [bbox[1], bbox[3]]) for (const h of [0, 2]) pts.push(this.V(x, y, h));
    const target = this.V((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, 0.3);
    const proj = (d) => {
      cam.position.copy(target).addScaledVector(dir, d); cam.lookAt(target); cam.updateMatrixWorld();
      const r = { x0: 9, x1: -9, y0: 9, y1: -9 };
      for (const p of pts) { const q = p.clone().project(cam); r.x0 = Math.min(r.x0, q.x); r.x1 = Math.max(r.x1, q.x); r.y0 = Math.min(r.y0, q.y); r.y1 = Math.max(r.y1, q.y); }
      return r;
    };
    let dist = 20;
    for (let it = 0; it < 4; it++) {
      let lo = 2, hi = 500;
      for (let k = 0; k < 28; k++) { const m = (lo + hi) / 2, r = proj(m); if (Math.max(-r.x0, r.x1) <= 0.86 && r.y1 - r.y0 <= 1.6 && r.y1 < 0.92 && r.y0 > -0.92) hi = m; else lo = m; }
      dist = hi;
      const r = proj(dist);
      target.addScaledVector(fwd, ((r.y0 + r.y1) / 2 + 0.02) * dist * 0.4);
    }
    return { pos: target.clone().addScaledVector(dir, dist), target };
  }
  _smokeSetup() {
    const W = this.fx + 6, H = this.fy + 6;
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const ctx = c.getContext("2d");
    let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    ctx.fillStyle = "rgba(255,255,255,0.08)"; ctx.fillRect(0, 0, 256, 256);
    for (let i = 0; i < 200; i++) {
      const x = rnd() * 256, y = rnd() * 256, r = 14 + rnd() * 60;
      for (const [dx, dy] of [[0, 0], [256, 0], [-256, 0], [0, 256], [0, -256]]) {
        const g = ctx.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, r);
        g.addColorStop(0, `rgba(255,255,255,${0.1 + rnd() * 0.3})`); g.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = g; ctx.fillRect(x + dx - r, y + dy - r, 2 * r, 2 * r);
      }
    }
    const noise = new THREE.CanvasTexture(c); noise.wrapS = noise.wrapT = THREE.RepeatWrapping; noise.repeat.set(2.2, 2.6);
    this.smoke = { planes: [], level: 0, noise, W, H };
    for (let i = 0; i < 7; i++) {
      const tex = noise.clone(); tex.needsUpdate = true; tex.offset.set(i * 0.37, i * 0.21);
      const col = new THREE.Color(0x4f5358).lerp(new THREE.Color(0xb4b9be), i / 6);
      const m = new THREE.Mesh(new THREE.PlaneGeometry(W, H), new THREE.MeshBasicMaterial({ color: col, map: tex, transparent: true, opacity: 0, depthWrite: false, fog: false }));
      m.rotation.x = -Math.PI / 2; m.position.y = 0.25 + i * 0.36; m.renderOrder = 15; m.visible = false;
      m.userData.drift = [(i % 2 ? 1 : -1) * (0.004 + i * 0.0015), 0.003 + (i % 3) * 0.002];
      this.scene.add(m); this.smoke.planes.push(m);
    }
    this._smokeTexture(null);
    this.inset = new THREE.PerspectiveCamera(64, 1.6, 0.1, 80); this.inset.layers.set(PHYS);
  }
  // denser over the scanned room, spreading thinner across the floor
  _smokeTexture(room) {
    this._smokeRoom = room?.id || null;
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const ctx = c.getContext("2d"), { W, H } = this.smoke;
    const cx = room ? room.centre[0] : this.cx, cy = room ? room.centre[1] : this.cy;
    const px = (cx + 3) / W * 128, py = 128 - (cy + 3) / H * 128;
    const g = ctx.createRadialGradient(px, py, 0, px, py, 128 * 0.95);
    g.addColorStop(0, "#fff"); g.addColorStop(0.16, "#fff"); g.addColorStop(0.5, "#777"); g.addColorStop(1, "#3a3a3a");
    ctx.fillStyle = g; ctx.fillRect(0, 0, 128, 128);
    const tex = new THREE.CanvasTexture(c);
    for (const m of this.smoke.planes) { m.material.alphaMap?.dispose(); m.material.alphaMap = tex; m.material.needsUpdate = true; }
  }
  setSmoke(level) {
    this.smoke.level = Math.max(0, Math.min(1, level));
    for (const m of this.smoke.planes) { m.visible = this.smoke.level > 0.01; m.material.opacity = 0.62 * this.smoke.level; }
    if (this.opts.onInsetRect) this.opts.onInsetRect(this.smoke.level > 0.01 ? this._insetRect() : null);
  }
  _insetRect() {
    const W = this.el.clientWidth || 800, H = this.el.clientHeight || 600;
    const w = Math.round(Math.max(160, Math.min(250, W * 0.26))), h = Math.round(w * 0.6);
    return { x: 10, y: 10, w, h, W, H };
  }
  _pickSetup() {
    const dom = this.renderer.domElement, ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    const tip = this.tip = document.createElement("div"); tip.className = "tip3d"; tip.hidden = true; this.el.appendChild(tip);
    const hit = (ev) => {
      const targets = this.scout.pick.concat(this.beacon.pick);
      if (!targets.length) return null;
      const r = dom.getBoundingClientRect();
      ndc.set((ev.clientX - r.left) / r.width * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, this.camera);
      return ray.intersectObjects(targets, false)[0]?.object || null;
    };
    let last = 0, down = null;
    dom.addEventListener("pointermove", (ev) => {
      const now = performance.now(); if (now - last < 50) return; last = now;
      const o = hit(ev);
      if (!o) { tip.hidden = true; dom.style.cursor = ""; return; }
      const r = this.el.getBoundingClientRect();
      tip.textContent = o.userData.tip; tip.hidden = false;
      tip.style.left = Math.min(ev.clientX - r.left + 14, r.width - 260) + "px"; tip.style.top = ev.clientY - r.top + 14 + "px";
      dom.style.cursor = o.userData.pick ? "pointer" : "";
    });
    dom.addEventListener("pointerleave", () => (tip.hidden = true));
    dom.addEventListener("pointerdown", (ev) => (down = [ev.clientX, ev.clientY]));
    dom.addEventListener("pointerup", (ev) => {
      if (!down || Math.hypot(ev.clientX - down[0], ev.clientY - down[1]) > 5) return;
      const o = hit(ev); if (o?.userData.pick) this.opts.onPick?.(o.userData.pick);
    });
  }
  _frame() {
    const now = performance.now();
    for (const g of Object.values(this.units)) {
      if (g.target) g.position.lerp(g.target, 0.25);
      const blink = g.mayday && Math.floor(now / 350) % 2 === 0;
      g.ball.material.emissive.set(blink ? this.pal.danger : g.ball.material.color);
      g.beam.material.color.set(blink ? this.pal.danger : g.ball.material.color);
      g.ball.scale.setScalar(g.mayday ? 1.25 : 1);
    }
    if (this._tween) {
      const T = this._tween, u = REDUCED ? 1 : Math.min(1, (now - T.t0) / 1100), e = u * u * (3 - 2 * u);
      this.camera.position.lerpVectors(T.p0, T.p1, e); this.controls.target.lerpVectors(T.q0, T.q1, e);
      if (u >= 1) this._tween = null;
    }
    this.scout.frame(now); this.beacon.frame(now);
    const sm = this.smoke;
    if (sm.level > 0.01) for (const m of sm.planes) { m.material.map.offset.x += m.userData.drift[0] * 0.016; m.material.map.offset.y += m.userData.drift[1] * 0.016; }
    this.controls.update();
    const R = this.renderer;
    this.scene.fog.density = 0;
    R.setScissorTest(false);
    R.render(this.scene, this.camera);
    if (sm.level > 0.01) this._renderInset();
  }
  // "what a camera or LiDAR sees": the same building, from the door, through smoke (fog), with no radio layer
  _renderInset() {
    const R = this.renderer, r = this._insetRect(), room = this._scoutRoom, cam = this.inset;
    let eye, look;
    if (room) {
      eye = this.V(room.inside[0], room.inside[1], 1.15);
      const far = room.polygon.reduce((b, p) => (Math.hypot(p[0] - room.door[0], p[1] - room.door[1]) > Math.hypot(b[0] - room.door[0], b[1] - room.door[1]) ? p : b), room.polygon[0]);
      look = this.V((far[0] + room.centre[0]) / 2, (far[1] + room.centre[1]) / 2, 0.55);
    } else { eye = this.V(this.cx, 1.5, 1.15); look = this.V(this.cx, this.fy / 2, 0.5); }
    cam.position.copy(eye); cam.lookAt(look); cam.aspect = r.w / r.h; cam.updateProjectionMatrix();
    this.scene.fog.density = 0.02 + this.smoke.level * 0.95;
    const fogC = new THREE.Color(SMOKE_COLOR), bg = new THREE.Color(0x10151b).lerp(fogC, Math.min(1, this.smoke.level * 1.4));
    const prev = R.getClearColor(new THREE.Color()), prevA = R.getClearAlpha();
    R.setScissorTest(true); R.setScissor(r.x, r.y, r.w, r.h); R.setViewport(r.x, r.y, r.w, r.h);
    R.setClearColor(bg, 1); R.clear();
    R.render(this.scene, cam);
    R.setClearColor(prev, prevA);
    R.setScissorTest(false); R.setViewport(0, 0, r.W, r.H);
    this.scene.fog.density = 0;
  }
  resize() {
    const w = this.el.clientWidth || 800, h = this.el.clientHeight || 600;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = "100%"; this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    if (this.smoke && this.smoke.level > 0.01) this.opts.onInsetRect?.(this._insetRect());
  }
}
