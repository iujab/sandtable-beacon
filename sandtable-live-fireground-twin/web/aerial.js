// The outside view: aerial ortho with vector overlays, and every interior unit projected onto it
// through the chain twin frame -> latitude/longitude -> image pixel.
const NS = "http://www.w3.org/2000/svg";
const el = (name, attrs = {}, text) => {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
};

export class Aerial {
  constructor(img, svg, seg, srcLabel, bundle, colors) {
    this.img = img; this.svg = svg; this.bundle = bundle; this.colors = colors; this.srcLabel = srcLabel;
    this.images = bundle.aerial.images.filter((i) => i.kind === "ortho");
    this.units = {};
    seg.innerHTML = this.images.map((i, k) => `<button data-i="${k}" class="${k === 0 ? "on" : ""}">${i.id === "ortho" ? "Site" : "Approach"}</button>`).join("");
    for (const b of seg.querySelectorAll("button")) b.onclick = () => { for (const q of seg.querySelectorAll("button")) q.classList.toggle("on", q === b); this.show(Number(b.dataset.i)); };
    if (this.images.length < 2) seg.hidden = true;
    this.show(0);
  }
  px(lat, lon) {
    const [w, s, e, n] = this.im.bbox;
    return [(lon - w) / (e - w) * this.im.width, (n - lat) / (n - s) * this.im.height];
  }
  geo(x, y) {
    const g = this.bundle.twin.georef, b = g.bearing_deg * Math.PI / 180;
    const east = x * Math.sin(b) - y * Math.cos(b), north = x * Math.cos(b) + y * Math.sin(b);
    return [g.origin.lat + north / 111320, g.origin.lon + east / (111320 * Math.cos(g.origin.lat * Math.PI / 180))];
  }
  show(k) {
    const im = this.im = this.images[k], site = this.bundle.site, twin = this.bundle.twin;
    this.img.src = im.url || "/" + im.path;
    this.srcLabel.textContent = `${im.synthetic ? "synthetic" : "real"} imagery: ${im.source}${im.capture_date ? ", " + im.capture_date : ""} · ${im.gsd_m} m/px${/Vexcel/i.test(im.source) ? "" : " · not Vexcel"}`;
    const svg = this.svg;
    svg.setAttribute("viewBox", `0 0 ${im.width} ${im.height}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid slice");
    svg.replaceChildren();
    const k1 = im.width / 420;                                  // overlay size relative to the picture
    const ring = site.footprint.coordinates[0].map(([lon, lat]) => this.px(lat, lon).join(",")).join(" ");
    svg.appendChild(el("polygon", { points: ring, class: "a-foot" }));
    const fl = twin.floors.find((f) => f.id === twin.exercise_floor).outline.map((p) => this.px(...this.geo(p[0], p[1])).join(",")).join(" ");
    svg.appendChild(el("polygon", { points: fl, class: "a-floor" }));
    const c = this.px(site.property.lat, site.property.lon), fr = site.frame || {};
    const reach = (Math.max(fr.length_m || 40, fr.width_m || 40) / 2 + 9) / im.gsd_m;
    for (const [letter, s] of Object.entries(site.sides || {})) {
      const b = s.bearing_deg * Math.PI / 180, p = [c[0] + reach * Math.sin(b), c[1] - reach * Math.cos(b)];
      const g = el("g", { class: "a-side" });
      g.appendChild(el("circle", { cx: p[0], cy: p[1], r: 9 * k1 }));
      g.appendChild(el("text", { x: p[0], y: p[1], style: `font-size:${12 * k1}px` }, letter));
      g.appendChild(el("title", {}, `Side ${letter}${s.street ? ": " + s.street : ""}`));
      svg.appendChild(g);
    }
    for (const h of site.context.hydrants || []) {
      const p = this.px(h.lat, h.lon);
      if (p[0] < 0 || p[1] < 0 || p[0] > im.width || p[1] > im.height) continue;
      const m = el("circle", { cx: p[0], cy: p[1], r: 4.5 * k1, class: "a-hyd" });
      m.appendChild(el("title", {}, `Hydrant ${h.id}${h.size ? " (" + h.size + ")" : ""}: ${h.distance_m} m from the building. ${h.source}`));
      svg.appendChild(m);
    }
    for (const e of site.context.entrances || []) {
      const p = this.px(e.lat, e.lon);
      svg.appendChild(el("path", { d: `M${p[0] - 6 * k1} ${p[1]} L${p[0]} ${p[1] - 8 * k1} L${p[0] + 6 * k1} ${p[1]} Z`, fill: "#fff", stroke: "#000", "stroke-width": 1 }));
    }
    for (const a of site.context.apparatus_positions || []) {
      const p = this.px(a.lat, a.lon);
      svg.appendChild(el("rect", { x: p[0] - 6 * k1, y: p[1] - 4 * k1, width: 12 * k1, height: 8 * k1, rx: 2, class: "a-eng" }));
      svg.appendChild(el("text", { x: p[0] + 9 * k1, y: p[1] + 4 * k1, class: "a-lab", style: `font-size:${11 * k1}px` }, a.label));
    }
    this.layer = el("g"); svg.appendChild(this.layer);
    this.blayer = el("g"); svg.appendChild(this.blayer);
    this.vlayer = el("g"); svg.appendChild(this.vlayer);
    this.units = {}; this.k1 = k1;
    if (this.last) this.update(this.last);
    if (this.victim) this.setVictim(this.victim, this.scan);
    if (this.bview) { const v = this.bview; this.bview = null; this._bkey = null; this.setBeacons(v); }
  }
  // phones from the Beacon module, through the same chain; a ring stays a ring, candidates stay candidates
  setBeacons(view) {
    const key = view ? view.key : null;
    if (key === this._bkey) return;
    this._bkey = key; this.bview = view;
    this.blayer.replaceChildren();
    if (!view) return;
    const k1 = this.k1, P = (x, y) => this.px(...this.geo(x, y)), gsd = this.im.gsd_m;
    for (const ph of view.b.phones || []) {
      const t = el("title", {}, ph.text || ph.id);
      if (ph.geometry === "fix" && ph.x != null) {
        const p = P(ph.x, ph.y), g = el("g");
        g.append(el("circle", { cx: p[0], cy: p[1], r: Math.max(5 * k1, 2 * (ph.sigma_m || 0.3) / gsd), class: "a-vic-ring" }), el("circle", { cx: p[0], cy: p[1], r: 3.6 * k1, class: "a-vic" }),
          el("text", { x: p[0] + 6 * k1, y: p[1] - 5 * k1, class: "a-lab a-vic-lab", style: `font-size:${10 * k1}px` }, ph.id), t);
        this.blayer.appendChild(g);
      } else if (ph.geometry === "candidates") {
        const g = el("g");
        for (const c of ph.candidates || []) { const p = P(c.x, c.y); g.appendChild(el("circle", { cx: p[0], cy: p[1], r: 3.2 * k1, class: "a-cand" })); }
        const p = P(ph.candidates[0].x, ph.candidates[0].y);
        g.append(el("text", { x: p[0] + 6 * k1, y: p[1] - 5 * k1, class: "a-lab a-vic-lab", style: `font-size:${10 * k1}px` }, `${ph.id}?`), t);
        this.blayer.appendChild(g);
      } else if (ph.geometry === "ring" && ph.ring) {
        const R = ph.ring, g = el("g");
        for (const [a0, a1] of R.arcs || [[0, 360]]) {
          const pts = [];
          for (let a = a0; a <= a1 + 0.01; a += 4) pts.push(P(R.cx + R.r_m * Math.cos(a * Math.PI / 180), R.cy + R.r_m * Math.sin(a * Math.PI / 180)).map((v) => v.toFixed(1)).join(","));
          g.appendChild(el("polyline", { points: pts.join(" "), class: R.kind === "ble" ? "a-ring ble" : "a-ring", "stroke-width": R.kind === "ble" ? Math.max(2, 2 * R.sigma_m / gsd) : 2 }));
        }
        g.appendChild(t); this.blayer.appendChild(g);
      } else if (ph.geometry === "zone" && ph.zone?.polygon) {
        const g = el("polygon", { points: ph.zone.polygon.map((q) => P(q[0], q[1]).join(",")).join(" "), class: "a-zone" });
        g.appendChild(t); this.blayer.appendChild(g);
      }
    }
  }
  // a person located by the scout scan, projected through the same chain (twin -> lat/lon -> pixel)
  setVictim(v, scan) {
    this.victim = v; this.scan = scan;
    this.vlayer.replaceChildren();
    if (!v || v.x == null) return;
    const g = v.geo ? [v.geo.lat, v.geo.lon] : this.geo(v.x, v.y), p = this.px(g[0], g[1]), k1 = this.k1;
    const r = Math.max(6 * k1, (v.sigma_m || 0.4) / this.im.gsd_m);
    this.vlayer.append(el("circle", { cx: p[0], cy: p[1], r: r * 2.2, class: "a-vic-ring" }), el("circle", { cx: p[0], cy: p[1], r: 4.5 * k1, class: "a-vic" }),
      el("text", { x: p[0] - 9 * k1, y: p[1] + 14 * k1, class: "a-lab a-vic-lab", style: `font-size:${11 * k1}px`, "text-anchor": "end" }, `${v.id} ${scan?.simulated ? "(sim)" : ""}`));
    this.vlayer.lastChild.appendChild(el("title", {}, `${v.id}: located by the scout scan, ±${v.sigma_m ?? "?"} m`));
  }
  update(st) {
    this.last = st;
    for (const u of st.units) {
      if (u.x == null || !u.geo) continue;
      let g = this.units[u.id];
      if (!g) {
        const color = this.colors[u.id] || "#fff";
        g = this.units[u.id] = el("g");
        g.ring = el("circle", { class: "a-unit-ring", stroke: color, r: 11 * this.k1 });
        g.dot = el("circle", { class: "a-unit", fill: color, r: 5 * this.k1 });
        g.lab = el("text", { class: "a-lab", style: `font-size:${11 * this.k1}px;fill:${color}` }, u.id);
        g.append(g.ring, g.dot, g.lab);
        this.layer.appendChild(g);
      }
      const p = this.px(u.geo.lat, u.geo.lon);
      for (const c of [g.ring, g.dot]) { c.setAttribute("cx", p[0]); c.setAttribute("cy", p[1]); }
      g.lab.setAttribute("x", p[0] + 9 * this.k1); g.lab.setAttribute("y", p[1] - 8 * this.k1);
      g.ring.style.stroke = u.status === "mayday" ? "#ff3b30" : (this.colors[u.id] || "#fff");
    }
  }
}
